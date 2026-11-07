"""Tests never call a language model: keys from the developer's shell are removed for
every test. The LLM planner tests set their own fake key and fake model."""
import pytest


@pytest.fixture(autouse=True)
def no_llm_keys(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
