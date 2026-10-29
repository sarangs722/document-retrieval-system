import re
from collections.abc import AsyncIterator
from contextlib import aclosing
from functools import cached_property

import httpx
from openai import AsyncOpenAI

from app.prompts import SYSTEM_PROMPT, local_messages, source_payload
from app.retrieval import bm25

ABSTENTION = "I couldn't find supporting passages in the selected documents. Try another question or upload a relevant document."


def citation_check(answer: str, sources) -> tuple[str, list]:
    labels = {int(value) for value in re.findall(r"\[(\d+)\]", answer)}
    if not sources:
        return ("invalid" if labels else "not_applicable"), []
    if not labels:
        return "missing", []
    valid = {source.label for source in sources}
    status = "valid" if labels <= valid else "invalid"
    return status, [source for source in sources if source.label in labels]


class Generator:
    def __init__(self, settings):
        self.settings = settings

    @cached_property
    def client(self):
        return AsyncOpenAI(
            api_key=self.settings.openai_api_key.get_secret_value(), timeout=45, max_retries=1
        )

    @cached_property
    def ollama_client(self):
        return httpx.AsyncClient(
            base_url=str(self.settings.ollama_base_url).rstrip("/"),
            timeout=httpx.Timeout(self.settings.ollama_timeout_seconds, connect=5),
            trust_env=False,
        )

    async def local_stream(self, question, sources, history):
        import json

        completed = False
        async with self.ollama_client.stream(
            "POST",
            "/api/chat",
            json={
                "model": self.settings.llm_model,
                "messages": local_messages(question, sources, history),
                "stream": True,
                "think": False,
                "keep_alive": "5m",
                "options": {
                    "temperature": 0,
                    "num_ctx": self.settings.ollama_context_tokens,
                    "num_predict": self.settings.ollama_max_output_tokens,
                },
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise RuntimeError("The local model returned an error")
                if event.get("done") and event.get("done_reason") not in (None, "stop"):
                    raise RuntimeError("The local model did not complete its response")
                content = event.get("message", {}).get("content", "")
                if content:
                    yield content
                if event.get("done"):
                    completed = True
                    break
        if not completed:
            raise RuntimeError("The local model stream ended before completion")

    async def stream(self, question: str, sources, history: list[dict]) -> AsyncIterator[str]:
        if not sources:
            yield ABSTENTION
            return
        if self.settings.llm_provider == "extractive":
            # An honest offline fallback: quote evidence, do not pretend to synthesize an LLM answer.
            yield "Relevant passages from your documents:\n\n"
            candidates = [
                (source, index, sentence)
                for source in sources
                for index, sentence in enumerate(re.split(r"(?<=[.!?])\s+", source.text))
                if sentence.strip()
            ]
            scores = bm25(question, [sentence for _, _, sentence in candidates])
            best = max(scores, default=0)
            selected = {}
            for position in sorted(range(len(scores)), key=lambda i: (-scores[i], i)):
                source, index, sentence = candidates[position]
                if scores[position] <= 0 or scores[position] < best * 0.5:
                    break
                group = selected.setdefault(source.label, (source, []))[1]
                if len(group) < 2:
                    group.append((index, sentence))
                if sum(len(group) for _, group in selected.values()) >= 3:
                    break
            if not selected:
                # A semantic-only match can share no exact words with the question.
                selected = {sources[0].label: (sources[0], [(0, sources[0].text)])}
            for source, sentences in selected.values():
                excerpt = " ".join(sentence for _, sentence in sorted(sentences))[:700]
                yield f"“{excerpt}” [{source.label}]\n\n"
            return
        if self.settings.llm_provider == "ollama":
            async with aclosing(self.local_stream(question, sources, history)) as local:
                async for part in local:
                    yield part
            return
        payload = source_payload(question, sources)
        stream = await self.client.responses.create(
            model=self.settings.llm_model,
            instructions=SYSTEM_PROMPT,
            input=[*history, {"role": "user", "content": payload}],
            max_output_tokens=1500,
            stream=True,
            store=False,
        )
        completed = False
        try:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    yield event.delta
                elif event.type == "response.completed":
                    completed = True
                elif event.type in ("error", "response.failed", "response.incomplete"):
                    raise RuntimeError("The model did not complete its response")
            if not completed:
                raise RuntimeError("The model stream ended before completion")
        finally:
            await stream.close()

    async def close(self):
        if "client" in self.__dict__:
            await self.client.close()
        if "ollama_client" in self.__dict__:
            await self.ollama_client.aclose()
