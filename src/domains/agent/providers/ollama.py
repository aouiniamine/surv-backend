import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from domains.agent.providers.base import ModelReply, ToolCall


class OllamaProvider:
    provider_id = "ollama"

    def __init__(self, base_url: str, model_id: str) -> None:
        self._base_url = base_url.rstrip("/")
        self.model_id = model_id

    async def check_ready(self) -> None:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post(
                f"{self._base_url}/api/show", json={"model": self.model_id}
            )
            response.raise_for_status()
            capabilities = response.json().get("capabilities", [])
            if "tools" not in capabilities:
                raise RuntimeError(f"Ollama model {self.model_id} does not support tools")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text: Callable[[str], Awaitable[None]],
    ) -> ModelReply:
        content: list[str] = []
        calls: list[ToolCall] = []
        native_calls: list[dict[str, Any]] = []
        input_tokens: int | None = None
        output_tokens: int | None = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=8)) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json={
                    "model": self.model_id,
                    "messages": messages,
                    "tools": tools,
                    "stream": True,
                    "think": False,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    item = json.loads(line)
                    if item.get("error"):
                        raise RuntimeError(str(item["error"])[:300])
                    message = item.get("message") or {}
                    chunk = message.get("content") or ""
                    if chunk:
                        content.append(chunk)
                        await on_text(chunk)
                    for call in message.get("tool_calls") or []:
                        function = call.get("function") or {}
                        arguments = function.get("arguments") or {}
                        if not isinstance(arguments, dict):
                            raise ValueError("Ollama returned invalid tool arguments")
                        name = function.get("name")
                        if not isinstance(name, str):
                            raise ValueError("Ollama returned an unnamed tool")
                        calls.append(ToolCall(name, arguments))
                        native_calls.append(call)
                    if item.get("done"):
                        input_tokens = item.get("prompt_eval_count")
                        output_tokens = item.get("eval_count")
        assistant = {"role": "assistant", "content": "".join(content)}
        if native_calls:
            assistant["tool_calls"] = native_calls
        return ModelReply(
            content="".join(content),
            tool_calls=tuple(calls),
            assistant_message=assistant,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
