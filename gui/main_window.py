import sys
import os

from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QApplication,
    QScrollArea, QWidget, QVBoxLayout, QSplitter, QMessageBox, QMenuBar,
    QPushButton, QHBoxLayout,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QPixmap, QPalette, QColor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.config_manager import ConfigManager
from gui.settings_tab import SettingsTab
from gui.search_tab import SearchDownloadTab
from gui.summarize_tab import SummarizeTab
from gui.output_tab import OutputTab
from gui.audit_tab import AuditTab
from gui.prompt_tab import PromptEditorTab
from gui.logs_tab import LogsTab
from gui.tour import TourOverlay
from gui.theme_manager import ThemeManager
from gui.app_info import icon, APP_VERSION
from gui.style import STYLE_QSS, refresh_qss


class _LogRedirector:
    """Thread-safe stdout/stderr → Qt Signal bridge."""

    def __init__(self, signal: Signal, original_stream):
        self._signal = signal
        self._original = original_stream
        self._buffer = ""

    def write(self, text: str):
        if self._original:
            self._original.write(text)
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip("\r")
            if line:
                self._signal.emit(line)

    def flush(self):
        if self._original:
            self._original.flush()
        if self._buffer.strip():
            self._signal.emit(self._buffer.strip("\r"))
            self._buffer = ""


class MainWindow(QMainWindow):
    """Main application window with 6-tab layout and global logging.

    Tabs: Settings & Prompts | Search & Download | Generate Reports | Check Summaries | Logs

    Owns ConfigManager, ThemeManager, and the tour overlay. Redirects stdout/stderr
    to the Logs tab via _LogRedirector. Handles session restore on startup and graceful
    async close with worker cleanup.
    """
    log_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"ResearchForge {APP_VERSION}")
        icon_path = icon("app_icone.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setMinimumSize(1100, 715)
        self.cfg = ConfigManager()
        self._init_theme()
        self._setup_ui()
        self._connect_logs()
        self._apply_global_palette()

    def _init_theme(self):
        tm = ThemeManager()
        saved = self.cfg.get("theme", "")
        if saved:
            tm.load(saved)
        QApplication.instance().setStyleSheet(refresh_qss())
        self._setup_tour()

    def _setup_ui(self):
        self.tab_widget = QTabWidget()

        settings_prompts_tab = QWidget()
        sp_split = QSplitter(Qt.Vertical)
        sp_split.setHandleWidth(3)
        sp_split.setChildrenCollapsible(False)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self.settings_tab = SettingsTab(self.cfg, self.log_signal)
        scroll.setWidget(self.settings_tab)
        sp_split.addWidget(scroll)

        self.prompt_tab = PromptEditorTab(self.cfg, self.log_signal)
        sp_split.addWidget(self.prompt_tab)
        sp_split.setSizes([270, 530])
        sl = QVBoxLayout(settings_prompts_tab)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(sp_split)

        self.search_tab = SearchDownloadTab(self.cfg, self.log_signal)
        self.summarize_tab = SummarizeTab(self.cfg, self.log_signal)
        self.summarize_tab.session_save_requested.connect(self.search_tab._session_save)
        self.output_tab = OutputTab(self.cfg, self.log_signal)
        self.audit_tab = AuditTab(self.cfg, self.log_signal)
        self.audit_tab.session_save_requested.connect(self.search_tab._session_save)
        self.search_tab.set_audit_tab(self.audit_tab)
        self.search_tab._load_last_session()
        self.logs_tab = LogsTab()

        self.tab_widget.addTab(settings_prompts_tab, " Settings & Prompts ")
        self.tab_widget.addTab(self.search_tab, " Search & Download ")
        self.tab_widget.addTab(self.summarize_tab, "Generate Reports")
        self.tab_widget.addTab(self.output_tab, " Check Reports ")
        self.tab_widget.addTab(self.audit_tab, " Audit My Paper ")
        self.tab_widget.addTab(self.logs_tab, " Logs ")

        self.tab_widget.setCurrentIndex(1)

        self.btn_help = QPushButton()
        self.btn_help.setObjectName("btn_help")
        self.btn_help.setFixedSize(28, 28)
        self.btn_help.setToolTip("Start Tour")
        self.btn_help.setIcon(QIcon(self._icon_path("help.png")))
        self.btn_help.setIconSize(self.btn_help.size())
        self.btn_help.clicked.connect(self._start_tour)

        self.btn_theme_toggle = QPushButton()
        self.btn_theme_toggle.setObjectName("btn_theme_toggle")
        self.btn_theme_toggle.setFixedSize(28, 28)
        self.btn_theme_toggle.setToolTip("Toggle dark/light theme")
        self.btn_theme_toggle.clicked.connect(self._toggle_theme)
        self._update_theme_icon()

        corner_w = QWidget()
        corner_l = QHBoxLayout(corner_w)
        corner_l.setContentsMargins(0, 0, 6, 0)
        corner_l.setSpacing(4)
        corner_l.addWidget(self.btn_help)
        corner_l.addWidget(self.btn_theme_toggle)
        self.tab_widget.setCornerWidget(corner_w, Qt.TopRightCorner)

        self.setCentralWidget(self.tab_widget)
        self._apply_tab_bar_bg()

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _connect_logs(self):
        self.log_signal.connect(self.logs_tab.add_log)
        self.log_signal.connect(self.status_bar.showMessage)
        self._stdout_redirect = _LogRedirector(self.log_signal, sys.stdout)
        self._stderr_redirect = _LogRedirector(self.log_signal, sys.stderr)
        sys.stdout = self._stdout_redirect
        sys.stderr = self._stderr_redirect

    def _setup_tour(self):
        self._tour = TourOverlay(self)
        self._tour.finished.connect(lambda: self.log_signal.emit("Tour ended."))

    def _start_tour(self):
        self._tour.resize(self.size())
        self._tour.move(0, 0)
        self._tour.show()
        self._tour.raise_()
        QTimer.singleShot(100, self._tour.start)

    def _toggle_theme(self):
        tm = ThemeManager()
        is_dark = "dark" in (tm.name or "").lower()
        next_theme = "default" if is_dark else "dark"
        if tm.load(next_theme):
            self.cfg.set("theme", next_theme)
            qss = refresh_qss()
            QApplication.instance().setStyleSheet(qss)
            self._apply_global_palette()
            self._apply_tab_bar_bg()
            self._update_theme_icon()
            self.log_signal.emit(f"Theme: {next_theme}")

    def _apply_tab_bar_bg(self):
        tm = ThemeManager()
        bg = QColor(tm.get("bg_main", "#f1f5f9"))
        bg_card = QColor(tm.get("bg_card", "#ffffff"))
        text_color = QColor(tm.get("text_primary", "#1e293b"))
        for widget in (self, self.tab_widget, self.tab_widget.tabBar()):
            widget.setAutoFillBackground(True)
            pal = widget.palette()
            pal.setColor(QPalette.Window, bg)
            pal.setColor(QPalette.Base, bg)
            pal.setColor(QPalette.Button, bg)
            pal.setColor(QPalette.WindowText, text_color)
            pal.setColor(QPalette.Text, text_color)
            widget.setPalette(pal)
        corner = self.tab_widget.cornerWidget(Qt.TopRightCorner)
        if corner:
            c_pal = corner.palette()
            c_pal.setColor(QPalette.Window, bg)
            c_pal.setColor(QPalette.Base, bg)
            c_pal.setColor(QPalette.Button, bg)
            c_pal.setColor(QPalette.WindowText, text_color)
            corner.setAutoFillBackground(True)
            corner.setPalette(c_pal)
        if self.menuBar():
            mb_pal = self.menuBar().palette()
            mb_pal.setColor(QPalette.Window, bg_card)
            mb_pal.setColor(QPalette.Base, bg_card)
            mb_pal.setColor(QPalette.Button, bg_card)
            mb_pal.setColor(QPalette.WindowText, text_color)
            mb_pal.setColor(QPalette.Text, text_color)
            self.menuBar().setAutoFillBackground(True)
            self.menuBar().setPalette(mb_pal)

    def _apply_global_palette(self):
        tm = ThemeManager()
        bg = QColor(tm.get("bg_main", "#f1f5f9"))
        text_color = QColor(tm.get("text_primary", "#1e293b"))
        app_pal = QPalette()
        app_pal.setColor(QPalette.Window, bg)
        app_pal.setColor(QPalette.WindowText, text_color)
        app_pal.setColor(QPalette.Base, bg)
        app_pal.setColor(QPalette.AlternateBase, QColor(tm.get("bg_alt", "#f8fafc")))
        app_pal.setColor(QPalette.Text, text_color)
        app_pal.setColor(QPalette.Button, bg)
        app_pal.setColor(QPalette.ButtonText, text_color)
        app_pal.setColor(QPalette.ToolTipBase, QColor(tm.get("tooltip_bg", "#1e293b")))
        app_pal.setColor(QPalette.ToolTipText, QColor(tm.get("tooltip_text", "#f1f5f9")))
        QApplication.instance().setPalette(app_pal)

    def _icon_path(self, name):
        return icon(name)

    def _update_theme_icon(self):
        icon = QIcon(self._icon_path("light-dark.png"))
        self.btn_theme_toggle.setIcon(icon)
        self.btn_theme_toggle.setIconSize(self.btn_theme_toggle.size())
        self.btn_theme_toggle.setText("")

    def closeEvent(self, event):
        try:
            self.audit_tab._save_state()
        except Exception:
            pass
        active = []
        if getattr(self.search_tab, '_search_active', False):
            active.append("search")
        if getattr(self.search_tab, '_current_dl_worker', None) and self.search_tab._current_dl_worker and self.search_tab._current_dl_worker.isRunning():
            active.append("download")
        if getattr(self.search_tab, '_scoring_worker', None) and self.search_tab._scoring_worker and self.search_tab._scoring_worker.isRunning():
            active.append("scoring")
        if getattr(self.search_tab, '_pdf_import_worker', None) and self.search_tab._pdf_import_worker and self.search_tab._pdf_import_worker.isRunning():
            active.append("import")
        if getattr(self.summarize_tab, '_llm_worker', None) and self.summarize_tab._llm_worker and self.summarize_tab._llm_worker.isRunning():
            active.append("summarization")
        if getattr(self.audit_tab, '_worker', None) and self.audit_tab._worker and self.audit_tab._worker.isRunning():
            active.append("audit")

        if active:
            reply = QMessageBox.question(
                self, "Active Operations",
                f"Still running: {', '.join(active)}\n\nStop and close?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return

            event.ignore()
            self._closing_async = True
            self._start_async_close()
            return

        if getattr(self, '_closing_async', False):
            self.cfg.save()
            try:
                self.search_tab._session_save()
            except Exception:
                pass
            super().closeEvent(event)
            return

        self.cfg.save()
        try:
            self.search_tab._session_save()
        except Exception:
            pass
        super().closeEvent(event)

    def _start_async_close(self):
        import time as _time
        self.status_bar.showMessage("Shutting down...")
        self._close_deadline = _time.time() + 5.0
        self._close_workers = []
        self.search_tab._closing = True

        for w in ('_current_search_worker', '_scoring_worker', '_current_dl_worker', '_pdf_import_worker'):
            worker = getattr(self.search_tab, w, None)
            if worker and worker.isRunning():
                try:
                    if hasattr(worker, 'stop'):
                        worker.stop()
                    self._close_workers.append(worker)
                except Exception:
                    pass

        for w in ('_score_stop_worker', '_dl_stop_worker'):
            worker = getattr(self.search_tab, w, None)
            if worker and worker.isRunning():
                self._close_workers.append(worker)
            setattr(self.search_tab, w, None)

        for t in ('_score_stop_timer', '_dl_stop_timer'):
            timer = getattr(self.search_tab, t, None)
            if timer and timer.isActive():
                timer.stop()

        llm_w = getattr(self.summarize_tab, '_llm_worker', None)
        if llm_w and llm_w.isRunning():
            try:
                if hasattr(llm_w, 'stop'):
                    llm_w.stop()
                self._close_workers.append(llm_w)
            except Exception:
                pass

        audit_w = getattr(self.audit_tab, '_worker', None)
        if audit_w and audit_w.isRunning():
            try:
                self.audit_tab._closing = True
                if hasattr(audit_w, 'stop'):
                    audit_w.stop()
                self._close_workers.append(audit_w)
            except Exception:
                pass

        if not self._close_workers:
            self.close()
            return

        self._close_timer = QTimer()
        self._close_timer.timeout.connect(self._poll_close)
        self._close_timer.start(100)

    def _poll_close(self):
        import time as _time
        still_running = [w for w in self._close_workers if w.isRunning()]
        if not still_running or _time.time() >= self._close_deadline:
            self._close_timer.stop()
            for w in still_running:
                try:
                    w.terminate()
                    w.wait(1000)
                except Exception:
                    pass
            self.close()
