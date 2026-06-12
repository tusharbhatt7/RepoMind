"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  MessageSquare,
  ScrollText,
  BarChart3,
  GitBranch,
  Zap,
  Cpu,
  ExternalLink,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Database,
  ChevronRight,
  RefreshCw,
  Trash2,
  Settings as SettingsIcon,
} from "lucide-react";
import {
  deleteCollection,
  fetchCollections,
  fetchIngestStatus,
  ingestRepo,
  type Collection,
  type IngestStatus,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

// ─── Nav config ───────────────────────────────────────────────────────────────

const NAV = [
  {
    href: "/chat",
    label: "Chat",
    icon: MessageSquare,
    color: "text-sky-400",
    bg: "bg-sky-500/15",
    activeBg: "bg-sky-500/20",
    activeBorder: "border-sky-500/30",
  },
  {
    href: "/logs",
    label: "Logs",
    icon: ScrollText,
    color: "text-teal-400",
    bg: "bg-teal-500/15",
    activeBg: "bg-teal-500/20",
    activeBorder: "border-teal-500/30",
  },
  {
    href: "/benchmarks",
    label: "Benchmarks",
    icon: BarChart3,
    color: "text-emerald-400",
    bg: "bg-emerald-500/15",
    activeBg: "bg-emerald-500/20",
    activeBorder: "border-emerald-500/30",
  },
  {
    href: "/settings",
    label: "Settings",
    icon: SettingsIcon,
    color: "text-amber-400",
    bg: "bg-amber-500/15",
    activeBg: "bg-amber-500/20",
    activeBorder: "border-amber-500/30",
  },
] as const;

// ─── Sidebar ──────────────────────────────────────────────────────────────────

// Bare collection name the backend will use, matching qualify_collection() in auth.py.
function collectionNameFor(repo: string, mode: "ast" | "naive"): string {
  const [owner, name] = repo.split("/", 2);
  return `${owner}_${name}_${mode}`;
}

const PHASE_LABEL: Record<IngestStatus["phase"], string> = {
  fetching: "Fetching files…",
  embedding: "Embedding chunks…",
  done: "Ready",
  error: "Failed",
};

export function Sidebar() {
  const pathname = usePathname();
  const [repo, setRepo] = useState("");
  const [mode, setMode] = useState<"ast" | "naive">("ast");
  const [collections, setCollections] = useState<Collection[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [status, setStatus] = useState<{ msg: string; type: "ok" | "err" } | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  // Pending/recent ingests keyed by collection name; merged into the indexed-repos list.
  const [pendingMap, setPendingMap] = useState<Record<string, IngestStatus>>({});

  // Resizable sidebar
  const [sidebarWidth, setSidebarWidth] = useState(220);
  const [isDragging, setIsDragging] = useState(false);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(0);

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragStartX.current = e.clientX;
    dragStartWidth.current = sidebarWidth;
    setIsDragging(true);
  }, [sidebarWidth]);

  useEffect(() => {
    if (!isDragging) return;

    function onMouseMove(e: MouseEvent) {
      const delta = e.clientX - dragStartX.current;
      const next = Math.min(400, Math.max(160, dragStartWidth.current + delta));
      setSidebarWidth(next);
    }
    function onMouseUp() {
      setIsDragging(false);
    }

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    return () => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };
  }, [isDragging]);

  useEffect(() => {
    void loadCollections();
    void loadIngestStatus();
  }, []);

  // Poll ingest-status + collections every 3 s while any entry is still in
  // flight, so the badge count grows + the spinner clears without a reload.
  useEffect(() => {
    const anyPending = Object.values(pendingMap).some(
      (p) => p.phase !== "done" && p.phase !== "error",
    );
    if (!anyPending) return;
    const id = setInterval(() => {
      void loadIngestStatus();
      void loadCollections({ silent: true });
    }, 3000);
    return () => clearInterval(id);
  }, [pendingMap]);

  async function loadCollections(opts: { silent?: boolean } = {}) {
    if (!opts.silent) setRefreshing(true);
    try {
      const cols = await fetchCollections();
      setCollections(cols);
      if (cols.length > 0 && !selected) {
        setSelected(cols[0].name);
        window.dispatchEvent(new CustomEvent("collection-changed", { detail: cols[0].name }));
      }
    } finally {
      if (!opts.silent) setRefreshing(false);
    }
  }

  async function loadIngestStatus() {
    const list = await fetchIngestStatus();
    setPendingMap((prev) => {
      const next: Record<string, IngestStatus> = { ...prev };
      // Replace any locally-optimistic entries with the server's truth.
      for (const s of list) next[s.collection_name] = s;
      // Drop entries the server has GC'd that we'd marked done — keeps the
      // sidebar in sync after the 5-minute TTL elapses.
      for (const k of Object.keys(next)) {
        if (!list.find((s) => s.collection_name === k)) {
          if (next[k].phase === "done" || next[k].phase === "error") delete next[k];
        }
      }
      return next;
    });
  }

  async function handleDelete(name: string) {
    const confirmed = window.confirm(
      `Delete "${name}"?\n\nThis removes its chunks from the vector DB and ` +
      `aborts any in-flight ingest. Cannot be undone.`
    );
    if (!confirmed) return;

    // Optimistic remove — feels instant; if the call fails we re-fetch + show error.
    setCollections((prev) => prev.filter((c) => c.name !== name));
    setPendingMap((prev) => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
    if (selected === name) {
      setSelected("");
      window.dispatchEvent(new CustomEvent("collection-changed", { detail: "" }));
    }
    try {
      await deleteCollection(name);
    } catch (e: unknown) {
      setStatus({
        msg: e instanceof Error ? e.message : "Delete failed",
        type: "err",
      });
      // Restore by reloading authoritative state.
      void loadCollections({ silent: true });
      void loadIngestStatus();
    }
  }

  async function handleIngest() {
    if (!repo.includes("/")) {
      setStatus({ msg: "Format: owner/repo", type: "err" });
      return;
    }
    const name = collectionNameFor(repo, mode);
    setLoading(true);
    setStatus(null);
    // Optimistic add — the entry shows up in INDEXED REPOS immediately,
    // with a spinner ring around the count badge until ingest finishes.
    setPendingMap((prev) => ({
      ...prev,
      [name]: {
        collection_name: name,
        repo,
        mode,
        phase: "fetching",
        files_seen: 0,
        total_chunks: 0,
        embed_errors: 0,
        error: null,
        started_at: Date.now() / 1000,
        finished_at: null,
      },
    }));
    try {
      await ingestRepo(repo, mode);
      setStatus({ msg: `Indexing ${repo}…`, type: "ok" });
      // First server poll fast so the UI catches the phase flip to "embedding".
      setTimeout(() => void loadIngestStatus(), 1000);
    } catch (e: unknown) {
      setStatus({ msg: e instanceof Error ? e.message : "Ingest failed", type: "err" });
      setPendingMap((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    } finally {
      setLoading(false);
    }
  }

  return (
    <aside
      style={{ width: sidebarWidth }}
      className="relative flex flex-col shrink-0 h-full border-r border-white/[0.06] bg-[#0d0d10] overflow-hidden select-none"
    >
      {/* Drag handle */}
      <div
        onMouseDown={onDragStart}
        className={cn(
          "absolute top-0 right-0 h-full w-1 z-50 cursor-col-resize group transition-colors",
          isDragging ? "bg-sky-500/60" : "hover:bg-sky-500/40"
        )}
      >
        {/* Centre dots */}
        <div className="absolute top-1/2 -translate-y-1/2 left-1/2 -translate-x-1/2 flex flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          {[0, 1, 2].map((i) => (
            <div key={i} className="w-1 h-1 rounded-full bg-sky-400/80" />
          ))}
        </div>
      </div>

      {/* ── Branding ─────────────────────────────────────────────────────── */}
      <div className="px-4 pt-5 pb-4">
        <div className="flex items-center gap-3">
          {/* Logo mark */}
          <div className="relative shrink-0">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-sky-500 to-blue-600 flex items-center justify-center shadow-lg shadow-sky-500/30">
              <Zap className="w-4 h-4 text-white" />
            </div>
            <div className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-green-400 border-2 border-[#0d0d10]" />
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-tight text-white leading-tight">
              RepoMind
            </h1>
            <p className="text-[11px] text-white/40 leading-tight mt-0.5">RepoMind Agent</p>
          </div>
        </div>
      </div>

      {/* ── Nav ──────────────────────────────────────────────────────────── */}
      <div className="px-2 pb-2">
        <nav className="flex flex-col gap-0.5">
          {NAV.map(({ href, label, icon: Icon, color, bg, activeBg, activeBorder }) => {
            const isActive = pathname.startsWith(href);
            return (
              <Link key={href} href={href}>
                <motion.div
                  whileHover={{ x: 2 }}
                  transition={{ type: "spring", stiffness: 400, damping: 30 }}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-xl cursor-pointer transition-colors",
                    isActive
                      ? `${activeBg} border ${activeBorder} text-white`
                      : "text-white/50 hover:text-white/80 hover:bg-white/[0.04] border border-transparent"
                  )}
                >
                  {/* Icon badge */}
                  <div className={cn(
                    "w-7 h-7 rounded-lg flex items-center justify-center shrink-0 transition-colors",
                    isActive ? bg : "bg-white/[0.06]"
                  )}>
                    <Icon className={cn("w-3.5 h-3.5", isActive ? color : "text-white/40")} />
                  </div>

                  <span className="text-[13px] font-medium leading-none">{label}</span>

                  {isActive && (
                    <ChevronRight className={cn("w-3 h-3 ml-auto", color)} />
                  )}
                </motion.div>
              </Link>
            );
          })}
        </nav>
      </div>

      {/* divider */}
      <div className="mx-3 h-px bg-white/[0.06] mb-1" />

      {/* ── Add Repository ────────────────────────────────────────────────── */}
      <div className="px-3 py-3 flex flex-col gap-2.5">
        <div className="flex items-center gap-2 px-1">
          <GitBranch className="w-3 h-3 text-white/30" />
          <p className="text-[10px] font-semibold text-white/30 uppercase tracking-widest">
            Add Repository
          </p>
        </div>

        <Input
          placeholder="owner/repo"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleIngest()}
          className="h-8 text-xs rounded-lg bg-white/[0.04] border-white/[0.08] text-white/80 placeholder:text-white/20 focus-visible:ring-sky-500/40 focus-visible:border-sky-500/40"
        />

        {/* AST / Naive toggle */}
        <div className="flex gap-1.5 p-1 bg-white/[0.04] rounded-lg border border-white/[0.06]">
          {(["ast", "naive"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={cn(
                "flex-1 py-1 rounded-md text-[11px] font-semibold tracking-wide transition-all",
                mode === m
                  ? "bg-sky-500 text-white shadow-sm shadow-sky-500/30"
                  : "text-white/30 hover:text-white/60"
              )}
            >
              {m.toUpperCase()}
            </button>
          ))}
        </div>

        <motion.div whileTap={{ scale: 0.98 }}>
          <Button
            onClick={handleIngest}
            disabled={loading}
            size="sm"
            className={cn(
              "w-full h-8 text-xs rounded-lg font-semibold tracking-wide transition-all",
              "bg-gradient-to-r from-sky-500 to-blue-600 hover:from-sky-400 hover:to-blue-500",
              "text-white border-0 shadow-md shadow-sky-500/20"
            )}
          >
            {loading ? (
              <span className="flex items-center gap-1.5">
                <Loader2 className="w-3 h-3 animate-spin" /> Triggering…
              </span>
            ) : (
              "Ingest Repo"
            )}
          </Button>
        </motion.div>

        <AnimatePresence>
          {status && (
            <motion.div
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              className={cn(
                "flex items-center gap-1.5 text-[11px] px-1",
                status.type === "ok" ? "text-emerald-400" : "text-red-400"
              )}
            >
              {status.type === "ok"
                ? <CheckCircle2 className="w-3 h-3 shrink-0" />
                : <AlertCircle className="w-3 h-3 shrink-0" />
              }
              <span>{status.msg}</span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* divider */}
      <div className="mx-3 h-px bg-white/[0.06] mb-1" />

      {/* ── Indexed Repos ─────────────────────────────────────────────────── */}
      <div className="px-3 py-3 flex flex-col gap-2 flex-1 min-h-0 overflow-y-auto">
        <div className="flex items-center justify-between px-1">
          <div className="flex items-center gap-2">
            <Database className="w-3 h-3 text-white/30" />
            <p className="text-[10px] font-semibold text-white/30 uppercase tracking-widest">
              Indexed Repos
            </p>
          </div>
          <button
            onClick={() => void loadCollections()}
            disabled={refreshing}
            title="Refresh"
            className="w-5 h-5 inline-flex items-center justify-center rounded text-white/30 hover:text-white/70 hover:bg-white/[0.06] transition-colors"
          >
            <RefreshCw className={cn("w-3 h-3", refreshing && "animate-spin")} />
          </button>
        </div>

        {(() => {
          // Merge confirmed collections with optimistic / pending ingests.
          // A pending entry hides itself once the real collection is back AND
          // the server reports phase=done — that's the moment the badge stops
          // spinning. Order: pending first (so the user sees their click), then
          // existing collections.
          const colNames = new Set(collections.map((c) => c.name));
          const pendingList = Object.values(pendingMap).filter(
            (p) => p.phase !== "done" || !colNames.has(p.collection_name)
          );
          const merged: Array<{ name: string; chunk_count: number; pending?: IngestStatus }> = [
            ...pendingList.map((p) => ({
              name: p.collection_name,
              chunk_count: p.total_chunks,
              pending: p,
            })),
            ...collections
              .filter((c) => !pendingList.find((p) => p.collection_name === c.name))
              .map((c) => ({ name: c.name, chunk_count: c.chunk_count })),
          ];

          if (merged.length === 0) {
            return <p className="text-[11px] text-white/20 px-1 py-1">No repos ingested yet</p>;
          }
          return (
            <div className="flex flex-col gap-1">
              {merged.map((col) => {
                const isActive = selected === col.name;
                const p = col.pending;
                const isBusy = !!p && (p.phase === "fetching" || p.phase === "embedding");
                const isFailed = !!p && p.phase === "error";
                return (
                  <div
                    key={col.name}
                    className={cn(
                      "group/repo flex flex-col w-full px-2.5 py-2 rounded-xl border transition-all",
                      isActive
                        ? "bg-sky-500/10 border-sky-500/25 text-white"
                        : isFailed
                          ? "border-red-500/20 text-red-300/80 hover:bg-red-500/5"
                          : "border-transparent text-white/40 hover:bg-white/[0.04] hover:text-white/70",
                    )}
                  >
                    <div className="flex items-center justify-between w-full gap-1.5">
                      <motion.button
                        onClick={() => {
                          if (isBusy) return; // can't open a still-ingesting repo
                          setSelected(col.name);
                          window.dispatchEvent(
                            new CustomEvent("collection-changed", { detail: col.name })
                          );
                        }}
                        whileHover={!isBusy ? { x: 2 } : undefined}
                        whileTap={!isBusy ? { scale: 0.98 } : undefined}
                        transition={{ type: "spring", stiffness: 400, damping: 30 }}
                        disabled={isBusy}
                        className={cn(
                          "flex items-center gap-2 min-w-0 flex-1 text-left",
                          isBusy && "cursor-default"
                        )}
                      >
                        <div className={cn(
                          "w-1.5 h-1.5 rounded-full shrink-0",
                          isActive ? "bg-sky-400" : isFailed ? "bg-red-400" : "bg-white/20"
                        )} />
                        <span className="text-[11px] font-medium truncate">{col.name}</span>
                      </motion.button>
                      <div className="flex items-center gap-1 shrink-0">
                        <span className={cn(
                          "relative text-[10px] font-mono px-1.5 py-0.5 rounded-md",
                          isActive
                            ? "bg-sky-500/20 text-sky-300"
                            : isFailed
                              ? "bg-red-500/15 text-red-300"
                              : "bg-white/[0.06] text-white/30",
                        )}>
                          {/* Spinner ring around the count badge while ingesting. */}
                          {isBusy && (
                            <span className="absolute -inset-[2px] rounded-md border border-sky-400/40 border-t-sky-400 animate-spin" />
                          )}
                          <span className="relative">{col.chunk_count}</span>
                        </span>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleDelete(col.name);
                          }}
                          title={isBusy ? "Cancel ingest & delete" : "Delete collection"}
                          className="w-5 h-5 inline-flex items-center justify-center rounded text-white/25 hover:text-red-400 hover:bg-red-500/10 opacity-0 group-hover/repo:opacity-100 transition-all"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    </div>
                    {p && (
                      <p className={cn(
                        "mt-1 pl-3.5 text-[10px] truncate",
                        isFailed ? "text-red-300/80" : "text-white/40"
                      )}>
                        {isFailed
                          ? (p.error || "Failed").slice(0, 80)
                          : p.phase === "embedding" && p.total_chunks > 0
                            ? `${PHASE_LABEL[p.phase]} (${p.total_chunks} chunks)`
                            : PHASE_LABEL[p.phase]}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })()}
      </div>

      {/* ── Footer ───────────────────────────────────────────────────────── */}
      <div className="px-3 pb-4">
        <div className="mx-0 h-px bg-white/[0.06] mb-3" />

        {/* Model info pill */}
        <div className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-white/[0.03] border border-white/[0.05] mb-2">
          <Cpu className="w-3 h-3 text-white/30 shrink-0" />
          <span className="text-[10px] text-white/30 truncate">Qwen2.5-7B · Modal GPU</span>
        </div>

        <a
          href="http://localhost:8288"
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-[10px] text-white/30 hover:text-sky-400 hover:bg-sky-500/5 transition-all"
        >
          <ExternalLink className="w-3 h-3 shrink-0" />
          <span>Inngest Dev UI</span>
        </a>
      </div>
    </aside>
  );
}
