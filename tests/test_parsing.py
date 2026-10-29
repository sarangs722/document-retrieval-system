import pytest
from pydantic import ValidationError
from pypdf import PdfWriter

from app.config import Settings
from app.embeddings import Embedder, cosine
from app.parsing import DocumentError, Page, chunk_pages, parse


def test_chunk_limit_overlap_and_page_provenance(settings):
    settings.chunk_words, settings.chunk_overlap_words = 50, 10
    pages = [Page(1, " ".join(f"word{i}" for i in range(170))), Page(2, "Second page only.")]
    chunks = chunk_pages(pages, settings, Embedder(settings))
    assert all(len(chunk.text.split()) <= 50 for chunk in chunks)
    assert chunks[-1].page == 2 and chunks[-1].text == "Second page only."
    first_page = [chunk for chunk in chunks if chunk.page == 1]
    assert first_page[0].text.split()[-10:] == first_page[1].text.split()[:10]
    assert set(" ".join(chunk.text for chunk in first_page).split()) == set(pages[0].text.split())


def test_semantic_boundary_uses_embeddings(settings):
    settings.chunk_strategy, settings.chunk_words, settings.chunk_overlap_words = "semantic", 200, 0
    first = " ".join(["solar"] * 45) + "."
    second = " ".join(["database"] * 45) + "."

    class Embeddings:
        def encode(self, texts):
            return [[1.0, 0.0], [0.0, 1.0]]

    chunks = chunk_pages([Page(1, f"{first} {second}")], settings, Embeddings())
    assert len(chunks) == 2
    assert "database" not in chunks[0].text


def test_pdf_empty_encrypted_and_limits(settings, tmp_path):
    path = tmp_path / "empty.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(path)
    with pytest.raises(DocumentError, match="OCR"):
        parse(path, ".pdf", settings)
    writer.encrypt("secret")
    writer.write(path)
    with pytest.raises(DocumentError, match="Encrypted"):
        parse(path, ".pdf", settings)


def test_real_pdf_text_and_page_numbers(settings, tmp_path):
    # A tiny valid PDF with two text streams, built without a second PDF dependency.
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    for sentence in ("Solar energy comes from sunlight.", "Databases store persistent records."):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({sentence}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    path = tmp_path / "text.pdf"
    writer.write(path)
    pages = parse(path, ".pdf", settings)
    assert len(pages) == 2 and "Solar energy" in pages[0].text
    chunks = chunk_pages(pages, settings, Embedder(settings))
    assert [chunk.page for chunk in chunks] == [1, 2]


def test_stable_lexical_embeddings_and_signature(settings):
    embedder = Embedder(settings)
    a, b, c = embedder.encode(["solar energy", "solar energy", "database records"])
    assert a == b and cosine(a, b) == pytest.approx(1)
    assert cosine(a, c) < 0.2
    changed = settings.model_copy(update={"chunk_words": 80})
    assert embedder.signature != Embedder(changed).signature


def test_configuration_validates_modes():
    with pytest.raises(ValidationError, match="Semantic chunking"):
        Settings(_env_file=None, chunk_strategy="semantic", embedding_provider="hash")
    with pytest.raises(ValidationError, match="smaller"):
        Settings(_env_file=None, chunk_words=40, chunk_overlap_words=40)
