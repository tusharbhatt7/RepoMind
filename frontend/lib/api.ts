import { getOrCreateTenantId } from "./tenant";

// Local dev: BASE = "/api" → next.config.ts rewrites to http://localhost:8000.
// Production (Vercel): set NEXT_PUBLIC_API_URL=https://<render-app>.onrender.com/api
// to call the Render backend directly. CORS middleware on the backend allows it.
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "/api";

// ─── User-supplied API key override ─────────────────────────────────────────
// If the user has pasted a personal GitHub PAT on the Settings page, we read
// it from localStorage and send it as X-Github-Token. The backend's
// set_github_token_override() consumes it. Falls back to env GITHUB_TOKEN
// (configured on Render) when absent — i.e. no header sent ≠ broken request.
export const USER_TOKENS_KEY = "repomind:user-tokens:v1";

export const LLM_PROVIDERS = ["vllm", "openai", "gemini"] as const;
export type LlmProvider = (typeof LLM_PROVIDERS)[number];

export const PROVIDER_LABEL: Record<LlmProvider, string> = {
  vllm: "Modal (Qwen2.5-7B) — bundled default",
  openai: "OpenAI",
  gemini: "Google Gemini",
};

export const PROVIDER_DEFAULT_MODEL: Record<LlmProvider, string> = {
  vllm: "",
  openai: "gpt-4o-mini",
  gemini: "gemini-2.5-flash",
};

// Embedding provider — separate from text-gen because the choice has different
// constraints (vector dimensions per provider). Anthropic isn't here because
// Claude is chat-only and doesn't ship an embedding model.
export const EMBED_PROVIDERS = ["vllm", "openai", "gemini"] as const;
export type EmbedProvider = (typeof EMBED_PROVIDERS)[number];

export const EMBED_PROVIDER_LABEL: Record<EmbedProvider, string> = {
  vllm: "Modal (bge-small-en-v1.5, 384d) — bundled default",
  openai: "OpenAI (text-embedding-3-small, 1536d)",
  gemini: "Google Gemini (gemini-embedding-001, 3072d)",
};

export const EMBED_PROVIDER_DEFAULT_MODEL: Record<EmbedProvider, string> = {
  vllm: "",
  openai: "text-embedding-3-small",
  gemini: "gemini-embedding-001",
};

type StoredTokens = {
  github_token?: string;
  vllm_api_key?: string;
  llm_provider?: LlmProvider;
  llm_api_key?: string;
  llm_model?: string;
  embed_provider?: EmbedProvider;
  embed_api_key?: string;
  embed_model?: string;
};

function readTokens(): StoredTokens {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(USER_TOKENS_KEY);
    if (!raw) return {};
    return JSON.parse(raw) as StoredTokens;
  } catch {
    return {};
  }
}

function writeTokens(t: StoredTokens): void {
  if (typeof window === "undefined") return;
  try {
    const cleaned: StoredTokens = {};
    if (t.github_token?.trim())  cleaned.github_token  = t.github_token.trim();
    if (t.vllm_api_key?.trim())  cleaned.vllm_api_key  = t.vllm_api_key.trim();
    if (t.llm_provider && LLM_PROVIDERS.includes(t.llm_provider)) cleaned.llm_provider = t.llm_provider;
    if (t.llm_api_key?.trim())   cleaned.llm_api_key   = t.llm_api_key.trim();
    if (t.llm_model?.trim())     cleaned.llm_model     = t.llm_model.trim();
    if (t.embed_provider && EMBED_PROVIDERS.includes(t.embed_provider)) cleaned.embed_provider = t.embed_provider;
    if (t.embed_api_key?.trim()) cleaned.embed_api_key = t.embed_api_key.trim();
    if (t.embed_model?.trim())   cleaned.embed_model   = t.embed_model.trim();
    if (Object.keys(cleaned).length === 0) {
      window.localStorage.removeItem(USER_TOKENS_KEY);
    } else {
      window.localStorage.setItem(USER_TOKENS_KEY, JSON.stringify(cleaned));
    }
  } catch {
    /* private mode / quota — silent */
  }
}

export function getStoredGithubToken(): string {
  return (readTokens().github_token ?? "").trim();
}

export function setStoredGithubToken(token: string): void {
  writeTokens({ ...readTokens(), github_token: token });
}

export function getStoredVllmApiKey(): string {
  return (readTokens().vllm_api_key ?? "").trim();
}

export function setStoredVllmApiKey(key: string): void {
  writeTokens({ ...readTokens(), vllm_api_key: key });
}

export function getStoredLlmProvider(): LlmProvider {
  const p = readTokens().llm_provider;
  return p && LLM_PROVIDERS.includes(p) ? p : "vllm";
}

export function setStoredLlmProvider(provider: LlmProvider): void {
  writeTokens({ ...readTokens(), llm_provider: provider });
}

export function getStoredLlmApiKey(): string {
  return (readTokens().llm_api_key ?? "").trim();
}

export function setStoredLlmApiKey(key: string): void {
  writeTokens({ ...readTokens(), llm_api_key: key });
}

export function getStoredLlmModel(): string {
  return (readTokens().llm_model ?? "").trim();
}

export function setStoredLlmModel(model: string): void {
  writeTokens({ ...readTokens(), llm_model: model });
}

export function getStoredEmbedProvider(): EmbedProvider {
  const p = readTokens().embed_provider;
  return p && EMBED_PROVIDERS.includes(p) ? p : "vllm";
}

export function setStoredEmbedProvider(provider: EmbedProvider): void {
  writeTokens({ ...readTokens(), embed_provider: provider });
}

export function getStoredEmbedApiKey(): string {
  return (readTokens().embed_api_key ?? "").trim();
}

export function setStoredEmbedApiKey(key: string): void {
  writeTokens({ ...readTokens(), embed_api_key: key });
}

export function getStoredEmbedModel(): string {
  return (readTokens().embed_model ?? "").trim();
}

export function setStoredEmbedModel(model: string): void {
  writeTokens({ ...readTokens(), embed_model: model });
}

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const tenant = getOrCreateTenantId();
  if (tenant) headers["X-Tenant-Id"] = tenant;
  const gh   = getStoredGithubToken();
  const vllm = getStoredVllmApiKey();
  if (gh)   headers["X-Github-Token"] = gh;
  if (vllm) headers["X-VLLM-Key"]     = vllm;
  // Text-gen override (Settings → Text Generation card). When the user picks
  // openai / gemini we forward provider + key + (optional) model so the
  // backend's _generate routes through that API instead of Modal Qwen.
  // For provider=vllm we skip these headers — backend defaults to vllm anyway.
  const provider = getStoredLlmProvider();
  if (provider !== "vllm") {
    headers["X-LLM-Provider"] = provider;
    const llmKey = getStoredLlmApiKey();
    if (llmKey) headers["X-LLM-Key"] = llmKey;
    const llmModel = getStoredLlmModel();
    if (llmModel) headers["X-LLM-Model"] = llmModel;
  }
  // Embed-provider override (Settings → Embeddings card). Different provider
  // = different vector dimension = a separate Chroma collection, so the
  // backend always forwards this even when it matches vllm — so listing /
  // qualify_collection can include it in the suffix.
  const embedProvider = getStoredEmbedProvider();
  headers["X-Embed-Provider"] = embedProvider;
  if (embedProvider !== "vllm") {
    const embedKey = getStoredEmbedApiKey();
    if (embedKey) headers["X-Embed-Key"] = embedKey;
    const embedModel = getStoredEmbedModel();
    if (embedModel) headers["X-Embed-Model"] = embedModel;
  }
  return headers;
}

export interface Collection {
  name: string;
  chunk_count: number;
}

export interface LogEntry {
  timestamp: string;
  session_id: string;
  step: number;
  event: string;
  data: Record<string, unknown>;
}

export interface Metrics {
  total_sessions?: number;
  avg_latency_s?: number;
  median_latency_s?: number;
  avg_steps?: number;
  total_input_tokens?: number;
  total_output_tokens?: number;
  total_cost_usd?: number;
}

export interface ContextMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AgentResult {
  session_id: string;
  answer: string;
  steps: number;
  stop_reason: string;
  total_latency_s: number;
  embed_ms: number | null;
  chroma_ms: number | null;
  compressed_history: ContextMessage[];
}

export async function fetchCollections(): Promise<Collection[]> {
  const res = await fetch(`${BASE}/collections`, { headers: authHeaders() });
  if (!res.ok) return [];
  const data = await res.json();
  return data.collections ?? [];
}

export type IngestPhase = "fetching" | "embedding" | "done" | "error";

export interface IngestStatus {
  collection_name: string;
  repo: string;
  mode: string;
  phase: IngestPhase;
  files_seen: number;
  total_chunks: number;
  embed_errors: number;
  error: string | null;
  started_at: number | null;
  finished_at: number | null;
}

export async function fetchIngestStatus(): Promise<IngestStatus[]> {
  const res = await fetch(`${BASE}/ingest/status`, { headers: authHeaders() });
  if (!res.ok) return [];
  const data = await res.json();
  return data.pending ?? [];
}

export async function deleteCollection(name: string): Promise<boolean> {
  const res = await fetch(`${BASE}/collections/${encodeURIComponent(name)}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Delete failed (${res.status})`);
  const data = await res.json();
  return !!data.deleted;
}

export async function ingestRepo(repo: string, mode: string): Promise<void> {
  const res = await fetch(`${BASE}/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ repo, mode }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `Ingest failed (${res.status})`);
  }
}

export async function triggerQuery(
  query: string,
  collection_name: string,
  history: ContextMessage[] = []
): Promise<string> {
  const res = await fetch(`${BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ query, collection_name, history }),
  });
  if (!res.ok) throw new Error(`Query trigger failed (${res.status})`);
  const data = await res.json();
  return data.session_id as string;
}

export async function pollResult(
  sessionId: string
): Promise<AgentResult | null> {
  const res = await fetch(`${BASE}/result/${sessionId}`, { headers: authHeaders() });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Poll failed (${res.status})`);
  return res.json();
}

export async function fetchLogs(limit = 50): Promise<LogEntry[]> {
  const res = await fetch(`${BASE}/logs?limit=${limit}`, { headers: authHeaders() });
  if (!res.ok) return [];
  const data = await res.json();
  return data.logs ?? [];
}

export async function fetchSessionLogs(sessionId: string): Promise<LogEntry[]> {
  const res = await fetch(`${BASE}/logs/${sessionId}`, { headers: authHeaders() });
  if (!res.ok) return [];
  const data = await res.json();
  return data.logs ?? [];
}

export async function fetchMetrics(): Promise<Metrics> {
  const res = await fetch(`${BASE}/metrics`, { headers: authHeaders() });
  if (!res.ok) return {};
  return res.json();
}
