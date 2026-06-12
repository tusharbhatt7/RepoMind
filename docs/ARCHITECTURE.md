# Architecture

End-to-end working of repomind — a grounded code-Q&A agent over a GitHub repository.

---

## What it does

You give it a GitHub repo and a question. It finds the relevant code through a semantic index, then asks Qwen to answer using only what it retrieved. The model decides what to search, when to read a whole file, and when it has enough — nothing is hard-coded.

---

## System overview

```
  ┌────────────────────────────────────────────────────────────────────┐
  │                   Next.js Frontend  (port 3000)                    │
  │                                                                    │
  │   Sidebar                /chat                   /benchmarks       │
  │   • owner/repo input     • animated messages     • AST vs naive    │
  │   • AST / Naive toggle   • per-collection queue  • score chart     │
  │   • drag-resize          • history context ref                     │
  └────┬──────────────────────────┬───────────────────────────────────┘
       │ POST /api/ingest         │ POST /api/query  (+history[])
       │                          │ GET  /api/result/{event_id}  ← poll
       ▼                          ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │                  server.py  (FastAPI  port 8000)                 │
  │                  Inngest webhook at /api/inngest                 │
  │                                                                  │
  │  repomind/ingest_repo          repomind/run_agent                │
  │    step 1: fetch-and-chunk       step 1: compress-history        │
  │    step 2: embed-and-store       step 2: query-rewrite           │
  │                                  step 3: llm-generate-N          │
  │  repomind/agent_completed        step 4: vector-search-N / tool  │
  │    step: compute-metrics                                         │
  └───────────────────────────┬──────────────────────────────────────┘
                              │  (all blocking I/O via asyncio.to_thread)
                              ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │                        agent.py                                  │
  │  1. query_rewrite(query, history) → compact search terms         │
  │  2. ReAct loop (max 6 steps):                                    │
  │       Qwen → parse "Action / Action Input" text                  │
  │       run_tool(name, args) → Observation                         │
  │       repeat until "Final Answer:" appears                       │
  │  3. return {answer, compressed_history, steps}                   │
  └──────────┬────────────────────────────┬───────────────────────────┘
             │ embed query                │ fetch raw file
             ▼ (Modal EMBED_BASE_URL)     ▼ (PyGithub)
  ┌──────────────────────┐   ┌────────────────────────┐
  │  ChromaDB            │   │  GitHub API            │
  │  ./chroma_db/        │   │  (GITHUB_TOKEN)        │
  │  owner_repo_ast      │   └────────────────────────┘
  │  owner_repo_naive    │
  └──────────────────────┘

  Side channels:
    logger.py       → agent_logs.jsonl  → eval/metrics.py → /logs page
    _TOOL_METRICS   → embed_ms / chroma_ms per call (ContextVar, thread-safe)
    eval/compare.py → Qwen-as-judge     → benchmark_results.json → /benchmarks
    Inngest Dev UI  → localhost:8288    ← every step trace, retry, event log
```

---

## Components

| File | What it does |
|---|---|
| `ingest.py` | Fetches repo via PyGithub, chunks every file (AST / heading / naive), embeds via Modal, upserts into ChromaDB. |
| `agent.py` | Text-based ReAct loop. Calls Qwen with `httpx`, parses `Action:` / `Action Input:` text, dispatches tools, loops until `Final Answer:`. |
| `tools.py` | Three tools: `vector_search` (embed → Chroma), `get_file` (raw GitHub), `get_recent_commits`. Formats results as plain text for the LLM. |
| `prompts.py` | `REACT_PROMPT_TEMPLATE`, `QUERY_REWRITE_PROMPT`, `COMPRESS_HISTORY_PROMPT`. |
| `server.py` | FastAPI + Inngest. Hosts `ingest_repo`, `run_agent`, `agent_completed` functions. All step handlers are `async def` + `asyncio.to_thread`. |
| `logger.py` | Appends one JSON line per event to `agent_logs.jsonl`. |
| `inngest_setup.py` | Shared Inngest client singleton. |
| `frontend/app/chat/page.tsx` | Chat UI. Per-collection queue. `contextRef` for compressed history. Framer-motion animations. |
| `frontend/components/Sidebar.tsx` | Resizable sidebar (160–400 px). Ingest form, AST/naive toggle, indexed repos list. |
| `frontend/lib/api.ts` | `triggerQuery`, `pollResult`, `fetchCollections`, `ingestRepo`. |
| `eval/compare.py` | AST vs naive benchmark (8 general queries). Retrieves top-3 chunks per query, asks Qwen to score 1–5. Writes `frontend/public/benchmark_results.json`. |
| `eval/compare1.py` | Python-targeted benchmark (10 queries designed to expose AST advantages: function boundaries, class headers, docstrings, retry logic). Same scoring method as `compare.py`. |
| `eval/inspect_chunks.py` | ChromaDB inspection tool — browse chunks by type, file, or keyword with `--text`, `--type`, `--file`, `--limit` flags. |
| `eval/metrics.py` | Reads `agent_logs.jsonl`, computes latency / token / cost stats. |

---

## Workflow 1 — Ingestion (ingest a repo)

```
User clicks "Ingest Repo"
        │
        ▼
POST /api/ingest  →  server.py fires repomind/ingest_repo event to Inngest
        │
        ├── Step 1: fetch-and-chunk  (ingest.fetch_and_chunk_repo)
        │     • PyGithub walks the repo tree
        │     • skips: node_modules / .git / dist / build / files > 500 KB
        │     • for every allowed file (.py .md .ts .tsx .js .jsx .txt
        │                               .dart .go .rs .java .kt .swift .rb .cs):
        │         .py  + ast mode  →  AST chunking   (see below)
        │         .md              →  heading chunks
        │         anything else    →  naive sliding window
        │     • writes all chunks to a temp JSONL:
        │         ./chroma_db/.chunks_<collection>_<event_id>.jsonl
        │
        └── Step 2: embed-and-store  (ingest.embed_and_store_chunks)
              • reads the temp JSONL line by line
              • embeds each chunk via openai SDK → Modal BAAI/bge-small-en-v1.5
              • upserts into ChromaDB collection  owner_repo_ast  or  owner_repo_naive
              • deletes the temp file in a finally block
```

**CLI shortcut** (no Inngest, same logic):
```bash
python ingest.py <owner>/<repo> <ast|naive>
```

---

## How chunking works

Every file is split into **chunks** — short text pieces that get embedded and stored in ChromaDB. The chunk boundaries determine retrieval quality. repomind uses three strategies chosen automatically by file type and mode.

---

### Strategy 1 — AST chunking  (`.py` files in `ast` mode)

#### What is an AST?

When Python loads a source file it first builds an **Abstract Syntax Tree** — a structured tree where every node is a named language construct: module, class, function, import, assignment, etc. Instead of blindly cutting at character counts, repomind walks this tree and uses the boundaries it already knows: where each function starts and ends, what its name is, and what its docstring says.

#### Step-by-step (current implementation)

```
source code string
       │
       ▼
Step 1 — parse
  ast.parse(source)
  → builds the full syntax tree
  → if SyntaxError: return [] and fall back to naive chunking

       │
       ▼
Step 2 — top-level scan  (ast.iter_child_nodes(tree))
  Only direct children of the module are visited here — NOT a deep flat walk.
  This prevents methods from appearing both inside the class chunk AND on their own.

       │
       ├─ FunctionDef / AsyncFunctionDef  ──────────────────────────┐
       │                                                             │
       │    Step 3a — extract function text                         │
       │      lines[node.lineno-1 : node.end_lineno]                │
       │      ast.get_docstring(node)                               │
       │                                                             │
       │    Step 3b — size check                                    │
       │      len(text) <= 2000 chars?                              │
       │        YES → one Chunk                                     │
       │        NO  → sub-chunk at line boundaries:                 │
       │              part0 = first 2000 chars (full header+body)   │
       │              part1+ = function header prepended + body     │
       │              each part: chunk_id …::part0, ::part1 …      │
       │                                                             ◄──┘
       │
       └─ ClassDef  ────────────────────────────────────────────────┐
                                                                     │
           Step 3c — class header chunk                             │
             find first_method_lineno                               │
             text = lines[class_start : first_method_lineno - 1]   │
             (class signature + docstring + class vars only —       │
              method bodies are NOT included here)                  │
                                                                     │
           Step 3d — method chunks                                  │
             for each FunctionDef / AsyncFunctionDef                │
             inside the class body:                                 │
               → same as Step 3a / 3b above                        │
                                                                    ◄┘
```

#### What a chunk looks like

```
Chunk(
  chunk_id = "src/auth.py::function::login::14"
  text     = "def login(self, user, password):\n
                  \"\"\"Validate credentials and return a JWT.\"\"\"\n
                  ...",
  metadata = {
    type       : "function",
    name       : "login",
    file_path  : "src/auth.py",
    line_start : 14,
    line_end   : 38,
    docstring  : "Validate credentials and return a JWT.",
    language   : "python",
  }
)
```

Sub-chunks of an oversized function add one extra field:
```
  chunk_id = "src/big.py::function::process::1::part2"
  metadata = { ..., part: 2 }
```

#### Concrete example

```python
# src/auth.py

class AuthService:                  ← class header chunk  (lines 1-7)
    """Handles all auth flows."""
    SECRET = "jwt-secret"

    def login(self, user):          ← function chunk  (lines 9-14)
        return generate_jwt(user)

    def logout(self, user):         ← function chunk  (lines 16-19)
        revoke_token(user)

def hash_password(pw):              ← function chunk  (lines 22-25)
    return bcrypt.hash(pw)
```

Produces **4 chunks**:

| chunk_id | type | lines | contains |
|---|---|---|---|
| `src/auth.py::class::AuthService::1` | class | 1–7 | class signature + docstring + `SECRET` — **no method bodies** |
| `src/auth.py::function::login::9` | function | 9–14 | full `login` method |
| `src/auth.py::function::logout::16` | function | 16–19 | full `logout` method |
| `src/auth.py::function::hash_password::22` | function | 22–25 | full top-level function |

---

### Strategy 2 — Heading-based chunking  (`.md` files)

```
README.md
  │
  ├─ text before first ##      →  preamble chunk  (if non-empty)
  ├─ ## Installation\n…        →  chunk 0  { heading: "Installation" }
  ├─ ## Usage\n…               →  chunk 1  { heading: "Usage" }
  └─ ## API Reference\n…       →  chunk 2  { heading: "API Reference" }
```

No H2 headings → whole file is one chunk.

---

### Strategy 3 — Naive sliding-window  (all other files + `.py` in `naive` mode)

```
source  (example: 6 000 chars)
  │
  window  = 2 000 chars
  overlap =   200 chars
  step    = 1 800 chars
  │
  ├─ chars    0 – 2000   →  chunk 0
  ├─ chars 1800 – 3800   →  chunk 1
  └─ chars 3600 – 5600   →  chunk 2
```

The 200-char overlap prevents code at a boundary from being split across two chunks with no context on either side.

---

### AST vs Naive — side-by-side

```
file: src/auth.py  (120 lines, 3 functions + 1 class)

── AST mode ─────────────────────────────────────────────────
  chunk 1  →  class AuthService header  (signature + class vars)
  chunk 2  →  def login(...)            complete, self-contained
  chunk 3  →  def logout(...)           complete, self-contained
  chunk 4  →  def hash_password(...)    complete, self-contained

  Each chunk has: name, line_start, line_end, docstring in metadata.

── Naive mode ───────────────────────────────────────────────
  chunk 0  →  chars 0–2000    (may end mid-function)
  chunk 1  →  chars 1800–3800 (may start in the middle of logout)
  chunk 2  →  chars 3600–5600 (may contain fragments of two functions)

  Each chunk has: chunk_index, language in metadata.  No name. No lines.
```

| | AST | Naive |
|---|---|---|
| Boundary | Language construct end | Fixed character count |
| Chunk content | Always a complete unit (function / class header / method) | Can start or end anywhere mid-logic |
| Metadata | `name`, `line_start`, `line_end`, `docstring` | `chunk_index` only |
| LLM citation quality | Can cite `login()` at `src/auth.py:9-14` | Can only cite file name |
| Works on | Python only (others fall back to naive) | Any text file |
| Oversized nodes | Sub-chunked with `::part0`, `::part1` labels + docstring surfaced in result header | No difference — all chunks are fixed-size anyway |

---

## How AST metadata flows from chunk to query answer

This is the full journey from storage to the LLM's final answer.

```
INGEST TIME
───────────
ingest.py  chunks src/auth.py
  → Chunk { text: "def login...", metadata: { name:"login", line_start:9, ... } }
  → embed(text) via Modal  →  384-dim vector
  → ChromaDB.upsert(id, vector, document=text, metadata)


QUERY TIME
──────────
User asks: "how does login work?"
  │
  ▼
Step 1 — query rewrite  (agent.py → Qwen)
  "how does login work?" → "login authentication JWT implementation"

  │
  ▼
Step 2 — vector_search  (tools.py)
  embed("login authentication JWT implementation") → query vector
  ChromaDB.query(query_vector, n_results=5)
  → returns docs + metadatas

  │
  ▼
Step 3 — format result  (tools.py vector_search output)
  "[1] function `login` in src/auth.py (lines 9-14)
   def login(self, user, password):
       ..."

  If it were a sub-chunk (part > 0):
  "[1] function `login` in src/auth.py (lines 9-14) [excerpt part 1]
       Docstring: Validate credentials and return a JWT.
   ..."

  │
  ▼
Step 4 — Qwen reads the result in the ReAct loop
  Prompt tells it:
    • cite file paths and line numbers  ← only possible because of AST metadata
    • if you see "[excerpt part N]", check the docstring; call get_file if needed
    • filter_type="function" to narrow to functions only

  │
  ▼
Step 5 — Final Answer
  "The `login` function in `src/auth.py` (lines 9-14) validates credentials
   and returns a JWT token. It calls `generate_jwt(user)` after..."
```

Without AST metadata the answer would be: *"In the file src/auth.py there is some authentication code"* — no line numbers, no function name, no ability to say *which* part of the file.

---

## Workflow 2 — Query (ask a question)

```
User types a question in /chat
        │
        ▼
POST /api/query  { query, collection_name, history[] }
  → server.py enqueues repomind/run_agent, returns { event_id }
  → frontend polls GET /api/result/{event_id} every second

        │ Inngest runs:
        │
        ├── Step 1: compress-history
        │     total chars of history > 12 000?
        │       YES → keep last 4 message pairs verbatim,
        │             summarize the rest with Qwen (COMPRESS_HISTORY_PROMPT)
        │       NO  → pass history through unchanged
        │
        ├── Step 2: query-rewrite
        │     Qwen rewrites the question into compact search terms
        │     uses history context to resolve "it" / "that" / "above"
        │
        └── Steps 3…N: ReAct loop
              Qwen generates:
                Thought: …
                Action: vector_search
                Action Input: {"query": "login JWT", "filter_type": "function"}
              tools.py runs vector_search → returns formatted result
              Qwen reads Observation, decides next action
              … repeats until "Final Answer:" or max_steps (6) reached

        │
        ▼
result cached in _RESULT_CACHE[event_id]
frontend poll returns { answer, compressed_history, steps, session_id }
frontend stores compressed_history in contextRef[collection] for next turn
```

---

## Workflow 3 — Evaluation

| Script | What it does | Output |
|---|---|---|
| `eval/compare.py <owner>/<repo>` | 8 general queries. Retrieves top-3 chunks from both `_ast` and `_naive` collections, asks Qwen to score each 1–5 for relevance. | `frontend/public/benchmark_results.json` |
| `eval/compare1.py <owner>/<repo>` | 10 Python-targeted queries designed to expose AST advantages over naive. Same scoring method. | `frontend/public/benchmark_results.json` |
| `eval/inspect_chunks.py` | Inspect ChromaDB chunks with `--text`, `--type`, `--file`, `--limit` flags. Useful for verifying chunking output without running the full agent. | printed to stdout |
| `eval/metrics.py` | Reads `agent_logs.jsonl`, computes avg / median / p95 latency, total tokens, cost. | printed to stdout |
| `eval/test_queries.py` | Runs 5 fixed queries through the agent, checks answers for must-contain / must-not-contain keywords. | `eval_results.jsonl` |

---

## Data stores

| Path | Written by | Read by | Gitignored |
|---|---|---|---|
| `./chroma_db/` | `ingest.embed_and_store_chunks` | `tools.vector_search`, `eval/compare.py` | ✅ |
| `./chroma_db/.chunks_*.jsonl` | `ingest.fetch_and_chunk_repo` | `ingest.embed_and_store_chunks` | ✅ temp, auto-deleted |
| `agent_logs.jsonl` | `logger.log_step` | `eval/metrics.py`, `/logs` page | ✅ |
| `eval_results.jsonl` | `eval/test_queries.py` | `/benchmarks` page | ✅ |
| `frontend/public/benchmark_results.json` | `eval/compare.py` | `/benchmarks` page | ✅ |
| `.env` | user | all modules | ✅ |

---

## Key design decisions

**Text-based ReAct, not OpenAI function calling.**
Qwen is served as a raw text-completion endpoint — no `tools=` support. The agent parses `Action:` / `Action Input:` from the model's text output manually.

**Tool-call-mediated retrieval.**
The model decides when to search, when to read a file, and when it has enough. No fixed "embed → retrieve → answer" pipeline.

**Embeddings via Modal, not local.**
All embedding calls go through `EMBED_BASE_URL` (rag-learning's Modal deployment, `BAAI/bge-small-en-v1.5`). No Ollama anywhere.

**All Inngest steps are `async def` + `asyncio.to_thread`.**
The Inngest Python SDK calls step handlers directly on the asyncio event loop. Any blocking I/O (httpx, chromadb) in a sync handler starves uvicorn's request handling. Every blocking call is wrapped with `asyncio.to_thread`.

**Stateless backend, stateful frontend history.**
The backend receives `history[]` per-request, optionally compresses it, and returns `compressed_history`. No server-side session state. `contextRef` in `chat/page.tsx` holds the compressed history between turns.

**Collections versioned by chunking mode.**
`owner_repo_ast` and `owner_repo_naive` live side by side in ChromaDB — this is what makes the benchmark possible without re-ingesting.

---

## Challenges & lessons learned

Ordered by depth of technical insight — core design decisions and retrieval quality issues first, infrastructure and UI issues last.

---

### 1. Sub-chunk overlap was the wrong concept for AST

**Problem:** When a function exceeded 2000 chars, `_sub_chunk` split it using the same overlapping sliding window used for naive chunking. Part1 started mid-function with zero context:

```python
# part0 — has signature and first half
def authenticate(self, user, password):
    """Validate credentials"""
    if not user:
        ...

# part1 — starts mid-function, no context
        token = generate_token()
        return token
```

The LLM sees part1 and has no idea which function it's in, what it does, or what came before.

**Why overlap is wrong for AST:** Overlap is designed for naive chunking where chunks are arbitrary cuts of continuous text — you repeat a few chars so neither side loses context at the cut point. For AST sub-chunks the context is not at the character boundary — it's the function signature and docstring at the top of the function. Overlapping random body lines doesn't help.

**Fix:** Every continuation part now prepends the function header (decorators + signature + docstring), computed from AST line info:

```python
# part0 — starts normally
def authenticate(self, user, password):
    """Validate credentials"""
    if not user:
        ...

# part1 — header prepended, self-contained
def authenticate(self, user, password):   # <- prepended
    """Validate credentials"""            # <- prepended
    # ... continued ...
        token = generate_token()
        return token
```

Header is extracted using `node.lineno` (first decorator or `def` line) through `first_stmt.end_lineno` (last line of docstring, if present). This correctly handles decorated functions and multi-line signatures without re-parsing the text.

The trade-off: the header costs ~200 chars per continuation part, leaving ~1800 chars of body instead of 2000. That's acceptable — unreadable fragments are far worse.

---

### 2. Vector search retrieved only some parts of a split function

**Problem:** When a large function is split into part0, part1, part2, `vector_search` returns top-N chunks by similarity score. It might return part2 (score 0.4) and skip part0 and part1 entirely — the LLM sees the middle of a function with no signature, no docstring, no beginning.

The header-prepend fix (challenge 1) partially helped — at least every part has the function name. But the body is still incomplete: the LLM sees part2's content without knowing what part0 set up.

**Fix — sibling-part expansion in `vector_search`:** After retrieving top-N chunks, check if any result has `part` in its metadata. If so, query ChromaDB for all chunks sharing the same `type + name + file_path + line_start` and merge them into the result. The LLM receives all parts of the function in order, not just the one that scored highest.

```
Before: query returns part2 only  (scored 0.4)
After:  query returns part0 + part1 + part2  (+2 sibling parts)
```

The result header shows `Found 5 results (+2 sibling parts)` so the LLM knows the expansion happened. Sibling fetch is best-effort — a failure doesn't break the query.

---

### 3. `ast.walk` caused method bodies to appear twice

**Problem:** The original AST implementation used `ast.walk(tree)` — a flat traversal that visits every node at every depth. A class with 3 methods produced 4 chunks, but each method's code appeared in *both* the class chunk (full class body) and the method's own chunk.

**Root cause:** `ast.walk` doesn't distinguish between top-level nodes and nested ones.

**Fix:** Replaced with `ast.iter_child_nodes(tree)` for the top-level scan. For each `ClassDef`, the class chunk now contains only the class header (signature + docstring + class-level variables) — method bodies are stripped. Methods are extracted separately by iterating `ast.iter_child_nodes(class_node)`.

**Lesson:** Use `ast.iter_child_nodes` when you want one specific level of the tree. Use `ast.walk` only when you genuinely need every node at every depth.

---

### 4. Why AST chunking only works for Python

**Question:** Why does AST mode produce `function`/`class` chunks for Python repos but not for Dart/Flutter repos?

**Answer:** Python ships a built-in `ast` module in the standard library — one import, zero extra dependencies, gives a full syntax tree with exact line numbers for every function and class. Dart has no equivalent accessible from Python. Parsing Dart properly requires either `tree-sitter` (a C library with Python bindings, adds a native dependency) or regex heuristics (fragile, not a real parser). So the code does:

```python
if ext == ".py" and mode == "ast":
    chunks = extract_python_ast_chunks(content, path)
# everything else -> naive sliding window
```

For non-Python repos (Dart, Go, TS, etc.) both AST and naive mode produce identical chunks. The benchmark will always tie on those repos — the difference only shows up on Python codebases.

**How DeepWiki and similar tools solve this:** They use `tree-sitter`, which supports 40+ languages with a single consistent API. Each language has a `.so` grammar file; one Python call gives an AST for any of them. The cost is a native C dependency and ~50 MB of grammar files.

---

### 5. Oversized functions broke embedding quality

**Problem:** A 400-line function became one 6 000-char chunk. Embedding models compress long text poorly — the vector loses specificity and retrieval quality drops.

**Fix:** After extracting a function or method, if `len(text) > 2000`, split at line boundaries into parts (`::part0`, `::part1`, etc.). The `part` index is stored in metadata. When `vector_search` returns a sub-chunk it labels it `[excerpt part N]` and surfaces the docstring so the LLM can assess whether it needs the full file.

---

### 6. Chunking parameters: window, overlap, and how they differ between AST and naive

```
CHUNK_CHARS   = 2000   # max characters per chunk
CHUNK_OVERLAP = 200    # characters of overlap
```

**Naive sliding window** uses both constants:
- Window: 2000 chars
- Step: 1800 chars (CHUNK_CHARS - CHUNK_OVERLAP)
- Overlap: 200 chars — the last 200 chars of chunk N reappear as the first 200 chars of chunk N+1
- Cuts raw characters; no awareness of line or statement boundaries

**AST sub-chunking ignores CHUNK_OVERLAP entirely:**
- Part 0: function start up to 2000 chars, stops at a line boundary
- Part 1+: `header + "# ... continued ..." + next (2000 - len(header)) chars of body`
- No overlap; the header provides context instead

| | Naive | AST sub-chunks |
|---|---|---|
| Window | 2000 chars | 2000 chars |
| Step | 1800 chars (uses CHUNK_OVERLAP) | variable (lines consumed) |
| Overlap | 200 chars of raw text | none |
| Context strategy | trailing chars from previous chunk | header prepend per continuation part |
| Boundary aware | no (character) | yes (line) |

`CHUNK_OVERLAP` is defined at module level but only read by `naive_chunk`. All AST sub-chunk logic ignores it.

---

### 7. End-to-end correctness of AST chunking

After tracing through all four functions in the call chain:

**`extract_python_ast_chunks`** — uses `ast.iter_child_nodes` (not `ast.walk`), so nested functions inside methods are not double-counted. Top-level functions and class methods each go through `_ast_function_chunks`. Class headers go through `_ast_class_header_chunk`. Correct.

**`_ast_function_chunks`** — extracts `text` using `lines[node.lineno-1 : node.end_lineno]`. For small functions (<=2000 chars), returns a single chunk. For large ones, computes the header from AST line info (handles decorators and multi-line signatures correctly) and passes it to `_sub_chunk`. Correct after the header-passing fix.

**`_ast_class_header_chunk`** — finds `first_method_lineno` via `iter_child_nodes`, slices text to just before it. Strips trailing blank lines. Result: class signature + docstring + class-level variables only, no method bodies. Correct. One known gap: if the class header itself exceeds 2000 chars it is not sub-chunked — non-issue in practice since class headers are almost never that long.

**`_sub_chunk`** — part 0 fills up to 2000 chars stopping at a line boundary. Continuation parts start `char_count` at `len(header)` so body content per part is `2000 - len(header)` chars. `start_idx` advances by `len(window_lines)` (lines consumed, not characters). Safety valve: if a single line exceeds 2000 chars the `window_lines` empty-check prevents an infinite loop. Correct.

---

### 8. AST metadata was stored but never shown to the LLM

**Problem:** `name`, `line_start`, `line_end`, `docstring` were all stored in ChromaDB metadata but `vector_search` only returned the raw chunk text. The metadata existed but the LLM never saw it.

**Fix:** `vector_search` now formats a header per result:
```
[1] function `login` in src/auth.py (lines 9-14)
```
Sub-chunks also show `[excerpt part N]` and the docstring. The ReAct prompt tells the agent to cite file:line and explains when to call `get_file` for the full body.

---

### 9. Conversation history required a stateless compression contract

**Problem:** The backend is stateless per-request. Passing full history every turn causes context bloat at 16 K+ chars.

**Fix:** Lazy compression at 12 K chars (75% of limit). Keep last 4 message pairs verbatim; summarize older messages with one Qwen call. Backend returns `compressed_history` in every response. Frontend stores it in `contextRef[collection]` and sends it back next turn.

---

### 10. Inngest sync handlers block the asyncio event loop

**Problem:** The frontend poll returned 500 even though Inngest Dev UI showed the function completing.

**Root cause:** The Inngest Python SDK's `step_async.py` calls handlers via `maybe_await(handler(*args))` directly on the event loop. A blocking sync handler (httpx, chromadb) holds the thread, starving uvicorn.

**Fix:** All step handlers converted to `async def`. Every blocking call wrapped with `asyncio.to_thread(fn, ...)`.

**Lesson:** Inngest doesn't warn you. Any sync I/O in an async handler is a silent blocker.

---

### 11. Benchmark used the wrong embedding backend

**Problem:** `eval/compare.py` called `ollama.embeddings()` while the rest of the project had moved to Modal. Benchmark retrieval was in a completely different vector space from the indexed collections — results were meaningless.

**Fix:** Replaced with `openai.OpenAI(base_url=EMBED_BASE_URL)` — identical to `tools.py`. Output path moved from project root to `frontend/public/benchmark_results.json` so the `/benchmarks` page can read it.

---

### 12. Tiny tail fragments from line-boundary sub-chunking

**Problem:** After switching `_sub_chunk` to line-boundary windowing, the tail of large functions produced useless micro-chunks:

```
part3  ->  131 chars  ("retry_delay=...  log_level=...  )")
part4  ->   70 chars  ("log_level=...  )")
part5  ->    9 chars  ("        )")   <- just a closing paren
```

**Root cause:** With short tail lines (~50 chars each), the overlap logic backed up by 1 line per iteration — advancing `start_idx` by only 1 line at a time through the last few lines, creating a new chunk for each.

**Fix:** Skip any chunk after the first whose stripped content is shorter than `CHUNK_OVERLAP` chars — it's already fully covered by the previous chunk's window and adds no new information.

---

### 13. Per-collection message queuing

**Problem:** Sending a second question before the first finishes produces incoherent compressed history.

**Fix:** `inFlightRef` + `queuesRef` (useRef, not useState) in the chat page. Same-collection queries queue; different-collection queries run in parallel. Queue drains in `executeQuery`'s `finally` block.

---

### 14. Sidebar drag required document-level mouse listeners

**Problem:** Fast mouse movement escaped the 4 px drag handle, breaking the gesture.

**Fix:** `mousedown` on the handle sets `isDragging`; `useEffect` attaches `mousemove` and `mouseup` to `document` (not the element) for the duration of the drag.

---

### 15. Sidebar scroll required `min-h-0` on the flex child

**Problem:** `overflow-y-auto` on the collections list had no effect.

**Root cause:** Flex children won't shrink below their content height unless `min-height: 0` is set explicitly.

**Fix:** Collections section gets `flex-1 min-h-0 overflow-y-auto`. Sidebar `<aside>` gets `h-full overflow-hidden`.
