"""Provider-agnostic LLM interfaces."""

from cogalpha.llm.client import CompletionClient, OpenAICompatibleClient

__all__ = ["CompletionClient", "OpenAICompatibleClient"]
