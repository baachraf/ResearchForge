"""
LLM provider abstraction — unified interface for local and remote providers.

Providers:
  - LM Studio (http://127.0.0.1:1234/v1) — OpenAI-compatible
  - Ollama    (http://127.0.0.1:11434/v1) — OpenAI-compatible
  - DeepSeek  (https://api.deepseek.com/v1) — OpenAI-compatible
  - Gemini    (https://generativelanguage.googleapis.com/v1beta) — custom HTTP wrapper

Key functions:
  create_llm_client(endpoint, api_key, provider_name, timeout)
      Returns an object with .chat.completions.create() for any provider.
      Gemini returns a _GeminiClientWrapper; others return openai.OpenAI.

  fetch_models_for_provider(provider_name, endpoint, api_key)
      Returns list of model ID strings for the selected provider.

  get_provider_api_key(provider_name, cfg)
      Extracts the API key from ConfigManager for the given provider.

  check_provider_connection(provider_name, endpoint, api_key, model_id)
      Pings the provider to verify connectivity and model availability.

  ensure_llm_available(cfg, parent)
      Guard: checks LLM settings exist and connection works. Shows QMessageBox on failure.

Gemini wrapper classes (_GeminiClientWrapper, _GeminiChat, _GeminiCompletions,
_GeminiResult, _GeminiChoice, _GeminiResponse) adapt Gemini's REST API to the
openai.OpenAI interface expected by the rest of the codebase.
"""
import re
import requests
from PySide6.QtWidgets import QMessageBox


PROVIDER_NAMES = ["LM Studio", "Ollama", "DeepSeek", "Gemini"]

PROVIDER_URLS = {
    0: "http://127.0.0.1:1234/v1",
    1: "http://127.0.0.1:11434/v1",
    2: "https://api.deepseek.com/v1",
    3: "https://generativelanguage.googleapis.com/v1beta",
}

DEEPSEEK_BASE = "https://api.deepseek.com"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def get_provider_api_key(provider_name, cfg):
    if provider_name == "DeepSeek":
        return cfg.get("deepseek_api_key", "")
    if provider_name == "Gemini":
        return cfg.get("gemini_api_key", "")
    return "not-needed"


def provider_needs_api_key(provider_name):
    return provider_name in ("DeepSeek", "Gemini")


class _GeminiResponse:
    def __init__(self, data):
        self._data = data
        candidate = data.get("candidates", [{}])[0]
        content_data = candidate.get("content", {})
        parts = content_data.get("parts", [])
        text = ""
        for part in parts:
            if "text" in part:
                text += part["text"]
        reasoning = ""
        self.content = text
        self.role = "assistant"
        self.reasoning_content = reasoning


class _GeminiChoice:
    def __init__(self, data):
        self.message = _GeminiResponse(data)
        self.finish_reason = data.get("candidates", [{}])[0].get("finishReason", "stop")


class _GeminiResult:
    def __init__(self, data):
        self.choices = [_GeminiChoice(data)]
        self._data = data


class _GeminiCompletions:
    def __init__(self, api_key, base_url=None):
        self._api_key = api_key
        self._base_url = base_url or GEMINI_BASE

    def create(self, model, messages, temperature=0.0, timeout=180, max_tokens=None, **kwargs):
        system_instruction = None
        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_instruction = {"parts": [{"text": content}]}
            else:
                gemini_role = "user" if role == "user" else "model"
                contents.append({"role": gemini_role, "parts": [{"text": content}]})

        payload = {"contents": contents, "generationConfig": {"temperature": temperature}}
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens

        url = f"{self._base_url}/models/{model}:generateContent?key={self._api_key}"

        resp = requests.post(url, json=payload, timeout=timeout)
        if resp.status_code != 200:
            raise Exception(f"Gemini API error {resp.status_code}: {resp.text[:500]}")
        return _GeminiResult(resp.json())


class _GeminiChat:
    def __init__(self, api_key, base_url=None):
        self.completions = _GeminiCompletions(api_key, base_url)


class _GeminiClientWrapper:
    """Mimics openai.OpenAI client interface for Gemini."""

    def __init__(self, api_key, base_url=None, timeout=None):
        self.chat = _GeminiChat(api_key, base_url)
        self._client = self

    def close(self):
        pass


def create_llm_client(endpoint, api_key="not-needed", provider_name="", timeout=120.0):
    """
    Create an LLM client appropriate for the provider.
    Returns an object with .chat.completions.create() interface.
    """
    if provider_name == "Gemini":
        return _GeminiClientWrapper(api_key, base_url=endpoint, timeout=timeout)

    from openai import OpenAI
    return OpenAI(base_url=endpoint, api_key=api_key, timeout=timeout)


def fetch_models_for_provider(provider_name, endpoint="", api_key=""):
    """
    Fetch available models for a provider.
    Returns list of model ID strings.
    """
    if provider_name == "LM Studio":
        return _fetch_openai_compatible(endpoint, "LM Studio")
    elif provider_name == "Ollama":
        return _fetch_ollama(endpoint)
    elif provider_name == "DeepSeek":
        return _fetch_deepseek(api_key)
    elif provider_name == "Gemini":
        return _fetch_gemini(api_key)
    return []


def _fetch_openai_compatible(endpoint, label):
    base = endpoint.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        r = requests.get(f"{base}/v1/models", timeout=5)
        r.raise_for_status()
        models = [m["id"] for m in r.json().get("data", [])]
        if models:
            return models
    except Exception:
        pass
    return []


def _fetch_ollama(endpoint):
    base = endpoint.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        r = requests.get(f"{base}/api/ps", timeout=5)
        r.raise_for_status()
        data = r.json()
        models = [m.get("model", m.get("name", "")) for m in data.get("models", [])]
        models = [m for m in models if m]
        if models:
            return models
    except Exception:
        pass
    try:
        r = requests.get(f"{base}/v1/models", timeout=5)
        r.raise_for_status()
        models = [m["id"] for m in r.json().get("data", [])]
        if models:
            return models
    except Exception:
        pass
    return []


def _fetch_deepseek(api_key):
    if not api_key:
        return []
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        r = requests.get(f"{DEEPSEEK_BASE}/models", timeout=10, headers=headers)
        r.raise_for_status()
        data = r.json()
        models = [m.get("id", "") for m in data.get("data", [])]
        models = [m for m in models if m]
        if not models:
            models = ["deepseek-chat", "deepseek-reasoner"]
        return models
    except Exception:
        return []


def _fetch_gemini(api_key):
    if not api_key:
        return []
    try:
        url = f"{GEMINI_BASE}/models?key={api_key}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        models = []
        for m in data.get("models", []):
            name = m.get("name", "").replace("models/", "")
            supported = m.get("supportedGenerationMethods", [])
            if "generateContent" in supported:
                models.append(name)
        if not models:
            models = ["gemini-2.0-flash", "gemini-2.5-pro-preview-05-06", "gemini-2.5-flash-preview-05-20"]
        return models
    except Exception:
        return []


def check_provider_connection(provider_name, endpoint="", api_key="", model=""):
    """
    Quick check that the provider is reachable and the model is available.
    Returns (ok: bool, message: str).
    """
    if provider_name == "Gemini":
        if not api_key:
            return False, "Gemini API key not set."
        models = _fetch_gemini(api_key)
        if not models:
            return False, "Cannot reach Gemini API. Check your API key."
        if model and model not in models:
            return False, f"Model '{model}' not found. Available: {', '.join(models[:10])}"
        return True, f"Gemini OK — {len(models)} model(s)"

    if provider_name == "DeepSeek":
        if not api_key:
            return False, "DeepSeek API key not set."
        models = _fetch_deepseek(api_key)
        if not models:
            return False, "Cannot reach DeepSeek API. Check your API key."
        if model and model not in models:
            return False, f"Model '{model}' not found. Available: {', '.join(models[:10])}"
        return True, f"DeepSeek OK — {len(models)} model(s)"

    base = endpoint.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    try:
        r = requests.get(f"{base}/v1/models", timeout=5)
        r.raise_for_status()
        model_ids = [m.get("id", "") for m in r.json().get("data", [])]
        if model and model not in model_ids:
            return False, f"Model '{model}' not found. Available: {', '.join(model_ids[:10])}"
        return True, f"Connected OK — {len(model_ids)} model(s)"
    except Exception as e:
        return False, f"Cannot connect: {e}"


def provider_index_from_endpoint(endpoint):
    """Guess provider index from endpoint URL (for backward compat)."""
    if not endpoint:
        return 0
    if "11434" in endpoint:
        return 1
    if "deepseek" in endpoint:
        return 2
    if "googleapis" in endpoint or "generativelanguage" in endpoint:
        return 3
    return 0


def provider_name_from_config(cfg):
    """Get provider name from config, guessing from endpoint if needed."""
    name = cfg.get("llm_provider", "")
    if name and name in PROVIDER_NAMES:
        return name
    endpoint = cfg.get("llm_endpoint", "")
    if "deepseek" in endpoint:
        return "DeepSeek"
    if "googleapis" in endpoint or "generativelanguage" in endpoint:
        return "Gemini"
    if "11434" in endpoint:
        return "Ollama"
    return "LM Studio"


_MISSING_MSG = (
    "No LLM model is configured.\n\n"
    "Set the provider, endpoint and model in the Settings tab."
)


def ensure_llm_available(cfg, parent=None, fast_check=True) -> bool:
    """Check that an LLM model is configured AND reachable.
    Returns True if ready, False if blocked (popup already shown).
    """
    model = cfg.get("llm_model", "")
    endpoint = cfg.get("llm_endpoint", "")
    provider_name = cfg.get("llm_provider", "LM Studio")
    api_key = get_provider_api_key(provider_name, cfg)

    if not model or not endpoint:
        QMessageBox.warning(parent, "No LLM Model Available", _MISSING_MSG)
        return False

    try:
        ok, msg = check_provider_connection(
            provider_name, endpoint, api_key, model
        )
    except Exception as e:
        ok, msg = False, str(e)

    if not ok:
        QMessageBox.warning(
            parent, "LLM Not Reachable",
            f"Cannot connect to {provider_name}.\n\n{msg}\n\n"
            "Make sure the server is running and the model is loaded."
        )
        return False

    return True
