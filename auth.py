"""Per-request overrides for credentials + tenancy.

Three ContextVars carry per-request state set by REST handlers (and forwarded
into Inngest function handlers via event data):

  GitHub PAT       — X-Github-Token header  → get_github_token()
  LLM Bearer key   — X-VLLM-Key header      → get_vllm_api_key()
  Tenant ID        — X-Tenant-Id header     → get_tenant_id()

Tenants isolate anonymous users — a UUID minted in the browser scopes every
ChromaDB collection, every agent log entry, every metric. With no header, the
tenant falls back to ``SHARED_TENANT`` (used by CLI ingests + local smoke
tests, never reachable from the browser because browsers always send a UUID).
"""
from __future__ import annotations

import contextvars
import os
import re

_github_token_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "github_token_override", default=None
)

# Same override pattern for the LLM / embeddings auth key (Modal Bearer).
# Settings UI lets a user paste their own VLLM_API_KEY in case the deploy's
# default is wrong / rotated / they're pointing at their own Modal app.
_vllm_api_key_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vllm_api_key_override", default=None
)

# Per-request tenant ID (UUID). Set from X-Tenant-Id header. Falls back to
# SHARED_TENANT when missing so CLI ingests + dev have a consistent home.
_tenant_id_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tenant_id_override", default=None
)

SHARED_TENANT = "shared"
# Double-underscore separator between tenant + repo slug. UUIDs only contain
# hex + single hyphens, so "__" cannot appear inside a tenant ID and the split
# is unambiguous.
TENANT_SEP = "__"
# Whitelist tenant IDs to a safe character class. Block anything path-y or
# containing the separator to keep qualify/strip reversible.
_TENANT_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def set_github_token_override(token: str | None) -> None:
    """Set the per-context PAT override. Pass None or "" to clear."""
    _github_token_override.set(token.strip() if token and token.strip() else None)


def get_github_token() -> str:
    """Return the effective GitHub PAT — override first, env second.

    Raises RuntimeError if neither is configured. Callers can choose to surface
    that as a 4xx with a hint that the user should paste a token in Settings.
    """
    token = _github_token_override.get()
    if token:
        return token
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError(
            "No GitHub token available. Set GITHUB_TOKEN in env, or paste one "
            "into the dashboard's Settings page (sent as X-Github-Token header)."
        )
    return token


def set_vllm_api_key_override(key: str | None) -> None:
    """Per-context LLM/embeddings key override. Pass None or "" to clear."""
    _vllm_api_key_override.set(key.strip() if key and key.strip() else None)


def get_vllm_api_key() -> str:
    """Return the effective Modal / vLLM Bearer key — override first, env second.

    Returns "" if neither is set (downstream HTTP calls then fail with 401 from
    Modal; callers can choose to validate up-front if they want a nicer error).
    """
    key = _vllm_api_key_override.get()
    if key:
        return key
    return os.getenv("VLLM_API_KEY", "")


# ─── LLM provider switching ─────────────────────────────────────────────────
# Text generation can be routed through one of four providers per request.
# Embeddings always use the Modal endpoint (see ingest.py / tools.py).
#
# Provider is selected by the X-LLM-Provider header (Settings → dropdown).
# When unset, falls back to "vllm" (the deploy's bundled Modal Qwen endpoint).

SUPPORTED_PROVIDERS = ("vllm", "openai", "gemini")

# Sensible default model per provider — cheap + fast tiers. Override via the
# X-LLM-Model header (Settings → model text input).
DEFAULT_MODELS = {
    "openai":    "gpt-4o-mini",
    "gemini":    "gemini-2.5-flash",
}

# ─── Embedding provider (separate from text-gen — they're independent) ──────
# vllm = Modal bge-small-en-v1.5 (384-d, current default)
# openai = text-embedding-3-small (1536-d)
# gemini = text-embedding-004 (768-d)
#
# Anthropic is NOT in this list — Claude is chat-only, no embeddings.
SUPPORTED_EMBED_PROVIDERS = ("vllm", "openai", "gemini")

DEFAULT_EMBED_MODELS = {
    "vllm":   "BAAI/bge-small-en-v1.5",
    "openai": "text-embedding-3-small",
    "gemini": "gemini-embedding-001",
}

_llm_provider_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_provider_override", default=None
)
_llm_api_key_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_api_key_override", default=None
)
_llm_model_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_model_override", default=None
)


def set_llm_provider_override(provider: str | None) -> None:
    """Set the active text-generation provider. Unknown values clear the override."""
    if not provider:
        _llm_provider_override.set(None)
        return
    p = provider.strip().lower()
    _llm_provider_override.set(p if p in SUPPORTED_PROVIDERS else None)


def get_llm_provider() -> str:
    """Return the active text-generation provider — override first, ``vllm`` otherwise."""
    return _llm_provider_override.get() or "vllm"


def set_llm_api_key_override(key: str | None) -> None:
    """Set the API key for the active non-vllm provider (Anthropic / OpenAI / Gemini)."""
    _llm_api_key_override.set(key.strip() if key and key.strip() else None)


def get_llm_api_key() -> str:
    """Provider-specific key for Anthropic / OpenAI / Gemini.

    Returns "" if unset — caller should raise a helpful error pointing the user
    to Settings before making the HTTP call.
    """
    return _llm_api_key_override.get() or ""


def set_llm_model_override(model: str | None) -> None:
    """Set the model name for the active provider. Pass None / "" to fall back to default."""
    _llm_model_override.set(model.strip() if model and model.strip() else None)


def get_llm_model(provider: str | None = None) -> str:
    """Return the effective model name — override first, provider default otherwise."""
    model = _llm_model_override.get()
    if model:
        return model
    return DEFAULT_MODELS.get(provider or get_llm_provider(), "")


# ─── Embedding provider overrides (X-Embed-Provider/Key/Model headers) ──────

_embed_provider_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "embed_provider_override", default=None
)
_embed_api_key_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "embed_api_key_override", default=None
)
_embed_model_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "embed_model_override", default=None
)


def set_embed_provider_override(provider: str | None) -> None:
    if not provider:
        _embed_provider_override.set(None)
        return
    p = provider.strip().lower()
    _embed_provider_override.set(p if p in SUPPORTED_EMBED_PROVIDERS else None)


def get_embed_provider() -> str:
    """Active embedding provider — override first, falls back to ``vllm``."""
    return _embed_provider_override.get() or "vllm"


def set_embed_api_key_override(key: str | None) -> None:
    _embed_api_key_override.set(key.strip() if key and key.strip() else None)


def get_embed_api_key() -> str:
    """Key for OpenAI / Gemini embeddings. For vllm, use get_vllm_api_key()."""
    return _embed_api_key_override.get() or ""


def set_embed_model_override(model: str | None) -> None:
    _embed_model_override.set(model.strip() if model and model.strip() else None)


def get_embed_model(provider: str | None = None) -> str:
    """Effective embedding model name — override first, provider default otherwise."""
    model = _embed_model_override.get()
    if model:
        return model
    return DEFAULT_EMBED_MODELS.get(provider or get_embed_provider(), "")


# ─── Tenancy ────────────────────────────────────────────────────────────────

def set_tenant_id_override(tenant_id: str | None) -> None:
    """Set the per-context tenant. Pass None or "" to clear (falls back to SHARED_TENANT)."""
    if tenant_id is None:
        _tenant_id_override.set(None)
        return
    tid = tenant_id.strip()
    if not tid or not _TENANT_RE.match(tid) or TENANT_SEP in tid:
        _tenant_id_override.set(None)
        return
    _tenant_id_override.set(tid)


def get_tenant_id() -> str:
    """Return the effective tenant ID — override first, ``SHARED_TENANT`` otherwise.

    Always returns a non-empty string safe to embed in a collection name.
    """
    tid = _tenant_id_override.get()
    return tid or SHARED_TENANT


def qualify_collection(
    name: str,
    tenant_id: str | None = None,
    embed_provider: str | None = None,
) -> str:
    """Build the full ChromaDB collection name.

    Format:  ``{tenant_id}__{bare_name}__{embed_provider}``

    The embed-provider suffix forces a separate Chroma collection per
    embedding model — ChromaDB collections have a fixed vector dimension, so
    bge-small (384d), text-embedding-3-small (1536d), and text-embedding-004
    (768d) can't share a collection. Tagging the name keeps them apart.

    Idempotent: if ``name`` is already fully qualified, returned unchanged.
    """
    tid = tenant_id or get_tenant_id()
    ep = embed_provider or get_embed_provider()
    # Normalise: strip any prior tenant/embed wrapper, then re-wrap.
    _, bare, _ = strip_collection(name)
    return f"{tid}{TENANT_SEP}{bare}{TENANT_SEP}{ep}"


def strip_collection(name: str) -> tuple[str, str, str]:
    """Decompose a qualified collection name into (tenant_id, bare_name, embed_provider).

    Handles three formats:
      - new format     ``{tenant}__{bare}__{embed_provider}`` → all three set
      - legacy format  ``{tenant}__{bare}``                   → embed_provider=""
      - completely bare ``{bare}``                             → tenant="" embed_provider=""

    We split on the FIRST and LAST ``__`` rather than partition so a bare name
    containing the separator (unusual but possible) doesn't break parsing.
    """
    if TENANT_SEP not in name:
        return "", name, ""
    head, rest = name.split(TENANT_SEP, 1)
    # Try to peel off an embed-provider suffix at the end.
    if TENANT_SEP in rest:
        bare, _, ep = rest.rpartition(TENANT_SEP)
        if ep in SUPPORTED_EMBED_PROVIDERS:
            return head, bare, ep
        # Unknown suffix — assume the whole `rest` is the bare name (legacy
        # format predates the embed-provider tag).
    return head, rest, ""


def strip_tenant(name: str) -> tuple[str, str]:
    """Back-compat shim: (tenant_id, bare_name) — drops the embed_provider field.

    Kept for callers that don't care about embed provider (e.g. logging).
    Prefer ``strip_collection`` for new code.
    """
    tid, bare, _ = strip_collection(name)
    return tid, bare


def belongs_to(
    name: str,
    tenant_id: str | None = None,
    embed_provider: str | None = None,
) -> bool:
    """True iff a qualified name matches the given tenant AND embed provider.

    Embed provider only checked when ``embed_provider`` is provided; otherwise
    legacy collections (no provider suffix) are accepted as belonging to the
    tenant.
    """
    tid_want = tenant_id or get_tenant_id()
    tid, _, ep = strip_collection(name)
    if tid != tid_want:
        return False
    if embed_provider is not None and ep != embed_provider:
        return False
    return True


# ─── Cooperative ingest cancellation ────────────────────────────────────────
# When a user clicks the trash icon on an in-flight ingest, we mark the
# qualified collection name as cancelled here. The embed loop in ingest.py
# checks ``is_ingest_cancelled`` between chunks and bails out, so a mid-flight
# ingest is actually stopped rather than left to finish writing rows the user
# just deleted.
_cancelled_ingests: set[str] = set()


def mark_ingest_cancelled(qualified_name: str) -> None:
    _cancelled_ingests.add(qualified_name)


def is_ingest_cancelled(qualified_name: str) -> bool:
    return qualified_name in _cancelled_ingests


def clear_ingest_cancelled(qualified_name: str) -> None:
    _cancelled_ingests.discard(qualified_name)
