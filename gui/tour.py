"""
Guided tour overlay for the application.
Shows step-by-step highlights with description bubbles.
Can open dialogs and spotlight widgets inside them.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSizePolicy, QApplication,
)
from PySide6.QtCore import (
    Qt, QRect, QPoint, Signal, QTimer,
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPainterPath, QFont,
)


TOUR_STEPS = [
    {
        "title": "Welcome!",
        "text": (
            "Welcome to <b>ResearchForge</b>!<br><br>"
            "This tour will walk you through the main features of the app.<br><br>"
            "You can restart it anytime from <b>Help \u2192 Start Tour</b>."
        ),
        "target": None,
        "tab": None,
    },
    {
        "title": "Settings & Prompts Tab",
        "text": (
            "This is where you configure your <b>LLM provider</b> (LM Studio, Ollama, etc.), "
            "set <b>API keys</b>, choose <b>output directories</b>, and edit the <b>system prompts</b> "
            "used during summarization."
        ),
        "target": "tab_widget",
        "tab": 0,
    },
    {
        "title": "LLM Configuration",
        "text": (
            "Select your <b>LLM provider</b> and <b>model</b> here.<br><br>"
            "Click the <b>\u21bb</b> button to refresh the model list from your local server. "
            "The status label confirms the connection."
        ),
        "target": "grp_llm",
        "tab": 0,
    },
    {
        "title": "API Keys",
        "text": (
            "Enter API keys for <b>DeepSeek</b>, <b>Gemini</b>, <b>Brave Search</b>, "
            "and <b>Semantic Scholar</b>.<br><br>"
            "Keys for local LLMs (LM Studio / Ollama) are not required."
        ),
        "target": "grp_keys",
        "tab": 0,
    },
    {
        "title": "Output Directories",
        "text": (
            "Set the <b>Output</b> folder (where PDFs are downloaded) and the "
            "<b>Summaries</b> folder (where LLM-generated summaries are saved)."
        ),
        "target": "grp_dirs",
        "tab": 0,
    },
    {
        "title": "Query Defaults & Sources",
        "text": (
            "Configure default search parameters: date filter, max results, max PDF size, "
            "title keyword threshold, and which <b>sources</b> to search "
            "(arXiv, Semantic Scholar, DuckDuckGo, Brave, PubMed)."
        ),
        "target": "grp_def",
        "tab": 0,
    },
    {
        "title": "Prompt Editor",
        "text": (
            "The bottom section of this tab contains 9 <b>editable system prompts</b> "
            "that control how the LLM analyzes papers, synthesizes topics, and generates "
            "related-work sections."
        ),
        "target": "prompt_tab",
        "tab": 0,
    },
    {
        "title": "Search & Download Tab",
        "text": (
            "This is your main workspace. Manage <b>sessions</b>, build <b>search queries</b>, "
            "run searches across multiple databases, and download PDFs."
        ),
        "target": "tab_widget",
        "tab": 1,
    },
    {
        "title": "Session Management",
        "text": (
            "Use <b>+ New</b> to create a session (describe your research \u2192 AI generates queries).<br>"
            "<b>Load</b> restores a previous session.<br>"
            "<b>Edit</b> lets you modify the session context and queries."
        ),
        "target": "btn_session_new",
        "tab": 1,
    },
    {
        "title": "Session Creator \u2014 3 Ways to Create",
        "text": (
            "This dialog has different tabs depending on your <b>Settings \u2192 Search Mode</b>:<br><br>"
            "<b>Academic mode</b> (3 tabs):<br>"
            "\u2022 <b>Manual</b> \u2014 Describe research, AI generates queries<br>"
            "\u2022 <b>Analyze My Paper</b> \u2014 Upload your paper for LLM analysis<br>"
            "\u2022 <b>Find Papers</b> \u2014 Search &amp; download by paper title<br><br>"
            "<b>General mode</b> (2 tabs, no Analyze My Paper):<br>"
            "\u2022 <b>Manual</b> \u2014 AI query generation OR direct query input<br>"
            "\u2022 <b>Find Papers</b> \u2014 Search &amp; download by paper title"
        ),
        "target": "_tab_widget",
        "tab": None,
        "dialog": "session_creator",
        "dialog_tab": None,
    },
    {
        "title": "Manual Tab \u2014 AI Query Generation",
        "text": (
            "Write your research description here, then click <b>Enhance</b> to polish it "
            "into academic language. Add <b>focus keywords</b> and <b>avoid topics</b> "
            "to guide the AI.<br><br>"
            "Click <b>Find Queries</b> to generate search queries from your context.<br>"
            "Queries appear in the <b>Suggested</b> table on the left \u2014 use "
            "<b>&gt;&gt;</b> to move them to <b>Search Queries</b> on the right."
        ),
        "target": "research_text",
        "tab": None,
        "dialog": "session_creator",
        "dialog_tab": 0,
    },
    {
        "title": "Manual Tab \u2014 Direct Entry (General mode)",
        "text": (
            "In <b>General</b> mode, the Manual tab shows a <b>Direct query input</b> "
            "box instead of the AI section.<br><br>"
            "Type search terms one per line and click <b>Add Queries</b> \u2014 "
            "no AI needed. Great for quick web or manual searches.<br><br>"
            "<i>Switch mode in Settings \u2192 Search Mode dropdown.</i>"
        ),
        "target": "_tab_widget",
        "tab": None,
        "dialog": "session_creator",
        "dialog_tab": 0,
    },
    {
        "title": "Suggested \u2192 Search Queries",
        "text": (
            "The <b>Suggested</b> table (left) shows AI-generated queries. "
            "Check the ones you want, then use the arrow buttons to "
            "transfer them to the <b>Search Queries</b> table (right).<br><br>"
            "Only queries in <b>Search Queries</b> will be executed. "
            "You can also click <b>+ Manual</b> to add a query from scratch."
        ),
        "target": "suggested_table",
        "tab": None,
        "dialog": "session_creator",
        "dialog_tab": 0,
    },
    {
        "title": "Analyze My Paper Tab",
        "text": (
            "<b>Academic mode only.</b> Upload your own paper (PDF or text) and "
            "the LLM will extract its claims, methods, comparisons, and key results.<br><br>"
            "It also generates targeted search queries to find competing and related work. "
            "Click <b>Analyze Paper</b> to start, then <b>Apply to Session</b> "
            "to populate the Manual tab with the extracted context and queries."
        ),
        "target": "btn_analyze",
        "tab": None,
        "dialog": "session_creator",
        "dialog_tab": 1,
    },
    {
        "title": "Find Papers Tab",
        "text": (
            "Enter a <b>paper title</b> and click <b>+ Add</b> to save it. "
            "All papers are searched as exact phrases and downloaded into a "
            "subfolder named by the <b>Topic Name</b> you provide.<br><br>"
            "Click <b>Add to Search Queries</b> to turn them into executable search queries. "
            "Use <b>\u00d7 Remove Selected</b> to clean up the list."
        ),
        "target": "_tab_widget",
        "tab": None,
        "dialog": "session_creator",
        "dialog_tab": 2,
    },
    {
        "title": "Load & Manage Sessions",
        "text": (
            "Click <b>Load</b> to browse all saved sessions with their "
            "query counts, result counts, and creation dates.<br><br>"
            "Select a session and click <b>OK</b> to restore everything: "
            "queries, search results, and the research context. "
            "Your Summarize tab settings are also restored.<br><br>"
            "Check sessions in the list and click <b>Delete Checked</b> to "
            "clean up old sessions you no longer need."
        ),
        "target": "btn_session_load",
        "tab": 1,
    },
    {
        "title": "Query Table",
        "text": (
            "Each row is a search query. <b>Check</b> the ones you want to run, "
            "then click <b>\u25b6 Search</b>.<br><br>"
            "Double-click a row to edit it with the advanced Query Builder "
            "(AND/OR terms, must-contain, must-NOT, source selection)."
        ),
        "target": "query_table",
        "tab": 1,
    },
    {
        "title": "Search Results",
        "text": (
            "After searching, results appear here. Use <b>title filters</b>, "
            "<b>source checkboxes</b>, and the <b>score threshold</b> to narrow down."
        ),
        "target": "results_table",
        "tab": 1,
    },
    {
        "title": "Downloading Papers",
        "text": (
            "Three download modes below the results table:<br><br>"
            "<b>\u2b07 Selected</b> \u2014 Downloads only the papers you checked in the table.<br>"
            "<b>\u2b07 Score</b> \u2014 Downloads all papers above the Min score, "
            "ignoring any title filter \u2014 great for bulk downloads.<br>"
            "<b>\u2b07 All</b> \u2014 Downloads every visible paper in the results table.<br><br>"
            "PDFs are saved to <b>Output Root / [Session Name] / [Query Folder] /</b>.<br>"
            "HTML pages and invalid files are automatically skipped."
        ),
        "target": "btn_download_selected",
        "tab": 1,
    },
    {
        "title": "Relevance Scoring & Filters",
        "text": (
            "<b>\u2605 Check Relevance</b> \u2014 Uses your LLM to score each paper's relevance "
            "to your research context on a 0\u2013100 scale.<br><br>"
            "<b>Check Depth</b> \u2014 Controls how much of the PDF the LLM reads:<br>"
            "\u2022 <b>Quick (2 pages)</b> \u2014 fastest, reads title + abstract<br>"
            "\u2022 <b>Half (~50%)</b> \u2014 balanced speed vs accuracy<br>"
            "\u2022 <b>Full paper</b> \u2014 slowest, most accurate<br><br>"
            "<b>Min Score</b> + <b>Show \u2265</b> checkbox \u2014 Filter the table to show "
            "only papers above your chosen threshold after scoring."
        ),
        "target": "btn_rate",
        "tab": 1,
    },
    {
        "title": "Summarize Tab",
        "text": (
            "This is the LLM pipeline. Feed your downloaded papers through "
            "<b>per-paper analysis \u2192 topic synthesis \u2192 global synthesis \u2192 related work</b>."
        ),
        "target": "tab_widget",
        "tab": 2,
    },
    {
        "title": "Research Context",
        "text": (
            "Describe <b>your research</b> here. This text is included in every LLM prompt "
            "so the model can compare papers against your work.<br><br>"
            "Click <b>\u2728 Enhance</b> to let the LLM rewrite your description in academic language."
        ),
        "target": "grp_ctx",
        "tab": 2,
    },
    {
        "title": "Analysis Lenses",
        "text": (
            "These checkboxes control which <b>analysis lenses</b> are active:<br>"
            "\u2022 <b>Similarity</b> \u2014 only papers sharing methods/goals with yours<br>"
            "\u2022 <b>Novelty</b> \u2014 highlight genuine contributions<br>"
            "\u2022 <b>Methodology</b> \u2014 compare methods against yours<br>"
            "\u2022 <b>Gaps</b> \u2014 identify open problems your work could address"
        ),
        "target": "chk_similarity",
        "tab": 2,
    },
    {
        "title": "Pipeline Mode",
        "text": (
            "Choose the pipeline stage to run:<br>"
            "\u2022 <b>Per-Paper</b> \u2014 analyze each PDF individually<br>"
            "\u2022 <b>Topic</b> \u2014 synthesize all papers per topic folder<br>"
            "\u2022 <b>Global</b> \u2014 cross-topic synthesis<br>"
            "\u2022 <b>Related Work</b> \u2014 generate a Related Work section<br>"
            "\u2022 <b>All</b> \u2014 runs everything end-to-end in sequence"
        ),
        "target": "btn_all",
        "tab": 2,
    },
    {
        "title": "Papers Tree",
        "text": (
            "Shows downloaded papers organized by <b>topic folder</b>. "
            "The status column shows progress: <b>Pending \u2192 Done \u2192 Skipped</b>.<br><br>"
            "Click <b>\u27f3 Refresh</b> after downloads to update the tree."
        ),
        "target": "paper_tree",
        "tab": 2,
    },
    {
        "title": "Check Summaries Tab",
        "text": (
            "Browse all generated summaries in a tree view. Click any file to preview its content "
            "in the viewer below. Organized by model \u2192 topic \u2192 individual paper analysis."
        ),
        "target": "tab_widget",
        "tab": 3,
    },
    {
        "title": "You're All Set!",
        "text": (
            "Here's a quick recap of the workflow:<br><br>"
            "<b>1.</b> Configure LLM &amp; API keys (Settings tab)<br>"
            "<b>2.</b> Create a session with search queries (Search tab)<br>"
            "<b>3.</b> Run searches &amp; download PDFs<br>"
            "<b>4.</b> Score relevance &amp; filter papers<br>"
            "<b>5.</b> Run the LLM pipeline (Summarize tab)<br>"
            "<b>6.</b> Browse results (Check Summaries tab)<br><br>"
            "Happy researching!"
        ),
        "target": None,
        "tab": None,
    },
]


class TourOverlay(QWidget):
    finished = Signal()

    # Widget paths (tab_attr, widget_attr) to clear during the tour
    SENSITIVE_WIDGETS = [
        ("summarize_tab", "our_work"),  # Research Context text
    ]

    def __init__(self, main_window):
        super().__init__(None)
        self._main_window = main_window
        self._steps = TOUR_STEPS
        self._current = -1
        self._spotlight_rect = QRect()
        self._spotlight_padding = 6
        self._active_dialog = None
        self._spin_timer = None
        self._saved_texts = {}
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setObjectName("tour_overlay")
        self._build_bubble()
        self.hide()

    def _build_bubble(self):
        self._bubble = QFrame(self)
        self._bubble.setObjectName("tour_bubble")
        self._bubble.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)

        bl = QVBoxLayout(self._bubble)
        bl.setContentsMargins(16, 14, 16, 12)
        bl.setSpacing(8)

        self._lbl_step = QLabel("")
        self._lbl_step.setObjectName("tour_step_label")
        bl.addWidget(self._lbl_step)

        self._lbl_title = QLabel("")
        self._lbl_title.setObjectName("tour_title")
        font = self._lbl_title.font()
        font.setBold(True)
        font.setPointSize(13)
        self._lbl_title.setFont(font)
        bl.addWidget(self._lbl_title)

        self._lbl_text = QLabel("")
        self._lbl_text.setObjectName("tour_text")
        self._lbl_text.setWordWrap(True)
        self._lbl_text.setTextFormat(Qt.RichText)
        self._lbl_text.setMinimumWidth(320)
        self._lbl_text.setMaximumWidth(460)
        bl.addWidget(self._lbl_text)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setObjectName("tour_separator")
        bl.addWidget(sep)

        hb = QHBoxLayout()
        hb.setSpacing(8)

        self._btn_skip = QPushButton("Skip Tour")
        self._btn_skip.setObjectName("tour_btn_skip")
        self._btn_skip.setCursor(Qt.PointingHandCursor)
        self._btn_skip.clicked.connect(self._on_skip)
        hb.addWidget(self._btn_skip)

        hb.addStretch()

        self._btn_back = QPushButton("\u2190 Back")
        self._btn_back.setObjectName("tour_btn_back")
        self._btn_back.setCursor(Qt.PointingHandCursor)
        self._btn_back.clicked.connect(self._on_back)
        hb.addWidget(self._btn_back)

        self._btn_next = QPushButton("Next \u2192")
        self._btn_next.setObjectName("tour_btn_next")
        self._btn_next.setCursor(Qt.PointingHandCursor)
        self._btn_next.clicked.connect(self._on_next)
        hb.addWidget(self._btn_next)

        bl.addLayout(hb)

    # ── Dialog management ────────────────────────────────

    def _open_dialog(self, dialog_type):
        if dialog_type == "session_creator":
            from gui.session_creator import SessionCreatorDialog
            dlg = SessionCreatorDialog(self._main_window.cfg, None)
            dlg.setModal(False)
            dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
            dlg.setAttribute(Qt.WA_DeleteOnClose, True)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            dlg.finished.connect(lambda: self._on_dialog_closed(dialog_type))
            self._active_dialog = dlg
            self.raise_()
            QTimer.singleShot(150, self._refresh_target)

    def _close_active_dialog(self):
        if self._active_dialog:
            try:
                self._active_dialog.reject()
            except Exception:
                pass
            self._active_dialog = None

    def _on_dialog_closed(self, _dialog_type):
        self._active_dialog = None

    def _refresh_target(self):
        if 0 <= self._current < len(self._steps):
            step = self._steps[self._current]
            target = self._resolve_target(step)
            if target and target.isVisible():
                self._spotlight_rect = self._widget_rect_on_overlay(target)
            self._position_bubble(target)
            self.update()

    # ── Navigation ──────────────────────────────────────

    def start(self):
        self._current = -1
        self._save_sensitive_widgets()
        self._move_to_screen()
        self.show()
        self.raise_()
        self._go_next()

    def stop(self):
        self._close_active_dialog()
        self._restore_sensitive_widgets()
        self.hide()
        self.finished.emit()

    def _save_sensitive_widgets(self):
        self._saved_texts.clear()
        for tab_attr, widget_attr in self.SENSITIVE_WIDGETS:
            tab = getattr(self._main_window, tab_attr, None)
            if tab:
                widget = getattr(tab, widget_attr, None)
                if widget and hasattr(widget, 'toPlainText'):
                    self._saved_texts[(tab_attr, widget_attr)] = widget.toPlainText()
                    widget.clear()

    def _restore_sensitive_widgets(self):
        for (tab_attr, widget_attr), text in self._saved_texts.items():
            tab = getattr(self._main_window, tab_attr, None)
            if tab:
                widget = getattr(tab, widget_attr, None)
                if widget and hasattr(widget, 'setPlainText'):
                    widget.setPlainText(text)
        self._saved_texts.clear()

    def _on_skip(self):
        self.stop()

    def _on_back(self):
        if self._current > 0:
            self._current -= 1
            self._show_step(self._current)

    def _on_next(self):
        self._go_next()

    def _go_next(self):
        self._current += 1
        if self._current >= len(self._steps):
            self.stop()
            return
        self._show_step(self._current)

    def _move_to_screen(self):
        mw = self._main_window
        geo = mw.frameGeometry()
        self.resize(geo.size())
        self.move(geo.topLeft())

    # ── Target resolution ───────────────────────────────

    def _resolve_target(self, step):
        target_key = step.get("target")
        if not target_key:
            return None

        if self._active_dialog:
            widget = getattr(self._active_dialog, target_key, None)
            if widget and widget.isVisible():
                return widget

        mw = self._main_window
        widget = getattr(mw, target_key, None)
        if widget is None:
            for tab_attr in ("settings_tab", "search_tab", "summarize_tab",
                             "output_tab", "logs_tab", "prompt_tab"):
                tab = getattr(mw, tab_attr, None)
                if tab:
                    widget = getattr(tab, target_key, None)
                    if widget:
                        return widget
        return widget

    def _switch_tab(self, tab_index):
        if tab_index is not None and tab_index >= 0:
            self._main_window.tab_widget.setCurrentIndex(tab_index)

    # ── Step rendering ──────────────────────────────────

    def _show_step(self, idx):
        step = self._steps[idx]
        total = len(self._steps)

        dialog_type = step.get("dialog")
        if dialog_type and not self._active_dialog:
            self._open_dialog(dialog_type)
        elif not dialog_type and self._active_dialog:
            self._close_active_dialog()

        tab_index = step.get("tab")
        if tab_index is not None and tab_index >= 0:
            self._switch_tab(tab_index)

        dialog_tab = step.get("dialog_tab")
        if dialog_tab is not None and self._active_dialog:
            tw = getattr(self._active_dialog, "_tab_widget", None)
            if tw and dialog_tab < tw.count():
                tw.setCurrentIndex(dialog_tab)

        self._lbl_step.setText(f"Step {idx + 1} of {total}")
        self._lbl_title.setText(step["title"])
        self._lbl_text.setText(step["text"])

        self._btn_back.setVisible(idx > 0)
        if idx >= total - 1:
            self._btn_next.setText("Finish")
        else:
            self._btn_next.setText("Next \u2192")

        target = self._resolve_target(step)
        if target and target.isVisible():
            self._spotlight_rect = self._widget_rect_on_overlay(target)
        else:
            self._spotlight_rect = QRect()

        self._position_bubble(target)
        self.update()
        QApplication.processEvents()

    def _widget_rect_on_overlay(self, widget):
        top_left = widget.mapToGlobal(QPoint(0, 0))
        top_left = self.mapFromGlobal(top_left)
        size = widget.size()
        r = QRect(top_left, size)
        r = r.adjusted(
            -self._spotlight_padding, -self._spotlight_padding,
            self._spotlight_padding, self._spotlight_padding,
        )
        return r

    def _position_bubble(self, target):
        self._bubble.adjustSize()
        bw = self._bubble.width()
        bh = self._bubble.height()
        parent_rect = self.rect()
        margin = 20
        candidate_positions = []

        if target and self._spotlight_rect.isValid():
            sr = self._spotlight_rect
            above = QPoint(
                sr.center().x() - bw // 2,
                sr.top() - bh - margin,
            )
            below = QPoint(
                sr.center().x() - bw // 2,
                sr.bottom() + margin,
            )
            right_of = QPoint(
                sr.right() + margin,
                sr.center().y() - bh // 2,
            )
            left_of = QPoint(
                sr.left() - bw - margin,
                sr.center().y() - bh // 2,
            )
            candidate_positions = [below, above, right_of, left_of]
        else:
            cx = parent_rect.center().x() - bw // 2
            cy = parent_rect.center().y() - bh // 2
            candidate_positions = [QPoint(cx, cy)]

        chosen = candidate_positions[0]
        for pos in candidate_positions:
            clamped = QPoint(
                max(margin, min(pos.x(), parent_rect.width() - bw - margin)),
                max(margin, min(pos.y(), parent_rect.height() - bh - margin)),
            )
            if self._rect_fits(clamped, bw, bh, parent_rect, margin):
                chosen = clamped
                break
        else:
            chosen = QPoint(
                max(margin, min(chosen.x(), parent_rect.width() - bw - margin)),
                max(margin, min(chosen.y(), parent_rect.height() - bh - margin)),
            )

        self._bubble.move(chosen)

    def _rect_fits(self, pos, w, h, parent_rect, margin):
        return (pos.x() >= margin and
                pos.y() >= margin and
                pos.x() + w <= parent_rect.width() - margin and
                pos.y() + h <= parent_rect.height() - margin)

    # ── Events ──────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._bubble.resize(self._bubble.sizeHint())
        if 0 <= self._current < len(self._steps):
            step = self._steps[self._current]
            target = self._resolve_target(step)
            if target and target.isVisible():
                self._spotlight_rect = self._widget_rect_on_overlay(target)
            self._position_bubble(target)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if self._active_dialog:
            if self._spotlight_rect.isValid():
                pen = QPen(QColor("#1a73e8"), 2.5)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(self._spotlight_rect, 8, 8)
            painter.end()
            return

        overlay_color = QColor(0, 0, 0, 160)

        if self._spotlight_rect.isValid():
            path = QPainterPath()
            path.addRect(self.rect())
            rounded = QPainterPath()
            rounded.addRoundedRect(self._spotlight_rect, 8, 8)
            path -= rounded
            painter.fillPath(path, overlay_color)

            pen = QPen(QColor("#1a73e8"), 2.5)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(self._spotlight_rect, 8, 8)
        else:
            painter.fillRect(self.rect(), overlay_color)

        painter.end()

    def mousePressEvent(self, event):
        if self._spotlight_rect.isValid():
            mapped = self._spotlight_rect
            if not mapped.contains(event.pos()):
                return
        super().mousePressEvent(event)

    def _apply_tour_style(self):
        from gui.style import STYLE_QSS
        self.setStyleSheet(STYLE_QSS)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_tour_style()
        self._move_to_screen()

    def moveEvent(self, event):
        super().moveEvent(event)
        if 0 <= self._current < len(self._steps):
            step = self._steps[self._current]
            target = self._resolve_target(step)
            if target and target.isVisible():
                self._spotlight_rect = self._widget_rect_on_overlay(target)
            self._position_bubble(target)
            self.update()
