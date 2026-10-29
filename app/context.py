"""Conservative input packing for small local model context windows."""

import json

from app.prompts import local_messages


def message_bytes(question, sources, history):
    return len(
        json.dumps(local_messages(question, sources, history), ensure_ascii=False).encode("utf-8")
    )


def fit_local_context(question, history, sources, settings):
    # UTF-8 bytes conservatively overestimate ordinary byte/subword token counts.
    # Reserve output and template overhead instead of trusting server-side truncation.
    limit = settings.ollama_context_tokens - settings.ollama_max_output_tokens - 512
    baseline = message_bytes(question, [], [])
    if baseline >= limit:
        raise ValueError("Question is too long for the configured local model context")
    history_allowance = (limit - baseline) // 3
    per_message = max(64, history_allowance // 4)
    packed_history = [
        {
            "role": message["role"],
            "content": message["content"]
            .encode("utf-8")[:per_message]
            .decode("utf-8", errors="ignore"),
        }
        for message in history
    ]
    while (
        packed_history
        and message_bytes(question, [], packed_history) - baseline > history_allowance
    ):
        packed_history = packed_history[2:]
    packed_sources = []
    for source in sources:
        low, high = 0, len(source.text)
        while low < high:
            middle = (low + high + 1) // 2
            trial = source.model_copy(update={"text": source.text[:middle]})
            if message_bytes(question, [*packed_sources, trial], packed_history) <= limit:
                low = middle
            else:
                high = middle - 1
        if low < min(100, len(source.text)):
            break
        packed_sources.append(source.model_copy(update={"text": source.text[:low]}))
    if sources and not packed_sources:
        raise ValueError(
            "Local model context is full. Shorten the question to leave room for evidence."
        )
    return packed_history, packed_sources
