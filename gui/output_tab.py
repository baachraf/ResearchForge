"""
Check Summaries tab: browse generated summaries for the active session.
"""
import os
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTreeWidget, QTreeWidgetItem,
    QLabel, QTextEdit, QSplitter, QHeaderView,
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, Signal
from gui.theme_manager import ThemeManager


class OutputTab(QWidget):
    """Check Summaries tab: browse generated summaries for the active session.

    Displays a collapsible tree view of the summaries output directory, organized
    by model → topic → summary files. Click any file to view its content in a
    read-only text pane. Auto-points to the current session/model directory.

    Signals:
      log(str) — Forwarded to Logs tab
    """
    log = Signal(str)

    def __init__(self, config_manager, log_signal, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.log = log_signal
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        splitter = QSplitter(Qt.Vertical)

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["File", "Type", "Relevance"])
        self.file_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.file_tree.header().setSectionResizeMode(1, QHeaderView.Interactive)
        self.file_tree.header().setSectionResizeMode(2, QHeaderView.Interactive)
        self.file_tree.header().resizeSection(1, 110)
        self.file_tree.header().resizeSection(2, 70)
        self.file_tree.setAlternatingRowColors(True)
        self.file_tree.itemClicked.connect(self._on_item_clicked)
        splitter.addWidget(self.file_tree)

        self.viewer = QTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setPlaceholderText("Click a file to view its content...")
        splitter.addWidget(self.viewer)

        splitter.setSizes([250, 400])

        hb = QHBoxLayout()
        hb.setSpacing(4)
        self.lbl_path = QLabel("")
        hb.addWidget(self.lbl_path, 1)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh)
        hb.addWidget(btn_refresh)
        btn_expand = QPushButton("Expand All")
        btn_expand.clicked.connect(self.file_tree.expandAll)
        hb.addWidget(btn_expand)
        btn_collapse = QPushButton("Collapse All")
        btn_collapse.clicked.connect(self.file_tree.collapseAll)
        hb.addWidget(btn_collapse)
        layout.addLayout(hb)
        layout.addWidget(splitter, 1)

    def _get_active_root(self):
        out_dir = self.cfg.get("summary_output_dir", "").strip()
        download_name = self.cfg.get("session_download_name", "")
        if not out_dir or not download_name:
            return ""
        return os.path.join(out_dir, download_name)

    def showEvent(self, event):
        super().showEvent(event)
        root = self._get_active_root()
        self.lbl_path.setText(root if root else "No active session or summary output not configured")
        self._refresh()

    def _refresh(self):
        self.file_tree.clear()
        root = self._get_active_root()
        self.lbl_path.setText(root if root else "No session loaded")
        if not root or not os.path.isdir(root):
            self.log.emit("No summaries found for current session.")
            return
        for entry in sorted(os.scandir(root), key=lambda e: e.name.lower()):
            if entry.is_dir():
                self._add_model_dir(entry.path, entry.name)
        self.log.emit("Summary tree refreshed.")

    def _add_model_dir(self, path, name):
        topic_files = sorted(
            [f for f in os.listdir(path) if f.endswith("_SUMMARY.md") and f != "GLOBAL_SUMMARY.md"],
            key=str.lower,
        )

        scores = {}
        scores_path = os.path.join(path, "_relevance_scores.json")
        if os.path.isfile(scores_path):
            try:
                with open(scores_path, 'r', encoding='utf-8') as sf:
                    scores = json.load(sf)
            except Exception:
                pass

        model_item = QTreeWidgetItem(self.file_tree)
        model_item.setText(0, name)
        model_item.setText(1, f"{len(topic_files)} topics")
        model_item.setExpanded(True)

        for label in ("GLOBAL_SUMMARY", "RELATED_WORK", "INTRODUCTION"):
            md_path = os.path.join(path, f"{label}.md")
            if os.path.isfile(md_path):
                item = QTreeWidgetItem(model_item)
                item.setText(0, f"{label}.md")
                item.setText(1, label.replace("_", " ").title())
                item.setData(0, Qt.UserRole, md_path)
                tm = ThemeManager()
                link_color = tm.color("primary")
                item.setForeground(0, link_color)
                font = item.font(0); font.setBold(True); item.setFont(0, font)

        for tf in topic_files:
            topic_name = tf.replace("_SUMMARY.md", "")
            t_item = QTreeWidgetItem(model_item)
            t_item.setText(0, topic_name)
            t_item.setText(1, "Topic Summary")
            summary_path = os.path.join(path, tf)
            t_item.setData(0, Qt.UserRole, summary_path)
            t_item.setExpanded(True)

            master = os.path.join(path, "detailed_topic_reviews", topic_name, "MASTER_REPORT.md")
            if os.path.isfile(master):
                m_item = QTreeWidgetItem(t_item)
                m_item.setText(0, "MASTER_REPORT.md")
                m_item.setText(1, "Full Report")
                m_item.setData(0, Qt.UserRole, master)

            cache_dir = os.path.join(path, "detailed_topic_reviews", topic_name, "_cache")
            if os.path.isdir(cache_dir):
                for cf in sorted(os.listdir(cache_dir), key=str.lower):
                    if cf.endswith(".md"):
                        p_item = QTreeWidgetItem(t_item)
                        p_item.setText(0, cf)
                        p_item.setText(1, "Paper Analysis")
                        p_item.setData(0, Qt.UserRole, os.path.join(cache_dir, cf))
                        pdf_name = cf.replace(".md", ".pdf")
                        if pdf_name in scores:
                            sv = scores[pdf_name]
                            p_item.setText(2, str(sv))
                            if sv >= 70:
                                p_item.setForeground(2, tm.color("success"))
                            elif sv >= 40:
                                p_item.setForeground(2, tm.color("badge_orange"))
                            else:
                                p_item.setForeground(2, tm.color("danger"))

    def _on_item_clicked(self, item, col):
        path = item.data(0, Qt.UserRole)
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                self.viewer.setText(f.read())
