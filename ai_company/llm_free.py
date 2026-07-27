
from __future__ import annotations

import os
import time

import requests

from .agents import LLMBackend

class OpenAICompatibleLLM(LLMBackend):

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        max_tokens: int = 1500,
        temperature: float = 0.3,
        timeout: int = 30,
        max_retries: int = 2,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        last_error: Exception | None = None
        MAX_RATE_LIMIT_WAIT = 15   # cap how long we'll silently wait on a
                                   # 429's retry-after header -- some
                                   # providers return values far longer
                                   # than makes sense for a synchronous UI
                                   # wait; better to fail with a clear
                                   # "rate limited" error than hang
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers, json=payload, timeout=self.timeout,
                )
                if resp.status_code == 429:
                    server_wait = float(resp.headers.get("retry-after", 2 * attempt))
                    wait = min(server_wait, MAX_RATE_LIMIT_WAIT)
                    last_error = RuntimeError(
                        f"rate limited (429); server asked to wait {server_wait:.0f}s"
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except (requests.RequestException, KeyError, IndexError) as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 10))
        raise RuntimeError(
            f"OpenAI-compatible call failed after {self.max_retries} attempts: {last_error}"
        )

class GroqLLM(OpenAICompatibleLLM):

    def __init__(self, model: str = "llama-3.3-70b-versatile",
                api_key: str | None = None, **kwargs):
        api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "No Groq API key provided. Get a free key at "
                "https://console.groq.com and either pass api_key= "
                "explicitly or run: export GROQ_API_KEY=your_key"
            )
        super().__init__(
            base_url="https://api.groq.com/openai/v1",
            model=model, api_key=api_key, **kwargs,
        )

class GeminiLLM(OpenAICompatibleLLM):
    """Google Gemini via its OpenAI-compatible endpoint. Free tier available
    at aistudio.google.com -- generate a key there, no separate SDK needed."""

    def __init__(self, model: str = "gemini-2.0-flash",
                api_key: str | None = None, **kwargs):
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "No Gemini API key provided. Get a free key at "
                "https://aistudio.google.com/apikey and either pass "
                "api_key= explicitly or run: export GEMINI_API_KEY=your_key"
            )
        super().__init__(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            model=model, api_key=api_key, **kwargs,
        )

class OllamaLLM(OpenAICompatibleLLM):

    def __init__(self, model: str = "llama3.1", host: str = "http://localhost:11434", **kwargs):
        super().__init__(
            base_url=f"{host.rstrip('/')}/v1",
            model=model, api_key=None, **kwargs,
        )

class CustomOpenAICompatibleLLM(OpenAICompatibleLLM):
    """Escape hatch for any OpenAI-compatible provider not given its own
    class -- Together.ai, Fireworks, OpenRouter, Cerebras, a self-hosted
    vLLM/LM Studio server, etc. Needs the user to supply base_url
    themselves, since there's no single default that fits all of these."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None, **kwargs):
        if not base_url:
            raise RuntimeError(
                "Custom provider needs a base_url (e.g. "
                "https://api.together.xyz/v1 or https://openrouter.ai/api/v1)"
            )
        if not model:
            raise RuntimeError("Custom provider needs a model name")
        api_key = api_key or os.environ.get("CUSTOM_API_KEY")
        super().__init__(base_url=base_url, model=model, api_key=api_key, **kwargs)

def make_free_llm(provider: str, model: str | None = None,
                  api_key: str | None = None, base_url: str | None = None) -> LLMBackend:
    if provider == "groq":
        return GroqLLM(model=model, api_key=api_key) if model else GroqLLM(api_key=api_key)
    if provider == "gemini":
        return GeminiLLM(model=model, api_key=api_key) if model else GeminiLLM(api_key=api_key)
    if provider == "ollama":
        return OllamaLLM(model=model) if model else OllamaLLM()
    if provider == "custom":
        return CustomOpenAICompatibleLLM(base_url=base_url, model=model, api_key=api_key)
    if provider == "mock":
        from .agents import MockLLM
        return MockLLM()
    raise ValueError(
        f"Unknown provider '{provider}'. Use 'groq', 'gemini', 'ollama', 'custom', or 'mock'."
    )
