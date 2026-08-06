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


LOCAL_PROVIDERS = ("LM Studio", "Ollama")

# A reasoning model (Qwen3.x, R1 distils) spends tokens thinking before it writes
# anything: the chain-of-thought goes to `reasoning_content` and `content` stays
# empty until it finishes. Every call site here reads `content`, so on too small a
# budget the model returns nothing at all — scoring silently drops to its keyword
# fallback and analysis fails outright. Measured on LM Studio + qwen3.5-9b:
# max_tokens=300 -> 299 reasoning tokens, empty content; max_tokens=3000 -> answers
# after 786. Asking the chat template to skip thinking does NOT work there
# (`chat_template_kwargs={"enable_thinking": False}` and a `/no_think` suffix were
# both ignored), so budget is the only lever. Retry once with a bigger one rather
# than raising every call, so a normal answer never pays for it.
_REASONING_RETRY_FACTOR = 4
_REASONING_RETRY_CEILING = 32000


def content_of(res) -> str:
    """The assistant text of a completion, or "" when the model produced none."""
    try:
        return (res.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, TypeError):
        return ""


def empty_reason(res) -> str:
    """Why a completion came back empty — a specific cause beats "empty response"."""
    try:
        choice = res.choices[0]
    except (AttributeError, IndexError, TypeError):
        return "the model returned no choices"
    reasoning = getattr(choice.message, "reasoning_content", None)
    if choice.finish_reason == "length" and reasoning:
        # Measured cause on LM Studio: the model was loaded with a 4096-token context,
        # so max_tokens is clamped to what the prompt leaves and no client-side budget
        # can help. The context length is a server-side load setting, not an API field.
        return ("the model spent its whole token budget on reasoning and never wrote an "
                "answer — reload it with a larger context length (a reasoning model needs "
                "thousands of tokens before it answers) or use a non-reasoning model")
    if choice.finish_reason == "length":
        return "the reply hit the token limit before any content — raise max_tokens"
    if reasoning:
        return "the model returned only reasoning, no answer"
    return "the model returned an empty reply"


class _RetryingCompletions:
    """Retries once with a larger budget when a reasoning model runs out mid-thought."""

    def __init__(self, inner):
        self._inner = inner

    def create(self, **kwargs):
        res = self._inner.create(**kwargs)
        requested = kwargs.get("max_tokens")
        if content_of(res) or not requested:
            return res
        try:
            if res.choices[0].finish_reason != "length":
                return res
        except (AttributeError, IndexError, TypeError):
            return res
        bigger = min(requested * _REASONING_RETRY_FACTOR, _REASONING_RETRY_CEILING)
        if bigger <= requested:
            return res
        return self._inner.create(**{**kwargs, "max_tokens": bigger})

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _RetryingChat:
    def __init__(self, inner):
        self._inner = inner
        self.completions = _RetryingCompletions(inner.completions)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _LocalReasoningClient:
    """Wraps a local OpenAI-compatible client; delegates everything it does not adapt."""

    def __init__(self, inner):
        self._inner = inner
        self.chat = _RetryingChat(inner.chat)

    def __getattr__(self, name):
        return getattr(self._inner, name)


LOCAL_MODEL_REQUIREMENT = (
    "Pick a NON-REASONING instruct model. A reasoning model (Qwen3.x, R1 distils, "
    "anything that 'thinks' first) writes its chain-of-thought to reasoning_content and "
    "leaves content empty until it finishes — this pipeline reads content, so such a "
    "model returns nothing at all: scoring falls back to keyword matching and synthesis "
    "fails. Turning thinking off per-request does not work (chat_template_kwargs and "
    "/no_think are ignored by LM Studio). If you must use one, reload it with a large "
    "context length — at 4096 it never reaches an answer."
)


def provider_for_endpoint(endpoint: str) -> str:
    """Which local provider serves this URL. Mirrors gui/llm_provider.py."""
    return "Ollama" if ":11434" in (endpoint or "") else "LM Studio"


def probe_local_model(endpoint: str, model: str, timeout: float = 45.0) -> dict:
    """Ask the model one trivial question to see whether it answers or only thinks.

    Catching a reasoning model here — at selection time — beats discovering it after a
    synthesis run that silently produced nothing. Returns
    ``{"ok": bool, "reasoning": bool, "detail": str}``.
    """
    try:
        from openai import OpenAI
        client = OpenAI(base_url=endpoint, api_key="not-needed", timeout=timeout)
        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            max_tokens=64, temperature=0.0,
        )
    except Exception as e:
        return {"ok": False, "reasoning": False,
                "detail": f"could not reach {endpoint or 'the local server'}: {e}"}
    if content_of(res):
        return {"ok": True, "reasoning": False, "detail": "answered a probe prompt directly"}
    return {"ok": False, "reasoning": True, "detail": empty_reason(res)}


def create_llm_client(endpoint="", api_key="not-needed", provider_name="", timeout=120.0):
    if provider_name == "Gemini":
        return _GeminiClientWrapper(api_key, base_url=endpoint, timeout=timeout)
    from openai import OpenAI
    client = OpenAI(base_url=endpoint or _config.get("llm_endpoint", "http://127.0.0.1:1234/v1"),
                    api_key=api_key, timeout=timeout)
    # Only local providers get the retry — a cloud call must never silently cost 4x.
    if provider_name in LOCAL_PROVIDERS:
        return _LocalReasoningClient(client)
    return client


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
