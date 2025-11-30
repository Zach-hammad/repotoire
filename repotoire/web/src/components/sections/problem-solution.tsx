"use client"

export function ProblemSolution() {
  return (
    <section className="py-20 px-4 sm:px-6 lg:px-8 border-t border-border">
      <div className="max-w-4xl mx-auto">
        <h2 className="text-3xl sm:text-4xl font-bold text-foreground mb-4 text-center">
          Linters see files. We see relationships.
        </h2>
        <p className="text-muted-foreground text-center mb-12 max-w-2xl mx-auto">
          Traditional tools analyze files in isolation. Repotoire builds a Neo4j knowledge graph combining structural,
          semantic, and relational analysis.
        </p>

        <div className="rounded-lg border border-border overflow-hidden">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border bg-muted">
                <th className="px-6 py-4 text-sm font-medium text-muted-foreground"></th>
                <th className="px-6 py-4 text-sm font-medium text-muted-foreground">Traditional Linters</th>
                <th className="px-6 py-4 text-sm font-medium text-emerald-400">Repotoire</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              <tr>
                <td className="px-6 py-4 text-sm text-foreground font-medium">Analysis scope</td>
                <td className="px-6 py-4 text-sm text-red-400">Single file at a time</td>
                <td className="px-6 py-4 text-sm text-emerald-400">Entire codebase as graph</td>
              </tr>
              <tr>
                <td className="px-6 py-4 text-sm text-foreground font-medium">Architectural issues</td>
                <td className="px-6 py-4 text-sm text-red-400">Not detected</td>
                <td className="px-6 py-4 text-sm text-emerald-400">Cycles, bottlenecks, modularity</td>
              </tr>
              <tr>
                <td className="px-6 py-4 text-sm text-foreground font-medium">Dead code detection</td>
                <td className="px-6 py-4 text-sm text-red-400">Unused variables only</td>
                <td className="px-6 py-4 text-sm text-emerald-400">Cross-file export analysis</td>
              </tr>
              <tr>
                <td className="px-6 py-4 text-sm text-foreground font-medium">Re-scan time (10k files)</td>
                <td className="px-6 py-4 text-sm text-red-400">Full rescan: 5-15 min</td>
                <td className="px-6 py-4 text-sm text-emerald-400">Incremental: 8 seconds</td>
              </tr>
              <tr>
                <td className="px-6 py-4 text-sm text-foreground font-medium">Fix generation</td>
                <td className="px-6 py-4 text-sm text-red-400">Text suggestions</td>
                <td className="px-6 py-4 text-sm text-emerald-400">GPT-4o + RAG code diffs</td>
              </tr>
              <tr>
                <td className="px-6 py-4 text-sm text-foreground font-medium">Health scoring</td>
                <td className="px-6 py-4 text-sm text-red-400">Pass/fail per rule</td>
                <td className="px-6 py-4 text-sm text-emerald-400">Structure + Quality + Architecture</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
