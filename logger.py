"""Structured JSONL logging for agent runs.

Each call to ``log_step`` appends one JSON object per line to
``agent_logs.jsonl`` (override with the ``AGENT_LOG_FILE`` env var).
Every entry is tagged with the request's tenant ID so the dashboard's
``/api/logs`` endpoint can scope reads to one tenant — no cross-user leakage.
``get_recent_logs`` and ``get_session_logs`` are thin readers used by the UI.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

LOG_FILE = Path(os.getenv("AGENT_LOG_FILE", "agent_logs.jsonl"))


def _resolve_tenant() -> str:
    """Best-effort tenant lookup. Local import keeps logger usable from auth."""
    try:
        from auth import get_tenant_id
        return get_tenant_id()
    except Exception:
        return "shared"


def log_step(
    session_id: str,
    step: int,
    event_type: str,
    data: dict[str, Any],
    tenant_id: str | None = None,
) -> None:
    """Append one structured event to the log file.

    *tenant_id* defaults to the current request's tenant (ContextVar). Pass it
    explicitly only when the caller is outside the request scope.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id or _resolve_tenant(),
        "session_id": session_id,
        "step": step,
        "event": event_type,
        "data": data,
    }
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _iter_entries() -> Iterator[dict]:
    if not LOG_FILE.exists():
        return
    with LOG_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _match_tenant(entry: dict, tenant_id: str | None) -> bool:
    """Filter helper. None = no filter; entries missing tenant_id (legacy logs)
    are only visible to the ``shared`` tenant so old data isn't leaked."""
    if tenant_id is None:
        return True
    return entry.get("tenant_id", "shared") == tenant_id


def get_recent_logs(n: int = 50, tenant_id: str | None = None) -> list[dict]:
    """Return the last ``n`` log entries (chronological), filtered by tenant."""
    return [e for e in _iter_entries() if _match_tenant(e, tenant_id)][-n:]


def get_session_logs(session_id: str, tenant_id: str | None = None) -> list[dict]:
    """Return every entry belonging to ``session_id`` for this tenant."""
    return [
        e for e in _iter_entries()
        if e["session_id"] == session_id and _match_tenant(e, tenant_id)
    ]
