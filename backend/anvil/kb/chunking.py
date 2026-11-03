"""Deterministic text chunking.

Chunks are built from paragraphs and packed up to ``max_chars`` with an overlap
of ``overlap_chars`` characters taken from the tail of the previous chunk. A
single paragraph longer than ``max_chars`` is split on sentence boundaries and,
if need be, hard-wrapped. No external splitter means the same input always
produces the same chunks, which keeps re-ingestion idempotent.
"""

from __future__ import annotations

import hashlib
import re

_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, max_chars: int = 2000, overlap_chars: int = 200) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be >= 0 and < max_chars")

    normalised = _normalise(text)
    if not normalised:
        return []

    units: list[str] = []
    for paragraph in _PARAGRAPH.split(normalised):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            units.append(paragraph)
        else:
            units.extend(_split_long(paragraph, max_chars))

    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            tail = current[-overlap_chars:] if overlap_chars else ""
            current = f"{tail}\n\n{unit}" if tail else unit
        else:
            current = unit
        while len(current) > max_chars:
            chunks.append(current[:max_chars])
            tail = (
                current[max_chars - overlap_chars : max_chars] if overlap_chars else ""
            )
            current = f"{tail}{current[max_chars:]}"
    if current.strip():
        chunks.append(current.strip())
    return chunks


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    sentences = _SENTENCE.split(paragraph)
    units: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            units.append(current)
        current = sentence
        while len(current) > max_chars:
            units.append(current[:max_chars])
            current = current[max_chars:]
    if current:
        units.append(current)
    return units
