"""
LLM endpoint auto-discovery — scans localhost for running LM Studio and Ollama.

Key functions:
  discover(timeout) — Returns list of dicts: [{endpoint, provider, models, label}]
  try_lm_studio(base_url, timeout) — Queries /v1/models for currently loaded models
  try_ollama_running(base_url, timeout) — Queries /api/ps for running models
  fetch_ollama_url(base_url, timeout) — Returns the dynamic Ollama IP:PORT

Only inference-ready (loaded/running) models are returned, not all downloaded.
"""
import requests
from typing import List, Dict, Optional


KNOWN_ENDPOINTS = [
    ("http://127.0.0.1:1234", "LM Studio"),
    ("http://localhost:1234", "LM Studio"),
    ("http://127.0.0.1:11434", "Ollama"),
    ("http://localhost:11434", "Ollama"),
]


def try_lm_studio(base_url: str, timeout: float = 3.0) -> Optional[List[str]]:
    """LM Studio /v1/models — returns currently loaded models only."""
    try:
        r = requests.get(f"{base_url}/v1/models", timeout=timeout)
        r.raise_for_status()
        data = r.json()
        models = [m["id"] for m in data.get("data", [])]
        return models if models else None
    except Exception:
        return None


def try_ollama_running(base_url: str, timeout: float = 3.0) -> Optional[Dict[str, str]]:
    """Ollama /api/ps — returns ONLY running models with their details.
    Returns dict of {model_name: model_name}."""
    try:
        r = requests.get(f"{base_url}/api/ps", timeout=timeout)
        r.raise_for_status()
        data = r.json()
        models = data.get("models", [])
        running = {}
        for m in models:
            name = m.get("model", m.get("name", ""))
            if name:
                running[name] = name
        return running if running else None
    except Exception:
        return None


def try_ollama_v1(base_url: str, timeout: float = 3.0) -> Optional[List[str]]:
    """Ollama's OpenAI-compatible /v1/models — returns loaded models."""
    try:
        r = requests.get(f"{base_url}/v1/models", timeout=timeout)
        r.raise_for_status()
        data = r.json()
        models = [m["id"] for m in data.get("data", [])]
        return models if models else None
    except Exception:
        return None


def discover(timeout: float = 3.0) -> List[Dict]:
    """
    Scan known ports for LM Studio and Ollama.
    Returns only inference-ready (loaded/running) models.
    Ollama: uses /api/ps for running models, not /api/tags (which returns all downloaded).

    Returns list of dicts:
        {"base_url": str, "provider": str, "models": [str], "v1_endpoint": str}
    """
    found = []
    seen = set()

    for base_url, provider in KNOWN_ENDPOINTS:
        base_key = base_url.rstrip("/")
        if base_key in seen:
            continue
        seen.add(base_key)

        if provider == "LM Studio":
            models = try_lm_studio(base_url, timeout)
            if models:
                found.append({
                    "base_url": base_url,
                    "provider": "LM Studio",
                    "models": models,
                    "v1_endpoint": f"{base_url}/v1",
                })
                continue

        if provider == "Ollama":
            running = try_ollama_running(base_url, timeout)
            if running:
                found.append({
                    "base_url": base_url,
                    "provider": "Ollama",
                    "models": list(running.keys()),
                    "v1_endpoint": f"{base_url}/v1",
                })
                continue

            models = try_ollama_v1(base_url, timeout)
            if models:
                found.append({
                    "base_url": base_url,
                    "provider": "Ollama",
                    "models": models,
                    "v1_endpoint": f"{base_url}/v1",
                })

    return found
