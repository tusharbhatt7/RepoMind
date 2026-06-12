"""Core agent orchestrator — text-based ReAct loop using rag-learning's generate endpoint.

Uses httpx to POST to QWEN_GENERATE_URL (rag-learning's QwenService) and drives a
Thought → Action → Observation loop until the model outputs "Final Answer:" or
max_steps is hit.

    LLM ──▶ parse Action ──▶ run_tool ──▶ Observation ──▶ LLM ...
                              ──▶ Final Answer ──▶ return

All Inngest checkpoint visibility is handled inside server.py's run_agent_fn via
ctx.step.run. This module is the synchronous implementation used by the CLI
(python agent.py ...) and called directly by server.py's Inngest function.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from typing import Any

import httpx
from dotenv import load_dotenv

from logger import log_step
from prompts import COMPRESS_HISTORY_PROMPT, QUERY_REWRITE_PROMPT, REACT_PROMPT_TEMPLATE
from tools import _TOOL_METRICS, run_tool

load_dotenv()

QWEN_GENERATE_URL = os.getenv("QWEN_GENERATE_URL", "")


def _generate(prompt: str, max_new_tokens: int = 512, temperature: float = 0.2) -> str:
    """Provider-switching text generation.

    Reads the active provider from auth (X-LLM-Provider header → ContextVar).
    Supported: ``vllm`` (default, Modal Qwen) | ``openai`` | ``gemini``.

    Anthropic was considered but dropped — Claude is chat-only, can't do
    embeddings, and supporting one half of a provider creates UX confusion.

    Each non-vllm branch needs a key in ``auth.get_llm_api_key()`` (X-LLM-Key);
    a missing key raises with a hint to set it in the dashboard's Settings page.
    """
    from auth import (
        get_llm_api_key,
        get_llm_model,
        get_llm_provider,
        get_vllm_api_key,
    )
    provider = get_llm_provider()
    if provider == "vllm":
        return _generate_vllm(prompt, max_new_tokens, temperature, get_vllm_api_key())
    if provider == "openai":
        return _generate_openai(prompt, max_new_tokens, temperature, get_llm_api_key(), get_llm_model(provider))
    if provider == "gemini":
        return _generate_gemini(prompt, max_new_tokens, temperature, get_llm_api_key(), get_llm_model(provider))
    raise RuntimeError(f"Unknown LLM provider {provider!r}")


def _require_key(provider: str, key: str) -> None:
    if not key:
        raise RuntimeError(
            f"No API key for {provider}. Paste one in the dashboard's Settings → "
            f"Text Generation card."
        )


def _generate_vllm(prompt: str, max_new_tokens: int, temperature: float, key: str) -> str:
    """Original Modal/Qwen path — POSTs to the rag-learning generate endpoint."""
    resp = httpx.post(
        QWEN_GENERATE_URL,
        json={"prompt": prompt, "max_new_tokens": max_new_tokens, "temperature": temperature},
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def _generate_openai(prompt: str, max_new_tokens: int, temperature: float, key: str, model: str) -> str:
    """OpenAI Chat Completions — also compatible with most ``OPENAI_BASE_URL`` clones."""
    _require_key("OpenAI", key)
    resp = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_new_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    return (resp.json()["choices"][0]["message"].get("content") or "").strip()


def _generate_gemini(prompt: str, max_new_tokens: int, temperature: float, key: str, model: str) -> str:
    """Google Gemini ``generateContent`` — key sent as ``x-goog-api-key`` header."""
    _require_key("Gemini", key)
    resp = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={
            "x-goog-api-key": key,
            "Content-Type": "application/json",
        },
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_new_tokens,
                "temperature": temperature,
            },
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", []) or []
    return "".join(p.get("text", "") for p in parts).strip()


def query_rewrite(user_query: str, history_context: str = "") -> str:
    """Rewrite a user question into a compact semantic-search query."""
    history_block = (
        f"\nConversation context (resolve any references like 'it', 'that', 'the above'):\n{history_context}\n"
        if history_context.strip() else ""
    )
    return _generate(
        QUERY_REWRITE_PROMPT.format(query=user_query, history_context=history_block),
        max_new_tokens=100,
        temperature=0.1,
    )


def _parse_action(text: str) -> tuple[str, dict] | None:
    """Extract (tool_name, args_dict) from a ReAct response, or None if not found."""
    action_match = re.search(r"Action:\s*(\w+)", text)
    if not action_match:
        return None
    tool_name = action_match.group(1).strip()

    input_match = re.search(r"Action Input:\s*(\{.*?\})", text, re.DOTALL)
    if input_match:
        try:
            args = json.loads(input_match.group(1))
        except json.JSONDecodeError:
            args = {}
    else:
        args = {}

    return tool_name, args


def run_agent(
    user_query: str,
    collection_name: str,
    max_steps: int = 6,
) -> dict[str, Any]:
    """Drive the ReAct loop until Final Answer or max_steps.

    Used by the CLI. Inngest visibility is provided by server.py's run_agent_fn
    which drives the same loop step-by-step via ctx.step.run.
    """
    session_id = str(uuid.uuid4())
    run_start = time.time()

    rewritten = query_rewrite(user_query)
    log_step(session_id, 0, "query_rewrite", {
        "original": user_query,
        "rewritten": rewritten,
    })

    scratchpad = ""
    total_embed_ms: int = 0
    total_chroma_ms: int = 0

    for step in range(1, max_steps + 1):
        prompt = REACT_PROMPT_TEMPLATE.format(
            question=user_query,
            rewritten=rewritten,
            scratchpad=scratchpad,
            history_block="",
        )

        t0 = time.time()
        raw = _generate(prompt, max_new_tokens=1000, temperature=0.2)
        llm_latency = round(time.time() - t0, 2)

        if "Final Answer:" in raw:
            answer = raw.split("Final Answer:", 1)[-1].strip()
            _REACT_TOKENS = ("Thought:", "Action:", "Action Input:", "Observation:")
            answer = "\n".join(
                line for line in answer.splitlines()
                if not any(line.strip().startswith(tok) for tok in _REACT_TOKENS)
            ).strip()
            total_latency = round(time.time() - run_start, 2)
            log_step(session_id, step, "final_answer", {
                "answer": answer,
                "llm_latency_s": llm_latency,
                "total_latency_s": total_latency,
                "total_steps": step,
            })
            return {
                "session_id": session_id,
                "answer": answer,
                "steps": step,
                "messages": [{"role": "assistant", "content": scratchpad + raw}],
            }

        parsed = _parse_action(raw)
        if parsed is None:
            log_step(session_id, step, "unexpected_stop", {"llm_latency_s": llm_latency})
            return {
                "session_id": session_id,
                "answer": raw,
                "steps": step,
                "messages": [{"role": "assistant", "content": scratchpad + raw}],
            }

        tool_name, args = parsed
        log_step(session_id, step, "tool_call", {
            "tool": tool_name,
            "args": args,
            "llm_latency_s": llm_latency,
        })

        _TOOL_METRICS.set({})
        t1 = time.time()
        tool_result = run_tool(tool_name, args, collection_name)
        tool_latency = round(time.time() - t1, 2)

        tool_metrics = _TOOL_METRICS.get({})
        embed_ms = tool_metrics.get("embed_ms") or 0
        chroma_ms = tool_metrics.get("chroma_ms") or 0
        total_embed_ms += embed_ms
        total_chroma_ms += chroma_ms

        result_str = str(tool_result)
        log_step(session_id, step, "tool_result", {
            "tool": tool_name,
            "result_preview": result_str[:200],
            "result_chars": len(result_str),
            "tool_latency_s": tool_latency,
            **tool_metrics,
        })

        scratchpad += raw + f"\nObservation: {result_str}\n\n"

    log_step(session_id, max_steps, "max_steps_reached", {})
    return {
        "session_id": session_id,
        "answer": "I couldn't find a complete answer after max steps.",
        "steps": max_steps,
        "messages": [{"role": "assistant", "content": scratchpad}],
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python agent.py <collection_name> <query...>", file=sys.stderr)
        sys.exit(2)
    collection = sys.argv[1]
    query = " ".join(sys.argv[2:])
    result = run_agent(query, collection)
    print("\n" + "=" * 60)
    print("FINAL ANSWER:")
    print(result["answer"])
    print(f"\nSteps: {result['steps']}")
