import asyncio
import json

import httpx
import pytest

from domains.agent.providers import gemini


def sse_response(*chunks: dict) -> httpx.Response:
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def test_gemini_preserves_function_ids_and_thought_signatures(monkeypatch):
    real_client = httpx.AsyncClient
    requests: list[dict] = []
    first_parts = [
        {
            "functionCall": {"id": "call-1", "name": "read_file", "args": {"path": "index.html"}},
            "thoughtSignature": "opaque-signature",
        },
        {"functionCall": {"id": "call-2", "name": "list_files", "args": {}}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "private-test-key"
        assert "private-test-key" not in str(request.url)
        if request.method == "GET":
            return httpx.Response(200, json={"supportedGenerationMethods": ["generateContent"]})
        assert request.url.path.endswith(":streamGenerateContent")
        assert request.url.params["alt"] == "sse"
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return sse_response(
                {"candidates": [{"content": {"role": "model", "parts": first_parts[:1]}}]},
                {"candidates": [{"content": {"role": "model", "parts": first_parts[1:]},
                                 "finishReason": "STOP"}],
                 "usageMetadata": {"promptTokenCount": 15, "candidatesTokenCount": 8}},
            )
        return sse_response(
            {"candidates": [{"content": {"role": "model", "parts": [{"text": "Ready "}]}}]},
            {"candidates": [{"content": {"role": "model", "parts": [
                {"text": "to review."}, {"text": "", "thoughtSignature": "final-signature"},
            ]}, "finishReason": "STOP"}]},
        )

    monkeypatch.setattr(
        gemini.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    async def scenario():
        provider = gemini.GeminiProvider("private-test-key", "gemini-3.8-flash")
        await provider.check_ready()
        chunks: list[str] = []

        async def on_text(chunk: str) -> None:
            chunks.append(chunk)

        messages = [{"role": "system", "content": "Build the site"},
                    {"role": "user", "content": "Create a page"}]
        tools = [{"type": "function", "function": {
            "name": "read_file", "description": "Read a file", "parameters": {"type": "object"}
        }}]
        first = await provider.complete(messages, tools, on_text)
        assert [(call.name, call.id) for call in first.tool_calls] == [
            ("read_file", "call-1"), ("list_files", "call-2")
        ]
        assert first.input_tokens == 15
        assert first.output_tokens == 8
        messages.extend([
            first.assistant_message,
            {"role": "tool", "tool_name": "read_file", "tool_call_id": "call-1",
             "content": "<h1>Old</h1>"},
            {"role": "tool", "tool_name": "list_files", "tool_call_id": "call-2",
             "content": "index.html"},
        ])
        second = await provider.complete(messages, tools, on_text)
        assert second.content == "Ready to review."
        assert chunks == ["Ready ", "to review."]
        assert second.assistant_message["_gemini_content"]["parts"][-1] == {
            "text": "", "thoughtSignature": "final-signature",
        }
        assert requests[0]["systemInstruction"]["parts"][0]["text"] == "Build the site"
        assert requests[0]["tools"][0]["functionDeclarations"][0]["name"] == "read_file"
        assert requests[1]["contents"][1] == {"role": "model", "parts": first_parts}
        responses = requests[1]["contents"][2]["parts"]
        assert [part["functionResponse"]["id"] for part in responses] == ["call-1", "call-2"]
        assert [part["functionResponse"]["response"]["result"] for part in responses] == [
            "<h1>Old</h1>", "index.html"
        ]

    asyncio.run(scenario())


def test_gemini_requires_key_and_plain_model_id():
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        gemini.GeminiProvider("", "gemini-3.8-flash")
    with pytest.raises(ValueError, match="GEMINI_MODEL"):
        gemini.GeminiProvider("private-test-key", "../other-model")


def test_gemini_logs_api_error_without_leaking_key(monkeypatch, caplog):
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={
            "error": {"status": "INVALID_ARGUMENT", "message": "Bad model private-test-key"}
        })

    monkeypatch.setattr(
        gemini.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = gemini.GeminiProvider("private-test-key", "gemini-3.8-flash")
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.check_ready())
    assert "INVALID_ARGUMENT" in caplog.text
    assert "Bad model [redacted]" in caplog.text
    assert "private-test-key" not in caplog.text


@pytest.mark.parametrize("first_failure", ["unavailable", "timeout"])
def test_gemini_retries_transient_generation_failure(monkeypatch, first_failure):
    real_client = httpx.AsyncClient
    attempts = 0
    delays: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            if first_failure == "timeout":
                raise httpx.ReadTimeout("waiting for Gemini")
            return httpx.Response(503, json={
                "error": {"status": "UNAVAILABLE", "message": "High demand"}
            })
        return sse_response({
            "candidates": [{"content": {"role": "model", "parts": [{"text": "Ready"}]},
                            "finishReason": "STOP"}],
        })

    async def no_sleep(delay: int) -> None:
        delays.append(delay)

    monkeypatch.setattr(
        gemini.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(gemini.asyncio, "sleep", no_sleep)

    async def scenario():
        provider = gemini.GeminiProvider("private-test-key", "gemini-3.5-flash")

        async def on_text(chunk: str) -> None:
            assert chunk == "Ready"

        reply = await provider.complete(
            [{"role": "user", "content": "Create a page"}], [], on_text,
        )
        assert reply.content == "Ready"

    asyncio.run(scenario())
    assert attempts == 2
    assert delays == [2]


def test_gemini_does_not_retry_after_stream_starts(monkeypatch):
    real_client = httpx.AsyncClient
    attempts = 0
    chunks: list[str] = []

    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"candidates":[{"content":{"parts":[{"text":"Partial"}]}}]}\n\n'
            raise httpx.ReadTimeout("stream stalled")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200, stream=InterruptedStream(), headers={"content-type": "text/event-stream"},
        )

    monkeypatch.setattr(
        gemini.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    async def scenario():
        provider = gemini.GeminiProvider("private-test-key", "gemini-3.5-flash")

        async def on_text(chunk: str) -> None:
            chunks.append(chunk)

        with pytest.raises(RuntimeError, match="stream timed out after it started"):
            await provider.complete([{"role": "user", "content": "Create a page"}], [], on_text)

    asyncio.run(scenario())
    assert attempts == 1
    assert chunks == ["Partial"]
