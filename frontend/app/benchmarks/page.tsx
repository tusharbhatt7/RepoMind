"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";

interface BenchmarkResult {
  query: string;
  ast_avg_score: number;
  naive_avg_score: number;
  winner: string;
  delta: number;
}

interface BenchmarkData {
  ast_wins: number;
  total_queries: number;
  avg_ast_score: number;
  avg_naive_score: number;
  results: BenchmarkResult[];
}

interface EvalResult {
  passed: boolean;
  query?: string;
  [key: string]: unknown;
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <Card className="bg-card border-border">
      <CardContent className="px-4 py-3">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-xl font-semibold mt-0.5">{value}</p>
      </CardContent>
    </Card>
  );
}

export default function BenchmarksPage() {
  const [benchmark, setBenchmark] = useState<BenchmarkData | null>(null);
  const [evalResults, setEvalResults] = useState<EvalResult[] | null>(null);
  const [benchError, setBenchError] = useState(false);
  const [evalError, setEvalError] = useState(false);

  useEffect(() => {
    fetch("/benchmark_results.json")
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setBenchmark)
      .catch(() => setBenchError(true));

    fetch("/eval_results.jsonl")
      .then((r) => (r.ok ? r.text() : Promise.reject()))
      .then((text) => {
        const lines = text
          .split("\n")
          .filter(Boolean)
          .map((l) => JSON.parse(l));
        setEvalResults(lines);
      })
      .catch(() => setEvalError(true));
  }, []);

  const passRate =
    evalResults && evalResults.length > 0
      ? evalResults.filter((r) => r.passed).length / evalResults.length
      : null;

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-y-auto px-6 py-6 space-y-8">
      <h2 className="text-lg font-semibold">Benchmarks</h2>

      {/* AST vs naive */}
      <section>
        <h3 className="text-sm font-medium mb-3">AST vs Naive chunking</h3>
        {benchError || !benchmark ? (
          <p className="text-sm text-muted-foreground">
            {benchError
              ? "No benchmark data found. Run: python eval/compare.py owner/repo"
              : "Loading…"}
          </p>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-3 mb-4">
              <MetricCard
                label="AST wins"
                value={`${benchmark.ast_wins}/${benchmark.total_queries}`}
              />
              <MetricCard
                label="Avg AST score"
                value={String(benchmark.avg_ast_score)}
              />
              <MetricCard
                label="Avg naive score"
                value={String(benchmark.avg_naive_score)}
              />
            </div>
            <div className="rounded-md border border-border overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-muted text-muted-foreground">
                  <tr>
                    {["Query", "AST score", "Naive score", "Winner", "Delta"].map((h) => (
                      <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {benchmark.results.map((r, i) => (
                    <tr key={i} className="hover:bg-accent/30 transition-colors">
                      <td className="px-3 py-2 max-w-[240px] truncate">{r.query}</td>
                      <td className="px-3 py-2">{r.ast_avg_score}</td>
                      <td className="px-3 py-2">{r.naive_avg_score}</td>
                      <td className="px-3 py-2">
                        <span
                          className={`font-medium ${
                            r.winner === "ast" ? "text-green-400" : "text-yellow-400"
                          }`}
                        >
                          {r.winner}
                        </span>
                      </td>
                      <td className="px-3 py-2">{r.delta}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      {/* Correctness */}
      <section>
        <h3 className="text-sm font-medium mb-3">Correctness tests</h3>
        {evalError || !evalResults ? (
          <p className="text-sm text-muted-foreground">
            {evalError
              ? "No eval data found. Run: python eval/test_queries.py <collection>"
              : "Loading…"}
          </p>
        ) : (
          <>
            {passRate !== null && (
              <div className="grid grid-cols-1 gap-3 mb-4 max-w-xs">
                <MetricCard label="Pass rate" value={`${(passRate * 100).toFixed(0)}%`} />
              </div>
            )}
            <div className="rounded-md border border-border overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-muted text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">Query</th>
                    <th className="px-3 py-2 text-left font-medium">Passed</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {evalResults.map((r, i) => (
                    <tr key={i} className="hover:bg-accent/30 transition-colors">
                      <td className="px-3 py-2 max-w-[320px] truncate">
                        {(r.query as string) ?? "—"}
                      </td>
                      <td className="px-3 py-2">
                        <span className={r.passed ? "text-green-400" : "text-destructive"}>
                          {r.passed ? "✅ Yes" : "❌ No"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
