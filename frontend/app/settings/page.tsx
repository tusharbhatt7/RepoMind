"use client";

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  CheckCircle2,
  Copy,
  Cpu,
  Eye,
  EyeOff,
  KeyRound,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  getStoredGithubToken,
  setStoredGithubToken,
  getStoredVllmApiKey,
  setStoredVllmApiKey,
  getStoredLlmProvider,
  setStoredLlmProvider,
  getStoredLlmApiKey,
  setStoredLlmApiKey,
  getStoredLlmModel,
  setStoredLlmModel,
  getStoredEmbedProvider,
  setStoredEmbedProvider,
  getStoredEmbedApiKey,
  setStoredEmbedApiKey,
  getStoredEmbedModel,
  setStoredEmbedModel,
  LLM_PROVIDERS,
  PROVIDER_LABEL,
  PROVIDER_DEFAULT_MODEL,
  EMBED_PROVIDERS,
  EMBED_PROVIDER_LABEL,
  EMBED_PROVIDER_DEFAULT_MODEL,
  type LlmProvider,
  type EmbedProvider,
} from "@/lib/api";
import {
  getOrCreateTenantId,
  resetTenantId,
  setTenantId,
} from "@/lib/tenant";

/**
 * Settings page — currently just the GitHub PAT override.
 *
 * The token is stored in localStorage and sent as `X-Github-Token` on every
 * /api/ingest and /api/query call. When set, it overrides the server-side
 * GITHUB_TOKEN env var (handled by backend/auth.py:set_github_token_override).
 *
 * Use case: the deploy's pre-configured PAT expired or got rate-limited —
 * paste your own here to keep ingesting + querying without redeploying.
 */
export default function SettingsPage() {
  const [token, setToken] = useState("");
  const [reveal, setReveal] = useState(false);
  const [saved, setSaved] = useState<"idle" | "saved" | "cleared">("idle");

  const [vllmKey, setVllmKey] = useState("");
  const [revealVllm, setRevealVllm] = useState(false);
  const [savedVllm, setSavedVllm] = useState<"idle" | "saved" | "cleared">("idle");

  const [tenant, setTenant] = useState("");
  const [tenantInput, setTenantInput] = useState("");
  const [tenantStatus, setTenantStatus] = useState<"idle" | "copied" | "reset" | "imported" | "invalid">("idle");

  // Text-generation provider override (OpenAI / Gemini / vllm).
  const [provider, setProvider] = useState<LlmProvider>("vllm");
  const [providerKey, setProviderKey] = useState("");
  const [revealProvider, setRevealProvider] = useState(false);
  const [providerModel, setProviderModel] = useState("");
  const [savedProvider, setSavedProvider] = useState<"idle" | "saved" | "cleared">("idle");

  // Embedding provider override (Modal / OpenAI / Gemini).
  const [embedProvider, setEmbedProvider] = useState<EmbedProvider>("vllm");
  const [embedKey, setEmbedKey] = useState("");
  const [revealEmbed, setRevealEmbed] = useState(false);
  const [embedModel, setEmbedModel] = useState("");
  const [savedEmbed, setSavedEmbed] = useState<"idle" | "saved" | "cleared">("idle");

  // Hydrate inputs from localStorage on mount.
  useEffect(() => {
    setToken(getStoredGithubToken());
    setVllmKey(getStoredVllmApiKey());
    setTenant(getOrCreateTenantId());
    setProvider(getStoredLlmProvider());
    setProviderKey(getStoredLlmApiKey());
    setProviderModel(getStoredLlmModel());
    setEmbedProvider(getStoredEmbedProvider());
    setEmbedKey(getStoredEmbedApiKey());
    setEmbedModel(getStoredEmbedModel());
  }, []);

  const handleSave = () => {
    setStoredGithubToken(token);
    setSaved(token.trim() ? "saved" : "cleared");
    setTimeout(() => setSaved("idle"), 2500);
  };

  const handleClear = () => {
    setToken("");
    setStoredGithubToken("");
    setSaved("cleared");
    setTimeout(() => setSaved("idle"), 2500);
  };

  const handleSaveVllm = () => {
    setStoredVllmApiKey(vllmKey);
    setSavedVllm(vllmKey.trim() ? "saved" : "cleared");
    setTimeout(() => setSavedVllm("idle"), 2500);
  };

  const handleClearVllm = () => {
    setVllmKey("");
    setStoredVllmApiKey("");
    setSavedVllm("cleared");
    setTimeout(() => setSavedVllm("idle"), 2500);
  };

  const handleCopyTenant = async () => {
    try {
      await navigator.clipboard.writeText(tenant);
      setTenantStatus("copied");
      setTimeout(() => setTenantStatus("idle"), 2500);
    } catch {
      /* clipboard denied — silent */
    }
  };

  const handleResetTenant = () => {
    const next = resetTenantId();
    setTenant(next);
    setTenantInput("");
    setTenantStatus("reset");
    setTimeout(() => setTenantStatus("idle"), 2500);
  };

  const handleSaveProvider = () => {
    setStoredLlmProvider(provider);
    setStoredLlmApiKey(providerKey);
    setStoredLlmModel(providerModel);
    const cleared = provider === "vllm" && !providerKey.trim() && !providerModel.trim();
    setSavedProvider(cleared ? "cleared" : "saved");
    setTimeout(() => setSavedProvider("idle"), 2500);
  };

  const handleClearProvider = () => {
    setProvider("vllm");
    setProviderKey("");
    setProviderModel("");
    setStoredLlmProvider("vllm");
    setStoredLlmApiKey("");
    setStoredLlmModel("");
    setSavedProvider("cleared");
    setTimeout(() => setSavedProvider("idle"), 2500);
  };

  const handleSaveEmbed = () => {
    setStoredEmbedProvider(embedProvider);
    setStoredEmbedApiKey(embedKey);
    setStoredEmbedModel(embedModel);
    const cleared = embedProvider === "vllm" && !embedKey.trim() && !embedModel.trim();
    setSavedEmbed(cleared ? "cleared" : "saved");
    setTimeout(() => setSavedEmbed("idle"), 2500);
  };

  const handleClearEmbed = () => {
    setEmbedProvider("vllm");
    setEmbedKey("");
    setEmbedModel("");
    setStoredEmbedProvider("vllm");
    setStoredEmbedApiKey("");
    setStoredEmbedModel("");
    setSavedEmbed("cleared");
    setTimeout(() => setSavedEmbed("idle"), 2500);
  };

  const handleImportTenant = () => {
    const ok = setTenantId(tenantInput);
    if (!ok) {
      setTenantStatus("invalid");
      setTimeout(() => setTenantStatus("idle"), 2500);
      return;
    }
    setTenant(tenantInput.trim());
    setTenantInput("");
    setTenantStatus("imported");
    setTimeout(() => setTenantStatus("idle"), 2500);
  };

  const masked = token && !reveal ? "•".repeat(Math.min(token.length, 36)) : token;
  const maskedVllm = vllmKey && !revealVllm ? "•".repeat(Math.min(vllmKey.length, 36)) : vllmKey;
  const maskedProviderKey = providerKey && !revealProvider ? "•".repeat(Math.min(providerKey.length, 36)) : providerKey;
  const providerKeyPlaceholder: Record<LlmProvider, string> = {
    vllm: "(not needed — uses the Modal key below)",
    openai: "sk-…",
    gemini: "AIza…",
  };
  const providerKeyDocsUrl: Record<LlmProvider, string> = {
    vllm: "",
    openai: "https://platform.openai.com/api-keys",
    gemini: "https://aistudio.google.com/apikey",
  };

  const maskedEmbedKey = embedKey && !revealEmbed ? "•".repeat(Math.min(embedKey.length, 36)) : embedKey;
  const embedKeyPlaceholder: Record<EmbedProvider, string> = {
    vllm: "(not needed — uses the Modal key below)",
    openai: "sk-… (can reuse your Text Generation key)",
    gemini: "AIza… (can reuse your Text Generation key)",
  };
  const embedKeyDocsUrl: Record<EmbedProvider, string> = {
    vllm: "",
    openai: "https://platform.openai.com/api-keys",
    gemini: "https://aistudio.google.com/apikey",
  };

  return (
    <div className="h-full overflow-y-auto">
    <div className="mx-auto max-w-2xl py-10 px-6 space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Override the deploy&apos;s API keys with your own. Stored locally in
          this browser only (localStorage) — never sent anywhere except as a
          header on requests to the backend.
        </p>
      </div>

      {/* ─── GitHub PAT ─────────────────────────────────────────────────── */}
      <section className="rounded-xl border border-border bg-card p-6 space-y-4">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-amber-500/15 border border-amber-500/30 p-2">
            <KeyRound className="h-5 w-5 text-amber-400" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-foreground">
              GitHub Personal Access Token
            </h2>
            <p className="text-xs text-muted-foreground">
              Used for repo ingestion. If the server&apos;s default token is
              expired or rate-limited, paste a fresh one here.
            </p>
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            PAT
          </label>
          <div className="flex gap-2">
            <Input
              type={reveal ? "text" : "password"}
              value={reveal ? token : masked}
              onChange={(e) => setToken(e.target.value)}
              placeholder="ghp_… or github_pat_…"
              className="font-mono text-sm flex-1"
              autoComplete="off"
              spellCheck={false}
            />
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={() => setReveal((v) => !v)}
              title={reveal ? "Hide token" : "Reveal token"}
            >
              {reveal ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </Button>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Generate one at{" "}
            <a
              href="https://github.com/settings/tokens/new?scopes=repo&description=repomind"
              target="_blank"
              rel="noreferrer"
              className="text-sky-400 hover:underline"
            >
              github.com/settings/tokens/new
            </a>{" "}
            — needs <code className="px-1 rounded bg-muted">repo</code> (or just{" "}
            <code className="px-1 rounded bg-muted">public_repo</code>) read access.
          </p>
        </div>

        <div className="flex items-center gap-2 pt-2">
          <Button onClick={handleSave} className="bg-sky-500 hover:bg-sky-600 text-white">
            <Save className="h-4 w-4 mr-2" />
            Save
          </Button>
          <Button onClick={handleClear} variant="outline" disabled={!token}>
            <Trash2 className="h-4 w-4 mr-2" />
            Clear
          </Button>
          {saved === "saved" && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-emerald-400">
              <CheckCircle2 className="h-4 w-4" />
              Saved — next request uses this token.
            </span>
          )}
          {saved === "cleared" && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
              <CheckCircle2 className="h-4 w-4" />
              Cleared — falling back to the server&apos;s default.
            </span>
          )}
        </div>
      </section>

      {/* ─── Text Generation provider ───────────────────────────────────── */}
      <section className="rounded-xl border border-border bg-card p-6 space-y-4">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-fuchsia-500/15 border border-fuchsia-500/30 p-2">
            <Sparkles className="h-5 w-5 text-fuchsia-400" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-foreground">
              Text Generation
            </h2>
            <p className="text-xs text-muted-foreground">
              Pick the LLM provider for agent answers. Embeddings always use the
              deploy&apos;s Modal endpoint (configured below) — this only routes
              text generation. Bring your own Anthropic / OpenAI / Gemini key, or
              keep the bundled Qwen on Modal.
            </p>
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            Provider
          </label>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value as LlmProvider)}
            className="w-full h-9 px-3 rounded-md border border-border bg-background text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fuchsia-500/40 focus-visible:border-fuchsia-500/40"
          >
            {LLM_PROVIDERS.map((p) => (
              <option key={p} value={p}>{PROVIDER_LABEL[p]}</option>
            ))}
          </select>
        </div>

        {provider !== "vllm" && (
          <>
            <div className="space-y-2">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                API Key
              </label>
              <div className="flex gap-2">
                <Input
                  type={revealProvider ? "text" : "password"}
                  value={revealProvider ? providerKey : maskedProviderKey}
                  onChange={(e) => setProviderKey(e.target.value)}
                  placeholder={providerKeyPlaceholder[provider]}
                  className="font-mono text-sm flex-1"
                  autoComplete="off"
                  spellCheck={false}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => setRevealProvider((v) => !v)}
                  title={revealProvider ? "Hide key" : "Reveal key"}
                >
                  {revealProvider ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
              </div>
              <p className="text-[11px] text-muted-foreground">
                Get a key at{" "}
                <a
                  href={providerKeyDocsUrl[provider]}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sky-400 hover:underline"
                >
                  {providerKeyDocsUrl[provider].replace("https://", "")}
                </a>
                . Sent as <code className="px-1 rounded bg-muted">X-LLM-Key</code>.
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Model <span className="text-[10px] normal-case text-muted-foreground/70">(optional)</span>
              </label>
              <Input
                value={providerModel}
                onChange={(e) => setProviderModel(e.target.value)}
                placeholder={`default: ${PROVIDER_DEFAULT_MODEL[provider]}`}
                className="font-mono text-sm"
                autoComplete="off"
                spellCheck={false}
              />
              <p className="text-[11px] text-muted-foreground">
                Override the default. Pick any model your key can access.
              </p>
            </div>
          </>
        )}

        <div className="flex items-center gap-2 pt-2">
          <Button onClick={handleSaveProvider} className="bg-fuchsia-500 hover:bg-fuchsia-600 text-white">
            <Save className="h-4 w-4 mr-2" />
            Save
          </Button>
          <Button onClick={handleClearProvider} variant="outline">
            <Trash2 className="h-4 w-4 mr-2" />
            Reset to default
          </Button>
          {savedProvider === "saved" && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-emerald-400">
              <CheckCircle2 className="h-4 w-4" />
              Saved — next query uses {PROVIDER_LABEL[provider].split(" — ")[0]}.
            </span>
          )}
          {savedProvider === "cleared" && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
              <CheckCircle2 className="h-4 w-4" />
              Reset — falling back to the bundled Modal Qwen.
            </span>
          )}
        </div>
      </section>

      {/* ─── Embeddings provider ────────────────────────────────────────── */}
      <section className="rounded-xl border border-border bg-card p-6 space-y-4">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-emerald-500/15 border border-emerald-500/30 p-2">
            <Boxes className="h-5 w-5 text-emerald-400" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-foreground">
              Embeddings
            </h2>
            <p className="text-xs text-muted-foreground">
              Encoder for vector search. Each provider outputs a different
              dimension — switching here gives you a fresh workspace (old
              collections from another provider stay isolated and you&apos;ll
              need to re-ingest). Anthropic isn&apos;t listed because Claude is
              chat-only.
            </p>
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            Provider
          </label>
          <select
            value={embedProvider}
            onChange={(e) => setEmbedProvider(e.target.value as EmbedProvider)}
            className="w-full h-9 px-3 rounded-md border border-border bg-background text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/40 focus-visible:border-emerald-500/40"
          >
            {EMBED_PROVIDERS.map((p) => (
              <option key={p} value={p}>{EMBED_PROVIDER_LABEL[p]}</option>
            ))}
          </select>
        </div>

        {embedProvider !== "vllm" && (
          <>
            <div className="space-y-2">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                API Key
              </label>
              <div className="flex gap-2">
                <Input
                  type={revealEmbed ? "text" : "password"}
                  value={revealEmbed ? embedKey : maskedEmbedKey}
                  onChange={(e) => setEmbedKey(e.target.value)}
                  placeholder={embedKeyPlaceholder[embedProvider]}
                  className="font-mono text-sm flex-1"
                  autoComplete="off"
                  spellCheck={false}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => setRevealEmbed((v) => !v)}
                  title={revealEmbed ? "Hide key" : "Reveal key"}
                >
                  {revealEmbed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
              </div>
              <p className="text-[11px] text-muted-foreground">
                Get a key at{" "}
                <a
                  href={embedKeyDocsUrl[embedProvider]}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sky-400 hover:underline"
                >
                  {embedKeyDocsUrl[embedProvider].replace("https://", "")}
                </a>
                . Sent as <code className="px-1 rounded bg-muted">X-Embed-Key</code>. You can
                reuse the same key as your Text Generation provider.
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Model <span className="text-[10px] normal-case text-muted-foreground/70">(optional)</span>
              </label>
              <Input
                value={embedModel}
                onChange={(e) => setEmbedModel(e.target.value)}
                placeholder={`default: ${EMBED_PROVIDER_DEFAULT_MODEL[embedProvider]}`}
                className="font-mono text-sm"
                autoComplete="off"
                spellCheck={false}
              />
              <p className="text-[11px] text-muted-foreground">
                Vector dimension is fixed by the model — changing this requires re-ingest.
              </p>
            </div>
          </>
        )}

        <div className="flex items-center gap-2 pt-2">
          <Button onClick={handleSaveEmbed} className="bg-emerald-500 hover:bg-emerald-600 text-white">
            <Save className="h-4 w-4 mr-2" />
            Save
          </Button>
          <Button onClick={handleClearEmbed} variant="outline">
            <Trash2 className="h-4 w-4 mr-2" />
            Reset to default
          </Button>
          {savedEmbed === "saved" && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-emerald-400">
              <CheckCircle2 className="h-4 w-4" />
              Saved — re-ingest a repo to use this embedder.
            </span>
          )}
          {savedEmbed === "cleared" && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
              <CheckCircle2 className="h-4 w-4" />
              Reset — falling back to the bundled Modal bge-small.
            </span>
          )}
        </div>
      </section>

      {/* ─── Modal Bearer Key (used when Modal is picked above) ─────────── */}
      <section className="rounded-xl border border-border bg-card p-6 space-y-4">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-sky-500/15 border border-sky-500/30 p-2">
            <Cpu className="h-5 w-5 text-sky-400" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-foreground">
              Modal Bearer Key <span className="text-xs font-normal text-muted-foreground">(Bearer)</span>
            </h2>
            <p className="text-xs text-muted-foreground">
              Used by either Text Generation or Embeddings when their provider is set
              to <code className="px-1 rounded bg-muted">Modal</code>. Paste your own if
              the deploy&apos;s default has rotated or to point at your own Modal
              deployment.
            </p>
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            API Key
          </label>
          <div className="flex gap-2">
            <Input
              type={revealVllm ? "text" : "password"}
              value={revealVllm ? vllmKey : maskedVllm}
              onChange={(e) => setVllmKey(e.target.value)}
              placeholder="Bearer key from your Modal app's secret"
              className="font-mono text-sm flex-1"
              autoComplete="off"
              spellCheck={false}
            />
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={() => setRevealVllm((v) => !v)}
              title={revealVllm ? "Hide key" : "Reveal key"}
            >
              {revealVllm ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </Button>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Sent as <code className="px-1 rounded bg-muted">X-VLLM-Key</code>.
            Backend uses it as the <code className="px-1 rounded bg-muted">Authorization: Bearer</code>{" "}
            for both Qwen generation and embedding calls.
          </p>
        </div>

        <div className="flex items-center gap-2 pt-2">
          <Button onClick={handleSaveVllm} className="bg-sky-500 hover:bg-sky-600 text-white">
            <Save className="h-4 w-4 mr-2" />
            Save
          </Button>
          <Button onClick={handleClearVllm} variant="outline" disabled={!vllmKey}>
            <Trash2 className="h-4 w-4 mr-2" />
            Clear
          </Button>
          {savedVllm === "saved" && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-emerald-400">
              <CheckCircle2 className="h-4 w-4" />
              Saved — next LLM/embedding call uses this key.
            </span>
          )}
          {savedVllm === "cleared" && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
              <CheckCircle2 className="h-4 w-4" />
              Cleared — falling back to the server&apos;s default.
            </span>
          )}
        </div>
      </section>

      {/* ─── Workspace identity (anonymous tenant ID) ───────────────────── */}
      <section className="rounded-xl border border-border bg-card p-6 space-y-4">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-violet-500/15 border border-violet-500/30 p-2">
            <Users className="h-5 w-5 text-violet-400" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-foreground">
              Workspace Identity <span className="text-xs font-normal text-muted-foreground">(no login needed)</span>
            </h2>
            <p className="text-xs text-muted-foreground">
              A random ID minted in this browser. Scopes <em>your</em> ingested
              repos, chat history, and logs — nobody else can see them. Clearing
              browser data, switching browsers, or switching devices means losing
              access to this workspace unless you copy the ID over manually.
            </p>
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            Your tenant ID
          </label>
          <div className="flex gap-2">
            <Input
              value={tenant}
              readOnly
              className="font-mono text-sm flex-1"
              onFocus={(e) => e.currentTarget.select()}
            />
            <Button
              type="button"
              variant="outline"
              size="icon"
              onClick={handleCopyTenant}
              title="Copy to clipboard"
              disabled={!tenant}
            >
              <Copy className="h-4 w-4" />
            </Button>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Sent as <code className="px-1 rounded bg-muted">X-Tenant-Id</code> on
            every request. Treat it like a session token — anyone with this string
            can read your workspace.
          </p>
        </div>

        <div className="space-y-2 pt-2 border-t border-border">
          <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            Import a tenant ID
          </label>
          <div className="flex gap-2">
            <Input
              value={tenantInput}
              onChange={(e) => setTenantInput(e.target.value)}
              placeholder="Paste an existing workspace ID to switch into it"
              className="font-mono text-sm flex-1"
              autoComplete="off"
              spellCheck={false}
            />
            <Button onClick={handleImportTenant} variant="outline" disabled={!tenantInput.trim()}>
              Use this ID
            </Button>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Use this to access the same workspace from another device — copy your
            tenant ID from one browser, paste it here in another.
          </p>
        </div>

        <div className="flex items-center gap-2 pt-2 border-t border-border">
          <Button
            onClick={handleResetTenant}
            variant="outline"
            className="text-amber-400 border-amber-500/30 hover:bg-amber-500/10"
          >
            <RefreshCw className="h-4 w-4 mr-2" />
            Generate new ID
          </Button>
          {tenantStatus === "copied" && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-emerald-400">
              <CheckCircle2 className="h-4 w-4" />
              Copied to clipboard.
            </span>
          )}
          {tenantStatus === "reset" && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-amber-400">
              <AlertTriangle className="h-4 w-4" />
              New workspace started — old repos/logs are no longer visible to you.
            </span>
          )}
          {tenantStatus === "imported" && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-emerald-400">
              <CheckCircle2 className="h-4 w-4" />
              Switched workspace — reload to see its repos.
            </span>
          )}
          {tenantStatus === "invalid" && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-rose-400">
              <AlertTriangle className="h-4 w-4" />
              Invalid ID — letters / digits / dot / dash / underscore only.
            </span>
          )}
        </div>
      </section>

      {/* ─── Notes ───────────────────────────────────────────────────────── */}
      <section className="rounded-xl border border-border bg-muted/30 p-5 text-xs text-muted-foreground space-y-2">
        <p>
          <strong className="text-foreground">Scope of override:</strong> the
          token is used for <em>your</em> requests only (this browser). Other
          users of the deployed app see the server&apos;s default token.
        </p>
        <p>
          <strong className="text-foreground">Security:</strong> stored in
          localStorage of this browser, sent only to the configured backend
          over HTTPS. Clear when you&apos;re done if this is a shared device.
        </p>
        <p>
          <strong className="text-foreground">When to use:</strong> server-side
          PAT expired, got rate-limited, or you want to access a private repo
          your team owns but the deploy&apos;s token doesn&apos;t.
        </p>
      </section>
    </div>
    </div>
  );
}
