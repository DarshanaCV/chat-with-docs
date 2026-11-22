from app.services.ingestion.chunker import chunk_text


def test_short_text_returns_a_single_chunk():
    text = "hello world"
    assert chunk_text(text, chunk_size=1000, chunk_overlap=200) == [text]


def test_empty_text_returns_one_empty_chunk():
    # Documents current behavior rather than asserting it's ideal: chunking
    # an empty page produces a single empty-string chunk, not zero chunks.
    assert chunk_text("") == [""]


def test_long_text_splits_into_multiple_chunks_within_size_limit():
    text = "".join(str(i % 10) for i in range(2500))
    chunks = chunk_text(text, chunk_size=1000, chunk_overlap=200)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1000 for chunk in chunks)


def test_consecutive_chunks_overlap_by_chunk_overlap_characters():
    text = "".join(str(i % 10) for i in range(2500))
    chunks = chunk_text(text, chunk_size=1000, chunk_overlap=200)
    for current_chunk, next_chunk in zip(chunks, chunks[1:]):
        assert current_chunk[-200:] == next_chunk[:200]
