"""Ingest a GitHub repo into ChromaDB with AST-based or naive chunking.

Public API (used by Inngest steps in server.py):
    fetch_and_chunk_repo(repo_slug, mode, event_id) -> dict
    embed_and_store_chunks(chunks_data)             -> dict

CLI usage (calls both functions sequentially):
    python ingest.py <owner/name> <ast|naive>
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import chromadb
import openai
from dotenv import load_dotenv

load_dotenv()
from github import Auth, Github, GithubException, RateLimitExceededException
from github.ContentFile import ContentFile
from github.Repository import Repository


SKIP_DIRS = {"node_modules", ".git", "dist", "build"}
ALLOWED_EXTS = {
    ".py", ".md", ".txt",
    ".js", ".ts", ".tsx", ".jsx",
    ".dart",
    ".go", ".rs", ".java", ".kt", ".swift", ".rb", ".cs",
}
MAX_FILE_BYTES = 500 * 1024
CHUNK_CHARS = 2000
CHUNK_OVERLAP = 200

EXT_LANGUAGE = {
    ".py":    "python",
    ".md":    "markdown",
    ".txt":   "text",
    ".js":    "javascript",
    ".ts":    "typescript",
    ".tsx":   "typescript",
    ".jsx":   "javascript",
    ".dart":  "dart",
    ".go":    "go",
    ".rs":    "rust",
    ".java":  "java",
    ".kt":    "kotlin",
    ".swift": "swift",
    ".rb":    "ruby",
    ".cs":    "csharp",
}

# Extension → tree-sitter grammar identifier (see tree-sitter-language-pack
# for the full list of 306 supported languages). When mode="ast" and the file
# isn't Python/Markdown, ``chunk_file`` looks up this map and invokes the
# generic cAST chunker. Unmapped extensions fall through to naive_chunk.
EXT_TO_TREESITTER = {
    ".js":    "javascript",
    ".jsx":   "javascript",
    ".ts":    "typescript",
    ".tsx":   "tsx",
    ".dart":  "dart",
    ".go":    "go",
    ".rs":    "rust",
    ".java":  "java",
    ".kt":    "kotlin",
    ".swift": "swift",
    ".rb":    "ruby",
    ".cs":    "csharp",
}

# Match an H2 heading only (## , but not ### or deeper).
H2_PATTERN = re.compile(r"^##(?!#)\s+(.*)$", re.MULTILINE)


@dataclass
class Chunk:
    chunk_id: str
    text: str
    metadata: dict


def is_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def should_skip_path(path: str) -> bool:
    parts = path.split("/")
    return any(p in SKIP_DIRS for p in parts)


def _sub_chunk(text: str, base_id: str, base_meta: dict, header: str = "") -> list[Chunk]:
    """Split an oversized AST node into parts, prepending the function/class header
    to every continuation part so each chunk is self-contained.

    `header` must be passed in by the caller (computed from AST line info) —
    we do not re-parse it here to avoid bugs with decorators and multi-line signatures.
    No overlap is used — the header provides the context for each continuation part.
    """
    all_lines = text.splitlines(keepends=True)
    chunks: list[Chunk] = []
    part = 0

    # part0: as much of the function as fits from the beginning
    window_lines: list[str] = []
    char_count = 0
    for line in all_lines:
        if char_count + len(line) > CHUNK_CHARS and window_lines:
            break
        window_lines.append(line)
        char_count += len(line)

    chunks.append(Chunk(
        chunk_id=f"{base_id}::part0",
        text="".join(window_lines),
        metadata={**base_meta, "part": 0},
    ))
    start_idx = len(window_lines)
    part = 1

    # continuation parts: prepend header so each chunk is self-contained
    while start_idx < len(all_lines):
        window_lines = []
        char_count = len(header)
        for line in all_lines[start_idx:]:
            if char_count + len(line) > CHUNK_CHARS and window_lines:
                break
            window_lines.append(line)
            char_count += len(line)

        if not window_lines:
            start_idx += 1
            continue

        window = header + "    # ... continued ...\n" + "".join(window_lines)
        chunks.append(Chunk(
            chunk_id=f"{base_id}::part{part}",
            text=window,
            metadata={**base_meta, "part": part},
        ))
        part += 1
        start_idx += len(window_lines)

    return chunks


def _effective_start_line(node: ast.AST) -> int:
    """First line owned by a function/class — earliest decorator if any, else node.lineno.
    Without this, `@decorator` lines fall into neither the function chunk nor the
    preceding module-level run, dropping context like `@app.route(...)` / `@st.cache_resource`.
    """
    decorators = getattr(node, "decorator_list", None)
    if decorators:
        return decorators[0].lineno
    return node.lineno


def _ast_function_chunks(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    file_path: str,
) -> list[Chunk]:
    """One chunk per function/method; sub-chunks if the body exceeds CHUNK_CHARS."""
    if node.end_lineno is None:
        return []
    start = _effective_start_line(node)
    text = "\n".join(lines[start - 1 : node.end_lineno])
    docstring = ast.get_docstring(node) or ""
    base_id = f"{file_path}::function::{node.name}::{start}"
    base_meta: dict = {
        "type": "function",
        "name": node.name,
        "file_path": file_path,
        "line_start": start,
        "line_end": node.end_lineno,
        "docstring": docstring,
        "language": "python",
    }
    if len(text) <= CHUNK_CHARS:
        return [Chunk(chunk_id=base_id, text=text, metadata=base_meta)]

    # Compute header: decorators + signature + docstring (if present).
    # Using AST line info avoids re-parsing decorated/multi-line signatures.
    if node.body:
        first_stmt = node.body[0]
        if (isinstance(first_stmt, ast.Expr) and
                isinstance(first_stmt.value, ast.Constant)):
            # Docstring is first statement — include it in the header
            header_end = first_stmt.end_lineno or first_stmt.lineno
        else:
            header_end = first_stmt.lineno - 1
        header = "\n".join(lines[start - 1 : header_end]) + "\n"
    else:
        header = lines[start - 1] + "\n"

    return _sub_chunk(text, base_id, base_meta, header=header)


def _ast_class_header_chunk(node: ast.ClassDef, lines: list[str], file_path: str) -> Chunk:
    """Class chunk contains only the class signature + docstring + class-level
    attributes.  Method bodies are excluded — each method is its own chunk.
    This prevents the same code appearing in both the class chunk and the
    method chunk (the duplication the old ast.walk approach caused).
    """
    start = _effective_start_line(node)
    # Find where the first method starts so we can trim before it.
    first_method_line: int | None = None
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            child_start = _effective_start_line(child)
            if first_method_line is None or child_start < first_method_line:
                first_method_line = child_start

    if first_method_line is not None:
        header = lines[start - 1 : first_method_line - 1]
        while header and not header[-1].strip():
            header.pop()
        text = "\n".join(header) if header else "\n".join(lines[start - 1 : node.end_lineno])
    else:
        # No methods — use the full class body.
        text = "\n".join(lines[start - 1 : node.end_lineno])

    docstring = ast.get_docstring(node) or ""
    return Chunk(
        chunk_id=f"{file_path}::class::{node.name}::{start}",
        text=text,
        metadata={
            "type": "class",
            "name": node.name,
            "file_path": file_path,
            "line_start": start,
            "line_end": node.end_lineno,
            "docstring": docstring,
            "language": "python",
        },
    )


def extract_python_ast_chunks(source: str, file_path: str) -> list[Chunk]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines()
    chunks: list[Chunk] = []

    # Accumulator for contiguous module-level statements (imports, top-level
    # assignments, top-level Streamlit/Flask/CLI calls). Without this we silently
    # dropped everything that wasn't a top-level FunctionDef/ClassDef — fatal for
    # script-style files where the actual logic lives at module level.
    module_run_start: int | None = None
    module_run_end: int | None = None
    module_idx = 0

    def flush_module() -> None:
        nonlocal module_run_start, module_run_end, module_idx
        if module_run_start is None or module_run_end is None:
            return
        text = "\n".join(lines[module_run_start - 1 : module_run_end]).rstrip()
        if text.strip():
            base_id = f"{file_path}::code::module::{module_idx}::{module_run_start}"
            base_meta = {
                "type": "code",
                "file_path": file_path,
                "line_start": module_run_start,
                "line_end": module_run_end,
                "language": "python",
                "chunk_index": module_idx,
            }
            if len(text) <= CHUNK_CHARS:
                chunks.append(Chunk(chunk_id=base_id, text=text, metadata=base_meta))
            else:
                chunks.extend(_sub_chunk(text, base_id, base_meta, header=""))
            module_idx += 1
        module_run_start = None
        module_run_end = None

    # Iterate only over top-level module statements (not ast.walk which would
    # visit nested nodes and cause method bodies to appear in both the class
    # chunk and the individual method chunks).
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            flush_module()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chunks.extend(_ast_function_chunks(node, lines, file_path))
            else:  # ClassDef
                if node.end_lineno is None:
                    continue
                chunks.append(_ast_class_header_chunk(node, lines, file_path))
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        chunks.extend(_ast_function_chunks(child, lines, file_path))
        else:
            stmt_start = node.lineno
            stmt_end = getattr(node, "end_lineno", None) or node.lineno
            if module_run_start is None:
                module_run_start = stmt_start
            module_run_end = stmt_end

    flush_module()
    return chunks


def extract_markdown_chunks(source: str, file_path: str) -> list[Chunk]:
    matches = list(H2_PATTERN.finditer(source))
    if not matches:
        return [
            Chunk(
                chunk_id=f"{file_path}::doc::0",
                text=source,
                metadata={
                    "type": "doc",
                    "file_path": file_path,
                    "heading": "",
                    "language": "markdown",
                },
            )
        ]

    chunks: list[Chunk] = []
    first_start = matches[0].start()
    if first_start > 0 and source[:first_start].strip():
        chunks.append(
            Chunk(
                chunk_id=f"{file_path}::doc::preamble",
                text=source[:first_start].strip(),
                metadata={
                    "type": "doc",
                    "file_path": file_path,
                    "heading": "",
                    "language": "markdown",
                },
            )
        )
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(source)
        chunks.append(
            Chunk(
                chunk_id=f"{file_path}::doc::{i}",
                text=source[start:end].rstrip(),
                metadata={
                    "type": "doc",
                    "file_path": file_path,
                    "heading": m.group(1).strip(),
                    "language": "markdown",
                },
            )
        )
    return chunks


def naive_chunk(source: str, file_path: str, language: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    step = CHUNK_CHARS - CHUNK_OVERLAP
    idx = 0
    i = 0
    while i < len(source):
        text = source[i : i + CHUNK_CHARS]
        if text.strip():
            chunks.append(
                Chunk(
                    chunk_id=f"{file_path}::code::{idx}",
                    text=text,
                    metadata={
                        "type": "code",
                        "file_path": file_path,
                        "chunk_index": idx,
                        "language": language,
                    },
                )
            )
            idx += 1
        i += step
    return chunks


def chunk_file(path: str, content: str, mode: str) -> list[Chunk]:
    """Route a file to the right chunker.

    Dispatch (mode="ast"):
      .md          → extract_markdown_chunks   (heading-based, always)
      .py          → extract_python_ast_chunks (stdlib ast, rich metadata)
                     ↳ on SyntaxError, falls through to cast_chunk
      mapped ext   → cast_chunk via tree-sitter (306 languages, structural)
                     ↳ on parser error, falls through to naive_chunk
      else         → naive_chunk                (sliding window fallback)

    Dispatch (mode="naive"):
      .md          → extract_markdown_chunks
      else         → naive_chunk

    Tree-sitter import is local so a missing dep (or a wheel that fails to
    load on some odd platform) doesn't break ingestion entirely — we just
    fall back to naive for that file.
    """
    ext = os.path.splitext(path)[1]
    language = EXT_LANGUAGE.get(ext, "text")

    if ext == ".md":
        return extract_markdown_chunks(content, path)

    if mode != "ast":
        return naive_chunk(content, path, language)

    # mode = "ast" ---------------------------------------------------------
    if ext == ".py":
        chunks = extract_python_ast_chunks(content, path)
        if chunks:
            return chunks
        # SyntaxError or empty file — try tree-sitter (recovers from errors),
        # then fall through to naive if that also fails.

    ts_lang = EXT_TO_TREESITTER.get(ext) or ("python" if ext == ".py" else None)
    if ts_lang:
        try:
            from tree_sitter_chunker import cast_chunk
            chunks = cast_chunk(content, path, ts_lang, max_bytes=CHUNK_CHARS)
            if chunks:
                return chunks
        except Exception as e:
            print(f"[warn] cast_chunk failed for {path} ({ts_lang}): {e}", flush=True)

    return naive_chunk(content, path, language)


def wait_for_rate_limit(gh: Github) -> None:
    rl = gh.get_rate_limit().core
    wait = max(int(rl.reset.timestamp() - time.time()) + 1, 1)
    print(f"[rate-limit] GitHub API exhausted. Waiting {wait}s until reset...", flush=True)
    time.sleep(wait)


def walk_repo(gh: Github, repo: Repository) -> Iterable[ContentFile]:
    stack: list[str] = [""]
    while stack:
        path = stack.pop()
        entries: list[ContentFile] | ContentFile
        while True:
            try:
                entries = repo.get_contents(path)
                break
            except RateLimitExceededException:
                wait_for_rate_limit(gh)
            except GithubException as e:
                print(f"[warn] Skipping {path!r}: {e}", flush=True)
                entries = []
                break
        if isinstance(entries, ContentFile):
            entries = [entries]
        for entry in entries:
            if should_skip_path(entry.path):
                continue
            if entry.type == "dir":
                stack.append(entry.path)
            elif entry.type == "file":
                yield entry


def fetch_file_bytes(gh: Github, entry: ContentFile) -> bytes | None:
    if entry.size and entry.size > MAX_FILE_BYTES:
        return None
    while True:
        try:
            return entry.decoded_content
        except RateLimitExceededException:
            wait_for_rate_limit(gh)
        except (GithubException, AssertionError) as e:
            print(f"[warn] Could not read {entry.path}: {e}", flush=True)
            return None


def embed(text: str) -> list[float]:
    """Provider-switching single-text embedding.

    Reads the active embed provider from auth (X-Embed-Provider header).
    Supported: ``vllm`` (Modal bge-small) | ``openai`` | ``gemini``.

    Each provider produces vectors of different dimension — switching providers
    requires a fresh collection (the qualify_collection() name suffix handles
    that automatically).
    """
    from auth import (
        get_embed_api_key,
        get_embed_model,
        get_embed_provider,
        get_vllm_api_key,
    )
    provider = get_embed_provider()
    model = get_embed_model(provider)
    if provider == "vllm":
        return _embed_vllm(text, model, get_vllm_api_key())
    if provider == "openai":
        return _embed_openai(text, model, get_embed_api_key())
    if provider == "gemini":
        return _embed_gemini(text, model, get_embed_api_key())
    raise RuntimeError(f"Unknown embedding provider {provider!r}")


def _embed_vllm(text: str, model: str, key: str) -> list[float]:
    """Original Modal-hosted bge-small (OpenAI-compatible /v1/embeddings)."""
    client = openai.OpenAI(base_url=os.getenv("EMBED_BASE_URL"), api_key=key)
    resp = client.embeddings.create(input=[text], model=model)
    return resp.data[0].embedding


def _embed_openai(text: str, model: str, key: str) -> list[float]:
    """OpenAI ``/v1/embeddings`` — uses the openai SDK with cloud base URL."""
    if not key:
        raise RuntimeError(
            "No OpenAI API key. Paste one in the dashboard's Settings → Embeddings card."
        )
    client = openai.OpenAI(base_url="https://api.openai.com/v1", api_key=key)
    resp = client.embeddings.create(input=[text], model=model)
    return resp.data[0].embedding


def _embed_gemini(text: str, model: str, key: str) -> list[float]:
    """Google Gemini ``models/{model}:embedContent`` — different shape from OpenAI."""
    if not key:
        raise RuntimeError(
            "No Gemini API key. Paste one in the dashboard's Settings → Embeddings card."
        )
    import httpx
    resp = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={
            "model": f"models/{model}",
            "content": {"parts": [{"text": text}]},
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["embedding"]["values"]


def fetch_and_chunk_repo(
    repo_slug: str,
    mode: str,
    event_id: str = "cli",
    tenant_id: str | None = None,
    embed_provider: str | None = None,
) -> dict:
    """Step 1: Walk a GitHub repo, chunk every file, write to a temp JSONL on disk.

    The temp file is named with *event_id* so Inngest retries are idempotent —
    a retry of this step will overwrite the same file rather than producing a
    duplicate.

    *tenant_id* qualifies the collection name so two anonymous users ingesting
    the same repo never collide. *embed_provider* is appended as a second
    suffix so ChromaDB collections stay separate per embedding model (which
    have different vector dimensions). Both fall back to ContextVar defaults
    when omitted (CLI runs become ``shared`` tenant + ``vllm`` provider).

    Returns a small dict (safe to serialise as an Inngest step result):
        {collection_name, temp_path, files_seen, total_chunks}
    """
    load_dotenv()
    if repo_slug.count("/") != 1:
        raise ValueError(f"repo must be 'owner/name'; got {repo_slug!r}")
    owner, name = repo_slug.split("/", 1)

    # ContextVar override (PAT from dashboard Settings) wins; env-var is fallback.
    from auth import get_github_token, qualify_collection
    gh = Github(auth=Auth.Token(get_github_token()))
    repo = gh.get_repo(repo_slug)

    collection_name = qualify_collection(
        f"{owner}_{name}_{mode}", tenant_id, embed_provider
    )
    safe_event_id = event_id.replace("/", "-")[:40]
    temp_path = str(Path(os.getenv("CHROMA_DB_PATH", "./chroma_db")) / f".chunks_{collection_name}_{safe_event_id}.jsonl")
    Path(temp_path).parent.mkdir(parents=True, exist_ok=True)

    files_seen = 0
    total_chunks = 0
    with open(temp_path, "w", encoding="utf-8") as f:
        for entry in walk_repo(gh, repo):
            if os.path.splitext(entry.path)[1] not in ALLOWED_EXTS:
                continue
            raw = fetch_file_bytes(gh, entry)
            if raw is None or is_binary(raw):
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            files_seen += 1
            for chunk in chunk_file(entry.path, text, mode):
                f.write(json.dumps({
                    "id": chunk.chunk_id,
                    "text": chunk.text,
                    "metadata": chunk.metadata,
                }) + "\n")
                total_chunks += 1
            if total_chunks % 50 == 0 and total_chunks:
                print(f"[progress] {total_chunks} chunks chunked (last: {entry.path})", flush=True)

    print(f"[fetch-and-chunk] {total_chunks} chunks from {files_seen} files → {temp_path}", flush=True)
    return {
        "collection_name": collection_name,
        "temp_path": temp_path,
        "files_seen": files_seen,
        "total_chunks": total_chunks,
    }


class IngestCancelled(RuntimeError):
    """Raised by embed_and_store_chunks when the dashboard cancels the ingest mid-flight."""


def embed_and_store_chunks(chunks_data: dict) -> dict:
    """Step 2: Read the temp JSONL from fetch_and_chunk_repo, embed, upsert ChromaDB.

    The temp file is cleaned up in a finally block so a failed/retried step 2
    can still find its input on retry (the file is only deleted on success).

    Cooperatively cancellable — the dashboard's DELETE endpoint sets a flag in
    ``auth._cancelled_ingests``; this loop checks it between chunks and raises
    ``IngestCancelled`` so a deleted collection doesn't get rebuilt mid-flight.
    """
    from auth import is_ingest_cancelled

    collection_name: str = chunks_data["collection_name"]
    temp_path: str = chunks_data["temp_path"]

    if is_ingest_cancelled(collection_name):
        raise IngestCancelled(f"Ingest cancelled before start: {collection_name}")

    client = chromadb.PersistentClient(path=os.getenv("CHROMA_DB_PATH", "./chroma_db"))
    collection = client.get_or_create_collection(collection_name)

    total = 0
    errors = 0
    try:
        with open(temp_path, encoding="utf-8") as f:
            for line in f:
                if is_ingest_cancelled(collection_name):
                    raise IngestCancelled(
                        f"Ingest cancelled at chunk {total}: {collection_name}"
                    )
                line = line.strip()
                if not line:
                    continue
                chunk = json.loads(line)
                try:
                    vec = embed(chunk["text"])
                except Exception as e:
                    print(f"[warn] Embedding failed for {chunk['id']}: {e}", flush=True)
                    errors += 1
                    continue
                collection.upsert(
                    ids=[chunk["id"]],
                    documents=[chunk["text"]],
                    embeddings=[vec],
                    metadatas=[chunk["metadata"]],
                )
                total += 1
                if total % 50 == 0:
                    print(f"[progress] {total} chunks embedded", flush=True)
    finally:
        # Only delete if the file exists — safe for retries (retry re-runs
        # fetch-and-chunk first, which recreates the file before this step).
        try:
            Path(temp_path).unlink(missing_ok=True)
        except OSError:
            pass

    print(
        f"[embed-and-store] Done. {total} chunks stored, {errors} errors → '{collection_name}'",
        flush=True,
    )
    return {
        "collection_name": collection_name,
        "total_chunks": total,
        "embed_errors": errors,
        "files_seen": chunks_data.get("files_seen", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a GitHub repo into ChromaDB.")
    parser.add_argument("repo", help='Repo in "owner/name" form, e.g. facebook/react')
    parser.add_argument("mode", choices=["ast", "naive"], help="Chunking mode")
    args = parser.parse_args()

    try:
        chunks_data = fetch_and_chunk_repo(args.repo, args.mode, event_id="cli")
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 2
    except GithubException as e:
        if e.status == 404:
            print(
                f"Repo {args.repo!r} returned 404. Common causes:\n"
                f"  1. Typo in the repo name (case-sensitive).\n"
                f"  2. Repo is private and your GITHUB_TOKEN doesn't grant access "
                f"to it. Fine-grained PATs require you to explicitly list the "
                f"repos they can read — check https://github.com/settings/"
                f"personal-access-tokens and add this repo (or use a classic "
                f"PAT with the 'repo' scope).\n"
                f"  3. Repo was deleted or you're on the wrong owner/org.\n"
                f"Verify with: curl -H 'Authorization: Bearer $GITHUB_TOKEN' "
                f"https://api.github.com/repos/{args.repo}",
                file=sys.stderr,
            )
        elif e.status == 401:
            print(
                "GITHUB_TOKEN was rejected (401). Check it hasn't expired, "
                "and that the token string in .env is complete.",
                file=sys.stderr,
            )
        else:
            print(f"Failed to open repo {args.repo!r}: {e}", file=sys.stderr)
        return 1

    result = embed_and_store_chunks(chunks_data)
    print(
        f"\nDone. Ingested {result['total_chunks']} chunks from "
        f"{result['files_seen']} files into collection '{result['collection_name']}'."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
