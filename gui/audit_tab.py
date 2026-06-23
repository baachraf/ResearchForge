"""Audit My Paper tab — LLM-based pre-submission self-audit of a PDF.

Two modes:
  Internal Review  — paper PDF only (paper_review_prompt / paper_review_synthesis_prompt)
  Full Audit       — paper PDF + session synthesis (paper_audit_prompt / paper_audit_synthesis_prompt)

PDF context options:
  Section-by-section — detect sections locally, audit each section, then synthesize
  Full paper         — single LLM call with full text (needs large-context model, 32K+)

LaTeX source (.tex) is recommended over PDF: section boundaries are exact, math is
preserved, and there are no OCR or column-order artefacts.
"""
import os
import re
import json
import time
from pathlib import Path
from datetime import datetime
from collections import OrderedDict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QTextEdit, QLineEdit, QFileDialog, QMessageBox,
    QSizePolicy, QFrame, QGridLayout, QButtonGroup,
    QDialog, QSplitter, QListWidget, QDialogButtonBox,
)
from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtGui import QFont, QColor, QPainter, QBrush

from gui.llm_provider import (
    create_llm_client, get_provider_api_key,
    ensure_llm_available, provider_name_from_config,
)
from gui.app_info import APP_VERSION
from gui import paths

def _detect_pdf_title(pdf_path: str) -> str:
    """Return the paper title from a PDF or LaTeX file.
    Returns empty string if nothing reliable is found."""
    if pdf_path.lower().endswith('.tex'):
        try:
            from pathlib import Path as _Path
            content = _Path(pdf_path).read_text(encoding='utf-8', errors='replace')
            m = re.search(r'\\title\s*\{([^}]+)\}', content)
            if m:
                title = m.group(1).strip()
                title = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', title)
                title = re.sub(r'\\[a-zA-Z]+', '', title).strip()
                if len(title) > 8:
                    return title
        except Exception:
            pass
        return ""

    try:
        import fitz
        doc = fitz.open(pdf_path)
        try:
            # 1. Embedded metadata — fastest and most reliable when present
            meta = (doc.metadata.get("title") or "").strip()
            if meta and len(meta) > 8 and meta.lower() not in ("untitled", "unknown"):
                return meta

            # 2. First page: largest font span that looks like a title
            page = doc[0]
            spans = []
            for blk in page.get_text("dict", flags=0).get("blocks", []):
                if blk.get("type") != 0:
                    continue
                for ln in blk.get("lines", []):
                    for sp in ln.get("spans", []):
                        t = sp.get("text", "").strip()
                        if t:
                            spans.append((sp.get("size", 0), t))

            if not spans:
                return ""

            spans.sort(key=lambda x: x[0], reverse=True)
            skip_words = {"abstract", "introduction", "ieee", "arxiv", "preprint",
                          "conference", "journal", "proceedings", "workshop"}
            for _size, text in spans:
                if len(text) < 10 or len(text) > 250:
                    continue
                if any(w in text.lower() for w in skip_words):
                    continue
                if "@" in text or text.count(".") > 3:   # skip email / DOI lines
                    continue
                return text
        finally:
            doc.close()
    except Exception:
        pass
    return ""


def _title_to_slug(title: str, max_words: int = 6, min_words: int = 4,
                   max_chars: int = 55) -> str:
    """Convert a paper title to an underscore-separated filename slug.
    Thin wrapper around ``paths.title_to_slug`` so existing call sites keep
    working; the canonical implementation lives in ``gui.paths`` so the
    headless API produces identical slugs."""
    return paths.title_to_slug(title, max_words=max_words, min_words=min_words,
                               max_chars=max_chars)


def _make_audit_filename(title: str, source_type: str = "pdf") -> str:
    """Build the default audit save name. Delegates to ``paths.audit_filename``
    so the GUI and the API produce byte-identical filenames (and API-saved
    bundles appear in the GUI's Load Audit dialog)."""
    return paths.audit_filename(title, source_type=source_type)


# ─────────────────────────────────────────────────────────────────────────────
#  Score helpers
# ─────────────────────────────────────────────────────────────────────────────

_SCORE_COLOR_BREAKS = [
    (80, "#27ae60"),   # green
    (65, "#f39c12"),   # amber
    (40, "#e67e22"),   # orange
    (0,  "#e74c3c"),   # red
]

def _score_to_color(score: int) -> str:
    for threshold, color in _SCORE_COLOR_BREAKS:
        if score >= threshold:
            return color
    return "#e74c3c"


def _parse_scores(text: str) -> dict:
    """Extract integer scores from the SCORES…END_SCORES block in LLM output."""
    cleaned = re.sub(r'```\w*\n?', '', text)   # strip code fences
    m = re.search(r'\bSCORES\b(.*?)\bEND_SCORES\b', cleaned, re.DOTALL | re.IGNORECASE)
    if not m:
        return {}
    scores = {}
    for line in m.group(1).splitlines():
        # Accept "Key: 75" or "**Key**: 75" or "Key : 75"
        kv = re.match(r'^\s*\**([A-Za-z][A-Za-z_\s]*?)\**\s*:\s*(\d+)', line)
        if kv:
            # Normalise key: title-case then replace spaces with underscores
            key = kv.group(1).strip().title().replace(' ', '_')
            scores[key] = min(100, max(0, int(kv.group(2))))
    return scores


def _strip_scores_block(text: str) -> str:
    """Remove the machine-readable SCORES block; it is shown visually in the panel."""
    text = re.sub(r'```\w*\n?\bSCORES\b.*?\bEND_SCORES\b\n?```\n?', '',
                  text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\n?\bSCORES\b.*?\bEND_SCORES\b\n?', '\n',
                  text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def _parse_questions(text: str) -> str:
    """Extract the QUESTIONS FOR THE AUTHOR block. Returns '' when absent or 'no questions'."""
    m = re.search(
        r'QUESTIONS\s+FOR\s+THE\s+AUTHOR\s*\n(.*?)(?=\n\s*SCORES\b|\Z)',
        text, re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return ""
    # Strip separator lines (━ ─ ═) and leading/trailing whitespace
    content = re.sub(r'^[━─═\s]+$', '', m.group(1), flags=re.MULTILINE).strip()
    if not content or re.match(r'^no questions', content, re.IGNORECASE):
        return ""
    return content


def _strip_questions_block(text: str) -> str:
    """Remove the QUESTIONS block from the results text; it is shown in its own panel."""
    text = re.sub(
        r'\n?[━─═]*\s*QUESTIONS\s+FOR\s+THE\s+AUTHOR.*?(?=\n\s*SCORES\b|\Z)',
        '\n', text, flags=re.DOTALL | re.IGNORECASE,
    )
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
#  Score panel widget
# ─────────────────────────────────────────────────────────────────────────────

class _ScoreOverallBox(QWidget):
    """Rounded colored box for the overall score.
    Draws its own background via paintEvent so it is never invisible
    regardless of QSS cascade from parent widgets."""

    _EMPTY_HEX = "#95a5a6"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hex = self._EMPTY_HEX
        self.setFixedHeight(92)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(6, 4, 6, 4)
        hl.setSpacing(0)

        self.lbl_score = QLabel()
        self.lbl_score.setAlignment(Qt.AlignCenter)
        self.lbl_score.setTextFormat(Qt.RichText)
        self.lbl_score.setStyleSheet("background:transparent; border:none;")
        self._render(None)
        hl.addWidget(self.lbl_score)

    def _render(self, value):
        if value is None:
            self.lbl_score.setText(
                "<span style='font-size:40px;font-weight:900;color:#fff;'>—</span>"
            )
        else:
            self.lbl_score.setText(
                f"<span style='font-size:40px;font-weight:900;color:#fff;'>"
                f"{value}/100</span>"
            )

    def set_score(self, value):
        """value = int or None to reset."""
        self._render(value)
        self._hex = self._EMPTY_HEX if value is None else _score_to_color(int(value))
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(self._hex)))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), 12, 12)
        p.end()


class _ScorePanel(QFrame):
    """Right-side panel: overall score box + one colored chip per dimension."""

    _DIMS = [
        ("Abstract",              "Abstract"),
        ("Contributions",         "Contributions"),
        ("Claims",                "Claims"),
        ("References",            "References"),
        ("Transitions",           "Transitions"),
        ("Discussion",            "Discussion"),
        ("Conclusion",            "Conclusion"),
        ("Reproducibility",       "Reproducibility"),
        ("Statistical_Reporting", "Stat. Reporting"),
        ("Style",                 "Style"),
    ]
    _CHIP_EMPTY = (
        "font-size:12px; font-weight:bold; color:#fff;"
        " background:#bdc3c7; border-radius:5px;"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedWidth(225)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background:#f0f3f7; border-radius:10px;")
        self._chips: dict = {}
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.setContentsMargins(10, 12, 10, 12)

        # ── header ───────────────────────────────────────────────────────────
        hdr = QLabel("PAPER SCORE")
        hdr.setAlignment(Qt.AlignCenter)
        hdr.setStyleSheet(
            "font-size:10px; font-weight:bold; color:#95a5a6;"
            " letter-spacing:2px; background:transparent;"
        )
        outer.addWidget(hdr)
        outer.addSpacing(8)

        # ── overall colored box (paintEvent-based, always visible) ────────────
        self.overall_box = _ScoreOverallBox()
        outer.addWidget(self.overall_box)
        outer.addSpacing(10)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet("background:#c8cdd5; max-height:1px;")
        outer.addWidget(sep)
        outer.addSpacing(4)

        # ── dimension rows — 2 px gap, equal height share ─────────────────────
        dims_w = QWidget()
        dims_w.setStyleSheet("background:transparent;")
        dims_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        dims_lay = QVBoxLayout(dims_w)
        dims_lay.setContentsMargins(0, 0, 0, 0)
        dims_lay.setSpacing(2)

        for key, label in self._DIMS:
            row_w = QWidget()
            row_w.setStyleSheet("background:transparent;")
            row_w.setMinimumHeight(24)
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(2, 0, 2, 0)
            rl.setSpacing(6)

            name_lbl = QLabel(label)
            name_lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            name_lbl.setStyleSheet(
                "font-size:12px; color:#2c3e50; background:transparent;"
            )
            rl.addWidget(name_lbl, 1)

            chip = QLabel("—")
            chip.setAlignment(Qt.AlignCenter)
            chip.setFixedSize(50, 22)
            chip.setStyleSheet(self._CHIP_EMPTY)
            rl.addWidget(chip)

            self._chips[key] = chip
            dims_lay.addWidget(row_w, 1)

        outer.addWidget(dims_w, 1)

    def update_scores(self, scores: dict):
        self.overall_box.set_score(scores.get("Overall"))

        for key, chip in self._chips.items():
            score = scores.get(key)
            if score is None:
                chip.setText("—")
                chip.setStyleSheet(self._CHIP_EMPTY)
            else:
                c = _score_to_color(int(score))
                chip.setText(str(score))
                chip.setStyleSheet(
                    f"font-size:12px; font-weight:bold; color:#fff;"
                    f" background:{c}; border-radius:5px;"
                )

    def clear(self):
        self.overall_box.set_score(None)
        for chip in self._chips.values():
            chip.setText("—")
            chip.setStyleSheet(self._CHIP_EMPTY)


def _resolve_audit_dir(cfg) -> Path:
    return Path(paths.audit_root(cfg))


# ─────────────────────────────────────────────────────────────────────────────
#  Section panel widget
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_STYLE = {
    "waiting":  ("○", "",        "normal"),
    "running":  ("▶", "#3498db", "bold"),
    "done":     ("✓", "#27ae60", "bold"),
    "error":    ("✗", "#e67e22", "bold"),
}


class _SectionPanel(QFrame):
    """2-column grid showing detected sections with live audit-progress status."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self._widgets: dict = {}   # name → (icon_lbl, chars_lbl)
        self._sections: OrderedDict = OrderedDict()
        self._method: str = ""
        self._collapsed = False
        self._setup_ui()
        self.hide()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        # ── header row (clicking anywhere toggles collapse) ─────────────────
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        self.lbl_header = QLabel("Detected sections")
        self.lbl_header.setStyleSheet("font-weight: 700; font-size: 11px;")
        hdr.addWidget(self.lbl_header)
        hdr.addStretch()
        self.lbl_inspect = QLabel("[ inspect ]")
        self.lbl_inspect.setStyleSheet(
            "font-size: 10px; color: #888; text-decoration: underline; cursor: pointer;")
        self.lbl_inspect.setToolTip("View the full text of each detected section")
        self.lbl_inspect.mousePressEvent = lambda _e: self._open_inspector()
        hdr.addWidget(self.lbl_inspect)
        hdr.addSpacing(10)
        self.lbl_toggle = QLabel("[ hide ]")
        self.lbl_toggle.setStyleSheet(
            "font-size: 10px; color: #888; text-decoration: underline; cursor: pointer;")
        self.lbl_toggle.setToolTip("Collapse / expand section list")
        self.lbl_toggle.mousePressEvent = lambda _e: self._toggle()
        hdr.addWidget(self.lbl_toggle)
        outer.addLayout(hdr)

        # ── grid content (4 sections per row, 8 columns) ────────────────────
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 2, 0, 2)
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(1)
        for col in (0, 2, 4, 6):   # name columns — expand
            self.grid.setColumnStretch(col, 3)
        for col in (1, 3, 5, 7):   # chars columns — fixed-ish
            self.grid.setColumnStretch(col, 1)
        outer.addWidget(self.content)

    # ── public API ──────────────────────────────────────────────────────────

    def set_sections(self, sections: dict, method: str):
        """Populate the grid. sections is an OrderedDict {name: text}."""
        self._sections = sections
        self._method   = method
        # clear old widgets
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._widgets = {}

        names = list(sections.keys())
        n = len(names)

        for i, name in enumerate(names):
            chars = len(sections[name])
            row      = i // 4
            col_base = (i % 4) * 2          # 0, 2, 4, or 6

            icon_name_lbl = QLabel(f"○  {name}")
            icon_name_lbl.setProperty("section_key", name)
            icon_name_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

            chars_lbl = QLabel(f"{chars:,} ch")
            chars_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            chars_lbl.setStyleSheet("color: #888; font-size: 11px;")
            chars_lbl.setFixedWidth(58)
            chars_lbl.setToolTip(f"{name}: first 120 chars\n{sections[name][:120]}…")

            self.grid.addWidget(icon_name_lbl, row, col_base)
            self.grid.addWidget(chars_lbl,     row, col_base + 1)
            self._widgets[name] = (icon_name_lbl, chars_lbl)

        # synthesis row spans all 8 columns
        synth_row = (n + 3) // 4
        synth_lbl = QLabel("○  Final synthesis")
        synth_lbl.setProperty("section_key", "__synthesis__")
        synth_lbl.setStyleSheet("color: #888; font-size: 11px; font-style: italic;")
        self.grid.addWidget(synth_lbl, synth_row, 0, 1, 8)
        self._widgets["__synthesis__"] = (synth_lbl, None)

        # footer
        method_str = {
            "font":     "font metadata",
            "regex":    "text patterns",
            "fallback": "character split",
            "latex":    "LaTeX source",
        }.get(method, method)
        total = sum(len(t) for t in sections.values())
        footer = QLabel(f"  {n} sections · {total:,} chars · detected via {method_str}")
        footer.setStyleSheet("color: #888; font-size: 10px; font-style: italic;")
        self.grid.addWidget(footer, synth_row + 1, 0, 1, 8)

        self.lbl_header.setText(f"Detected sections  ·  {n} found")
        if n < 3:
            self.lbl_header.setStyleSheet(
                "font-weight: 700; font-size: 11px; color: #e67e22;")
        else:
            self.lbl_header.setStyleSheet("font-weight: 700; font-size: 11px;")

        self.content.setVisible(not self._collapsed)
        self.show()

    def update_status(self, section_name: str, status: str):
        entry = self._widgets.get(section_name)
        if not entry:
            return
        lbl = entry[0]
        key = lbl.property("section_key") or section_name
        display = key if key != "__synthesis__" else "Final synthesis"

        icon, color, weight = _STATUS_STYLE.get(status, _STATUS_STYLE["waiting"])
        lbl.setText(f"{icon}  {display}")
        is_synth = (section_name == "__synthesis__")
        if not color and is_synth:
            color = "#888"   # synthesis row stays gray while waiting
        style = f"color: {color}; font-weight: {weight};" if color else f"font-weight: {weight};"
        if is_synth:
            style += " font-size: 11px; font-style: italic;"
        lbl.setStyleSheet(style)

    def reset_statuses(self):
        for name in self._widgets:
            self.update_status(name, "waiting")

    def _toggle(self):
        self._collapsed = not self._collapsed
        self.content.setVisible(not self._collapsed)
        self.lbl_toggle.setText("[ show ]" if self._collapsed else "[ hide ]")

    def _open_inspector(self):
        if not self._sections:
            return
        dlg = _SectionInspectorDialog(self._sections, self._method, self)
        dlg.exec()


# ─────────────────────────────────────────────────────────────────────────────
#  Section inspector dialog
# ─────────────────────────────────────────────────────────────────────────────

class _SectionInspectorDialog(QDialog):
    """Read-only 2-panel dialog: section list (left) + full section text (right)."""

    def __init__(self, sections: "OrderedDict[str, str]", method: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Section Split Inspector")
        self.resize(920, 600)
        self._sections = sections
        self._names    = list(sections.keys())

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # ── splitter ─────────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Left: section list
        self._list = QListWidget()
        for name in self._names:
            chars = len(sections[name])
            self._list.addItem(f"{name}  ({chars:,} ch)")
        self._list.setFixedWidth(210)
        self._list.currentRowChanged.connect(self._on_row_changed)
        splitter.addWidget(self._list)

        # Right: text viewer
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        self._chars_lbl = QLabel("")
        self._chars_lbl.setStyleSheet("color: #888; font-size: 11px;")
        right_layout.addWidget(self._chars_lbl)
        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)
        mono = QFont("Courier New", 10)
        mono.setStyleHint(QFont.Monospace)
        self._text_edit.setFont(mono)
        right_layout.addWidget(self._text_edit)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter, 1)

        # ── footer ────────────────────────────────────────────────────────────
        method_str = {
            "font":     "font metadata",
            "regex":    "text patterns",
            "fallback": "character split",
            "latex":    "LaTeX source",
        }.get(method, method)
        total  = sum(len(t) for t in sections.values())
        footer = QLabel(
            f"{len(self._names)} sections · {total:,} total chars · "
            f"detected via {method_str}")
        footer.setStyleSheet("color: #888; font-size: 11px; font-style: italic;")
        layout.addWidget(footer)

        # ── close button ──────────────────────────────────────────────────────
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.accept)
        layout.addWidget(btns)

        if self._names:
            self._list.setCurrentRow(0)

    def _on_row_changed(self, row: int):
        if row < 0 or row >= len(self._names):
            self._text_edit.clear()
            self._chars_lbl.setText("")
            return
        name = self._names[row]
        text = self._sections[name]
        self._text_edit.setPlainText(text)
        self._chars_lbl.setText(f"Section: {name} · {len(text):,} chars")


# ─────────────────────────────────────────────────────────────────────────────
#  Background worker
# ─────────────────────────────────────────────────────────────────────────────

class _AuditWorker(QThread):
    progress       = Signal(str)
    result         = Signal(str)
    error          = Signal(str)
    section_status = Signal(str, str)   # (section_name, status)

    def __init__(self, pdf_path: str, prompt: str, cfg,
                 pdf_char_limit=None,
                 section_mode: bool = False,
                 sections: dict = None,
                 section_preaudit_prompt: str = "",
                 synthesis_prompt: str = ""):
        super().__init__()
        self.pdf_path                = pdf_path
        self.prompt                  = prompt
        self.cfg                     = cfg
        self.pdf_char_limit          = pdf_char_limit
        self.section_mode            = section_mode
        self.sections                = sections or {}
        self.section_preaudit_prompt = section_preaudit_prompt
        self.synthesis_prompt        = synthesis_prompt
        self._stop                   = False

    def stop(self):
        self._stop = True

    def run(self):
        client = None
        try:
            provider_name = provider_name_from_config(self.cfg)
            endpoint = self.cfg.get("llm_endpoint", "")
            api_key  = get_provider_api_key(provider_name, self.cfg)
            model    = self.cfg.get("llm_model", "")
            client   = create_llm_client(endpoint, api_key, provider_name, timeout=120.0)

            if self.section_mode and self.sections:
                self._run_section_mode(client, model)
            else:
                self._run_single_mode(client, model)

        except Exception as e:
            self.error.emit(str(e))
        finally:
            if client:
                try:
                    if hasattr(client, '_client') and hasattr(client._client, 'close'):
                        client._client.close()
                except Exception:
                    pass
                time.sleep(0.5)

    # ── single-call path (existing behaviour) ───────────────────────────────

    def _run_single_mode(self, client, model):
        if self.pdf_path.lower().endswith('.tex'):
            self.progress.emit("Extracting LaTeX text…")
            from gui.section_detector import extract_latex_text
            full_text = extract_latex_text(self.pdf_path)
            if self._stop:
                return
            excerpt   = full_text[:self.pdf_char_limit] if self.pdf_char_limit else full_text
            truncated = self.pdf_char_limit and len(full_text) > self.pdf_char_limit
            pct  = int(100 * len(excerpt) / len(full_text)) if full_text else 100
            note = (f" (truncated to {pct}% of .tex source)"
                    if truncated else f" (.tex source, {len(excerpt):,} chars)")
        else:
            import fitz
            self.progress.emit("Extracting PDF text…")
            doc = fitz.open(self.pdf_path)
            try:
                full_text = "\n".join(page.get_text() for page in doc)
                n_pages   = len(doc)
            finally:
                doc.close()

            if self._stop:
                return

            excerpt   = full_text[:self.pdf_char_limit] if self.pdf_char_limit else full_text
            truncated = self.pdf_char_limit and len(full_text) > self.pdf_char_limit
            pct  = int(100 * len(excerpt) / len(full_text)) if full_text else 100
            note = (f" (truncated to {pct}% of {n_pages}-page PDF)"
                    if truncated else f" ({n_pages} pages, {len(excerpt):,} chars)")
        self.progress.emit(f"Calling LLM with paper text{note}…")

        filled = self.prompt.replace("{pdf_text}", excerpt)

        if self._stop:
            return

        resp   = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": filled}],
            temperature=0.2,
            max_tokens=4096,
        )
        output = resp.choices[0].message.content or ""

        if not output.strip():
            self.error.emit(
                "The LLM returned an empty response.\n\n"
                "Possible causes:\n"
                "  • The paper text + prompt exceeds the model's context window\n"
                "  • The model hit a safety filter or output token limit\n\n"
                "Try: a different model, a shorter excerpt, or increase max output tokens.")
            return

        self.result.emit(output)

    # ── section-by-section path ─────────────────────────────────────────────

    # Per-section finding is capped before synthesis assembly so the total
    # synthesis input stays within 8K-context models (8 sections × 1500 chars
    # ≈ 3000 tokens input, leaving headroom for the prompt and 6K output).
    _MAX_FINDING_CHARS = 1500

    def _run_section_mode(self, client, model):
        findings = []
        total = len(self.sections)

        for i, (name, text) in enumerate(self.sections.items()):
            if self._stop:
                return

            self.section_status.emit(name, "running")
            self.progress.emit(f"Auditing {name} ({i + 1}/{total})…")

            section_text = text[:12000]   # cap per section
            filled = (self.section_preaudit_prompt
                      .replace("{section_name}", name)
                      .replace("{section_text}", section_text))

            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": filled}],
                    temperature=0.2,
                    max_tokens=1024,
                )
                body = (resp.choices[0].message.content or "").strip() or "No issues found."
                self.section_status.emit(name, "done")
            except Exception as exc:
                body = f"(section analysis failed: {exc})"
                self.section_status.emit(name, "error")

            # Truncate before assembly: verbose per-section findings would push
            # the synthesis call over the model's context window.
            if len(body) > self._MAX_FINDING_CHARS:
                body = body[:self._MAX_FINDING_CHARS] + "\n…[truncated for synthesis]"

            findings.append(f"=== {name.upper()} ===\n{body}")

        if self._stop:
            return

        self.section_status.emit("__synthesis__", "running")
        self.progress.emit("Synthesizing final report…")

        section_audits = "\n\n".join(findings)
        filled = self.synthesis_prompt.replace("{section_audits}", section_audits)

        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": filled}],
            temperature=0.2,
            max_tokens=6000,   # raised from 4096: long reports + SCORES block at the end
        )
        output = (resp.choices[0].message.content or "").strip()

        if not output:
            self.section_status.emit("__synthesis__", "error")
            self.error.emit(
                "The LLM returned an empty response for the synthesis step.\n\n"
                "Possible causes:\n"
                "  • Section findings + synthesis prompt exceeds the model's context window\n"
                "  • The model hit a safety filter or output token limit\n\n"
                "Try a larger-context model or increase max output tokens.")
            return

        self.section_status.emit("__synthesis__", "done")
        self.result.emit(output)


# ─────────────────────────────────────────────────────────────────────────────
#  Questions dialog
# ─────────────────────────────────────────────────────────────────────────────

class _QuestionsDialog(QDialog):
    """Modal dialog showing the LLM's unanswered questions for the author."""

    def __init__(self, questions_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Questions for the Author")
        self.resize(640, 380)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        hdr = QLabel(
            "The following points could not be determined from the paper text.\n"
            "If a question is wrong, the relevant section may need to be clarified."
        )
        hdr.setWordWrap(True)
        hdr.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(hdr)

        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Segoe UI", 11))
        if questions_text:
            body.setPlainText(questions_text)
        else:
            body.setPlainText("No questions for the author — all information was found in the paper.")
        layout.addWidget(body, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.accept)
        layout.addWidget(btns)


# ─────────────────────────────────────────────────────────────────────────────
#  Main tab widget
# ─────────────────────────────────────────────────────────────────────────────

class AuditTab(QWidget):
    def __init__(self, cfg, log_signal: Signal, parent=None):
        super().__init__(parent)
        self.cfg      = cfg
        self.log      = log_signal
        self._pdf_path      = ""
        self._paper_title   = ""
        self._sections      = OrderedDict()
        self._last_scores: dict = {}
        self._questions_text: str = ""
        self._worker        = None
        self._closing       = False
        self._setup_ui()

    # ── UI construction ──────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(3)
        layout.setContentsMargins(12, 12, 12, 12)

        # ── Row 1: file picker ───────────────────────────────────────────────
        pdf_row = QHBoxLayout()
        self.btn_browse_pdf = QPushButton("📄 Browse Paper")
        self.btn_browse_pdf.setObjectName("btn_search")
        self.btn_browse_pdf.clicked.connect(self._browse_pdf)
        pdf_row.addWidget(self.btn_browse_pdf)
        self.lbl_pdf = QLabel("No file selected")
        self.lbl_pdf.setObjectName("sc_hint")
        pdf_row.addWidget(self.lbl_pdf, 1)
        lbl_tex_tip = QLabel("💡 LaTeX source (.tex) recommended")
        lbl_tex_tip.setObjectName("sc_hint")
        lbl_tex_tip.setToolTip(
            "LaTeX source gives exact section boundaries, preserves math, and avoids\n"
            "PDF column-order and font-detection artefacts.")
        pdf_row.addWidget(lbl_tex_tip)
        layout.addLayout(pdf_row)

        # ── Section panel (shown only in section mode after PDF loaded) ──────
        self.section_panel = _SectionPanel()
        layout.addWidget(self.section_panel)

        # ── Row 2: Run/Stop  |  PDF context ─────────────────────────────────
        run_row = QHBoxLayout()
        self.btn_run = QPushButton("▶ Run Audit")
        self.btn_run.setObjectName("btn_search")
        self.btn_run.clicked.connect(self._run_audit)
        run_row.addWidget(self.btn_run)
        self.btn_stop = QPushButton("⏹ Stop")
        self.btn_stop.setStyleSheet("background: #c0392b; color: #fff;")
        self.btn_stop.clicked.connect(self._stop_audit)
        self.btn_stop.hide()
        run_row.addWidget(self.btn_stop)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setFrameShadow(QFrame.Sunken)
        sep2.setFixedWidth(2)
        run_row.addSpacing(8)
        run_row.addWidget(sep2)
        run_row.addSpacing(8)

        # PDF context as mutually-exclusive radio buttons
        _ctx_defs = [
            ("Section-by-section",
             "sections",
             "Detects Abstract / Intro / Methods / … locally, audits each section\n"
             "in a separate LLM call, then synthesizes. Works with any model size."),
            ("Full paper  (large-context model required)",
             None,
             "Sends all extracted text in a single call (~30–60k chars).\n"
             "Requires a large-context model (32K+ tokens):\n"
             "Gemini 2.5 Flash, DeepSeek-V3, Qwen 2.5 32B, GPT-4o…"),
        ]
        self._ctx_group  = QButtonGroup(self)
        self._ctx_group.setExclusive(True)
        self._ctx_radios = []
        self._ctx_values = []
        for i, (label, value, tip) in enumerate(_ctx_defs):
            cb = QCheckBox(label)
            cb.setToolTip(tip)
            if i == 0:
                cb.setChecked(True)
            self._ctx_group.addButton(cb, i)
            self._ctx_radios.append(cb)
            self._ctx_values.append(value)
            run_row.addWidget(cb)

        self._ctx_group.idToggled.connect(self._on_context_changed)
        run_row.addStretch()

        # Questions button — far right of run row, same width as score panel
        self.btn_questions = QPushButton("❓ Author questions")
        self.btn_questions.setEnabled(False)
        self.btn_questions.setFixedWidth(225)
        self.btn_questions.setStyleSheet(
            "QPushButton { font-size:12px; font-weight:bold; border-radius:6px;"
            " background:#d5d8dc; color:#777; border:none; }"
            "QPushButton:enabled { background:#f39c12; color:#fff; }"
            "QPushButton:enabled:hover { background:#e67e22; }"
        )
        self.btn_questions.clicked.connect(self._open_questions_dialog)
        run_row.addWidget(self.btn_questions)
        layout.addLayout(run_row)

        # ── Progress label — fixed 13 px so it never creates a blank gap ────────
        self.lbl_progress = QLabel("")
        self.lbl_progress.setObjectName("sc_hint")
        self.lbl_progress.setFixedHeight(13)
        self.lbl_progress.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.lbl_progress)

        # ── Results area + score panel ────────────────────────────────────────
        self.results_edit = QTextEdit()
        self.results_edit.setReadOnly(True)
        mono = QFont("Courier New", 10)
        mono.setStyleHint(QFont.Monospace)
        self.results_edit.setFont(mono)

        self.score_panel = _ScorePanel()

        results_row = QHBoxLayout()
        results_row.setSpacing(8)
        results_row.addWidget(self.results_edit, 1)
        results_row.addWidget(self.score_panel)
        layout.addLayout(results_row, 1)

        # ── Save row ─────────────────────────────────────────────────────────
        save_row = QHBoxLayout()
        save_row.addWidget(QLabel("Save name:"))
        self.save_name = QLineEdit()
        self.save_name.setPlaceholderText("auto-generated when audit completes")
        save_row.addWidget(self.save_name, 1)
        self.btn_load = QPushButton("📂 Load")
        self.btn_load.setToolTip("Load a previously saved audit (.json)")
        self.btn_load.clicked.connect(self._load_audit)
        save_row.addWidget(self.btn_load)
        self.btn_save = QPushButton("💾 Save")
        self.btn_save.setToolTip("Save this audit as a reusable .json bundle (plus a readable .md copy)")
        self.btn_save.clicked.connect(self._save_results)
        save_row.addWidget(self.btn_save)
        self.btn_unload = QPushButton("🗑 Unload")
        self.btn_unload.setObjectName("btn_delete")  # filled red
        self.btn_unload.setToolTip("Clear the audit from this tab (saved files on disk are kept)")
        self.btn_unload.clicked.connect(self._unload)
        save_row.addWidget(self.btn_unload)
        layout.addLayout(save_row)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self._restore_state()

    def _restore_state(self):
        pdf = self.cfg.get("audit_last_pdf", "")
        self._pdf_path = pdf
        if pdf:
            exists = os.path.isfile(pdf)
            name   = os.path.basename(pdf)
            if exists:
                self.lbl_pdf.setText(name)
                self.lbl_pdf.setStyleSheet("")
                if not self._paper_title:
                    self._paper_title = _detect_pdf_title(pdf)
            else:
                self.lbl_pdf.setText(f"{name}  (file not found)")
                self.lbl_pdf.setStyleSheet("color: #e67e22;")
        else:
            self.lbl_pdf.setText("No file selected")
            self.lbl_pdf.setStyleSheet("")
            self._paper_title = ""

        ctx_idx = self.cfg.get("audit_last_context_index", 0)
        if 0 <= ctx_idx < len(self._ctx_radios):
            self._ctx_group.blockSignals(True)
            self._ctx_radios[ctx_idx].setChecked(True)
            self._ctx_group.blockSignals(False)

        results = self.cfg.get("audit_last_results", "")
        self.results_edit.setPlainText(results)

        scores = self.cfg.get("audit_last_scores", {})
        self._last_scores = scores
        self.score_panel.update_scores(scores)

        self._questions_text = self.cfg.get("audit_last_questions", "")

        save_nm = self.cfg.get("audit_last_save_name", "")
        self.save_name.setText(save_nm)

        if not results:
            self.btn_questions.setEnabled(False)
            self.btn_questions.setText("❓ Author questions")
        else:
            self._apply_questions_button()

        # re-detect sections only if visible and not already done for this PDF
        if self.isVisible() and self._is_section_mode() and self._pdf_path and os.path.isfile(self._pdf_path):
            if not self._sections:
                self._detect_sections()
            elif self.section_panel.isHidden():
                self.section_panel.show()

    def _apply_questions_button(self):
        """Set the 'questions for author' button from self._questions_text."""
        if self._questions_text:
            n = len(re.findall(r'^\s*\[\d+\]', self._questions_text, re.MULTILINE)) or 1
            self.btn_questions.setText(f"❓  {n} question{'s' if n != 1 else ''} for author")
        else:
            self.btn_questions.setText("✓  No questions for author")
        self.btn_questions.setEnabled(True)

    def _load_audit(self):
        """Load a saved audit bundle (.json) into the tab — independent of any session."""
        # Always open the dialog in the audit folder (create it if it doesn't
        # exist yet) so loading lands directly where audits are saved.
        audit_dir = _resolve_audit_dir(self.cfg)
        audit_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(self, "Load Audit", str(audit_dir), "Audit bundle (*.json)")
        if not path:
            return
        try:
            bundle = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(bundle, dict) or "audit" not in bundle:
                raise ValueError("This file is not a ResearchForge audit bundle.")
            paper = bundle.get("paper", {}) or {}
            audit = bundle.get("audit", {}) or {}
            self.cfg.update({
                "audit_last_pdf":           paper.get("path", "") or "",
                "audit_last_context_index": audit.get("context_index", 0),
                "audit_last_results":       audit.get("results", "") or "",
                "audit_last_save_name":     os.path.splitext(os.path.basename(path))[0],
                "audit_last_scores":        audit.get("scores", {}) or {},
                "audit_last_questions":     audit.get("questions", "") or "",
            })
            self._paper_title = paper.get("title", "") or ""
            self._restore_state()
            self.log.emit(f"Audit loaded: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Load failed", f"Could not load audit:\n{e}")

    def _unload(self):
        """Clear the audit from the tab. Saved files on disk are untouched."""
        self.cfg.update({
            "audit_last_pdf":           "",
            "audit_last_context_index": 0,
            "audit_last_results":       "",
            "audit_last_save_name":     "",
            "audit_last_scores":        {},
            "audit_last_questions":     "",
        })
        self._paper_title = ""
        self._questions_text = ""
        self._last_scores = {}
        self._sections = OrderedDict()
        self.section_panel.hide()
        self._restore_state()

    def _ctx_index(self) -> int:
        return self._ctx_group.checkedId()

    def _is_section_mode(self) -> bool:
        return self._ctx_group.checkedId() == 0

    def _ctx_char_limit(self):
        idx = self._ctx_group.checkedId()
        return self._ctx_values[idx] if idx >= 0 else None

    def _save_state(self):
        # Tab-local persistence only — the audit is independent of any session.
        self.cfg.update({
            "audit_last_pdf":           self._pdf_path,
            "audit_last_context_index": self._ctx_index(),
            "audit_last_results":       self.results_edit.toPlainText(),
            "audit_last_save_name":     self.save_name.text(),
            "audit_last_scores":        self._last_scores,
            "audit_last_questions":     self._questions_text,
        })

    # ── PDF picking & section detection ─────────────────────────────────────

    def _is_latex(self) -> bool:
        return self._pdf_path.lower().endswith('.tex')

    def _browse_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Paper",  "",
            "Paper Files (*.pdf *.tex);;PDF Files (*.pdf);;LaTeX Source (*.tex)")
        if not path:
            return
        self._pdf_path = path
        self.lbl_pdf.setText(os.path.basename(path))
        self.lbl_pdf.setStyleSheet("")

        self._paper_title = _detect_pdf_title(path)
        self.save_name.setText(_make_audit_filename(self._paper_title, "latex" if self._is_latex() else "pdf"))

        self._save_state()
        if self._is_section_mode():
            self._detect_sections()

    def _detect_sections(self):
        if not self._pdf_path or not os.path.isfile(self._pdf_path):
            return
        self.lbl_progress.setText("Detecting sections…")
        try:
            if self._is_latex():
                from gui.section_detector import detect_sections_latex
                sections, method = detect_sections_latex(self._pdf_path)
            else:
                from gui.section_detector import detect_sections
                sections, method = detect_sections(self._pdf_path)
        except Exception as exc:
            self.lbl_progress.setText(f"Section detection failed: {exc}")
            self.section_panel.hide()
            self._sections = OrderedDict()
            return

        self._sections = sections

        if not sections:
            self.lbl_progress.setText(
                "⚠  Could not detect sections — switch to a character-limit mode "
                "or check that the PDF is not scanned/locked.")
            self.section_panel.hide()
        else:
            self.section_panel.set_sections(sections, method)
            self.lbl_progress.setText("")

    def _on_context_changed(self, btn_id: int, checked: bool):
        if not checked:
            return
        if self._is_section_mode():
            if self._pdf_path and os.path.isfile(self._pdf_path):
                self._detect_sections()
        else:
            self.section_panel.hide()

    # ── audit run ────────────────────────────────────────────────────────────

    def _run_audit(self):
        if not self._pdf_path:
            QMessageBox.warning(self, "No file", "Browse and select a paper file first.")
            return
        if not ensure_llm_available(self.cfg, self):
            return

        is_section = self._is_section_mode()

        if is_section:
            if not self._sections:
                self._detect_sections()
            if not self._sections:
                QMessageBox.warning(
                    self, "No sections detected",
                    "Section detection found no sections in this file.\n"
                    "Switch to a character-limit mode and try again.")
                return
            preaudit_prompt = self.cfg.load_prompt("section_preaudit_prompt")
            if not preaudit_prompt:
                QMessageBox.critical(
                    self, "Prompt missing",
                    "section_preaudit_prompt.md not found in prompts directory.")
                return
            synthesis_prompt = self.cfg.load_prompt("paper_review_synthesis_prompt")
            if not synthesis_prompt:
                QMessageBox.critical(
                    self, "Prompt missing",
                    "paper_review_synthesis_prompt.md not found in prompts directory.")
                return
            prompt = ""
        else:
            prompt = self.cfg.load_prompt("paper_review_prompt")
            if not prompt:
                QMessageBox.critical(
                    self, "Prompt missing",
                    "paper_review_prompt.md not found in prompts directory.")
                return
            preaudit_prompt  = ""
            synthesis_prompt = ""

        self.results_edit.setPlainText("⏳ Starting audit…")
        self.lbl_progress.setText("Running…")
        self.score_panel.clear()
        self._last_scores = {}
        self._questions_text = ""
        self.btn_questions.setText("❓ Author questions")
        self.btn_questions.setEnabled(False)
        self.btn_run.hide()
        self.btn_stop.show()

        if is_section:
            self.section_panel.reset_statuses()

        self._worker = _AuditWorker(
            pdf_path=self._pdf_path,
            prompt=prompt,
            cfg=self.cfg,
            pdf_char_limit=None if is_section else self._ctx_char_limit(),
            section_mode=is_section,
            sections=dict(self._sections) if is_section else {},
            section_preaudit_prompt=preaudit_prompt,
            synthesis_prompt=synthesis_prompt,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.result.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)
        if is_section:
            self._worker.section_status.connect(self.section_panel.update_status)
        self._worker.start()

    # ── worker signal handlers ────────────────────────────────────────────────

    def _on_progress(self, msg: str):
        self.lbl_progress.setText(msg)
        current = self.results_edit.toPlainText()
        self.results_edit.setPlainText(current + "\n" + msg if current else msg)

    def _on_result(self, text: str):
        self._last_scores    = _parse_scores(text)
        self._questions_text = _parse_questions(text)
        display_text         = _strip_scores_block(_strip_questions_block(text))
        self.results_edit.setPlainText(display_text)
        self.results_edit.verticalScrollBar().setValue(0)
        if self._last_scores:
            self.score_panel.update_scores(self._last_scores)
        self._apply_questions_button()
        self.save_name.setText(_make_audit_filename(self._paper_title, "latex" if self._is_latex() else "pdf"))
        self._save_state()

    def _on_error(self, msg: str):
        if not self._closing:
            QMessageBox.critical(self, "Audit Error", msg)
        self.lbl_progress.setText(f"Error: {msg}")
        self._reset_buttons()
        self._worker = None

    def _on_finished(self):
        if self._worker is None:
            return
        self.lbl_progress.setText("Audit complete.")
        self._reset_buttons()
        self._worker = None

    def _stop_audit(self):
        w = self._worker
        if w and w.isRunning():
            w.stop()
            try:
                w.progress.disconnect()
                w.result.disconnect()
                w.error.disconnect()
                w.finished.disconnect()
                w.section_status.disconnect()
            except (TypeError, RuntimeError):
                pass
            QTimer.singleShot(5000, lambda: w.terminate() if w.isRunning() else None)
        self._worker = None
        self.lbl_progress.setText("Stopped.")
        self._reset_buttons()

    def _reset_buttons(self):
        self.btn_run.show()
        self.btn_stop.hide()

    def _open_questions_dialog(self):
        dlg = _QuestionsDialog(self._questions_text, self)
        dlg.exec()

    # ── save results ─────────────────────────────────────────────────────────

    def _render_markdown(self) -> str:
        """Human-readable report (the .md copy saved alongside the .json bundle)."""
        src_label = "LaTeX source" if self._is_latex() else "PDF"
        src_name  = os.path.basename(self._pdf_path) if self._pdf_path else "unknown"
        header = (
            f"# Audit Report\n"
            f"**File:** {src_name}  ({src_label})\n"
            f"**Date:** {datetime.now().strftime('%A, %d %B %Y  %H:%M')}\n\n"
            + "━" * 60 + "\n\n"
        )
        text = header + self.results_edit.toPlainText().strip()
        if self._questions_text:
            sep = "\n\n" + "━" * 60
            text = (text
                    + sep + "\nQUESTIONS FOR THE AUTHOR\n" + "━" * 60 + "\n"
                    + self._questions_text.strip())
        return text

    def _save_results(self):
        if not self.results_edit.toPlainText().strip():
            QMessageBox.information(self, "Nothing to save", "Run or load an audit first.")
            return
        audit_dir = _resolve_audit_dir(self.cfg)
        audit_dir.mkdir(parents=True, exist_ok=True)
        base = self.save_name.text().strip() or datetime.now().strftime("%Y-%m-%d_audit")
        for ext in (".json", ".md"):
            if base.lower().endswith(ext):
                base = base[:-len(ext)]
        # Canonical, reloadable bundle
        bundle = {
            "schema": "researchforge.audit/1",
            "app_version": APP_VERSION,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "paper": {
                "name": os.path.basename(self._pdf_path) if self._pdf_path else "",
                "path": self._pdf_path or "",
                "source_type": "latex" if self._is_latex() else "pdf",
                "title": self._paper_title or "",
            },
            "audit": {
                "context_index": self._ctx_index(),
                "model":    self.cfg.get("llm_model", ""),
                "endpoint": self.cfg.get("llm_endpoint", ""),
                "results":   self.results_edit.toPlainText(),
                "scores":    self._last_scores,
                "questions": self._questions_text,
            },
        }
        json_path = audit_dir / (base + ".json")
        md_path   = audit_dir / (base + ".md")
        json_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(self._render_markdown(), encoding="utf-8")
        self.log.emit(f"Audit saved: {json_path}")
        QMessageBox.information(self, "Saved", f"Saved:\n{json_path}\n{md_path}")
        self._save_state()
