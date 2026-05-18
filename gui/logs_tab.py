"""
Logs Tab: scrollable log viewer with clear button.
"""
from datetime import datetime
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit
from PySide6.QtCore import Signal


class LogsTab(QWidget):
    log = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)

        hb = QHBoxLayout()
        self.btn_clear = QPushButton("Clear Logs")
        self.btn_clear.clicked.connect(self._clear)
        hb.addWidget(self.btn_clear)
        hb.addStretch()
        layout.addLayout(hb)

    def add_log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{ts}] {msg}")

    def _clear(self):
        self.log_view.clear()
