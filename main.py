"""
ResearchForge — GUI entry point.

Usage:
    python main.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon, QPixmap
from gui.main_window import MainWindow
from gui.app_info import APP_NAME, APP_ID, APP_VERSION, icon


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_ID)
    app.setStyle("Fusion")

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass

    icon_path = icon("app_icone.png")
    if os.path.exists(icon_path):
        pix = QPixmap(icon_path)
        app_icon = QIcon(pix)
        app.setWindowIcon(app_icon)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
