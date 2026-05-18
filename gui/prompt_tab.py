"""
Prompt Editor Tab: edit per-paper, topic synthesis, and global synthesis prompts.
Reads/writes to ~/.ResearchForge/prompts/ with defaults from config/prompts/.
"""
import os
import shutil
import zipfile
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPushButton, QTextEdit, QFileDialog, QMessageBox, QLabel,
)
from PySide6.QtCore import Signal

from gui.config_manager import PROMPTS_DIR, BUNDLED_PROMPTS


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
            self.prompt_tabs.addTab(editor, name)
            self._editors[key] = editor

        layout.addWidget(self.prompt_tabs, 1)

        hb = QHBoxLayout()
        hb.setSpacing(4)

        self.btn_load = QPushButton("Load File...")
        self.btn_load.clicked.connect(self._load_prompt_file)
        hb.addWidget(self.btn_load)

        self.btn_save_as = QPushButton("Save As...")
        self.btn_save_as.clicked.connect(self._save_prompt_file)
        hb.addWidget(self.btn_save_as)

        self.btn_export_all = QPushButton("Export ZIP")
        self.btn_export_all.clicked.connect(self._export_zip)
        hb.addWidget(self.btn_export_all)

        self.btn_import_all = QPushButton("Import ZIP")
        self.btn_import_all.clicked.connect(self._import_zip)
        hb.addWidget(self.btn_import_all)

        hb.addStretch()

        self.btn_save = QPushButton("Save Prompts")
        self.btn_save.setObjectName("btn_save")
        self.btn_save.clicked.connect(self._save_prompts)
        hb.addWidget(self.btn_save)

        self.btn_reload = QPushButton("Reload Saved")
        self.btn_reload.setToolTip("Reload from your last saved prompts")
        self.btn_reload.clicked.connect(self._reload_prompts)
        hb.addWidget(self.btn_reload)

        self.btn_reset = QPushButton("Reset to Default")
        self.btn_reset.setObjectName("btn_remove")
        self.btn_reset.setToolTip("Restore original bundled prompts (overwrites your edits)")
        self.btn_reset.clicked.connect(self._reset_to_default)
        hb.addWidget(self.btn_reset)

        layout.addLayout(hb)

    def _load_prompts(self):
        for key, _name, _fname in PROMPT_KEYS:
            content = self.cfg.load_prompt(key)
            if content:
                self._editors[key].setText(content)

    def _save_prompts(self):
        for key, _name, _fname in PROMPT_KEYS:
            content = self._editors[key].toPlainText()
            if content.strip():
                self.cfg.save_prompt(key, content)
        self.log.emit("Prompts saved.")

    def _reload_prompts(self):
        reply = QMessageBox.question(self, "Reload", "Reload prompts from your last saved version?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._load_prompts()
            self.log.emit("Prompts reloaded.")

    def _reset_to_default(self):
        reply = QMessageBox.warning(
            self, "Reset to Default",
            "This will overwrite all your edits with the original bundled prompts.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for _key, _name, fname in PROMPT_KEYS:
            src = os.path.join(BUNDLED_PROMPTS, fname)
            dst = os.path.join(PROMPTS_DIR, fname)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
        self._load_prompts()
        self.log.emit("Prompts restored to original defaults.")

    def _current_key(self) -> str:
        idx = self.prompt_tabs.currentIndex()
        return PROMPT_KEYS[idx][0]

    def _current_editor(self) -> QTextEdit:
        return self._editors[self._current_key()]

    def _current_fname(self) -> str:
        idx = self.prompt_tabs.currentIndex()
        return PROMPT_KEYS[idx][2]

    def _load_prompt_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Prompt", "", "Markdown Files (*.md);;All (*)")
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                key = self._current_key()
                self._current_editor().setText(content)
                self.cfg.save_prompt(key, content)
                self.log.emit(f"Loaded and saved: {path}")
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))

    def _save_prompt_file(self):
        key = self._current_key()
        name = PROMPT_KEYS[self.prompt_tabs.currentIndex()][1].lower().replace(" ", "_")
        path, _ = QFileDialog.getSaveFileName(self, "Save Prompt", f"{name}.md", "Markdown (*.md)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._current_editor().toPlainText())
                self.cfg.set(key, path)
                self.log.emit(f"Saved: {path}")
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))

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
            self.log.emit(f"Imported and saved: {path}")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def get_prompts(self) -> dict:
        return {key: editor.toPlainText() for key, editor in self._editors.items()}
