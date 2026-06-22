import requests
from researchforge_api import _config

PROVIDER_NAMES = ["LM Studio", "Ollama", "DeepSeek", "Gemini"]

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def get_provider_api_key(provider_name: str) -> str:
    if provider_name == "DeepSeek":
        return _config.get("deepseek_api_key", "")
    if provider_name == "Gemini":
        return _config.get("gemini_api_key", "")
    return "not-needed"


def create_llm_client(endpoint="", api_key="not-needed", provider_name="", timeout=120.0):
    if provider_name == "Gemini":
        return _GeminiClientWrapper(api_key, base_url=endpoint, timeout=timeout)
    from openai import OpenAI
    return OpenAI(base_url=endpoint or _config.get("llm_endpoint", "http://127.0.0.1:1234/v1"),
                  api_key=api_key, timeout=timeout)


def create_client_from_config(timeout=120.0):
    endpoint = _config.get("llm_endpoint", "http://127.0.0.1:1234/v1")
    provider = _config.get("llm_provider", "LM Studio")
    api_key = get_provider_api_key(provider)
    return create_llm_client(endpoint, api_key, provider, timeout)


def check_connection(provider_name="", endpoint="", api_key="", model="") -> tuple[bool, str]:
    provider = provider_name or _config.get("llm_provider", "LM Studio")
    endpoint = endpoint or _config.get("llm_endpoint", "")
    model = model or _config.get("llm_model", "")
    api_key = api_key or get_provider_api_key(provider)

    if provider == "Gemini":
        if not api_key:
            return False, "Gemini API key not set."
        models = _fetch_gemini_models(api_key)
        if not models:
            return False, "Cannot reach Gemini API. Check your API key."
        if model and model not in models:
            return False, f"Model '{model}' not found. Available: {', '.join(models[:10])}"
        return True, f"Gemini OK — {len(models)} model(s)"

    if provider == "DeepSeek":
        if not api_key:
            return False, "DeepSeek API key not set."
        models = _fetch_deepseek_models(api_key)
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


def fetch_models(provider_name="", endpoint="", api_key="") -> list[str]:
    provider = provider_name or _config.get("llm_provider", "LM Studio")
    endpoint = endpoint or _config.get("llm_endpoint", "")
    api_key = api_key or get_provider_api_key(provider)

    if provider == "Gemini":
        return _fetch_gemini_models(api_key)
    elif provider == "DeepSeek":
        return _fetch_deepseek_models(api_key)
    else:
        base = endpoint.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        try:
            r = requests.get(f"{base}/v1/models", timeout=5)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        except Exception:
            return []


def _fetch_deepseek_models(api_key: str) -> list[str]:
    if not api_key:
        return []
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        r = requests.get("https://api.deepseek.com/models", timeout=10, headers=headers)
        r.raise_for_status()
        models = [m.get("id", "") for m in r.json().get("data", [])]
        return [m for m in models if m] or ["deepseek-chat", "deepseek-reasoner"]
    except Exception:
        return ["deepseek-chat", "deepseek-reasoner"]


def _fetch_gemini_models(api_key: str) -> list[str]:
    if not api_key:
        return []
    try:
        url = f"{GEMINI_BASE}/models?key={api_key}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        models = []
        for m in r.json().get("models", []):
            name = m.get("name", "").replace("models/", "")
            if "generateContent" in m.get("supportedGenerationMethods", []):
                models.append(name)
        return models or ["gemini-2.5-flash", "gemini-2.5-pro"]
    except Exception:
        return ["gemini-2.5-flash", "gemini-2.5-pro"]


class _GeminiResponse:
    def __init__(self, data):
        candidate = data.get("candidates", [{}])[0]
        parts = candidate.get("content", {}).get("parts", [])
        self.content = "".join(p.get("text", "") for p in parts)
        self.role = "assistant"
        self.reasoning_content = ""


class _GeminiChoice:
    def __init__(self, data):
        self.message = _GeminiResponse(data)
        self.finish_reason = data.get("candidates", [{}])[0].get("finishReason", "stop")


class _GeminiResult:
    def __init__(self, data):
        self.choices = [_GeminiChoice(data)]


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
    def __init__(self, api_key, base_url=None, timeout=None):
        self.chat = _GeminiChat(api_key, base_url)
        self._client = self

    def close(self):
        pass
