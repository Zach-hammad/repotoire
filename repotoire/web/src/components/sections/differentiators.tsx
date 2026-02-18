"use client"

const comparisons = [
  {
    title: "Speed",
    competitor: "5-15 min full codebase scans",
    repotoire: "8 second incremental analysis",
  },
  {
    title: "Detection",
    competitor: "Single-file syntax issues",
    repotoire: "Cross-file architectural problems",
  },
  {
    title: "Fixes",
    competitor: "Generic suggestions",
    repotoire: "AI-generated code with 70% approval rate",
  },
  {
    title: "Thresholds",
    competitor: "Fixed arbitrary numbers",
    repotoire: "Adaptive — learns YOUR coding style",
  },
]

export function Differentiators() {
  return (
    <section className="py-20 px-4 sm:px-6 lg:px-8 bg-muted/30">
      <div className="max-w-4xl mx-auto">
        <div className="text-center mb-12">
          <h2 className="text-3xl sm:text-4xl font-bold text-foreground mb-4">How we compare</h2>
        </div>

        <div className="bg-card rounded-lg border border-border overflow-hidden">
          <div className="grid grid-cols-3 text-sm font-medium border-b border-border">
            <div className="p-4"></div>
            <div className="p-4 text-center text-muted-foreground">Traditional</div>
            <div className="p-4 text-center text-primary">Repotoire</div>
          </div>
          {comparisons.map((row) => (
            <div key={row.title} className="grid grid-cols-3 text-sm border-b border-border last:border-0">
              <div className="p-4 font-medium text-foreground">{row.title}</div>
              <div className="p-4 text-center text-muted-foreground">{row.competitor}</div>
              <div className="p-4 text-center text-foreground">{row.repotoire}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
