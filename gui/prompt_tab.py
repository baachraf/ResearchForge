"""
Prompt Editor Tab: edit per-paper, topic synthesis, and global synthesis prompts.
Reads/writes to ~/.ResearchForge/prompts/ with defaults from config/prompts/.
"""
import zipfile
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPushButton, QTextEdit, QFileDialog, QMessageBox, QLabel,
    QMenu, QInputDialog,
)
from PySide6.QtCore import Signal


PROMPT_KEYS = [
    ("per_paper_prompt", "Per-Paper Analysis", "per_paper.md"),
    ("topic_synthesis_prompt", "Topic Synthesis", "topic_synthesis.md"),
    ("global_synthesis_prompt", "Global Synthesis", "global_synthesis.md"),
    ("query_generation_prompt", "Query Generation", "query_generation.md"),
    ("rate_relevance_prompt", "Rate Relevance", "rate_relevance.md"),
    ("enhance_research_prompt", "Enhance Research", "enhance_research.md"),
    ("enhance_intent_prompt", "Enhance Intent", "enhance_intent.md"),
    ("related_work_prompt", "Related Work", "related_work.md"),
    ("introduction_prompt", "Introduction", "introduction.md"),
    ("analyze_own_paper_prompt", "Analyze My Paper", "analyze_own_paper.md"),
    ("paper_review_prompt", "Paper Review", "paper_review_prompt.md"),
    ("paper_audit_prompt",  "Paper Full Audit", "paper_audit_prompt.md"),
]


class PromptEditorTab(QWidget):
    log = Signal(str)

    def __init__(self, config_manager, log_signal, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.log = log_signal
        self._editors = {}
        self._key_index = {}          # key -> tab index
        self._saved_text = {}         # key -> text last loaded/saved from disk
        self._setup_ui()
        self._load_prompts()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(2)
        layout.setContentsMargins(2, 2, 2, 2)

        self.prompt_tabs = QTabWidget()

        for key, name, _fname in PROMPT_KEYS:
            editor = QTextEdit()
            editor.setPlaceholderText(f"{name} prompt...")
            idx = self.prompt_tabs.addTab(editor, name)
            self._editors[key] = editor
            self._key_index[key] = idx
            editor.textChanged.connect(lambda k=key: self._on_edited(k))

        layout.addWidget(self.prompt_tabs, 1)

        self.lbl_hint = QLabel(
            "Edits take effect only after you Save. Save → set as your default, or "
            "keep it as a named preset (applied only when you load it)."
        )
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.lbl_hint)

        hb = QHBoxLayout()
        hb.setSpacing(4)

        self.btn_export_all = QPushButton("Export ZIP")
        self.btn_export_all.setObjectName("btn_secondary")
        self.btn_export_all.clicked.connect(self._export_zip)
        hb.addWidget(self.btn_export_all)

        self.btn_import_all = QPushButton("Import ZIP")
        self.btn_import_all.setObjectName("btn_secondary")
        self.btn_import_all.clicked.connect(self._import_zip)
        hb.addWidget(self.btn_import_all)

        hb.addStretch()

        self.btn_save = QPushButton("Save")
        self.btn_save.setObjectName("btn_save")
        self.btn_save.setToolTip("Save this prompt — set as your default, or keep as a named preset")
        self.btn_save.clicked.connect(self._save_current)
        hb.addWidget(self.btn_save)

        self.btn_presets = QPushButton("Presets ▾")
        self.btn_presets.setObjectName("btn_presets")
        self.btn_presets.setToolTip("Load or delete a saved preset for this prompt")
        self.btn_presets.clicked.connect(self._open_presets_menu)
        hb.addWidget(self.btn_presets)

        self.btn_reset = QPushButton("Reset to Default")
        self.btn_reset.setObjectName("btn_remove")
        self.btn_reset.setToolTip("Restore the original bundled prompt (this prompt or all)")
        self.btn_reset.clicked.connect(self._reset_to_default)
        hb.addWidget(self.btn_reset)

        layout.addLayout(hb)

    # ── loading / dirty tracking ─────────────────────────────────────────────

    def _load_prompts(self):
        """Load every prompt from disk into its editor and mark all clean."""
        for key, _name, _fname in PROMPT_KEYS:
            content = self.cfg.load_prompt(key)
            editor = self._editors[key]
            editor.blockSignals(True)
            editor.setText(content or "")
            editor.blockSignals(False)
            self._saved_text[key] = content or ""
            self._refresh_tab_title(key)

    def _name_for(self, key: str) -> str:
        for k, name, _f in PROMPT_KEYS:
            if k == key:
                return name
        return key

    def _is_dirty(self, key: str) -> bool:
        return self._editors[key].toPlainText() != self._saved_text.get(key, "")

    def _refresh_tab_title(self, key: str):
        idx = self._key_index.get(key)
        if idx is None:
            return
        name = self._name_for(key)
        self.prompt_tabs.setTabText(idx, f"{name} *" if self._is_dirty(key) else name)

    def _on_edited(self, key: str):
        self._refresh_tab_title(key)

    def _mark_saved(self, key: str):
        """Snapshot the editor's CURRENT text as the saved baseline and clear the
        dirty flag. Snapshotting straight from the editor guarantees _is_dirty is
        False immediately after a save (no content/editor mismatch)."""
        self._saved_text[key] = self._editors[key].toPlainText()
        self._refresh_tab_title(key)

    def has_unsaved_changes(self) -> bool:
        return any(self._is_dirty(k) for k in self._editors)

    def unsaved_names(self) -> list:
        return [self._name_for(k) for k in self._editors if self._is_dirty(k)]

    def save_all_dirty_as_default(self):
        """Persist every edited prompt as the active default (used by the close guard)."""
        for key in list(self._editors):
            if self._is_dirty(key):
                self.cfg.save_prompt(key, self._editors[key].toPlainText())
                self._mark_saved(key)
        self.log.emit("Unsaved prompt edits saved as default.")

    # ── save (default vs named preset) ───────────────────────────────────────

    def _save_current(self):
        key = self._current_key()
        content = self._current_editor().toPlainText()
        if not content.strip():
            QMessageBox.warning(self, "Empty prompt", "This prompt is empty — nothing to save.")
            return
        name = self._name_for(key)
        box = QMessageBox(self)
        box.setWindowTitle("Save Prompt")
        box.setIcon(QMessageBox.Question)
        box.setText(f"Save “{name}” — how?")
        box.setInformativeText(
            "• Set as my default: becomes the prompt the app uses every session.\n"
            "• Save as preset: keeps a named copy, applied only when you load it."
        )
        b_default = box.addButton("Set as my default", QMessageBox.AcceptRole)
        b_preset = box.addButton("Save as preset…", QMessageBox.ActionRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        b_default.setObjectName("btn_save")      # green (success) via theme
        b_preset.setObjectName("btn_presets")    # indigo via theme
        self._widen_dialog(box, 560)
        box.exec()
        clicked = box.clickedButton()
        if clicked == b_default:
            self.cfg.save_prompt(key, content)
            self._mark_saved(key)
            self.log.emit(f"'{name}' set as your default.")
        elif clicked == b_preset:
            dlg = QInputDialog(self)
            dlg.setWindowTitle("Save as preset")
            dlg.setLabelText(f"Preset name for “{name}”:")
            dlg.setTextValue("")
            dlg.resize(440, dlg.sizeHint().height())
            if dlg.exec() and dlg.textValue().strip():
                pname = dlg.textValue().strip()
                self.cfg.save_prompt_preset(key, pname, content)
                # the edit is now persisted (as a preset) → clear the dirty star
                self._mark_saved(key)
                self.log.emit(f"'{name}' saved as preset '{pname}'.")

    # ── presets (load / delete) ──────────────────────────────────────────────

    def _open_presets_menu(self):
        key = self._current_key()
        presets = self.cfg.list_prompt_presets(key)
        menu = QMenu(self)
        # Header makes it explicit the list is scoped to the ACTIVE prompt tab.
        header = menu.addAction(f"Presets for: {self._name_for(key)}")
        header.setEnabled(False)
        menu.addSeparator()
        if not presets:
            act = menu.addAction("(no presets saved for this prompt)")
            act.setEnabled(False)
        else:
            for p in presets:
                act = menu.addAction(f"Load:  {p}")
                act.triggered.connect(lambda _checked=False, n=p: self._load_preset(n))
            menu.addSeparator()
            del_menu = menu.addMenu("Delete preset")
            for p in presets:
                a = del_menu.addAction(p)
                a.triggered.connect(lambda _checked=False, n=p: self._delete_preset(n))
        menu.exec(self.btn_presets.mapToGlobal(self.btn_presets.rect().bottomLeft()))

    def _load_preset(self, name: str):
        key = self._current_key()
        content = self.cfg.load_prompt_preset(key, name)
        if not content:
            return
        # Loading a preset applies it: it becomes the active default the app uses.
        self._current_editor().setText(content)
        self.cfg.save_prompt(key, content)
        self._mark_saved(key)
        self.log.emit(f"Loaded preset '{name}' for '{self._name_for(key)}' (now active).")

    def _delete_preset(self, name: str):
        key = self._current_key()
        reply = QMessageBox.question(
            self, "Delete preset", f"Delete preset '{name}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.cfg.delete_prompt_preset(key, name)
            self.log.emit(f"Deleted preset '{name}'.")

    # ── reset to bundled default (this prompt / all) ─────────────────────────

    def _reset_to_default(self):
        key = self._current_key()
        name = self._name_for(key)
        box = QMessageBox(self)
        box.setWindowTitle("Reset to Default")
        box.setIcon(QMessageBox.Warning)
        box.setText("Restore the original bundled prompt?")
        box.setInformativeText(
            f"This overwrites your active prompt(s) with the shipped defaults.\n"
            f"Named presets are NOT affected."
        )
        b_this = box.addButton(f"This prompt ({name})", QMessageBox.AcceptRole)
        b_all = box.addButton("All prompts", QMessageBox.DestructiveRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        b_this.setObjectName("btn_secondary")    # mild, single-prompt restore
        b_all.setObjectName("btn_delete")        # red (destructive) via theme
        self._widen_dialog(box, 520)
        box.exec()
        clicked = box.clickedButton()
        if clicked == b_this:
            content = self.cfg.reset_prompt_to_default(key)
            editor = self._editors[key]
            editor.setText(content)
            self._mark_saved(key)
            self.log.emit(f"'{name}' restored to default.")
        elif clicked == b_all:
            self.cfg.reset_all_prompts_to_default()
            self._load_prompts()
            self.log.emit("All prompts restored to original defaults.")

    @staticmethod
    def _widen_dialog(box, min_width: int):
        """Force a QMessageBox wider so button labels aren't truncated. QMessageBox
        ignores setMinimumWidth, so we stretch its grid layout with a spacer."""
        try:
            from PySide6.QtWidgets import QSpacerItem, QSizePolicy
            layout = box.layout()
            spacer = QSpacerItem(min_width, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
            layout.addItem(spacer, layout.rowCount(), 0, 1, layout.columnCount())
        except Exception:
            pass

    def _current_key(self) -> str:
        idx = self.prompt_tabs.currentIndex()
        return PROMPT_KEYS[idx][0]

    def _current_editor(self) -> QTextEdit:
        return self._editors[self._current_key()]

    def _export_zip(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export All", "prompts.zip", "ZIP (*.zip)")
        if not path:
            return
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for key, _name, fname in PROMPT_KEYS:
                    zf.writestr(fname, self._editors[key].toPlainText())
            self.log.emit(f"Exported: {path}")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _import_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Prompts", "", "ZIP (*.zip)")
        if not path:
            return
        try:
            with zipfile.ZipFile(path, "r") as zf:
                for key, _name, fname in PROMPT_KEYS:
                    if fname in zf.namelist():
                        content = zf.read(fname).decode("utf-8")
                        self._editors[key].setText(content)
                        self.cfg.save_prompt(key, content)
                        self._mark_saved(key)
            self.log.emit(f"Imported and saved: {path}")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def get_prompts(self) -> dict:
        return {key: editor.toPlainText() for key, editor in self._editors.items()}
