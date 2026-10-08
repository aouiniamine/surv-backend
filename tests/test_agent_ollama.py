import asyncio
import json

import httpx
import pytest

from domains.agent.providers import ollama


def test_ollama_adapter_parses_text_and_tool_calls(monkeypatch):
    real_client = httpx.AsyncClient
    observed: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        observed.append(payload)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        return httpx.Response(
            200,
            content=(
                json.dumps({"message": {"content": "Working. "}}) + "\n"
                + json.dumps({"message": {"tool_calls": [{"function": {
                    "name": "write_file", "arguments": {
                        "path": "index.html", "content": "<h1>Hello</h1>"
                    }
                }}]}}) + "\n"
                + json.dumps({"done": True, "prompt_eval_count": 12, "eval_count": 7}) + "\n"
            ).encode(),
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        ollama.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    async def scenario():
        provider = ollama.OllamaProvider("http://ollama.local", "qwen3:4b")
        await provider.check_ready()
        chunks: list[str] = []

        async def on_text(chunk: str) -> None:
            chunks.append(chunk)

        reply = await provider.complete([{"role": "user", "content": "Build"}], [], on_text)
        assert chunks == ["Working. "]
        assert reply.tool_calls[0].name == "write_file"
        assert reply.tool_calls[0].arguments["path"] == "index.html"
        assert reply.input_tokens == 12
        assert reply.output_tokens == 7
        assert observed[-1]["model"] == "qwen3:4b"
        assert observed[-1]["think"] is False

    asyncio.run(scenario())


def test_ollama_rejects_model_without_tools(monkeypatch):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"capabilities": ["completion"]})
    )
    monkeypatch.setattr(
        ollama.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    with pytest.raises(RuntimeError, match="does not support tools"):
        asyncio.run(ollama.OllamaProvider("http://ollama.local", "gemma3:4b").check_ready())
