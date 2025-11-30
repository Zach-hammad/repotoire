"use client"

const testimonials = [
  {
    quote:
      "Found circular dependencies that had been silently breaking hot reload for 2 years. Fixed them all in an afternoon.",
    author: "Engineering Lead at a Series B startup",
    role: "(200k LOC codebase)",
  },
]

export function SocialProof() {
  return (
    <section className="py-20 px-4 sm:px-6 lg:px-8 border-t border-border">
      <div className="max-w-4xl mx-auto">
        <h2 className="text-2xl font-bold text-foreground mb-8 text-center">Results from real teams</h2>

        <div className="grid md:grid-cols-3 gap-6">
          <div className="bg-card rounded-lg border border-border p-6">
            <div className="text-3xl font-bold text-emerald-400 mb-2">47</div>
            <div className="text-sm text-foreground font-medium mb-1">circular dependencies fixed</div>
            <div className="text-xs text-muted-foreground">in a 200k LOC TypeScript monorepo</div>
          </div>

          <div className="bg-card rounded-lg border border-border p-6">
            <div className="text-3xl font-bold text-emerald-400 mb-2">12 min → 8 sec</div>
            <div className="text-sm text-foreground font-medium mb-1">CI time reduction</div>
            <div className="text-xs text-muted-foreground">with incremental analysis</div>
          </div>

          <div className="bg-card rounded-lg border border-border p-6">
            <div className="text-3xl font-bold text-emerald-400 mb-2">2,340</div>
            <div className="text-sm text-foreground font-medium mb-1">dead exports removed</div>
            <div className="text-xs text-muted-foreground">saving 18% bundle size</div>
          </div>
        </div>

        <div className="mt-12 bg-card rounded-lg border border-border p-8 text-center">
          <p className="text-lg text-foreground mb-4">
            "We found circular dependencies that had been silently breaking hot reload for 2 years. Fixed them all in an
            afternoon."
          </p>
          <div className="text-sm text-muted-foreground">
            — Engineering Lead at a Series B startup (200k LOC codebase)
          </div>
        </div>
      </div>
    </section>
  )
}
