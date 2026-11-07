"""Minimal LLM client: one function, JSON out, provider chosen by environment variables.

Configure one of:
  ANTHROPIC_API_KEY  (+ optional ANTHROPIC_MODEL, default below)
  OPENAI_API_KEY     (+ optional OPENAI_MODEL, default below)

Responses are cached on disk keyed by (provider, model, prompts), so repeated runs
are reproducible and free. Callers must treat output as untrusted: everything the
model returns is validated before use.
"""
import hashlib
import json
import logging
import os
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

CACHE_DIR = Path(".cache/llm")
ANTHROPIC_DEFAULT_MODEL = "claude-haiku-5-5"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"
TIMEOUT = 60


class LLMUnavailable(Exception):
    """No provider configured, or the call failed. Callers fall back to no-LLM behavior."""


def provider() -> tuple[str, str] | None:
    """(provider, model) from the environment, or None if no key is set."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic", os.getenv("ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_MODEL)
    if os.getenv("OPENAI_API_KEY"):
        return "openai", os.getenv("OPENAI_MODEL", OPENAI_DEFAULT_MODEL)
    return None


def configured() -> bool:
    return provider() is not None


def _cache_path(*parts: str) -> Path:
    digest = hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:32]
    return CACHE_DIR / f"{digest}.json"


def _call_anthropic(model: str, system: str, user: str, max_tokens: int) -> str:
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": model, "max_tokens": max_tokens, "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=TIMEOUT)
    r.raise_for_status()
    return "".join(block.get("text", "") for block in r.json().get("content", []))


def _call_openai(model: str, system: str, user: str, max_tokens: int) -> str:
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        json={"model": model, "max_tokens": max_tokens,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]},
        timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _parse_json(text: str) -> dict:
    """Parse a JSON object, tolerating code fences or a sentence around it."""
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


def complete_json(system: str, user: str, max_tokens: int = 600) -> dict:
    """Ask the configured model for a JSON object. Raises LLMUnavailable on any failure."""
    conf = provider()
    if conf is None:
        raise LLMUnavailable("no LLM API key configured")
    name, model = conf

    cache = _cache_path(name, model, system, user)
    if cache.exists():
        return json.loads(cache.read_text())

    try:
        call = _call_anthropic if name == "anthropic" else _call_openai
        data = _parse_json(call(model, system, user, max_tokens))
    except (httpx.HTTPError, ValueError, KeyError) as e:
        raise LLMUnavailable(f"{name} call failed: {e!r}") from e

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
    except OSError as e:
        logger.warning("LLM cache write failed: %s", e)
    return data
