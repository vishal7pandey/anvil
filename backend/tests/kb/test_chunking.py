from anvil.kb.chunking import chunk_text, content_hash


def test_empty_text_has_no_chunks():
    assert chunk_text("   \n\n ") == []


def test_short_text_is_one_chunk():
    assert chunk_text("Build erc32 and run hello on SIS.") == [
        "Build erc32 and run hello on SIS."
    ]


def test_chunks_respect_max_chars_and_keep_order():
    paragraphs = [f"Paragraph {i}. " + ("word " * 120) for i in range(8)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_text(text, max_chars=600, overlap_chars=60)
    assert len(chunks) > 1
    assert all(len(chunk) <= 600 for chunk in chunks)
    for marker in ("Paragraph 0.", "Paragraph 7."):
        assert any(marker in chunk for chunk in chunks)


def test_overlap_carries_tail_of_previous_chunk():
    first = "A" * 300
    second = "B" * 300
    chunks = chunk_text(f"{first}\n\n{second}", max_chars=400, overlap_chars=50)
    assert len(chunks) == 2
    assert chunks[1].startswith("A" * 50)


def test_chunking_is_deterministic():
    text = "\n\n".join("sentence. " * 30 for _ in range(10))
    assert chunk_text(text, 500, 50) == chunk_text(text, 500, 50)


def test_content_hash_is_stable_and_distinct():
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")
