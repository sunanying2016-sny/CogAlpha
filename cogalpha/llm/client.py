"""Provider-agnostic completion client contracts."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


class CompletionClient(Protocol):
    """LLM interface used by paper-defined skills and agentic decisions."""

    def complete_text(self, context: str) -> str:
        """Return one raw text completion."""

    def complete_json(
        self,
        context: str,
        schema_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Return a strict JSON string compatible with the requested schema."""


@dataclass(frozen=True)
class OpenAICompatibleClient:
    """Minimal OpenAI-compatible chat-completions adapter.

    This adapter intentionally targets the common `/chat/completions` shape so the
    project can point at OpenAI-compatible providers without changing graph nodes.
    """

    api_key: str
    base_url: str
    model: str
    temperature: float = 0.8
    timeout_seconds: int = 120
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    thinking: str | None = None
    response_format: str | None = "json_object"

    @classmethod
    def from_env(cls) -> OpenAICompatibleClient:
        """Create a client from environment variables."""

        api_key = (
            os.environ.get("COGALPHA_LLM_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        base_url = os.environ.get("COGALPHA_LLM_BASE_URL") or os.environ.get(
            "DEEPSEEK_BASE_URL",
            os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        model = (
            os.environ.get("COGALPHA_LLM_MODEL")
            or os.environ.get("DEEPSEEK_MODEL")
            or os.environ.get("CHAT_MODEL", "")
        )
        max_tokens = _parse_optional_int(os.environ.get("COGALPHA_LLM_MAX_TOKENS"))
        reasoning_effort = os.environ.get("COGALPHA_LLM_REASONING_EFFORT") or os.environ.get(
            "DEEPSEEK_REASONING_EFFORT"
        )
        thinking = os.environ.get("COGALPHA_LLM_THINKING") or os.environ.get("DEEPSEEK_THINKING")
        response_format = os.environ.get("COGALPHA_LLM_RESPONSE_FORMAT", "json_object")
        if response_format.lower() in {"", "none", "off", "false"}:
            response_format = None
        if not api_key:
            raise RuntimeError("Missing COGALPHA_LLM_API_KEY, DEEPSEEK_API_KEY, or OPENAI_API_KEY")
        if not model:
            raise RuntimeError("Missing COGALPHA_LLM_MODEL or CHAT_MODEL")
        return cls(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            thinking=thinking,
            response_format=response_format,
        )

    def complete_json(
        self,
        context: str,
        schema_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        system_prompt = (
            "You are executing a CogAlpha Standard Skill. "
            "Return strict JSON only. Do not include markdown fences or commentary. "
            f"The JSON must conform to the requested Runtime Schema: {schema_name}."
        )
        if metadata:
            policy = json.dumps(metadata, sort_keys=True)
            system_prompt += f"\nSkill invocation metadata: {policy}"
        payload = self._base_payload(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ]
        )
        if self.response_format:
            payload["response_format"] = {"type": self.response_format}
        return self._complete(payload)

    def complete_text(self, context: str) -> str:
        """Return a raw text completion for a paper-defined skill prompt."""

        payload = self._base_payload([{"role": "user", "content": context}])
        return self._complete(payload)

    def _base_payload(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if self.thinking != "enabled":
            payload["temperature"] = self.temperature
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.thinking:
            payload["thinking"] = {"type": self.thinking}
        return payload

    def _complete(self, payload: dict[str, Any]) -> str:
        request = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM request failed with HTTP {exc.code}: {body}") from exc
        # Guard a malformed/empty response (e.g. ``choices: []`` on a content
        # filter or overload) so the caller sees a clear error rather than a bare
        # IndexError/KeyError leaking the internal access path (WR-03).
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM response had no choices: {data!r}")
        try:
            return choices[0]["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(
                f"LLM response missing message content: {data!r}"
            ) from exc


def _parse_optional_int(raw_value: str | None) -> int | None:
    if raw_value is None or raw_value == "":
        return None
    return int(raw_value)
