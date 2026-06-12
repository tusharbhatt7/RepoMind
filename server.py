"""FastAPI + Inngest background job server for repomind.

Two Inngest functions:
  repomind/ingest_repo  — fetch-and-chunk → validate-chunks →
                          embed-and-store → log-summary
  repomind/run_agent    — query-rewrite → llm-generate-N →
                          vector-search-N / <tool>-N → ... →
                          check-anomalies → log-summary

Both Streamlit (via POST /api/query + poll) and direct API calls go through
the same Inngest functions, so every run has full per-step visibility in the
Inngest Dev UI.

REST endpoints:
  POST /api/ingest             — trigger a repo ingest job
  POST /api/query              — trigger an agent run, returns event_id
  GET  /api/result/{event_id}  — poll for the agent result

Run with:
    uvicorn server:app --reload --port 8000

Then start the Inngest Dev Server in another terminal:
    npx inngest-cli@latest dev -u http://localhost:8000/api/inngest

Inngest Dev UI is available at http://localhost:8288
"""
from __future__ import annotations

import logging
import os
import uuid

import inngest
import inngest.fast_api
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import chromadb

from auth import (
    SHARED_TENANT,
    TENANT_SEP,
    belongs_to,
    clear_ingest_cancelled,
    get_embed_provider,
    mark_ingest_cancelled,
    qualify_collection,
    set_embed_api_key_override,
    set_embed_model_override,
    set_embed_provider_override,
    set_github_token_override,
    set_llm_api_key_override,
    set_llm_model_override,
    set_llm_provider_override,
    set_tenant_id_override,
    set_vllm_api_key_override,
    strip_collection,
    strip_tenant,
)
from eval.metrics import compute_aggregate_metrics
from ingest import embed_and_store_chunks, fetch_and_chunk_repo
from inngest_setup import inngest_client
from logger import get_recent_logs, get_session_logs
from prompts import COMPRESS_HISTORY_PROMPT

logger = logging.getLogger("uvicorn")

# In-memory result cache keyed by event_id (used as session_id in run_agent_fn).
_RESULT_CACHE: dict[str, dict] = {}

# In-memory ingest tracker — keyed by QUALIFIED collection name. The status
# endpoint filters by tenant on read. Phase moves through:
#   fetching → embedding → done / error
# "done" / "error" entries linger for _INGEST_TTL_SECONDS so the UI can show
# the final state once, then are garbage-collected on the next read.
_INGEST_STATUS: dict[str, dict] = {}
_INGEST_TTL_SECONDS = 300


def _set_ingest_status(qualified_name: str, **fields) -> None:
    import time as _t
    entry = _INGEST_STATUS.get(qualified_name, {"started_at": _t.time()})
    entry.update(fields)
    _INGEST_STATUS[qualified_name] = entry


def _cleanup_ingest_status() -> None:
    import time as _t
    now = _t.time()
    for k in list(_INGEST_STATUS):
        v = _INGEST_STATUS[k]
        finished = v.get("finished_at")
        if v.get("phase") in ("done", "error") and finished and now - finished > _INGEST_TTL_SECONDS:
            del _INGEST_STATUS[k]

# ─── History compression constants ──────────────────────────────────────────
_HISTORY_COMPRESS_THRESHOLD = 12_000   # chars — trigger compression above this
_HISTORY_CHAR_LIMIT = 16_000           # hard cap sent from frontend
_HISTORY_KEEP_RECENT = 4               # message pairs kept verbatim after compress


def _total_history_chars(history: list[dict]) -> int:
    return sum(len(m.get("content", "")) for m in history)


def _format_history_block(history: list[dict]) -> str:
    """Format history as 'User: ...\nAssistant: ...' text for LLM consumption."""
    lines = []
    for m in history:
        role = "User" if m.get("role") == "user" else "Assistant"
        content = m.get("content", "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


# ─── Inngest function 1: ingest repo ────────────────────────────────────────

@inngest_client.create_function(
    fn_id="repomind-ingest-repo",
    trigger=inngest.TriggerEvent(event="repomind/ingest_repo"),
)
async def ingest_repo_fn(ctx: inngest.Context) -> dict:
    """Fetch a GitHub repo, chunk, embed, and store in ChromaDB.

    Steps:
      fetch-and-chunk  — walk repo, apply AST/naive chunking, write temp JSONL
      validate-chunks  — flag empty result before spending time on embeddings
      embed-and-store  — embed every chunk via Modal, upsert ChromaDB
      log-summary      — structured log with totals, errors, and latency
    """
    import time as _t
    repo_slug: str = ctx.event.data["repo"]
    mode: str = ctx.event.data.get("mode", "ast")
    event_id: str = ctx.event.id
    tenant_id: str = ctx.event.data.get("tenant_id") or SHARED_TENANT

    # User-supplied overrides from the dashboard Settings page (X-* headers
    # plumbed through Inngest event data). Each falls back to its env var.
    set_github_token_override(ctx.event.data.get("github_token"))
    set_vllm_api_key_override(ctx.event.data.get("vllm_api_key"))
    set_llm_provider_override(ctx.event.data.get("llm_provider"))
    set_llm_api_key_override(ctx.event.data.get("llm_api_key"))
    set_llm_model_override(ctx.event.data.get("llm_model"))
    set_embed_provider_override(ctx.event.data.get("embed_provider"))
    set_embed_api_key_override(ctx.event.data.get("embed_api_key"))
    set_embed_model_override(ctx.event.data.get("embed_model"))
    set_tenant_id_override(tenant_id)

    embed_provider = get_embed_provider()

    # Precompute the qualified collection name so the sidebar UI can poll for
    # progress under the same key the agent will later read.
    owner_repo = repo_slug.replace("/", "_") if "/" in repo_slug else repo_slug
    qualified_name = qualify_collection(
        f"{owner_repo}_{mode}", tenant_id, embed_provider
    )
    # If the user previously cancelled this collection and is now re-ingesting,
    # wipe the stale cancellation marker so this run isn't aborted at start.
    clear_ingest_cancelled(qualified_name)
    _set_ingest_status(
        qualified_name,
        tenant_id=tenant_id,
        repo=repo_slug,
        mode=mode,
        phase="fetching",
        files_seen=0,
        total_chunks=0,
        embed_errors=0,
        error=None,
        finished_at=None,
    )

    try:
        # Step 1: fetch and chunk — async so PyGithub HTTP calls don't block the loop
        async def _fetch_and_chunk() -> dict:
            import asyncio as _aio
            return await _aio.to_thread(
                fetch_and_chunk_repo, repo_slug, mode, event_id, tenant_id, embed_provider
            )

        chunks_data = await ctx.step.run("fetch-and-chunk", _fetch_and_chunk)
        _set_ingest_status(
            qualified_name,
            phase="embedding",
            files_seen=chunks_data.get("files_seen", 0),
            total_chunks=chunks_data.get("total_chunks", 0),
        )

        # Step 2: validate before embedding
        def _validate(d: dict) -> dict:
            issues = []
            if d.get("files_seen", 0) == 0:
                issues.append("no_files_found")
            if d.get("total_chunks", 0) == 0:
                issues.append("no_chunks_produced")
            if issues:
                logger.warning(
                    "repomind/ingest_repo validate: repo=%s issues=%s",
                    repo_slug, issues,
                )
            return {"valid": len(issues) == 0, "issues": issues}

        validated = await ctx.step.run("validate-chunks", lambda: _validate(chunks_data))

        # Step 3: embed and store — async so Modal embed API calls don't block the loop
        async def _embed_and_store() -> dict:
            import asyncio as _aio
            return await _aio.to_thread(embed_and_store_chunks, chunks_data)

        result = await ctx.step.run("embed-and-store", _embed_and_store)

        # Step 4: log summary
        def _log_summary(r: dict, v: dict) -> dict:
            embed_errors = r.get("embed_errors", 0)
            msg = (
                f"repomind/ingest_repo done: repo={repo_slug} "
                f"collection={r['collection_name']} chunks={r['total_chunks']} "
                f"files={r.get('files_seen', 0)} embed_errors={embed_errors}"
            )
            if v["issues"] or embed_errors > 0:
                logger.warning(msg + f" issues={v['issues']}")
            else:
                logger.info(msg)
            return {"logged": True}

        await ctx.step.run("log-summary", lambda: _log_summary(result, validated))
        _set_ingest_status(
            qualified_name,
            phase="done",
            finished_at=_t.time(),
            total_chunks=result.get("total_chunks", 0),
            embed_errors=result.get("embed_errors", 0),
        )
        return result
    except Exception as exc:
        # IngestCancelled = user clicked delete mid-flight — drop the status
        # entirely rather than show a red "error" line for a deletion they asked
        # for. Other exceptions = real failure; surface to the sidebar UI.
        from ingest import IngestCancelled
        if isinstance(exc, IngestCancelled):
            _INGEST_STATUS.pop(qualified_name, None)
            logger.info("repomind/ingest_repo cancelled: %s", qualified_name)
            return {"cancelled": True}
        _set_ingest_status(
            qualified_name,
            phase="error",
            finished_at=_t.time(),
            error=str(exc)[:200],
        )
        raise


# ─── Inngest function 2: agent run ──────────────────────────────────────────

@inngest_client.create_function(
    fn_id="repomind-run-agent",
    trigger=inngest.TriggerEvent(event="repomind/run_agent"),
)
async def run_agent_fn(ctx: inngest.Context) -> dict:
    """ReAct loop driven step-by-step — each LLM call and tool call is a
    separate timed checkpoint in the Inngest Dev UI.

    Steps:
      query-rewrite       — compact semantic-search rewrite of the user query
      llm-generate-N      — Qwen generates Thought + Action (or Final Answer)
      vector-search-N /   — tool execution; embed_ms + chroma_ms captured
        <tool>-N
      check-anomalies     — flag max_steps_reached, no_action, high latency
      log-summary         — structured log with steps, latency, stop reason
    """
    import time
    from agent import _generate, _parse_action, query_rewrite
    from logger import log_step
    from prompts import REACT_PROMPT_TEMPLATE
    from tools import _TOOL_METRICS, run_tool

    query: str = ctx.event.data["query"]
    collection_name: str = ctx.event.data["collection_name"]
    # session_id is generated by /api/query and passed in event data so the
    # caller can poll /api/result/{session_id} without depending on Inngest's
    # internal event ID format.
    session_id: str = ctx.event.data.get("session_id") or ctx.event.id
    history: list[dict] = ctx.event.data.get("history", [])
    tenant_id: str = ctx.event.data.get("tenant_id") or SHARED_TENANT
    run_start = time.time()

    # User-supplied overrides from the dashboard Settings page — fall back to env.
    set_github_token_override(ctx.event.data.get("github_token"))
    set_vllm_api_key_override(ctx.event.data.get("vllm_api_key"))
    set_llm_provider_override(ctx.event.data.get("llm_provider"))
    set_llm_api_key_override(ctx.event.data.get("llm_api_key"))
    set_llm_model_override(ctx.event.data.get("llm_model"))
    set_embed_provider_override(ctx.event.data.get("embed_provider"))
    set_embed_api_key_override(ctx.event.data.get("embed_api_key"))
    set_embed_model_override(ctx.event.data.get("embed_model"))
    set_tenant_id_override(tenant_id)

    # Step: compress-history — always runs so Inngest replay order is stable.
    # Only makes an LLM call when total history chars exceed the threshold.
    async def _compress_history() -> dict:
        import asyncio as _aio
        total = _total_history_chars(history)
        if total <= _HISTORY_COMPRESS_THRESHOLD or not history:
            return {"history": history, "compressed": False, "total_chars": total}

        # Keep the last KEEP_RECENT pairs verbatim, summarise everything older.
        keep = _HISTORY_KEEP_RECENT * 2
        recent = history[-keep:]
        old = history[:-keep]
        if not old:
            return {"history": history, "compressed": False, "total_chars": total}

        old_text = _format_history_block(old)
        summary = await _aio.to_thread(
            _generate,
            COMPRESS_HISTORY_PROMPT.format(history_text=old_text),
            200,
            0.1,
        )
        compressed = [
            {"role": "assistant", "content": f"[Summary of earlier conversation]: {summary.strip()}"}
        ] + recent
        log_step(session_id, 0, "history_compressed", {
            "original_chars": total,
            "compressed_chars": _total_history_chars(compressed),
            "messages_removed": len(old),
        })
        return {"history": compressed, "compressed": True, "total_chars": _total_history_chars(compressed)}

    compress_result: dict = await ctx.step.run("compress-history", _compress_history)
    effective_history: list[dict] = compress_result["history"]

    # Build the history block injected into the ReAct prompt and the rewrite prompt.
    history_text = _format_history_block(effective_history)
    history_block = (
        f"\nConversation history:\n{history_text}\n"
        if history_text else ""
    )
    # Use only the last 4 messages for the rewrite context (sufficient for reference resolution).
    rewrite_context = _format_history_block(effective_history[-4:]) if effective_history else ""

    # Step: query-rewrite — async so the blocking httpx.post doesn't freeze the
    # event loop; log_step runs inside so it only fires once (not on replays).
    async def _query_rewrite() -> str:
        import asyncio as _aio
        import time as _t
        t = _t.time()
        result = await _aio.to_thread(query_rewrite, query, rewrite_context)
        log_step(session_id, 0, "query_rewrite", {
            "original": query,
            "rewritten": result,
            "latency_s": round(_t.time() - t, 2),
        })
        return result

    rewritten: str = await ctx.step.run("query-rewrite", _query_rewrite)

    scratchpad = ""
    answer = "Could not find a complete answer after max steps."
    stop_reason = "max_steps_reached"
    total_embed_ms = 0
    total_chroma_ms = 0
    step_num = 0

    for step_num in range(1, 7):
        prompt = REACT_PROMPT_TEMPLATE.format(
            question=query,
            rewritten=rewritten,
            scratchpad=scratchpad,
            history_block=history_block,
        )

        # Step: llm-generate-N — async to avoid blocking the event loop
        async def _llm_generate(p: str = prompt) -> str:
            import asyncio as _aio
            return await _aio.to_thread(_generate, p, 1000, 0.2)

        raw: str = await ctx.step.run(f"llm-generate-{step_num}", _llm_generate)

        if "Final Answer:" in raw:
            answer = raw.split("Final Answer:", 1)[-1].strip()
            _REACT_TOKENS = ("Thought:", "Action:", "Action Input:", "Observation:")
            answer = "\n".join(
                line for line in answer.splitlines()
                if not any(line.strip().startswith(tok) for tok in _REACT_TOKENS)
            ).strip()
            stop_reason = "final_answer"
            _ans, _sn = answer, step_num
            await ctx.step.run(
                f"log-final-answer-{step_num}",
                lambda: log_step(session_id, _sn, "final_answer", {
                    "answer": _ans,
                    "total_latency_s": round(time.time() - run_start, 2),
                    "total_steps": _sn,
                }) or {},
            )
            break

        parsed = _parse_action(raw)
        if parsed is None:
            answer = raw
            stop_reason = "no_action"
            _sn = step_num
            await ctx.step.run(
                f"log-unexpected-stop-{step_num}",
                lambda: log_step(session_id, _sn, "unexpected_stop", {}) or {},
            )
            break

        tool_name, args = parsed
        step_label = (
            f"vector-search-{step_num}" if tool_name == "vector_search"
            else f"{tool_name}-{step_num}"
        )

        # Tool step: async so embed + chroma calls don't block the event loop.
        # log_step calls are inside so they only fire once across Inngest replays.
        async def _run_tool(tn: str = tool_name, a: str = args, sn: int = step_num) -> dict:
            import asyncio as _aio
            import time as _t
            log_step(session_id, sn, "tool_call", {"tool": tn, "args": a})
            _TOOL_METRICS.set({})
            t = _t.time()
            res = await _aio.to_thread(run_tool, tn, a, collection_name)
            tool_latency = round(_t.time() - t, 2)
            m = _TOOL_METRICS.get({})
            result_str = str(res)
            log_step(session_id, sn, "tool_result", {
                "tool": tn,
                "result_preview": result_str[:200],
                "result_chars": len(result_str),
                "tool_latency_s": tool_latency,
                "embed_ms": m.get("embed_ms"),
                "chroma_ms": m.get("chroma_ms"),
            })
            return {
                "result": result_str,
                "embed_ms": m.get("embed_ms"),
                "chroma_ms": m.get("chroma_ms"),
            }

        # Step: vector-search-N / <tool>-N
        step_out: dict = await ctx.step.run(step_label, _run_tool)
        total_embed_ms += step_out.get("embed_ms") or 0
        total_chroma_ms += step_out.get("chroma_ms") or 0
        scratchpad += raw + f"\nObservation: {step_out['result']}\n\n"

    total_latency_s = round(time.time() - run_start, 2)

    if stop_reason == "max_steps_reached":
        _sn = step_num
        await ctx.step.run(
            "log-max-steps",
            lambda: log_step(session_id, _sn, "max_steps_reached", {
                "total_latency_s": total_latency_s,
            }) or {},
        )

    # Step: check-anomalies
    def _check_anomalies(sr: str, steps: int, latency: float) -> dict:
        flags = []
        if sr in ("max_steps_reached", "no_action"):
            flags.append(f"incomplete_run:{sr}")
        if latency > 120:
            flags.append(f"high_latency:{latency}s")
        return {"flags": flags, "flagged": len(flags) > 0}

    anomalies = await ctx.step.run(
        "check-anomalies",
        lambda: _check_anomalies(stop_reason, step_num, total_latency_s),
    )

    # Step: log-summary
    def _log_summary(sid: str, steps: int, latency: float, sr: str,
                     emb: int, chro: int, flags: list) -> dict:
        msg = (
            f"repomind/run_agent done: session={sid[:8]} steps={steps} "
            f"latency={latency}s stop={sr} "
            f"embed_ms={emb or None} chroma_ms={chro or None}"
        )
        if flags:
            logger.warning(msg + f" flags={flags}")
        else:
            logger.info(msg)
        return {"logged": True}

    await ctx.step.run(
        "log-summary",
        lambda: _log_summary(
            session_id, step_num, total_latency_s, stop_reason,
            total_embed_ms, total_chroma_ms, anomalies["flags"],
        ),
    )

    result = {
        "session_id": session_id,
        "answer": answer,
        "steps": step_num,
        "stop_reason": stop_reason,
        "total_latency_s": total_latency_s,
        "embed_ms": total_embed_ms or None,
        "chroma_ms": total_chroma_ms or None,
        # Return the (possibly compressed) history so the frontend can use it
        # as the context base for the next message in this conversation.
        "compressed_history": effective_history,
    }
    # Stamp the tenant on the cached result so /api/result/{id} can refuse to
    # serve another tenant's session_id if it gets guessed/leaked.
    _RESULT_CACHE[session_id] = {"tenant_id": tenant_id, "result": result}
    return result


# ─── FastAPI app ─────────────────────────────────────────────────────────────

app = FastAPI(title="repomind server")

# CORS — let the deployed frontend (Vercel) call this backend cross-origin.
# CORS_ORIGINS is a comma-separated list, e.g. "https://repomind.vercel.app,https://repomind-foo.vercel.app".
# Default "*" is permissive for local/dev; set explicitly in production.
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

inngest.fast_api.serve(app, inngest_client, [ingest_repo_fn, run_agent_fn])


class IngestRequest(BaseModel):
    repo: str
    mode: str = "ast"


class QueryRequest(BaseModel):
    query: str
    collection_name: str
    history: list[dict] = []


def _extract_user_github_token(request: Request) -> str | None:
    """Per-request PAT override from the dashboard Settings page. None if absent."""
    tok = (request.headers.get("X-Github-Token") or "").strip()
    return tok or None


def _extract_user_vllm_key(request: Request) -> str | None:
    """Per-request LLM/embeddings Bearer key override (X-VLLM-Key). None if absent."""
    key = (request.headers.get("X-VLLM-Key") or "").strip()
    return key or None


def _extract_llm_provider(request: Request) -> str | None:
    """Selected text-generation provider (X-LLM-Provider). One of
    ``anthropic`` / ``openai`` / ``gemini`` / ``vllm``. None falls back to ``vllm``."""
    p = (request.headers.get("X-LLM-Provider") or "").strip().lower()
    return p or None


def _extract_llm_api_key(request: Request) -> str | None:
    """Provider-specific API key (X-LLM-Key) for non-vllm providers. None if absent."""
    k = (request.headers.get("X-LLM-Key") or "").strip()
    return k or None


def _extract_llm_model(request: Request) -> str | None:
    """Optional model override (X-LLM-Model). None if absent → provider default."""
    m = (request.headers.get("X-LLM-Model") or "").strip()
    return m or None


def _extract_embed_provider(request: Request) -> str | None:
    """Selected embedding provider (X-Embed-Provider). One of
    ``vllm`` / ``openai`` / ``gemini``. None falls back to ``vllm``."""
    p = (request.headers.get("X-Embed-Provider") or "").strip().lower()
    return p or None


def _extract_embed_api_key(request: Request) -> str | None:
    """Provider-specific embedding key (X-Embed-Key). Only used for non-vllm."""
    k = (request.headers.get("X-Embed-Key") or "").strip()
    return k or None


def _extract_embed_model(request: Request) -> str | None:
    """Optional embedding model override (X-Embed-Model)."""
    m = (request.headers.get("X-Embed-Model") or "").strip()
    return m or None


def _apply_embed_overrides(request: Request) -> str:
    """Pull X-Embed-* headers, set ContextVar overrides, return the active provider.

    Used by REST endpoints that need to qualify collection names with the
    embed provider before the Inngest job is even kicked off.
    """
    set_embed_provider_override(_extract_embed_provider(request))
    set_embed_api_key_override(_extract_embed_api_key(request))
    set_embed_model_override(_extract_embed_model(request))
    return get_embed_provider()


def _extract_tenant_id(request: Request) -> str:
    """Per-request tenant ID (X-Tenant-Id). Falls back to ``SHARED_TENANT``.

    Also applies validation + sets the ContextVar so any synchronous helpers
    invoked inside the request handler see the same tenant the Inngest job will.
    """
    tid = (request.headers.get("X-Tenant-Id") or "").strip()
    set_tenant_id_override(tid or None)
    from auth import get_tenant_id
    return get_tenant_id()


@app.post("/api/ingest")
async def trigger_ingest(req: IngestRequest, request: Request):
    """Trigger a background repo ingestion job."""
    if req.repo.count("/") != 1:
        raise HTTPException(status_code=400, detail="repo must be in 'owner/name' form")
    tenant_id = _extract_tenant_id(request)
    _apply_embed_overrides(request)  # so the in-process status tracker keys match
    await inngest_client.send(
        inngest.Event(
            name="repomind/ingest_repo",
            data={
                "repo": req.repo,
                "mode": req.mode,
                "github_token": _extract_user_github_token(request),
                "vllm_api_key": _extract_user_vllm_key(request),
                "llm_provider": _extract_llm_provider(request),
                "llm_api_key": _extract_llm_api_key(request),
                "llm_model": _extract_llm_model(request),
                "embed_provider": _extract_embed_provider(request),
                "embed_api_key": _extract_embed_api_key(request),
                "embed_model": _extract_embed_model(request),
                "tenant_id": tenant_id,
            },
        )
    )
    return {"status": "triggered", "repo": req.repo, "mode": req.mode}


@app.post("/api/query")
async def trigger_query(req: QueryRequest, request: Request):
    """Trigger an agent run. Poll /api/result/{session_id} for the answer."""
    tenant_id = _extract_tenant_id(request)
    embed_provider = _apply_embed_overrides(request)
    # Client sends the bare collection name (e.g. "0xnktd_fireranger_ast");
    # qualify here so the agent's vector_search hits THIS tenant's + THIS embed
    # provider's collection only — spoofing another tenant/provider's prefix is
    # ignored because we always rebuild the wrapper from the headers.
    _, bare_name, _ = strip_collection(req.collection_name)
    qualified = qualify_collection(bare_name, tenant_id, embed_provider)
    session_id = str(uuid.uuid4())
    await inngest_client.send(
        inngest.Event(
            name="repomind/run_agent",
            data={
                "query": req.query,
                "collection_name": qualified,
                "github_token": _extract_user_github_token(request),
                "vllm_api_key": _extract_user_vllm_key(request),
                "llm_provider": _extract_llm_provider(request),
                "llm_api_key": _extract_llm_api_key(request),
                "llm_model": _extract_llm_model(request),
                "embed_provider": _extract_embed_provider(request),
                "embed_api_key": _extract_embed_api_key(request),
                "embed_model": _extract_embed_model(request),
                "tenant_id": tenant_id,
                "session_id": session_id,
                "history": req.history,
            },
        )
    )
    return {"status": "triggered", "session_id": session_id}


@app.get("/api/ingest/status")
async def ingest_status(request: Request):
    """List in-progress / recently-finished ingests for this tenant.

    Returns:
        {
          pending: [
            {collection_name, repo, mode, phase, files_seen, total_chunks,
             embed_errors, error, started_at, finished_at}
          ]
        }

    Phase values: fetching | embedding | done | error.
    The frontend polls this while there's any non-``done`` entry; ``done`` /
    ``error`` entries linger for 5 min so the UI can show the final state once.
    """
    tenant_id = _extract_tenant_id(request)
    embed_provider = _apply_embed_overrides(request)
    _cleanup_ingest_status()
    out = []
    for qname, entry in _INGEST_STATUS.items():
        if entry.get("tenant_id") != tenant_id:
            continue
        # Hide ingests that belong to a different embed provider — they'd be
        # invisible in /api/collections too, so listing them here is misleading.
        _, bare, ep = strip_collection(qname)
        if ep and ep != embed_provider:
            continue
        out.append({
            "collection_name": bare,
            "repo": entry.get("repo", ""),
            "mode": entry.get("mode", ""),
            "phase": entry.get("phase", "fetching"),
            "files_seen": entry.get("files_seen", 0),
            "total_chunks": entry.get("total_chunks", 0),
            "embed_errors": entry.get("embed_errors", 0),
            "error": entry.get("error"),
            "started_at": entry.get("started_at"),
            "finished_at": entry.get("finished_at"),
        })
    return {"pending": out}


@app.get("/api/result/{session_id}")
async def get_result(session_id: str, request: Request):
    """Return cached agent result if ready, else 404.

    Refuses to serve a session that belongs to a different tenant — that way
    a guessed/leaked UUID can't be used to read another tenant's answer.
    """
    tenant_id = _extract_tenant_id(request)
    entry = _RESULT_CACHE.get(session_id)
    if entry is None or entry.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="result not ready yet")
    return entry["result"]


@app.get("/api/collections")
async def list_collections(request: Request):
    """List THIS tenant's ChromaDB collections with chunk counts (bare names).

    Filters to collections matching the calling tenant AND the current embed
    provider — collections from a different embed provider have a different
    vector dimension and can't be queried with the current setup, so listing
    them would be misleading.
    """
    tenant_id = _extract_tenant_id(request)
    embed_provider = _apply_embed_overrides(request)
    try:
        client = chromadb.PersistentClient(path=os.getenv("CHROMA_DB_PATH", "./chroma_db"))
        result = []
        for col in client.list_collections():
            if not belongs_to(col.name, tenant_id, embed_provider):
                continue
            _, bare, _ = strip_collection(col.name)
            try:
                count = client.get_collection(col.name).count()
            except Exception:
                count = 0
            result.append({"name": bare, "chunk_count": count})
        return {"collections": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/collections/{name}/chunks")
async def list_chunks(
    name: str,
    request: Request,
    limit: int = 50,
    offset: int = 0,
    file_path: str | None = None,
    chunk_type: str | None = None,
):
    """Inspect raw chunks stored in a ChromaDB collection.

    Useful for debugging the chunker (AST vs naive output) against the live
    deployed instance — no need to re-ingest locally.

    Query params:
      limit       — page size (default 50, max 500)
      offset      — pagination offset (default 0)
      file_path   — filter to chunks from this file (exact match)
      chunk_type  — filter by metadata.chunk_type (function | class | doc | code)

    Returns:
      {
        collection_name, chunks: [{id, text, metadata}, ...],
        total, limit, offset
      }
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    tenant_id = _extract_tenant_id(request)
    embed_provider = _apply_embed_overrides(request)
    # Strip any wrapper the client may have sent, then re-qualify with THIS
    # tenant + embed provider. Spoofing another tenant or provider's prefix
    # gets sanitised away.
    _, bare_name, _ = strip_collection(name)
    qualified = qualify_collection(bare_name, tenant_id, embed_provider)

    try:
        client = chromadb.PersistentClient(path=os.getenv("CHROMA_DB_PATH", "./chroma_db"))
        try:
            collection = client.get_collection(qualified)
        except Exception:
            raise HTTPException(status_code=404, detail=f"collection '{bare_name}' not found")

        # Build the where filter — ChromaDB needs $and for >1 condition.
        filters: list[dict] = []
        if file_path:
            filters.append({"file_path": file_path})
        if chunk_type:
            filters.append({"chunk_type": chunk_type})
        where: dict | None = None
        if len(filters) == 1:
            where = filters[0]
        elif len(filters) > 1:
            where = {"$and": filters}

        page = collection.get(
            limit=limit,
            offset=offset,
            where=where,
            include=["documents", "metadatas"],
        )

        # Total — fast path uses collection.count() when there's no filter;
        # with a filter, fetch all matching IDs (cheap; only IDs, not data).
        if where:
            all_matches = collection.get(where=where, include=["metadatas"])
            total = len(all_matches.get("ids") or [])
        else:
            total = collection.count()

        chunks = [
            {"id": cid, "text": doc, "metadata": meta or {}}
            for cid, doc, meta in zip(
                page.get("ids") or [],
                page.get("documents") or [],
                page.get("metadatas") or [],
            )
        ]

        return {
            "collection_name": bare_name,
            "chunks": chunks,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/collections/{name}")
async def delete_collection(name: str, request: Request):
    """Delete a collection (Chroma + ingest-status tracker) for the calling tenant.

    Idempotent: a missing collection returns 200 with ``deleted=false``. Sets a
    cancellation flag first so any in-flight ingest aborts before its next
    upsert — without this, a delete during embedding would let the job
    re-create the collection seconds later.
    """
    tenant_id = _extract_tenant_id(request)
    embed_provider = _apply_embed_overrides(request)
    _, bare_name, _ = strip_collection(name)
    qualified = qualify_collection(bare_name, tenant_id, embed_provider)

    # 1. Mark cancelled — any embed loop on this collection sees this on its
    # next iteration and raises IngestCancelled, which the Inngest handler
    # catches and surfaces as phase="error".
    mark_ingest_cancelled(qualified)

    # 2. Drop the in-memory status so the sidebar UI stops listing it.
    _INGEST_STATUS.pop(qualified, None)

    # 3. Drop the Chroma collection. Best-effort — Chroma raises if the
    # collection doesn't exist, which is fine here (idempotent semantics).
    deleted = False
    try:
        client = chromadb.PersistentClient(path=os.getenv("CHROMA_DB_PATH", "./chroma_db"))
        try:
            client.delete_collection(qualified)
            deleted = True
        except Exception:
            pass
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"deleted": deleted, "collection_name": bare_name}


@app.get("/api/logs")
async def recent_logs(request: Request, limit: int = 50):
    """Return the most recent agent log entries scoped to this tenant."""
    tenant_id = _extract_tenant_id(request)
    try:
        return {"logs": get_recent_logs(limit, tenant_id=tenant_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/logs/{session_id}")
async def session_logs(session_id: str, request: Request):
    """Return all log entries for a specific session, scoped to this tenant."""
    tenant_id = _extract_tenant_id(request)
    try:
        return {"logs": get_session_logs(session_id, tenant_id=tenant_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/metrics")
async def aggregate_metrics(request: Request):
    """Return aggregate metrics across this tenant's sessions."""
    tenant_id = _extract_tenant_id(request)
    try:
        metrics = compute_aggregate_metrics(tenant_id=tenant_id)
        return metrics or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
