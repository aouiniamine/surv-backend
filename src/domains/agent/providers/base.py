from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelReply:
    content: str
    tool_calls: tuple[ToolCall, ...]
    assistant_message: dict[str, Any]
    input_tokens: int | None = None
    output_tokens: int | None = None


class ModelProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    async def check_ready(self) -> None: ...

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text: Callable[[str], Awaitable[None]],
    ) -> ModelReply: ...
