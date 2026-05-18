"""
Folder PDF import dialog with live topic preview.
"""
import os
import shutil
import logging

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QRadioButton, QButtonGroup, QGroupBox, QTextEdit,
    QDialogButtonBox, QMessageBox,
)
from PySide6.QtCore import Qt

import pypdf


def extract_pdf_metadata(pdf_path: str) -> dict:
    """Extract title and first-page text from a PDF."""
    result = {"title": os.path.splitext(os.path.basename(pdf_path))[0], "abstract": ""}
    try:
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        reader = pypdf.PdfReader(pdf_path)
        info = reader.metadata
        if info and info.title:
            result["title"] = str(info.title).strip()
        if len(reader.pages) > 0:
            text = reader.pages[0].extract_text() or ""
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if lines:
                if not (info and info.title):
                    result["title"] = lines[0][:200]
                body = " ".join(lines[1:]) if len(lines) > 1 else lines[0]
                result["abstract"] = body[:1500]
    except Exception:
        pass
    result["title"] = result["title"].replace("\x00", "").strip()
    result["abstract"] = result["abstract"].replace("\x00", "").strip()
    return result


def scan_folder(folder_path: str) -> list:
    """Return list of (relative_path, filename) for all PDFs in folder tree."""
    pdfs = []
    for root, dirs, files in os.walk(folder_path):
        for f in files:
            if f.lower().endswith(".pdf"):
                rel_dir = os.path.relpath(root, folder_path)
                pdfs.append((rel_dir, f))
    return pdfs


def compute_topics(pdfs: list, max_depth: int, flat: bool) -> dict:
    """
    Group PDFs by topic name.
    Returns {topic_name: [(rel_dir, filename), ...]}
    """
    if flat:
        return {"Imported Papers": pdfs}

    topics = {}
    for rel_dir, fname in pdfs:
        parts = [p for p in rel_dir.replace("\\", "/").split("/") if p and p != "."]
        if not parts:
            topic = os.path.basename(fname).rsplit(".", 1)[0]
        else:
            parts = parts[-max_depth:]
            topic = "_".join(parts)
        topics.setdefault(topic, []).append((rel_dir, fname))

    # Deduplicate: if a topic-collision produces same name, append _2, _3
    seen = {}
    deduped = {}
    for topic, entries in topics.items():
        if topic in seen:
            n = 2
            while f"{topic}_{n}" in seen:
                n += 1
            topic = f"{topic}_{n}"
        seen[topic] = True
        deduped[topic] = entries
    return deduped


class FolderImportDialog(QDialog):
    """Dialog to preview and configure folder PDF import."""

    def __init__(self, folder_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Folder")
        self.setMinimumWidth(620)
        self.setMinimumHeight(480)

        self._folder_path = folder_path
        self._all_pdfs = scan_folder(folder_path)
        self._result = []

        self._setup_ui()
        self._update_preview()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # ── Summary line ──
        folder_name = os.path.basename(self._folder_path.rstrip(os.sep))
        total_pdfs = len(self._all_pdfs)

        # Count subfolders with PDFs
        folders_with_pdfs = set()
        root_pdfs = 0
        for rel_dir, _ in self._all_pdfs:
            parts = rel_dir.replace("\\", "/").strip("/").split("/")
            if parts and parts[0] and parts[0] != ".":
                folders_with_pdfs.add(parts[0])
            else:
                root_pdfs += 1

        hl = QHBoxLayout()
        hl.addWidget(QLabel(f"📁 <b>{folder_name}</b>"))
        hl.addStretch()
        hl.addWidget(QLabel(f"{total_pdfs} PDFs, {len(folders_with_pdfs)} subfolder(s)"))
        layout.addLayout(hl)

        # Mixed folder warning
        if root_pdfs > 0 and folders_with_pdfs:
            warn = QLabel(f"⚠ Root contains {root_pdfs} PDF(s) + subfolders → split mode will create a topic for root PDFs")
            warn.setStyleSheet("color: #e67e22; font-weight: bold;")
            layout.addWidget(warn)
        elif total_pdfs == 0:
            QMessageBox.warning(self, "No PDFs", "No PDF files found in this folder.")
            self.reject()
            return

        # ── Tree preview ──
        grp_tree = QGroupBox("Folder Structure")
        tree_layout = QVBoxLayout(grp_tree)
        self.tree_preview = QTextEdit()
        self.tree_preview.setReadOnly(True)
        self.tree_preview.setMaximumHeight(180)
        self.tree_preview.setStyleSheet("QTextEdit { background: palette(base); font-family: Consolas, monospace; }")
        tree_layout.addWidget(self.tree_preview)
        layout.addWidget(grp_tree)

        # ── Controls ──
        grp_ctrl = QGroupBox("Topic Settings")
        ctrl_layout = QVBoxLayout(grp_ctrl)

        depth_row = QHBoxLayout()
        depth_row.addWidget(QLabel("Topic depth:"))
        self.spin_depth = QSpinBox()
        self.spin_depth.setRange(1, 5)
        self.spin_depth.setValue(3)
        self.spin_depth.setToolTip(
            "How many folder levels to include in the topic name.\n"
            "Depth 1: only last folder → 'transformers'\n"
            "Depth 3: last 3 folders → 'nlp_rag_transformers'"
        )
        self.spin_depth.valueChanged.connect(self._on_setting_changed)
        depth_row.addWidget(self.spin_depth)
        depth_row.addStretch()
        ctrl_layout.addLayout(depth_row)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Structure:"))
        self.rb_split = QRadioButton("Keep folder topics (split)")
        self.rb_split.setChecked(True)
        self.rb_split.toggled.connect(self._on_setting_changed)
        self.rb_flat = QRadioButton("Flatten to single topic")
        self.rb_flat.toggled.connect(self._on_setting_changed)
        mode_row.addWidget(self.rb_split)
        mode_row.addWidget(self.rb_flat)
        mode_row.addStretch()
        ctrl_layout.addLayout(mode_row)
        layout.addWidget(grp_ctrl)

        # ── Topics summary ──
        grp_result = QGroupBox("Result")
        result_layout = QVBoxLayout(grp_result)
        self.lbl_summary = QLabel()
        self.lbl_summary.setStyleSheet("font-weight: bold; font-size: 12px;")
        result_layout.addWidget(self.lbl_summary)
        self.topics_text = QTextEdit()
        self.topics_text.setReadOnly(True)
        self.topics_text.setMaximumHeight(120)
        self.topics_text.setStyleSheet("QTextEdit { background: palette(base); }")
        result_layout.addWidget(self.topics_text)
        layout.addWidget(grp_result)

        # ── Buttons ──
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_setting_changed(self):
        self._update_preview()

    def _build_tree_text(self) -> str:
        """Build folder tree summary."""
        folder_name = os.path.basename(self._folder_path.rstrip(os.sep))
        lines = [f"📁 {folder_name}/"]

        # Group by first level
        by_folder = {}
        root_pdfs = []
        for rel_dir, fname in self._all_pdfs:
            parts = rel_dir.replace("\\", "/").strip("/").split("/")
            if parts and parts[0] and parts[0] != ".":
                key = parts[0]
                by_folder.setdefault(key, []).append((rel_dir, fname))
            else:
                root_pdfs.append(fname)

        for fname in root_pdfs:
            lines.append(f"    📄 {fname}")

        for folder, items in sorted(by_folder.items()):
            # Find max nested depth
            max_nest = 0
            for rel_dir, _ in items:
                parts = rel_dir.replace("\\", "/").strip("/").split("/")
                max_nest = max(max_nest, len(parts) - 1)
            indent = "    "
            if max_nest > 0:
                lines.append(f"{indent}📂 {folder}/  [{len(items)} PDFs, {max_nest} nested level(s)]")
                # Sample up to 5 subdirs
                subdirs = set()
                for rel_dir, _ in items:
                    parts = rel_dir.replace("\\", "/").strip("/").split("/")
                    if len(parts) > 1:
                        subdirs.add("/".join(parts[1:]))
                for sd in sorted(list(subdirs))[:5]:
                    lines.append(f"{indent}    📄 ...{sd}")
                if len(subdirs) > 5:
                    lines.append(f"{indent}    ... and {len(subdirs) - 5} more")
            else:
                lines.append(f"{indent}📂 {folder}/  [{len(items)} PDFs]")

        return "\n".join(lines)

    def _update_preview(self):
        max_depth = self.spin_depth.value()
        flat = self.rb_flat.isChecked()
        topics = compute_topics(self._all_pdfs, max_depth, flat)

        total = len(self._all_pdfs)
        self.lbl_summary.setText(f"{total} PDFs → {len(topics)} topic(s)")

        self.tree_preview.setText(self._build_tree_text())

        topic_lines = []
        for topic, entries in sorted(topics.items()):
            topic_lines.append(f"• <b>{topic}</b>  ({len(entries)} PDFs)")
        self.topics_text.setHtml("<br>".join(topic_lines))

    def _on_accept(self):
        max_depth = self.spin_depth.value()
        flat = self.rb_flat.isChecked()
        topics = compute_topics(self._all_pdfs, max_depth, flat)
        for topic, entries in topics.items():
            for rel_dir, fname in entries:
                self._result.append((topic, rel_dir, fname))
        self.accept()

    def result(self) -> list:
        """Return list of (topic_name, relative_path, filename)."""
        return self._result


def copy_pdf_to_topic_root(source_path: str, target_dir: str, filename: str) -> str:
    """Copy a PDF to the target topic folder, return the destination path."""
    os.makedirs(target_dir, exist_ok=True)
    dest = os.path.join(target_dir, filename)
    # Avoid overwriting: if exists, rename
    if os.path.exists(dest):
        base, ext = os.path.splitext(filename)
        n = 2
        while os.path.exists(os.path.join(target_dir, f"{base} ({n}){ext}")):
            n += 1
        dest = os.path.join(target_dir, f"{base} ({n}){ext}")
    shutil.copy2(source_path, dest)
    return dest
