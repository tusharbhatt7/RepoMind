"use client";

import { useState } from "react";
import type { LogEntry } from "@/lib/api";

const EVENT_ICONS: Record<string, string> = {
  query_rewrite: "✏️",
  tool_call: "🔧",
  tool_result: "📦",
  final_answer: "✅",
  unexpected_stop: "⚠️",
  max_steps_reached: "⏱️",
  llm_error: "❌",
  tool_error: "❌",
};

export function ReasoningExpander({
  steps,
  logs,
}: {
  steps: number;
  logs: LogEntry[];
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-2 rounded-md border border-border overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:bg-accent/50 transition-colors text-left"
      >
        <span>{open ? "▼" : "▶"}</span>
        <span>Agent reasoning ({steps} steps)</span>
      </button>
      {open && (
        <div className="border-t border-border divide-y divide-border">
          {logs.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">No logs available</p>
          ) : (
            logs.map((log, i) => (
              <LogRow key={i} log={log} />
            ))
          )}
        </div>
      )}
    </div>
  );
}

function LogRow({ log }: { log: LogEntry }) {
  const [expanded, setExpanded] = useState(false);
  const icon = EVENT_ICONS[log.event] ?? "•";

  return (
    <div className="px-3 py-2">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 text-xs text-left hover:text-foreground transition-colors"
      >
        <span>{icon}</span>
        <span className="font-medium text-foreground">{log.event}</span>
        <span className="text-muted-foreground">· step {log.step}</span>
        <span className="text-muted-foreground ml-auto">{log.timestamp.slice(11, 19)}</span>
      </button>
      {expanded && (
        <pre className="mt-2 p-2 rounded bg-muted text-xs overflow-x-auto text-foreground/80 whitespace-pre-wrap">
          {JSON.stringify(log.data, null, 2)}
        </pre>
      )}
    </div>
  );
}
