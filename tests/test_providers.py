import asyncio
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.embeddings import Embedder
from app.generation import SYSTEM_PROMPT, Generator


class FakeStream:
    def __init__(self, events):
        self.events, self.closed = events, False

    async def __aiter__(self):
        for event in self.events:
            yield SimpleNamespace(**event)

    async def close(self):
        self.closed = True


def test_openai_stream_adapter_sends_bounded_sources_and_checks_completion(settings):
    settings.llm_provider = "openai"
    settings.openai_api_key = SecretStr("unused-test-key")
    generator = Generator(settings)
    stream = FakeStream(
        [
            {"type": "response.output_text.delta", "delta": "Supported answer [1]."},
            {"type": "response.completed"},
        ]
    )
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return stream

    generator.__dict__["client"] = SimpleNamespace(responses=SimpleNamespace(create=create))

    async def run():
        return [
            part
            async for part in generator.stream(
                "Question",
                [SimpleNamespace(label=1, text="Source text")],
                [{"role": "user", "content": "Earlier question"}],
            )
        ]

    assert asyncio.run(run()) == ["Supported answer [1]."]
    assert stream.closed
    assert captured["stream"] is True and captured["store"] is False
    assert captured["instructions"] == SYSTEM_PROMPT
    assert '"label": 1' in captured["input"][-1]["content"]
    assert captured["input"][0]["content"] == "Earlier question"


@pytest.mark.parametrize(
    "event", ["response.failed", "response.incomplete", "error", "premature_end"]
)
def test_openai_incomplete_stream_never_counts_as_success(settings, event):
    settings.llm_provider = "openai"
    generator = Generator(settings)
    stream = FakeStream([] if event == "premature_end" else [{"type": event}])

    async def create(**kwargs):
        return stream

    generator.__dict__["client"] = SimpleNamespace(responses=SimpleNamespace(create=create))

    async def run():
        return [
            part
            async for part in generator.stream(
                "Question", [SimpleNamespace(label=1, text="Evidence")], []
            )
        ]

    with pytest.raises(RuntimeError):
        asyncio.run(run())
    assert stream.closed


def test_openai_embeddings_batch_and_restore_order(settings):
    settings.embedding_provider, settings.embedding_model = "openai", "test-model"
    embedder = Embedder(settings)
    calls = []

    def create(input, model):
        calls.append((input, model))
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=i, embedding=[float(value)])
                for i, value in reversed(list(enumerate(input)))
            ]
        )

    embedder.__dict__["openai"] = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    result = embedder.encode([str(i) for i in range(130)])
    assert result == [[float(i)] for i in range(130)]
    assert [len(items) for items, _ in calls] == [64, 64, 2]
    assert embedder.encode([]) == []
