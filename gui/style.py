"""
Global stylesheet — delegated to ThemeManager.
Import STYLE_QSS to get the current theme's QSS string.
"""
from gui.theme_manager import ThemeManager


def _get_qss():
    tm = ThemeManager()
    if not tm.name:
        tm.load("default")
    return tm.qss()


# Lazy: generated on first access from the currently loaded theme.
# After calling ThemeManager().load(...) or .set(...), re-access this
# module attribute to pick up changes.
STYLE_QSS = _get_qss()


def refresh_qss():
    """Force re-generation after a theme change."""
    global STYLE_QSS
    tm = ThemeManager()
    tm._qss_cache = ""
    STYLE_QSS = tm.qss()
    return STYLE_QSS

