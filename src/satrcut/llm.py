"""Pluggable text models for `satrcut write`.

Mock is the default and makes no network call. OpenAI-compatible endpoints and
Ollama are optional. Keys stay in the environment and are never written into a
plan, a log, or an error message.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Protocol

from satrcut.errors import SatrcutError

SYSTEM_WRITE = (
    "You write a vertical micro-drama plan as JSON. Hook in the first 3 seconds, "
    "cuts of 1.5 to 2.5 seconds, a cliffhanger that is an uncompleted action, and one "
    "emotional beat for the episode. Describe only what is in frame. Never say no, not, "
    "without, hands, fingers, or wheels. Reply with JSON only."
)


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float = 120) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class MockLLM:
    """Deterministic writer. The shot skeleton is built in code so the result can lint."""

    name = "mock"

    def complete(self, system: str, user: str) -> str:
        episode = 1
        premise = user.strip().splitlines()[-1][:240] if user.strip() else "A secret told in close-ups."
        for line in user.splitlines():
            if line.startswith("episode:"):
                try:
                    episode = int(line.split(":", 1)[1].strip())
                except ValueError:
                    episode = 1
            if line.startswith("premise:"):
                premise = line.split(":", 1)[1].strip() or premise
        beats = ("rupture", "confession", "threat", "reversal", "longing")
        return json.dumps(
            {
                "premise": premise,
                "emotional_beat": beats[(episode - 1) % len(beats)],
                "hook": "eyes in the first three seconds",
                "cliffhanger": "uncompleted_action",
                "stakes_line": "Don't answer that.",
                "last_line": "You were not supposed to be here.",
                "time_card": "Ten Minutes Earlier",
            }
        )


class OpenAICompatibleLLM:
    """Any chat endpoint that speaks the OpenAI messages schema. Needs SATRCUT_LLM_API_KEY."""

    name = "openai"

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("SATRCUT_LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.model = model or os.environ.get("SATRCUT_LLM_MODEL") or "gpt-4o-mini"

    def request(self, system: str, user: str) -> tuple[str, dict[str, Any], dict[str, str]]:
        key = os.environ.get("SATRCUT_LLM_API_KEY") or ""
        if not key:
            raise SatrcutError(
                "This LLM endpoint needs SATRCUT_LLM_API_KEY in the environment. "
                "Use --provider mock to write with no key."
            )
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0.4,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        return url, payload, headers

    def complete(self, system: str, user: str) -> str:
        url, payload, headers = self.request(system, user)
        body = _post_json(url, payload, headers)
        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise SatrcutError("The LLM response had no message content.") from exc


class OllamaLLM:
    """Local Ollama. No API key. Default URL http://127.0.0.1:11434."""

    name = "ollama"

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("SATRCUT_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
        self.model = model or os.environ.get("SATRCUT_OLLAMA_MODEL") or "llama3.2"

    def request(self, system: str, user: str) -> tuple[str, dict[str, Any], dict[str, str]]:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        return url, payload, {"Content-Type": "application/json"}

    def complete(self, system: str, user: str) -> str:
        url, payload, headers = self.request(system, user)
        try:
            body = _post_json(url, payload, headers)
        except OSError as exc:
            raise SatrcutError(f"Ollama is not reachable at {self.base_url}. Start it, or use --provider mock.") from exc
        try:
            return str(body["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise SatrcutError("Ollama returned no message content.") from exc


def get_llm(name: str | None) -> LLM:
    key = (name or os.environ.get("SATRCUT_LLM") or "mock").lower()
    if key == "mock":
        return MockLLM()
    if key in ("openai", "openai-compatible", "compatible"):
        return OpenAICompatibleLLM()
    if key == "ollama":
        return OllamaLLM()
    raise SatrcutError(f"unknown LLM provider {name!r} (mock, openai, ollama)")


def parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0]
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise SatrcutError("The model did not return a JSON object.")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise SatrcutError("The model JSON was not an object.")
    return data
