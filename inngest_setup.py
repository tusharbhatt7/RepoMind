"""Shared Inngest client — imported by server.py and agent.py.

Keep this module free of heavy imports so both the FastAPI server and the
standalone agent CLI can load it without pulling in the full web stack.

Mode selection:
  • Local dev (default) — set INNGEST_DEV=1 in .env. The local Inngest Dev
    Server (npx inngest-cli dev) handles routing, no signing key needed.
  • Production (Render + Inngest Cloud) — leave INNGEST_DEV unset/0. The SDK
    auto-reads INNGEST_EVENT_KEY + INNGEST_SIGNING_KEY from env for signed
    webhooks to/from Inngest Cloud.
"""
from __future__ import annotations

import logging
import os

import inngest
from dotenv import load_dotenv

load_dotenv()

_is_production = os.getenv("INNGEST_DEV", "0").strip().lower() not in ("1", "true", "yes")

inngest_client = inngest.Inngest(
    app_id="repomind",
    logger=logging.getLogger("uvicorn"),
    is_production=_is_production,
)
