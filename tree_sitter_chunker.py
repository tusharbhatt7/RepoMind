"""Generic AST-aware chunker via tree-sitter, language-agnostic.

Implements the cAST (arxiv:2506.15655) recursive split-then-merge algorithm:

  1. For each AST node, if its source text fits in max_chars → emit one chunk.
  2. If it's too big AND has children → recurse, then greedily MERGE sibling
     sub-chunks under the size budget. New chunk starts when adding the next
     sub-chunk would overflow.
  3. If a leaf is too big (no children) → char-split as a last resort,
     preferring newline boundaries.

Crucially, this algorithm **does not know what a function or class is**. It
just respects tree shape and a size budget — so the same code chunks 306+
languages out of the box (anything tree-sitter-language-pack supports).

We keep the Python-specific ``extract_python_ast_chunks`` in ingest.py for
its richer per-chunk metadata (function names, decorators, docstrings).
This module is the multi-language default for everything else.

Usage from ``ingest.chunk_file`` when mode="ast" and file isn't Python/Markdown.
"""
from __future__ import annotations

from dataclasses import dataclass

from tree_sitter_language_pack import get_parser


# Default chunk budget — same as ingest.CHUNK_CHARS so naive and cAST stay
# comparable. Sized in bytes (≈ chars for ASCII-dominated code).
DEFAULT_MAX_BYTES = 2000


@dataclass
class _Span:
    """Half-open byte range with 1-indexed inclusive line numbers."""
    start: int
    end: int
    line_start: int
    line_end: int


def cast_chunk(
    source: str,
    file_path: str,
    language: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> list:
    """Chunk *source* using tree-sitter for *language*.

    Returns a list of :class:`ingest.Chunk` whose ``metadata`` contains
    ``{type, file_path, language, line_start, line_end, chunk_index}``.

    Falls back gracefully — caller (``ingest.chunk_file``) wraps this in
    try/except and routes to ``naive_chunk`` on any failure (unsupported
    language, parser crash, etc).
    """
    # Import here to dodge a circular import (ingest.py imports this module).
    from ingest import Chunk

    parser = get_parser(language)  # raises LookupError if grammar missing
    tree = parser.parse(source)
    root = tree.root_node()
    source_bytes = source.encode("utf-8")

    spans = _split_node(root, source_bytes, max_bytes)

    chunks: list = []
    idx = 0
    for span in spans:
        text = source_bytes[span.start : span.end].decode("utf-8", errors="replace")
        text = text.strip("\n\r")
        if not text.strip():
            continue
        chunks.append(
            Chunk(
                chunk_id=f"{file_path}::cast::{idx}::{span.start}",
                text=text,
                metadata={
                    "type": "code",
                    "file_path": file_path,
                    "language": language,
                    "line_start": span.line_start,
                    "line_end": span.line_end,
                    "chunk_index": idx,
                },
            )
        )
        idx += 1
    return chunks


def _split_node(node, source_bytes: bytes, max_bytes: int) -> list[_Span]:
    """Recursive split-then-merge. Returns spans each ≤ max_bytes."""
    br = node.byte_range()
    sp = node.start_position()
    ep = node.end_position()

    node_size = br.end - br.start
    line_start = sp.row + 1  # tree-sitter rows are 0-indexed
    line_end = ep.row + 1

    if node_size <= max_bytes:
        return [_Span(br.start, br.end, line_start, line_end)]

    child_count = node.child_count()
    if child_count == 0:
        # Atomic leaf bigger than budget — fall back to a newline-preferring
        # byte split so chunks don't cut mid-line where avoidable.
        return _char_split(br.start, br.end, line_start, source_bytes, max_bytes)

    # Recurse into each child, then greedy-merge their sub-spans.
    out: list[_Span] = []
    current: _Span | None = None

    for i in range(child_count):
        child = node.child(i)
        for sub in _split_node(child, source_bytes, max_bytes):
            if current is None:
                current = sub
                continue
            # Try to extend current to include sub. Note: (sub.end - current.start)
            # captures any whitespace/punctuation BETWEEN siblings too — which is
            # exactly what we want, so chunks read naturally.
            if (sub.end - current.start) <= max_bytes:
                current = _Span(current.start, sub.end, current.line_start, sub.line_end)
            else:
                out.append(current)
                current = sub

    if current is not None:
        out.append(current)
    return out


def _char_split(
    start: int,
    end: int,
    line_start: int,
    source_bytes: bytes,
    max_bytes: int,
) -> list[_Span]:
    """Last-resort byte split, preferring newline boundaries.

    Used only when a leaf AST node (typically a giant string literal or a
    minified-JS-style mega-token) is itself larger than ``max_bytes``.
    """
    spans: list[_Span] = []
    pos = start
    cur_line = line_start
    while pos < end:
        chunk_end = min(pos + max_bytes, end)
        # Prefer breaking right after a newline within this window.
        if chunk_end < end:
            nl = source_bytes.rfind(b"\n", pos + 1, chunk_end)
            if nl != -1:
                chunk_end = nl + 1
        nlines = source_bytes[pos:chunk_end].count(b"\n")
        spans.append(_Span(pos, chunk_end, cur_line, cur_line + nlines))
        cur_line += nlines
        pos = chunk_end
    return spans
