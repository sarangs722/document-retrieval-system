import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.context import fit_local_context, message_bytes
from app.generation import Generator
from app.schemas import Citation


class EventStream(httpx.AsyncByteStream):
    def __init__(self, events):
        self.events, self.closed = events, False

    async def __aiter__(self):
        for event in self.events:
            yield (json.dumps(event) + "\n").encode()

    async def aclose(self):
        self.closed = True


def test_local_stream_protocol_and_closure(settings):
    settings.llm_provider, settings.llm_model = "ollama", "qwen3:1.7b"
    generator = Generator(settings)
    stream = EventStream(
        [
            {"message": {"thinking": "Private reasoning"}, "done": False},
            {"message": {"content": "Answer "}, "done": False},
            {"message": {"content": "[1]."}, "done": True, "done_reason": "stop"},
        ]
    )
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        assert request.url.path == "/api/chat"
        return httpx.Response(200, stream=stream)

    async def run():
        generator.__dict__["ollama_client"] = httpx.AsyncClient(
            base_url="http://local.test", transport=httpx.MockTransport(respond)
        )
        parts = [
            part
            async for part in generator.stream(
                "Question", [SimpleNamespace(label=1, text="Evidence")], []
            )
        ]
        await generator.close()
        return "".join(parts)

    assert asyncio.run(run()) == "Answer [1]."
    assert stream.closed
    assert requests[0]["think"] is False and requests[0]["stream"] is True
    assert requests[0]["messages"][0]["role"] == "system"
    assert requests[0]["options"]["num_predict"] == settings.ollama_max_output_tokens


@pytest.mark.parametrize(
    "events",
    [
        [{"message": {"content": "Partial"}, "done": False}],
        [{"error": "private upstream details"}],
        [{"done": True, "done_reason": "length"}],
    ],
)
def test_local_incomplete_or_failed_responses_are_rejected(settings, events):
    settings.llm_provider = "ollama"
    generator = Generator(settings)
    stream = EventStream(events)

    async def run():
        generator.__dict__["ollama_client"] = httpx.AsyncClient(
            base_url="http://local.test",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)),
        )
        try:
            return [
                part
                async for part in generator.stream(
                    "Q", [SimpleNamespace(label=1, text="Evidence")], []
                )
            ]
        finally:
            await generator.close()

    with pytest.raises(RuntimeError):
        asyncio.run(run())
    assert stream.closed


def test_context_packing_preserves_valid_unicode_and_exact_source_prefixes(settings):
    settings.llm_provider = "ollama"
    history = [
        {"role": role, "content": "日本語🙂" * 400}
        for _ in range(6)
        for role in ("user", "assistant")
    ]
    sources = [
        Citation(
            chunk_id=str(i),
            document_id="doc",
            filename="test.txt",
            page=1,
            text=("Evidence 日本語🙂 " * 300),
            score=1,
            label=i,
        )
        for i in range(1, 6)
    ]
    packed_history, packed_sources = fit_local_context(
        "What is the evidence?", history, sources, settings
    )
    assert packed_sources
    limit = settings.ollama_context_tokens - settings.ollama_max_output_tokens - 512
    assert message_bytes("What is the evidence?", packed_sources, packed_history) <= limit
    assert all(
        source.text.startswith(packed.text)
        for source, packed in zip(sources, packed_sources, strict=False)
    )
    assert all("�" not in message["content"] for message in packed_history)
    with pytest.raises(ValueError, match="too long"):
        fit_local_context("🙂" * 4000, [], sources, settings)


def test_local_provider_settings():
    settings = Settings(_env_file=None, llm_provider="ollama")
    assert settings.llm_model == "qwen3:1.7b" and settings.openai_api_key is None
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ollama_base_url="file:///etc/passwd")
    with pytest.raises(ValidationError, match="leave room"):
        Settings(_env_file=None, ollama_context_tokens=2048, ollama_max_output_tokens=2048)
