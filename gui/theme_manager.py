"""
Theme manager — loads JSON theme files, substitutes tokens into QSS templates,
and provides a programmatic API to change colors at runtime.
"""
import json
import os
import re

from gui.app_info import resource


class ThemeManager:
    """Singleton that loads a JSON theme and generates token-substituted QSS."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tokens = {}
            cls._instance._current_name = ""
            cls._instance._qss_cache = ""
        return cls._instance

    @property
    def tokens(self):
        return dict(self._tokens)

    @property
    def name(self):
        return self._current_name

    def load(self, name_or_path):
        path = name_or_path
        if not os.path.isabs(name_or_path) and not name_or_path.endswith(".json"):
            builtin = resource(os.path.join("themes", f"{name_or_path}.json"))
            if os.path.isfile(builtin):
                path = builtin
        if not os.path.isfile(path):
            path = resource(os.path.join("themes", "default.json"))
            if not os.path.isfile(path):
                return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._tokens = dict(data.get("tokens", {}))
            self._current_name = data.get("name", os.path.splitext(os.path.basename(path))[0])
            self._qss_cache = ""
            return True
        except Exception:
            return False

    def set(self, key, value):
        self._tokens[key] = value
        self._qss_cache = ""

    def get(self, key, default=None):
        return self._tokens.get(key, default)

    def expand(self, template):
        def _replace(match):
            key = match.group(1)
            return self._tokens.get(key, match.group(0))
        return re.sub(r'\$\{(\w+)\}', _replace, template)

    def qss(self):
        if self._qss_cache:
            return self._qss_cache
        self._qss_cache = self.expand(_BASE_QSS)
        return self._qss_cache

    @staticmethod
    def list_themes():
        themes_dir = resource("themes")
        if not os.path.isdir(themes_dir):
            return []
        return sorted(os.path.splitext(f)[0] for f in os.listdir(themes_dir) if f.endswith(".json"))

    def color(self, name):
        from PySide6.QtGui import QColor
        return QColor(self._tokens.get(name, "#000000"))


# ═══════════════════════════════════════════════════════════════════
#  BASE QSS TEMPLATE  —  professional widget styling
#  All colors as ${tokens}, no hardcoded hex values.
# ═══════════════════════════════════════════════════════════════════

_BASE_QSS = r"""
/* ═══  GLOBAL  ═══════════════════════════════════════════════════ */

* {
    font-family: ${font_sans};
}

QWidget {
    color: ${text_primary};
    background-color: transparent;
}

QMainWindow {
    background-color: ${bg_main};
}

QDialog {
    background-color: ${bg_main};
}

QScrollArea {
    background-color: transparent;
    border: none;
}

QFrame {
    color: ${text_primary};
    background-color: transparent;
}

QSplitter {
    background-color: ${bg_main};
}

QTabWidget {
    background-color: ${bg_main};
}

QTabWidget::pane {
    border: 1px solid ${border_default};
    border-radius: 6px;
    background: ${bg_main};
}

QTabWidget::tab-bar {
    background-color: ${bg_main};
}

QTabBar {
    background-color: ${bg_main};
}

/* ═══  SCROLLBARS  ══════════════════════════════════════════════ */

QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: ${scrollbar_handle};
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: ${scrollbar_handle_hover};
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: ${scrollbar_handle};
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: ${scrollbar_handle_hover};
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ═══  TAB WIDGET  ══════════════════════════════════════════════ */

QTabBar::tab {
    background: transparent;
    border: 1px solid ${border_disabled};
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    padding: 3px 8px;
    margin-right: 1px;
    font-size: 12px;
    font-weight: 500;
    color: ${text_secondary};
}

QTabBar::tab:selected {
    background: ${bg_main};
    color: ${primary};
    font-weight: 600;
    border-color: ${border_default};
    border-bottom: 2px solid ${primary};
}

QTabBar::tab:hover:!selected {
    background: ${primary_light};
    color: ${primary};
}

/* ═══  GROUP BOX  ═══════════════════════════════════════════════ */

QGroupBox {
    font-weight: 600;
    font-size: 12px;
    color: ${text_primary};
    border: 1px solid ${border_default};
    border-radius: 4px;
    margin-top: 8px;
    padding: 6px 4px 2px 4px;
    background: ${bg_card};
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: ${primary};
    font-weight: 700;
    font-size: 11px;
}

/* ═══  BUTTONS — DEFAULT  ══════════════════════════════════════ */

QPushButton {
    background: ${bg_card};
    border: 1px solid ${border_default};
    border-radius: 5px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 500;
    color: ${text_primary};
    min-height: 18px;
}

QPushButton:hover {
    background: ${bg_secondary};
    border-color: ${primary};
    color: ${primary};
}

QPushButton:pressed {
    background: ${primary_light};
}

QPushButton:disabled {
    background: ${bg_disabled};
    color: ${text_disabled};
    border-color: ${border_disabled};
}

/* ═══  BUTTONS — PRIMARY  ══════════════════════════════════════ */

QPushButton#btn_search,
QPushButton#btn_process,
QPushButton#btn_session_new,
QPushButton#sc_btn_find_queries,
QPushButton#sc_btn_enhance,
QPushButton#sc_btn_analyze {
    background: ${primary};
    color: ${text_inverse};
    border: none;
    border-radius: 5px;
    padding: 6px 16px;
    font-weight: 600;
    font-size: 12px;
}
QPushButton#btn_search:hover,
QPushButton#btn_process:hover,
QPushButton#btn_session_new:hover,
QPushButton#sc_btn_find_queries:hover,
QPushButton#sc_btn_enhance:hover,
QPushButton#sc_btn_analyze:hover {
    background: ${primary_hover};
}
QPushButton#btn_search:pressed,
QPushButton#btn_process:pressed {
    background: ${primary_pressed};
}
QPushButton#btn_search:disabled,
QPushButton#btn_process:disabled {
    background: ${bg_disabled};
    color: ${text_disabled};
}

/* ═══  BUTTONS — DANGER / STOP  ════════════════════════════════ */

QPushButton#btn_stop,
QPushButton#btn_delete {
    background: ${danger};
    color: ${text_inverse};
    border: none;
    border-radius: 5px;
    padding: 6px 16px;
    font-weight: 600;
    font-size: 12px;
}
QPushButton#btn_stop:hover,
QPushButton#btn_delete:hover {
    background: ${danger_hover};
}

QPushButton#btn_remove {
    background: transparent;
    color: ${danger};
    border: 1px solid ${danger};
    border-radius: 5px;
    padding: 5px 14px;
    font-weight: 500;
    font-size: 12px;
}
QPushButton#btn_remove:hover {
    background: ${danger_light};
}

QPushButton#btn_clear_small {
    font-size: 11px;
    padding: 2px 6px;
    color: ${danger};
    border: none;
    background: transparent;
}
QPushButton#btn_clear_small:hover {
    color: ${danger_hover};
    background: ${danger_light};
}

/* ═══  BUTTONS — SUCCESS / DONE  ═══════════════════════════════ */

QPushButton#btn_save,
QPushButton#btn_session_save {
    background: ${success};
    color: ${text_inverse};
    border: none;
    border-radius: 5px;
    padding: 5px 14px;
    font-weight: 600;
    font-size: 12px;
}
QPushButton#btn_save:hover,
QPushButton#btn_session_save:hover {
    background: ${success_hover};
}
QPushButton#btn_session_save {
    padding: 4px 12px;
    font-size: 11px;
}

QPushButton#btn_done {
    background: ${done_green};
    color: ${text_inverse};
    border: none;
    border-radius: 5px;
    padding: 5px 14px;
    font-weight: 600;
    font-size: 12px;
}

/* ═══  BUTTONS — ACTION COLORS  ════════════════════════════════ */

QPushButton#btn_download {
    background: ${action_teal};
    color: ${text_inverse};
    border: none;
    border-radius: 5px;
    padding: 5px 12px;
    font-weight: 600;
    font-size: 12px;
}
QPushButton#btn_download:hover {
    background: ${action_teal_hover};
}
QPushButton#btn_download:pressed {
    background: ${action_teal_pressed};
}
QPushButton#btn_download:disabled {
    background: ${bg_disabled};
    color: ${text_disabled};
}

QPushButton#btn_rate {
    background: ${action_indigo};
    color: ${text_inverse};
    border: none;
    border-radius: 5px;
    padding: 5px 12px;
    font-weight: 600;
    font-size: 12px;
}
QPushButton#btn_rate:hover {
    background: ${action_indigo_hover};
}
QPushButton#btn_rate:disabled {
    background: ${bg_disabled};
    color: ${text_disabled};
}

QPushButton#btn_session_load,
QPushButton#btn_session_edit {
    background: ${action_bluegrey};
    color: ${text_inverse};
    border: none;
    border-radius: 5px;
    padding: 5px 12px;
    font-weight: 500;
    font-size: 12px;
}
QPushButton#btn_session_load:hover,
QPushButton#btn_session_edit:hover {
    background: ${action_bluegrey_hover};
}

QPushButton#btn_discover {
    background: ${action_purple};
    color: ${text_inverse};
    border: none;
    border-radius: 5px;
    padding: 5px 12px;
    font-weight: 600;
}
QPushButton#btn_discover:hover {
    background: ${action_purple_hover};
}

QPushButton#btn_enhance {
    background: ${action_amber};
    color: ${text_inverse};
    border: none;
    border-radius: 5px;
    padding: 5px 12px;
    font-weight: 600;
    font-size: 12px;
}
QPushButton#btn_enhance:hover {
    background: ${action_amber_hover};
}

/* ═══  BUTTONS — SECONDARY / UTILITY (soft slate)  ═════════════ */

QPushButton#btn_secondary {
    background: ${bg_alt};
    color: ${text_secondary};
    border: 1px solid ${border_default};
    border-radius: 5px;
    padding: 5px 12px;
    font-weight: 500;
    font-size: 12px;
}
QPushButton#btn_secondary:hover {
    background: ${bg_secondary};
    border-color: ${border_input};
    color: ${text_primary};
}
QPushButton#btn_secondary:pressed {
    background: ${bg_secondary};
}
QPushButton#btn_secondary:disabled {
    background: ${bg_disabled};
    color: ${text_disabled};
    border-color: ${border_disabled};
}

/* ═══  BUTTONS — PRESETS (indigo accent)  ══════════════════════ */

QPushButton#btn_presets {
    background: ${action_indigo};
    color: ${text_inverse};
    border: none;
    border-radius: 5px;
    padding: 5px 14px;
    font-weight: 600;
    font-size: 12px;
}
QPushButton#btn_presets:hover {
    background: ${action_indigo_hover};
}

QPushButton#btn_refresh_tree {
    background: ${action_lightblue};
    color: ${text_inverse};
    border: none;
    border-radius: 5px;
    padding: 4px 10px;
    font-weight: 500;
    font-size: 12px;
}
QPushButton#btn_refresh_tree:hover {
    background: ${action_lightblue_hover};
}

QPushButton#btn_copy_link {
    font-size: 11px;
    padding: 2px 4px;
    border: none;
    background: transparent;
    color: ${link_blue};
}
QPushButton#btn_copy_link:hover {
    color: ${link_blue_hover};
}

/* ═══  LINE EDIT / TEXT INPUT  ═════════════════════════════════ */

QLineEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox {
    border: 1px solid ${border_input};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
    background: ${bg_input};
    color: ${text_primary};
    selection-background-color: ${selection_bg};
    selection-color: ${selection_fg};
}

QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus {
    border: 2px solid ${border_focus};
    padding: 3px 7px;
    background: ${bg_card};
}

QLineEdit:disabled,
QComboBox:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled {
    background: ${bg_disabled};
    color: ${text_disabled};
}

QComboBox::drop-down {
    border: none;
    width: 20px;
    background: transparent;
}

QComboBox::down-arrow {
    image: none;
    border: none;
}

QComboBox QAbstractItemView {
    background: ${bg_card};
    border: 1px solid ${border_default};
    border-radius: 4px;
    padding: 2px;
    selection-background-color: ${selection_bg};
    selection-color: ${selection_fg};
    outline: none;
}

QComboBox QAbstractItemView::item {
    min-height: 28px;
    padding: 4px 10px;
    border-radius: 3px;
}

/* ═══  TEXT EDIT  ══════════════════════════════════════════════ */

QTextEdit,
QPlainTextEdit {
    border: 1px solid ${border_input};
    border-radius: 6px;
    background: ${bg_input};
    color: ${text_primary};
    font-family: ${font_mono};
    font-size: 12px;
    padding: 6px;
    selection-background-color: ${selection_bg};
    selection-color: ${selection_fg};
}

QTextEdit:focus,
QPlainTextEdit:focus {
    border: 2px solid ${border_focus};
    padding: 5px;
}

QTextEdit#sc_research_area {
    font-family: ${font_sans};
    font-size: 13px;
    line-height: 1.5;
}

/* ═══  TABLES  ═════════════════════════════════════════════════ */

QTableWidget,
QTreeWidget {
    background: ${bg_card};
    alternate-background-color: ${bg_alt};
    border: 1px solid ${border_default};
    border-radius: 4px;
    gridline-color: ${border_disabled};
    font-size: 12px;
    outline: none;
}

QTableWidget::corner {
    background: ${bg_secondary};
    border: none;
}

QTableView QTableCornerButton::section {
    background: ${bg_secondary};
    border: none;
    border-right: 1px solid ${border_default};
    border-bottom: 2px solid ${primary};
}

QTableWidget::item,
QTreeWidget::item {
    padding: 4px 6px;
    border-bottom: 1px solid ${border_disabled};
}

QTableWidget::item:selected,
QTreeWidget::item:selected {
    background: ${primary_light};
    color: ${primary};
}

QHeaderView::section {
    background: ${bg_secondary};
    color: ${text_primary};
    padding: 5px 8px;
    border: none;
    border-right: 1px solid ${border_default};
    border-bottom: 2px solid ${primary};
    font-weight: 700;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}

/* ═══  LABELS  ═════════════════════════════════════════════════ */

QLabel {
    color: ${text_primary};
    font-size: 12px;
}

QLabel#sc_bold_label {
    font-weight: 700;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    color: ${text_secondary};
}

QLabel#lbl_status,
QLabel#search_status,
QLabel#lbl_session {
    font-weight: 600;
    color: ${primary};
    font-size: 12px;
}

QLabel#qa_title_label {
    font-weight: 700;
    font-size: 13px;
    color: ${text_primary};
}

QLabel#sc_model_status,
QLabel#sc_ctx_status,
QLabel#sc_paper_llm_status,
QLabel#sc_hint {
    font-size: 10px;
    color: ${hint_gray};
    font-style: italic;
}

QLabel#sc_paper_file {
    font-size: 11px;
    color: ${hint_gray};
    font-style: italic;
}

QLabel#sc_paper_info {
    font-size: 11px;
    color: ${text_secondary};
}

QLabel#sc_paper_status {
    font-size: 11px;
    color: ${primary};
    font-style: italic;
}

QLabel#sc_badge {
    font-size: 10px;
    font-weight: 600;
    color: ${badge_orange};
}

QLabel#sc_link_label {
    font-size: 11px;
    color: ${text_secondary};
}

QLabel#abstract_panel {
    color: ${panel_text};
    font-size: 11px;
    padding: 4px 6px;
    background: ${panel_bg};
    border: 1px solid ${panel_border};
    border-radius: 4px;
}

/* ═══  PROGRESS BAR  ═══════════════════════════════════════════ */

QProgressBar {
    border: 1px solid ${border_default};
    border-radius: 4px;
    text-align: center;
    height: 16px;
    font-size: 10px;
    font-weight: 600;
    background: ${bg_secondary};
    color: ${text_primary};
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 ${primary}, stop:1 ${progress_gradient_end});
    border-radius: 3px;
    margin: 1px;
}

/* ═══  STATUS BAR  ═════════════════════════════════════════════ */

QStatusBar {
    background: ${bg_card};
    color: ${text_secondary};
    border-top: 1px solid ${border_default};
    padding: 2px 8px;
    font-size: 11px;
}

QStatusBar::item {
    border: none;
}

/* ═══  SPLITTER  ═══════════════════════════════════════════════ */

QSplitter::handle {
    background: ${border_default};
}

QSplitter::handle:horizontal {
    width: 1px;
}

QSplitter::handle:vertical {
    height: 1px;
}

/* ═══  DOCK WIDGET  ════════════════════════════════════════════ */

QDockWidget {
    border: none;
    font-size: 12px;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}

QDockWidget::title {
    background: ${bg_secondary};
    padding: 6px 10px;
    font-weight: 700;
    color: ${text_primary};
    border-bottom: 1px solid ${border_default};
}

/* ═══  CHECKBOX  ═══════════════════════════════════════════════ */

QCheckBox {
    spacing: 6px;
    font-size: 12px;
    color: ${text_primary};
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid ${border_input};
    border-radius: 3px;
    background: ${bg_input};
}

QCheckBox::indicator:checked {
    background: ${primary};
    border-color: ${primary};
}

QCheckBox::indicator:hover {
    border-color: ${primary};
}

/* ═══  TOOLTIP  ════════════════════════════════════════════════ */

QToolTip {
    background: ${tooltip_bg};
    color: ${tooltip_text};
    border: 1px solid ${primary};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
}

/* ═══  MENU BAR  ═══════════════════════════════════════════════ */

QMenuBar {
    background: ${bg_card};
    border-bottom: 1px solid ${border_default};
    padding: 1px;
}

QMenuBar::item {
    padding: 3px 10px;
    border-radius: 3px;
}

QMenuBar::item:selected {
    background: ${primary_light};
}

QMenu {
    background: ${bg_card};
    border: 1px solid ${border_default};
    border-radius: 4px;
    padding: 3px;
}

QMenu::item {
    padding: 5px 24px 5px 10px;
    border-radius: 3px;
}

QMenu::item:selected {
    background: ${primary_light};
    color: ${primary};
}

QMenu::separator {
    height: 1px;
    background: ${border_default};
    margin: 3px 6px;
}

/* ═══  LIST WIDGET  ════════════════════════════════════════════ */

QListWidget {
    background: ${bg_card};
    border: 1px solid ${border_default};
    border-radius: 4px;
    padding: 2px;
    outline: none;
    font-size: 12px;
}

QListWidget::item {
    padding: 4px 8px;
    border-radius: 3px;
}

QListWidget::item:selected {
    background: ${primary_light};
    color: ${primary};
}

QListWidget::item:hover:!selected {
    background: ${bg_secondary};
}

/* ═══  TOUR WIDGETS  ═══════════════════════════════════════════ */

#tour_bubble {
    background: ${bg_card};
    border: 2px solid ${primary};
    border-radius: 10px;
}
#tour_step_label,
#tour_title {
    color: ${primary};
}
#tour_step_label {
    font-size: 10px;
    font-weight: 700;
}
#tour_text {
    color: ${text_primary};
    font-size: 12px;
    line-height: 1.5;
}
#tour_separator {
    color: ${border_disabled};
    max-height: 1px;
}
#tour_btn_skip {
    background: transparent;
    color: ${text_muted};
    border: none;
    font-size: 12px;
    padding: 4px 8px;
}
#tour_btn_skip:hover {
    color: ${text_secondary};
}
#tour_btn_back {
    background: ${bg_secondary};
    color: ${text_primary};
    border: 1px solid ${border_default};
    border-radius: 5px;
    padding: 5px 14px;
    font-size: 12px;
}
#tour_btn_back:hover {
    background: ${primary_light};
    border-color: ${primary};
    color: ${primary};
}
#tour_btn_next {
    background: ${primary};
    color: ${text_inverse};
    border: none;
    border-radius: 5px;
    padding: 5px 16px;
    font-weight: 700;
    font-size: 12px;
}
#tour_btn_next:hover {
    background: ${primary_hover};
}

/* ═══  DIALOG BUTTON BOX  ═════════════════════════════════════ */

QDialogButtonBox QPushButton {
    padding: 5px 14px;
    font-size: 12px;
}

/* ═══  THEME TOGGLE  ══════════════════════════════════════════ */

QPushButton#btn_theme_toggle,
QPushButton#btn_help,
QPushButton#btn_refresh_table,
QPushButton#btn_refresh_models,
QPushButton#btn_browse {
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 0px;
}
QPushButton#btn_theme_toggle:hover,
QPushButton#btn_help:hover,
QPushButton#btn_refresh_table:hover,
QPushButton#btn_refresh_models:hover,
QPushButton#btn_browse:hover {
    background: ${primary_light};
    border: 1px solid ${primary};
}
"""
