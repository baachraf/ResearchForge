"""
ResearchForge — entry point.

Usage:
    python main.py          # launch the desktop GUI
    python main.py --mcp    # run the headless MCP server over stdio (no GUI)

The same entry point backs the built ResearchForge.exe: double-click for the
GUI, or run it with --mcp so an MCP client (Claude Code / opencode) can spawn
it as a stdio server. Qt is imported only on the GUI path.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_mcp():
    """Headless MCP server over stdio — no Qt/GUI imported on this path."""
    from mcp_server_researchforge import mcp
    mcp.run(transport="stdio")


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon, QPixmap
    from gui.main_window import MainWindow
    from gui.app_info import APP_NAME, APP_ID, APP_VERSION, icon

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
    if "--mcp" in sys.argv:
        run_mcp()
    else:
        main()
