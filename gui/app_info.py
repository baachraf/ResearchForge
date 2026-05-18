"""
Centralised application metadata and resource-path resolver.

Works in all contexts:
  - python main.py
  - python -m package
  - PyInstaller --onefile / --onedir   (sets sys._MEIPASS)
  - Nuitka --standalone
"""
import sys
import os

APP_NAME = "ResearchForge"
APP_ID = "ResearchForge"
APP_VERSION = "v0.0.1"


def app_root() -> str:
    """Return the project root directory."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource(rel_path: str) -> str:
    """Return an absolute path to a bundled resource (icon, prompt, etc.).

    In a frozen build, resources live next to the executable (or inside
    ``sys._MEIPASS`` for PyInstaller ``--onefile``).
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel_path)


def icon(name: str) -> str:
    """Shortcut: ``resource("design/<name>")``."""
    return resource(os.path.join("design", name))
