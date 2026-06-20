import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QPushButton, QComboBox, QFileDialog, QLabel,
    QMessageBox, QCheckBox, QSpinBox, QDoubleSpinBox, QSizePolicy,
    QDialog, QDialogButtonBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from gui.llm_provider import (
    PROVIDER_NAMES, PROVIDER_URLS, fetch_models_for_provider,
    get_provider_api_key, provider_index_from_endpoint,
)
from gui.theme_manager import ThemeManager
from gui.app_info import icon
from gui.style import refresh_qss


class SettingsTab(QWidget):
    def __init__(self, config_manager, log_signal, parent=None):
        super().__init__(parent)
        self.cfg = config_manager
        self.log = log_signal
        self._setup_ui()
        self._load_from_config()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(4, 4, 4, 4)
        hb1 = QHBoxLayout()
        hb1.setSpacing(4)
        hb1.setContentsMargins(0, 0, 0, 0)

        self.grp_llm = QGroupBox("LLM")
        lg = QVBoxLayout(self.grp_llm); lg.setSpacing(4); lg.setContentsMargins(6,4,6,4)
        hp = QHBoxLayout(); hp.setSpacing(4)
        lb_p = QLabel("Provider:"); lb_p.setObjectName("sc_bold_label"); lb_p.setFixedWidth(70); hp.addWidget(lb_p)
        self.provider_combo = QComboBox(); self.provider_combo.setEditable(False)
        self.provider_combo.addItems(PROVIDER_NAMES)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        hp.addWidget(self.provider_combo, 1)
        lg.addLayout(hp)
        hm = QHBoxLayout(); hm.setSpacing(4)
        lb_m = QLabel("Model:"); lb_m.setObjectName("sc_bold_label"); lb_m.setFixedWidth(70); hm.addWidget(lb_m)
        self.model_combo = QComboBox(); self.model_combo.setEditable(False)
        hm.addWidget(self.model_combo, 1)
        self.btn_refresh_models = QPushButton()
        self.btn_refresh_models.setObjectName("btn_refresh_models")
        self.btn_refresh_models.setIcon(QIcon(icon("refresh.png")))
        btn_h = self.model_combo.sizeHint().height()
        self.btn_refresh_models.setIconSize(QSize(btn_h - 4, btn_h - 4))
        self.btn_refresh_models.setFixedSize(btn_h, btn_h)
        self.btn_refresh_models.setFlat(True)
        self.btn_refresh_models.setCursor(Qt.PointingHandCursor)
        self.btn_refresh_models.setToolTip("Refresh available models from server")
        self.btn_refresh_models.clicked.connect(self._on_refresh_clicked)
        hm.addWidget(self.btn_refresh_models, 0, Qt.AlignVCenter)
        lg.addLayout(hm)
        self.lbl_status = QLabel(""); self.lbl_status.setWordWrap(True)
        self.lbl_status.setObjectName("lbl_status")
        lg.addWidget(self.lbl_status)
        # LLM gains a little width; API keys now live behind the "Set Keys" dialog.
        hb1.addWidget(self.grp_llm, 4)

        self.grp_dirs = QGroupBox("Directories")
        dd = QVBoxLayout(self.grp_dirs); dd.setSpacing(4); dd.setContentsMargins(6,4,6,4)

        _browse_icon = QIcon(icon("browse.png"))

        ho = QHBoxLayout(); ho.setSpacing(4)
        lb_o = QLabel("Output:"); lb_o.setObjectName("sc_bold_label"); lb_o.setFixedWidth(70); ho.addWidget(lb_o)
        self.output_root = QLineEdit(); self.output_root.setPlaceholderText("PDF root")
        ho.addWidget(self.output_root, 1)
        bo = QPushButton(); bo.setObjectName("btn_browse"); bo.setIcon(_browse_icon); bo.setIconSize(bo.sizeHint())
        bo.setFixedSize(24, 24); bo.setToolTip("Browse output directory")
        bo.clicked.connect(lambda: self._browse_dir(self.output_root, "Output"))
        ho.addWidget(bo); dd.addLayout(ho)
        hs = QHBoxLayout(); hs.setSpacing(4)
        lb_s = QLabel("Summaries:"); lb_s.setObjectName("sc_bold_label"); lb_s.setFixedWidth(70); hs.addWidget(lb_s)
        self.summary_output_dir = QLineEdit()
        self.summary_output_dir.setPlaceholderText("Summary"); hs.addWidget(self.summary_output_dir, 1)
        bs = QPushButton(); bs.setObjectName("btn_browse"); bs.setIcon(_browse_icon); bs.setIconSize(bs.sizeHint())
        bs.setFixedSize(24, 24); bs.setToolTip("Browse summary directory")
        bs.clicked.connect(lambda: self._browse_dir(self.summary_output_dir, "Summaries"))
        hs.addWidget(bs); dd.addLayout(hs)
        ha = QHBoxLayout(); ha.setSpacing(4)
        lb_a = QLabel("Audit:"); lb_a.setObjectName("sc_bold_label"); lb_a.setFixedWidth(70); ha.addWidget(lb_a)
        self.audit_output_dir = QLineEdit()
        self.audit_output_dir.setPlaceholderText("Audit results"); ha.addWidget(self.audit_output_dir, 1)
        ba = QPushButton(); ba.setObjectName("btn_browse"); ba.setIcon(_browse_icon); ba.setIconSize(ba.sizeHint())
        ba.setFixedSize(24, 24); ba.setToolTip("Browse audit results directory")
        ba.clicked.connect(lambda: self._browse_dir(self.audit_output_dir, "Audit Results"))
        ha.addWidget(ba); dd.addLayout(ha)
        # Directories takes most of the width freed by the removed API Keys group.
        hb1.addWidget(self.grp_dirs, 8)

        vb = QVBoxLayout(); vb.setSpacing(4)
        for lbl, slt, obj in [("Save",self._on_save,"btn_save"),("Load",self._on_load_file,"btn_session_load"),
                                ("Export",self._on_export_file,"btn_session_edit"),("Reset",self._on_reset,"btn_remove")]:
            b = QPushButton(lbl)
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            b.setObjectName(obj)
            b.setMinimumWidth(96)  # wider — there is spare room now
            b.clicked.connect(slt); vb.addWidget(b)
        hb1.addLayout(vb)

        layout.addLayout(hb1)

        self.grp_def = QGroupBox("Query Defaults")
        gs = QVBoxLayout(self.grp_def); gs.setSpacing(4); gs.setContentsMargins(8,4,8,2)

        qp = QHBoxLayout(); qp.setSpacing(6)
        qp.addWidget(QLabel("After date:"))
        self.after_date = QLineEdit(); self.after_date.setPlaceholderText("YYYY-MM-DD")
        self.after_date.setFixedWidth(95); qp.addWidget(self.after_date)
        qp.addStretch()
        qp.addWidget(QLabel("Max results per query:"))
        self.max_results = QSpinBox(); self.max_results.setRange(1,200); self.max_results.setValue(100)
        self.max_results.setFixedWidth(60); qp.addWidget(self.max_results)
        qp.addStretch()
        qp.addWidget(QLabel("Max PDF size (MB):"))
        self.max_size_mb = QDoubleSpinBox(); self.max_size_mb.setRange(1.0,500.0)
        self.max_size_mb.setValue(100.0); self.max_size_mb.setFixedWidth(70); qp.addWidget(self.max_size_mb)
        qp.addStretch()
        qp.addWidget(QLabel("Min keywords in title:"))
        self.relevance_threshold = QSpinBox(); self.relevance_threshold.setRange(1,20)
        self.relevance_threshold.setValue(2); self.relevance_threshold.setFixedWidth(50); qp.addWidget(self.relevance_threshold)
        qp.addStretch()
        self.force_plus = QCheckBox("Force AND")
        self.force_plus.setToolTip("Force AND between search terms in web search")
        qp.addWidget(self.force_plus)
        gs.addLayout(qp)

        _PENDING = "Backend wiring pending — will be searched once implemented"

        # Row 1 — implemented + new keyless providers
        qs = QHBoxLayout(); qs.setSpacing(6)
        lbl_src = QLabel("Sources:"); lbl_src.setFixedWidth(55); qs.addWidget(lbl_src)
        self.src_arxiv = QCheckBox("arXiv"); self.src_arxiv.setChecked(True); qs.addWidget(self.src_arxiv)
        qs.addStretch()
        self.src_openalex = QCheckBox("OpenAlex"); self.src_openalex.setToolTip(_PENDING); qs.addWidget(self.src_openalex)
        qs.addStretch()
        self.src_crossref = QCheckBox("Crossref"); self.src_crossref.setToolTip(_PENDING); qs.addWidget(self.src_crossref)
        qs.addStretch()
        self.src_europepmc = QCheckBox("Europe PMC"); self.src_europepmc.setToolTip(_PENDING); qs.addWidget(self.src_europepmc)
        qs.addStretch()
        self.src_doaj = QCheckBox("DOAJ"); self.src_doaj.setToolTip(_PENDING); qs.addWidget(self.src_doaj)
        qs.addStretch()
        gs.addLayout(qs)

        # Row 2 — remaining implemented + CORE (keyed, backend pending)
        qs2 = QHBoxLayout(); qs2.setSpacing(6)
        lbl_pad = QLabel(""); lbl_pad.setFixedWidth(55); qs2.addWidget(lbl_pad)
        self.src_pubmed = QCheckBox("PubMed"); self.src_pubmed.setChecked(True); qs2.addWidget(self.src_pubmed)
        qs2.addStretch()
        self.src_s2 = QCheckBox("Semantic Scholar"); self.src_s2.setChecked(True); qs2.addWidget(self.src_s2)
        qs2.addStretch()
        self.src_core = QCheckBox("CORE"); self.src_core.setToolTip(_PENDING); qs2.addWidget(self.src_core)
        qs2.addStretch()
        self.src_brave = QCheckBox("Brave"); self.src_brave.setChecked(True); qs2.addWidget(self.src_brave)
        qs2.addStretch()
        self.src_duckduckgo = QCheckBox("DuckDuckGo"); self.src_duckduckgo.setChecked(True); qs2.addWidget(self.src_duckduckgo)
        qs2.addStretch()
        gs.addLayout(qs2)

        # Query Defaults group + tall "Set Keys" button (matches group height)
        hb_def = QHBoxLayout(); hb_def.setSpacing(4)
        hb_def.addWidget(self.grp_def, 1)
        self.btn_set_keys = QPushButton("\U0001F511\nSet Keys")
        self.btn_set_keys.setObjectName("btn_discover")  # purple — distinct from green Save
        self.btn_set_keys.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.btn_set_keys.setFixedWidth(88)
        self.btn_set_keys.setCursor(Qt.PointingHandCursor)
        self.btn_set_keys.setToolTip("Enter all LLM and search-provider API keys")
        self.btn_set_keys.clicked.connect(self._open_keys_dialog)
        hb_def.addWidget(self.btn_set_keys)
        layout.addLayout(hb_def)

    # ── Provider / Model ──

    def _current_provider_name(self) -> str:
        return self.provider_combo.currentText()

    def _current_endpoint(self) -> str:
        idx = self.provider_combo.currentIndex()
        return PROVIDER_URLS.get(idx, "")

    def _on_provider_changed(self, idx):
        provider = self._current_provider_name()
        endpoint = self._current_endpoint()
        self.cfg.set("llm_provider", provider)
        self.cfg.set("llm_endpoint", endpoint)
        if provider:
            self._fetch_models(provider, endpoint)

    def _on_refresh_clicked(self):
        provider = self._current_provider_name()
        endpoint = self._current_endpoint()
        self._fetch_models(provider, endpoint)

    def _fetch_models(self, provider, endpoint):
        self.lbl_status.setText("Loading...")

        try:
            api_key = get_provider_api_key(provider, self.cfg)
            models = fetch_models_for_provider(provider, endpoint, api_key)

            if models:
                saved = self.cfg.get("llm_model", "")
                self.model_combo.blockSignals(True)
                self.model_combo.clear()
                for m in models:
                    self.model_combo.addItem(m, m)
                if saved:
                    idx = self.model_combo.findData(saved)
                    if idx >= 0:
                        self.model_combo.setCurrentIndex(idx)
                elif len(models) == 1:
                    self.model_combo.setCurrentIndex(0)
                self.model_combo.blockSignals(False)

                chosen = self.model_combo.currentData() or self.model_combo.currentText()
                if chosen:
                    self.cfg.set("llm_model", chosen.strip())
                self.cfg.set("llm_endpoint", endpoint)

                self.lbl_status.setText(f"{provider} \u2014 {len(models)} model(s)")
                self.log.emit(f"{provider}: {len(models)} model(s)")
            else:
                self.model_combo.clear()
                self.lbl_status.setText("No models found")

        except Exception as e:
            self.lbl_status.setText(f"Error: {e}")
            QMessageBox.warning(self, "LLM Error", str(e))

    def _update_source_enables(self):
        has_brave = bool(self.cfg.get("brave_api_key", "").strip())
        self.src_brave.setEnabled(has_brave)
        if not has_brave:
            self.src_brave.setChecked(False)

    # ── API Keys dialog ──

    def _open_keys_dialog(self):
        dlg = KeysDialog(self.cfg, self)
        if dlg.exec():
            self._update_source_enables()
            self.log.emit("API keys updated.")

    # ── Config I/O ──

    def _load_from_config(self):
        endpoint = self.cfg.get("llm_endpoint", "")
        provider = self.cfg.get("llm_provider", "")

        self.provider_combo.blockSignals(True)
        idx = 0
        for i, name in enumerate(PROVIDER_NAMES):
            if name == provider:
                idx = i
                break
        else:
            idx = provider_index_from_endpoint(endpoint)
        self.provider_combo.setCurrentIndex(idx)
        self.provider_combo.blockSignals(False)

        model = self.cfg.get("llm_model", "")
        if model:
            idx = self.model_combo.findData(model)
            if idx < 0:
                idx = self.model_combo.findText(model)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
            elif self.model_combo.count() == 0:
                self.model_combo.addItem(model)
                self.model_combo.setCurrentIndex(0)

        active_endpoint = PROVIDER_URLS.get(idx, endpoint)
        active_provider = self._current_provider_name()
        if active_endpoint:
            self._fetch_models(active_provider, active_endpoint)

        self.output_root.setText(self.cfg.get("output_root", ""))
        self.output_root.end(False)
        self.summary_output_dir.setText(self.cfg.get("summary_output_dir", ""))
        self.summary_output_dir.end(False)
        self.audit_output_dir.setText(self.cfg.get("audit_output_dir", ""))
        self.audit_output_dir.end(False)
        self.output_root.textChanged.connect(lambda t: self.cfg.set("output_root", t.strip()))
        self.summary_output_dir.textChanged.connect(lambda t: self.cfg.set("summary_output_dir", t.strip()))
        self.audit_output_dir.textChanged.connect(lambda t: self.cfg.set("audit_output_dir", t.strip()))
        self.after_date.setText(self.cfg.get("default_after_date", "") or "2020-01-01")
        self.max_results.setValue(self.cfg.get("default_max_results", 100))
        self.max_size_mb.setValue(self.cfg.get("default_max_size_mb", 100.0))
        self.relevance_threshold.setValue(self.cfg.get("default_relevance_threshold", 2))
        self.force_plus.setChecked(self.cfg.get("default_force_plus", False))
        sources = self.cfg.get("default_sources", ["arxiv", "semantic_scholar", "duckduckgo", "brave", "pubmed"])
        self.src_arxiv.setChecked("arxiv" in sources)
        self.src_s2.setChecked("semantic_scholar" in sources)
        self.src_duckduckgo.setChecked("duckduckgo" in sources or "web" in sources)
        self.src_brave.setChecked("brave" in sources)
        self.src_pubmed.setChecked("pubmed" in sources)
        self.src_openalex.setChecked("openalex" in sources)
        self.src_crossref.setChecked("crossref" in sources)
        self.src_europepmc.setChecked("europe_pmc" in sources)
        self.src_doaj.setChecked("doaj" in sources)
        self.src_core.setChecked("core" in sources)
        self._update_source_enables()

        def _save_sources():
            srcs = []
            if self.src_arxiv.isChecked(): srcs.append("arxiv")
            if self.src_s2.isChecked(): srcs.append("semantic_scholar")
            if self.src_duckduckgo.isChecked(): srcs.append("web")
            if self.src_brave.isChecked(): srcs.append("brave")
            if self.src_pubmed.isChecked(): srcs.append("pubmed")
            if self.src_openalex.isChecked(): srcs.append("openalex")
            if self.src_crossref.isChecked(): srcs.append("crossref")
            if self.src_europepmc.isChecked(): srcs.append("europe_pmc")
            if self.src_doaj.isChecked(): srcs.append("doaj")
            if self.src_core.isChecked(): srcs.append("core")
            self.cfg.set("default_sources", srcs)

        for cb in [self.src_arxiv, self.src_s2, self.src_duckduckgo, self.src_brave,
                   self.src_pubmed, self.src_openalex, self.src_crossref,
                   self.src_europepmc, self.src_doaj, self.src_core]:
            cb.toggled.connect(_save_sources)

    def _save_to_config(self):
        endpoint = self._current_endpoint()
        provider = self._current_provider_name()
        if endpoint:
            self.cfg.set("llm_endpoint", endpoint)
        self.cfg.set("llm_provider", provider)
        model = self.model_combo.currentData() or self.model_combo.currentText()
        if model and model.strip():
            self.cfg.set("llm_model", model.strip())
        self.cfg.set("output_root", self.output_root.text().strip())
        self.cfg.set("summary_output_dir", self.summary_output_dir.text().strip())
        self.cfg.set("audit_output_dir", self.audit_output_dir.text().strip())
        self.cfg.set("default_after_date", self.after_date.text().strip())
        self.cfg.set("default_max_results", self.max_results.value())
        self.cfg.set("default_max_size_mb", self.max_size_mb.value())
        self.cfg.set("default_relevance_threshold", self.relevance_threshold.value())
        self.cfg.set("default_force_plus", self.force_plus.isChecked())
        sources = []
        if self.src_arxiv.isChecked():
            sources.append("arxiv")
        if self.src_s2.isChecked():
            sources.append("semantic_scholar")
        if self.src_duckduckgo.isChecked():
            sources.append("web")
        if self.src_brave.isChecked():
            sources.append("brave")
        if self.src_pubmed.isChecked():
            sources.append("pubmed")
        if self.src_openalex.isChecked():
            sources.append("openalex")
        if self.src_crossref.isChecked():
            sources.append("crossref")
        if self.src_europepmc.isChecked():
            sources.append("europe_pmc")
        if self.src_doaj.isChecked():
            sources.append("doaj")
        if self.src_core.isChecked():
            sources.append("core")
        self.cfg.set("default_sources", sources)

    def _on_save(self):
        self._save_to_config()
        self.log.emit("Settings saved.")

    def _on_reset(self):
        reply = QMessageBox.question(self, "Reset", "Reset all settings?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.provider_combo.blockSignals(True)
            self.provider_combo.setCurrentIndex(0)
            self.provider_combo.blockSignals(False)
            self.model_combo.clear()
            for k in ("deepseek_api_key", "gemini_api_key", "brave_api_key",
                      "semantic_scholar_api_key", "core_api_key",
                      "pubmed_email", "contact_email"):
                self.cfg.set(k, "")
            self.output_root.clear()
            self.summary_output_dir.clear()
            self.audit_output_dir.clear()
            self.max_results.setValue(100)
            self.max_size_mb.setValue(100.0)
            self.relevance_threshold.setValue(2)
            self.force_plus.setChecked(False)
            self.after_date.setText("2020-01-01")
            self.src_arxiv.setChecked(True)
            self.src_s2.setChecked(True)
            self.src_duckduckgo.setChecked(True)
            self.src_brave.setChecked(True)
            self.src_pubmed.setChecked(True)
            self.lbl_status.setText("")
            self._update_source_enables()  # Brave key now empty → disable its checkbox
            self._save_to_config()

    def _on_load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Settings", "", "JSON Files (*.json)")
        if path:
            self.cfg.import_from_file(path)
            self._load_from_config()
            self.log.emit(f"Settings loaded from: {path}")

    def _on_export_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Settings", "settings.json", "JSON Files (*.json)")
        if path:
            self._save_to_config()
            self.cfg.export_to_file(path)
            self.log.emit(f"Settings exported to: {path}")

    def _browse_dir(self, line_edit, title):
        path = QFileDialog.getExistingDirectory(self, title, line_edit.text())
        if path:
            line_edit.setText(path)

    def get_credentials(self) -> dict:
        return {
            "brave_search": {
                "api_key": self.cfg.get("brave_api_key", "").strip(),
                "enabled": self.src_brave.isChecked() and self.src_brave.isEnabled(),
            },
            "semantic_scholar": {
                "api_key": self.cfg.get("semantic_scholar_api_key", "").strip(),
                "enabled": self.src_s2.isChecked() and self.src_s2.isEnabled(),
            },
            "arxiv": {"enabled": True},
        }


class KeysDialog(QDialog):
    """Single place to enter every API key — LLM and search providers.

    Reads from / writes to the shared ConfigManager so the rest of the app keeps
    using the same cfg keys it always has. Keyless providers (OpenAlex, Crossref,
    Europe PMC, DOAJ) need no entry here.
    """

    # (cfg_key, label, is_password, hint)
    _LLM_FIELDS = [
        ("deepseek_api_key", "DeepSeek:", True, ""),
        ("gemini_api_key", "Gemini:", True, ""),
    ]
    _SEARCH_FIELDS = [
        ("brave_api_key", "Brave:", True, "required"),
        ("core_api_key", "CORE:", True, "required"),
        ("semantic_scholar_api_key", "Semantic Scholar:", True, "optional"),
        ("pubmed_email", "PubMed email:", False, "optional"),
        ("contact_email", "Contact email:", False, "optional"),
    ]

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._edits = {}
        self.setWindowTitle("Set API Keys")
        self.setModal(True)
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        grp_llm = QGroupBox("LLM")
        f_llm = QFormLayout(grp_llm)
        for key, label, pw, hint in self._LLM_FIELDS:
            f_llm.addRow(label, self._make_field(key, pw, hint))
        layout.addWidget(grp_llm)

        grp_search = QGroupBox("Search providers")
        f_search = QFormLayout(grp_search)
        for key, label, pw, hint in self._SEARCH_FIELDS:
            f_search.addRow(label, self._make_field(key, pw, hint))
        layout.addWidget(grp_search)

        info = QLabel(
            "OpenAlex, Crossref, Europe PMC and DOAJ need no key. "
            "Contact email is used for their “polite pool” rate limits."
        )
        info.setWordWrap(True)
        info.setObjectName("sc_hint")
        layout.addWidget(info)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _make_field(self, key, password, hint):
        le = QLineEdit(self.cfg.get(key, ""))
        if password:
            le.setEchoMode(QLineEdit.Password)
        self._edits[key] = le
        if not hint:
            return le
        wrap = QWidget()
        row = QHBoxLayout(wrap); row.setSpacing(6); row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(le, 1)
        lh = QLabel(hint); lh.setObjectName("sc_hint")
        row.addWidget(lh, 0)
        return wrap

    def _on_save(self):
        for key, le in self._edits.items():
            self.cfg.set(key, le.text().strip())
        self.accept()
