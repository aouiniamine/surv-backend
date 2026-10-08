"""Gemini streaming Generate Content adapter for Surv's bounded file-tool loop."""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from domains.agent.providers.base import ModelReply, ToolCall

GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
MODEL_ID = re.compile(r"^[a-zA-Z0-9._-]+$")
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
GENERATE_ATTEMPTS = 3
logger = logging.getLogger("uvicorn.error")


class GeminiProvider:
    provider_id = "gemini"

    def __init__(self, api_key: str, model_id: str) -> None:
        if not api_key.strip():
            raise ValueError("GEMINI_API_KEY is required when AGENT_PROVIDER=gemini")
        if not MODEL_ID.fullmatch(model_id):
            raise ValueError("GEMINI_MODEL must be a model ID without a path or query")
        self._api_key = api_key
        self.model_id = model_id

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self._api_key,
            "x-goog-api-client": "surv-agent/0.1.0",
        }

    @property
    def _model_url(self) -> str:
        return f"{GEMINI_API_ROOT}/models/{self.model_id}"

    def _check_response(self, response: httpx.Response, operation: str) -> None:
        if response.is_error:
            try:
                error = response.json().get("error", {})
                detail = error.get("message", response.text)
                code = error.get("status", "unknown")
            except (ValueError, AttributeError):
                detail, code = response.text, "unknown"
            detail = str(detail).replace(self._api_key, "[redacted]")[:1000]
            logger.error(
                "Gemini %s failed: model=%s HTTP %s code=%s detail=%s",
                operation, self.model_id, response.status_code, code, detail,
            )
        response.raise_for_status()

    async def _read_stream(
        self,
        response: httpx.Response,
        on_text: Callable[[str], Awaitable[None]],
    ) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        data_lines: list[str] = []
        event_count = 0
        finish_reason: str | None = None

        async def receive_event() -> None:
            nonlocal event_count, finish_reason, usage
            if not data_lines:
                return
            raw = "\n".join(data_lines)
            data_lines.clear()
            chunk = json.loads(raw)
            if not isinstance(chunk, dict):
                raise ValueError("Gemini returned an invalid stream event")
            event_count += 1
            usage = chunk.get("usageMetadata") or usage
            candidates = chunk.get("candidates") or []
            if not candidates:
                return
            candidate = candidates[0]
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                if not isinstance(part, dict):
                    raise ValueError("Gemini returned an invalid content part")
                parts.append(part)
                if not part.get("thought") and isinstance(part.get("text"), str) and part["text"]:
                    await on_text(part["text"])
            if candidate.get("finishReason"):
                finish_reason = candidate["finishReason"]

        try:
            async for line in response.aiter_lines():
                if not line:
                    await receive_event()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip(" "))
            await receive_event()
        except httpx.ReadTimeout as exc:
            if event_count:
                raise RuntimeError("Gemini stream timed out after it started") from exc
            raise
        if finish_reason is None:
            raise RuntimeError("Gemini stream ended before completion")
        if finish_reason != "STOP":
            raise RuntimeError(f"Gemini generation stopped with {finish_reason}")
        return {
            "candidates": [{"content": {"role": "model", "parts": parts}}],
            "usageMetadata": usage,
        }

    async def _generate(
        self,
        payload: dict[str, Any],
        on_text: Callable[[str], Awaitable[None]],
    ) -> dict[str, Any]:
        timeout = httpx.Timeout(connect=8, read=180, write=30, pool=8)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(1, GENERATE_ATTEMPTS + 1):
                try:
                    async with client.stream(
                        "POST",
                        f"{self._model_url}:streamGenerateContent",
                        params={"alt": "sse"},
                        headers=self._headers,
                        json=payload,
                    ) as response:
                        if response.is_error:
                            await response.aread()
                            if response.status_code not in RETRYABLE_STATUS_CODES:
                                self._check_response(response, "streamGenerateContent")
                            elif attempt == GENERATE_ATTEMPTS:
                                self._check_response(response, "streamGenerateContent")
                            else:
                                logger.warning(
                                    "Gemini stream returned HTTP %s: model=%s "
                                    "attempt=%s/%s; retrying",
                                    response.status_code, self.model_id,
                                    attempt, GENERATE_ATTEMPTS,
                                )
                        else:
                            return await self._read_stream(response, on_text)
                except httpx.ReadTimeout:
                    if attempt == GENERATE_ATTEMPTS:
                        raise
                    logger.warning(
                        "Gemini stream timed out before its first event: "
                        "model=%s attempt=%s/%s; retrying",
                        self.model_id, attempt, GENERATE_ATTEMPTS,
                    )
                await asyncio.sleep(min(2**attempt, 8))
        raise RuntimeError("Gemini generation exhausted its retry limit")

    async def check_ready(self) -> None:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(self._model_url, headers=self._headers)
            self._check_response(response, "model check")
            methods = response.json().get("supportedGenerationMethods", [])
            if methods and "generateContent" not in methods:
                raise RuntimeError(f"Gemini model {self.model_id} cannot generate content")

    @staticmethod
    def _request_messages(
        messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        system_text: list[str] = []
        contents: list[dict[str, Any]] = []
        tool_parts: list[dict[str, Any]] = []

        def flush_tool_parts() -> None:
            if tool_parts:
                contents.append({"role": "user", "parts": list(tool_parts)})
                tool_parts.clear()

        for message in messages:
            role = message["role"]
            if role == "system":
                system_text.append(message["content"])
            elif role == "tool":
                result: dict[str, Any] = {
                    "name": message["tool_name"],
                    "response": {"result": message["content"]},
                }
                if message.get("tool_call_id"):
                    result["id"] = message["tool_call_id"]
                tool_parts.append({"functionResponse": result})
            else:
                flush_tool_parts()
                if role == "assistant":
                    # Keep Gemini's original parts, including thought signatures and call IDs.
                    native = message.get("_gemini_content")
                    contents.append(
                        native or {"role": "model", "parts": [{"text": message["content"]}]}
                    )
                elif role == "user":
                    contents.append({"role": "user", "parts": [{"text": message["content"]}]})
                else:
                    raise ValueError("Unsupported agent message role")
        flush_tool_parts()
        return {"parts": [{"text": "\n\n".join(system_text)}]}, contents

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text: Callable[[str], Awaitable[None]],
    ) -> ModelReply:
        system_instruction, contents = self._request_messages(messages)
        declarations = [tool["function"] for tool in tools]
        payload = {
            "systemInstruction": system_instruction,
            "contents": contents,
            "tools": [{"functionDeclarations": declarations}],
        }
        data = await self._generate(payload, on_text)

        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError("Gemini returned no response candidate")
        candidate = candidates[0]
        native_content = candidate.get("content") or {}
        parts = native_content.get("parts") or []
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for part in parts:
            if not isinstance(part, dict):
                raise ValueError("Gemini returned an invalid content part")
            if not part.get("thought") and isinstance(part.get("text"), str) and part["text"]:
                text_parts.append(part["text"])
            function = part.get("functionCall")
            if function is not None:
                if not isinstance(function, dict):
                    raise ValueError("Gemini returned an invalid function call")
                name, arguments = function.get("name"), function.get("args", {})
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise ValueError("Gemini returned invalid function arguments")
                call_id = function.get("id")
                if call_id is not None and not isinstance(call_id, str):
                    raise ValueError("Gemini returned an invalid function call ID")
                calls.append(ToolCall(name, arguments, call_id))
        if not text_parts and not calls:
            reason = candidate.get("finishReason", "unknown")
            raise RuntimeError(f"Gemini returned no usable content ({reason})")
        usage = data.get("usageMetadata") or {}
        content = "".join(text_parts)
        return ModelReply(
            content=content,
            tool_calls=tuple(calls),
            assistant_message={
                "role": "assistant", "content": content, "_gemini_content": native_content,
            },
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
        )
