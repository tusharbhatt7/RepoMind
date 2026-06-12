# Deploying RepoMind

End-to-end deployment on **free tiers**:
- **Backend** (FastAPI + Inngest) → **Render** (free Python web service)
- **Frontend** (Next.js) → **Vercel** (free)
- **Background jobs** → **Inngest Cloud** (free tier)
- **LLM + Embeddings** → **Modal** (already deployed via `../rag-learning/`; uses your $30 credit)

> **Total expected cost: ~$0/month** for moderate use. Modal pay-per-inference may consume your credit over time depending on traffic.

---

## Architecture once deployed

```
            Browser ──HTTPS──► Vercel (Next.js)
                                    │
                                    │ NEXT_PUBLIC_API_URL=https://<render>.onrender.com/api
                                    ▼
                              Render (FastAPI + Inngest webhook)
                                    │
                ┌───────────────────┼────────────────────┐
                │                   │                    │
                ▼                   ▼                    ▼
        Inngest Cloud        Modal (LLM)         Modal (Embeddings)
       (jobs queue)       (Qwen 2.5-7B)         (bge-small-en-v1.5)
                                    │
                                    └─► local ChromaDB (ephemeral on free plan)
```

---

## Step 1 — Push the deployment changes to GitHub

These patches must be in your `main` branch before Render/Vercel can deploy:

```bash
cd repomind
git add render.yaml DEPLOY.md \
        server.py inngest_setup.py tools.py ingest.py app.py \
        eval/compare.py eval/inspect_chunks.py \
        frontend/lib/api.ts frontend/next.config.ts
git commit -m "feat(deploy): env-driven config + Render config + Inngest production mode"
git push origin main
```

The local `.env` stays put (gitignored).

---

## Step 2 — Backend on Render

1. Open https://dashboard.render.com → **New +** → **Blueprint**.
2. Connect your GitHub account (if not already done) → pick the **`tusharbhatt7/repomind`** repo.
3. Render detects `render.yaml` and shows the service config.
4. Click **Apply**.
5. On the next page, Render prompts for the missing env vars. Paste these values from your local `.env` (and Inngest Cloud / Modal dashboards):

   | Variable | Where to find it |
   |----------|------------------|
   | `VLLM_API_KEY` | `.env` line 2 (`whj9er3w94if9dsoitj0!…`) |
   | `VLLM_BASE_URL` | Modal dashboard → `repomind-vllm-serve` URL + `/v1` |
   | `QWEN_GENERATE_URL` | `.env` line 5 |
   | `EMBED_BASE_URL` | `.env` line 8 |
   | `GITHUB_TOKEN` | `.env` line 12 (the new `ghp_…` token) |
   | `INNGEST_EVENT_KEY` | https://app.inngest.com → your app → **Manage** → **Event Keys** |
   | `INNGEST_SIGNING_KEY` | Same place → **Signing Keys** |
   | `CORS_ORIGINS` | Leave blank for now — set after Vercel deploy (Step 3) |

6. Click **Deploy** at the bottom. First build takes 4–6 min (installs deps + downloads chromadb wheels).
7. Once it shows **Live**, copy the public URL — looks like `https://repomind-backend.onrender.com`. **Keep this URL** — you'll paste it into Vercel.

### Smoke-test the backend

```bash
curl https://<your-backend>.onrender.com/api/collections
# Should return: {"collections":[]}  (empty until you ingest a repo)
```

If you get a 502 / 503: the free tier was asleep — wait ~30 s and retry.

---

## Step 3 — Frontend on Vercel

1. Open https://vercel.com/new → **Import Git Repository** → pick **`tusharbhatt7/repomind`**.
2. **Important — set Root Directory** to `frontend` (Vercel needs to see `package.json` at the root of the deploy).
3. Framework Preset: **Next.js** (auto-detected).
4. **Environment Variables** — click **Add** and paste:

   | Variable | Value |
   |----------|-------|
   | `NEXT_PUBLIC_API_URL` | `https://<your-render-backend>.onrender.com/api` (from Step 2.7) |

5. Click **Deploy**. Build takes ~2 min.
6. Once live, copy the Vercel URL (e.g. `https://repomind.vercel.app`).

---

## Step 4 — Tie it back together (CORS + Inngest)

### 4a. Tell the backend which frontend is allowed (CORS)

1. Render dashboard → your service → **Environment**.
2. Add / update **`CORS_ORIGINS`** = `https://repomind.vercel.app` (or whichever Vercel URL you got).
3. Render redeploys automatically (~1 min).

### 4b. Register the backend with Inngest Cloud

1. Open https://app.inngest.com → your app.
2. Click **Sync new app** → enter the URL: `https://<your-render-backend>.onrender.com/api/inngest`.
3. Inngest pings the endpoint and registers the two functions: `repomind/ingest_repo` and `repomind/run_agent`.
4. You should see them appear in the Inngest **Functions** tab within seconds.

---

## Step 5 — Smoke test the live app

1. Open your Vercel URL.
2. In the sidebar, enter `tusharbhatt7/repomind` and click **Ingest Repo**.
3. The Inngest Functions tab will show the `ingest_repo` job firing and stepping through. Takes ~30–90 s for a small repo.
4. Once ingestion completes, the repo appears in the sidebar list.
5. Select it and ask: *"How does the agent decide which tool to call?"*
6. The chat shows "Agent working…" and the answer streams in once the ReAct loop completes.

---

## Caveats & operational notes

- **Render free tier sleeps after 15 min idle.** First request after sleep waits ~30 s for cold start. Upgrade to $7/mo "Starter" for always-on if you demo this often.
- **ChromaDB is ephemeral on Render free tier** — there's no persistent disk available below the Starter plan. Every redeploy = re-ingest. Upgrade to Starter + add a persistent disk (set `CHROMA_DB_PATH` to the mount path) for durable vectors.
- **Modal cost** — each LLM and embedding call burns your $30 credit. Set budget alerts at https://modal.com/settings/billing.
- **Rotate the GitHub PAT** — the one in chat history should be regenerated at https://github.com/settings/tokens once deploy is verified working. Update both the Render env var and your local `.env` with the new value.
- **Inngest free tier** — 50 k events/month. Each ingest = 2 events, each query = 2 events. Plenty for demo traffic.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| 502 on first request | Render free instance asleep | Wait 30 s, retry. Or upgrade to Starter |
| Frontend can't reach backend | `CORS_ORIGINS` not set on Render | Add your Vercel URL exactly (no trailing slash) |
| Inngest "function unreachable" | Backend not synced with Inngest Cloud | Re-run Step 4b with the correct `/api/inngest` URL |
| Ingestion silently fails | `GITHUB_TOKEN` missing or revoked | Generate a fresh PAT, update Render env var |
| "All API calls return empty" | Modal services not running | `modal app list` in `../rag-learning/`; redeploy if stopped |
| Vectors disappear after redeploy | Render free tier ephemeral disk | Re-ingest. Upgrade for persistence |
