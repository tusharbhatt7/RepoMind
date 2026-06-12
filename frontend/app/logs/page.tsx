"use client";

import { useEffect, useState } from "react";
import { fetchLogs, fetchMetrics, type LogEntry, type Metrics } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const EVENT_ICONS: Record<string, string> = {
  tool_call: "🔧",
  tool_result: "📦",
  final_answer: "✅",
  query_rewrite: "✏️",
  llm_error: "❌",
  tool_error: "❌",
  unexpected_stop: "⚠️",
  max_steps_reached: "⏱️",
};

function MetricCard({ label, value }: { label: string; value: string | number | undefined }) {
  return (
    <Card className="bg-card border-border">
      <CardContent className="px-4 py-3">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-xl font-semibold mt-0.5">{value ?? "—"}</p>
      </CardContent>
    </Card>
  );
}

export default function LogsPage() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [metrics, setMetrics] = useState<Metrics>({});
  const [expanded, setExpanded] = useState<number | null>(null);

  useEffect(() => {
    fetchLogs(50).then(setLogs);
    fetchMetrics().then(setMetrics);
  }, []);

  const totalTokens =
    (metrics.total_input_tokens ?? 0) + (metrics.total_output_tokens ?? 0);

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-y-auto px-6 py-6 space-y-6">
      <h2 className="text-lg font-semibold">Logs</h2>

      {/* Metrics */}
      {Object.keys(metrics).length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <MetricCard label="Total sessions" value={metrics.total_sessions} />
          <MetricCard label="Avg latency" value={metrics.avg_latency_s !== undefined ? `${metrics.avg_latency_s}s` : undefined} />
          <MetricCard label="Avg steps" value={metrics.avg_steps} />
          <MetricCard label="Total tokens" value={totalTokens > 0 ? totalTokens.toLocaleString() : "—"} />
          <MetricCard label="Total cost" value={metrics.total_cost_usd !== undefined ? `$${metrics.total_cost_usd.toFixed(3)}` : undefined} />
        </div>
      )}

      {/* Activity feed */}
      <div>
        <h3 className="text-sm font-medium text-muted-foreground mb-3">Recent activity</h3>
        {logs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No logs yet.</p>
        ) : (
          <div className="flex flex-col gap-1">
            {[...logs].reverse().map((log, i) => (
              <div key={i} className="rounded-md border border-border bg-card overflow-hidden">
                <button
                  onClick={() => setExpanded(expanded === i ? null : i)}
                  className="w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-accent/50 transition-colors text-left"
                >
                  <span>{EVENT_ICONS[log.event] ?? "•"}</span>
                  <span className="font-medium text-foreground">{log.event}</span>
                  <Badge variant="secondary" className="text-xs">step {log.step}</Badge>
                  <span className="text-muted-foreground truncate">
                    session {log.session_id.slice(0, 8)}
                  </span>
                  <span className="ml-auto text-muted-foreground shrink-0">
                    {log.timestamp.slice(11, 19)}
                  </span>
                </button>
                {expanded === i && (
                  <pre className="border-t border-border px-3 py-2 text-xs overflow-x-auto bg-muted text-foreground/80 whitespace-pre-wrap">
                    {JSON.stringify(log.data, null, 2)}
                  </pre>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
