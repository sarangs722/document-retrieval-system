import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from app.config import Settings
from app.embeddings import Embedder, cosine


class DocumentError(ValueError):
    """A safe, actionable parsing failure that can be shown to the user."""


@dataclass(frozen=True)
class Page:
    number: int
    text: str


@dataclass(frozen=True)
class TextChunk:
    page: int
    text: str


def normalize(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def parse(path: Path, suffix: str, settings: Settings) -> list[Page]:
    if suffix == ".pdf":
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise DocumentError("Encrypted PDFs are unsupported. Upload a decrypted copy.")
            if len(reader.pages) > settings.max_pages:
                raise DocumentError(f"PDF exceeds the {settings.max_pages}-page limit.")
            pages = []
            total = 0
            for number, page in enumerate(reader.pages, 1):
                content = normalize(page.extract_text() or "")
                total += len(content)
                if total > settings.max_document_chars:
                    raise DocumentError("Extracted document text exceeds the configured limit.")
                pages.append(Page(number, content))
        except DocumentError:
            raise
        except Exception as exc:
            raise DocumentError("Unable to parse this PDF. Check that the file is valid.") from exc
    else:
        try:
            content = normalize(path.read_text(encoding="utf-8-sig"))
        except UnicodeError as exc:
            raise DocumentError("Text documents must use UTF-8 encoding.") from exc
        if len(content) > settings.max_document_chars:
            raise DocumentError("Document text exceeds the configured limit.")
        pages = [Page(1, content)]
    if not any(page.text.strip() for page in pages):
        raise DocumentError("No readable text found. Scanned PDFs need OCR before upload.")
    return pages


def chunk_pages(pages: list[Page], settings: Settings, embedder: Embedder) -> list[TextChunk]:
    """Page-preserving sentence packing, optionally split on embedding similarity drops.

    Long sentences are split before packing, so chunk_words is a hard word limit.
    Semantic boundaries are evaluated only after enough new content has accumulated.
    """
    result = []
    max_unit = min(80, settings.chunk_words - settings.chunk_overlap_words)
    for page in pages:
        sentences = re.split(r"(?<=[.!?])\s+|\n\s*\n", page.text)
        units = []
        for sentence in sentences:
            words = sentence.split()
            units.extend(" ".join(words[i : i + max_unit]) for i in range(0, len(words), max_unit))
        embeddings = embedder.encode(units) if settings.chunk_strategy == "semantic" else []
        current: list[str] = []
        new_words = 0
        for index, unit in enumerate(units):
            words = unit.split()
            semantic_break = (
                bool(embeddings)
                and index > 0
                and new_words >= 40
                and cosine(embeddings[index - 1], embeddings[index]) < settings.semantic_threshold
            )
            if current and (len(current) + len(words) > settings.chunk_words or semantic_break):
                result.append(TextChunk(page.number, " ".join(current)))
                current = (
                    current[-settings.chunk_overlap_words :] if settings.chunk_overlap_words else []
                )
                new_words = 0
            current.extend(words)
            new_words += len(words)
        if current and new_words:
            result.append(TextChunk(page.number, " ".join(current)))
        if len(result) > settings.max_chunks:
            raise DocumentError("Document produces too many chunks. Split it into smaller files.")
    return result
