"use client"

export function HowItWorks() {
  return (
    <section id="how-it-works" className="py-20 px-4 sm:px-6 lg:px-8">
      <div className="max-w-6xl mx-auto">
        <div className="text-center mb-12">
          <h2 className="text-3xl sm:text-4xl font-bold text-foreground mb-4">Setup in 5 minutes</h2>
          <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
            Connect your repo, build the graph, get actionable insights.
          </p>
        </div>

        <div className="grid lg:grid-cols-3 gap-6">
          {/* Step 1 */}
          <div className="bg-card rounded-lg border border-border p-6">
            <div className="flex items-center gap-3 mb-4">
              <span className="text-3xl font-bold text-muted-foreground/30">01</span>
              <h3 className="text-lg font-semibold text-foreground">Connect</h3>
            </div>
            <div className="bg-muted rounded p-4 font-mono text-sm">
              <div className="text-muted-foreground"># Install CLI</div>
              <div className="text-emerald-400">npx repotoire init</div>
              <div className="text-muted-foreground mt-2">✓ Connected to github.com/acme/app</div>
              <div className="text-muted-foreground">✓ Found 2,847 files</div>
            </div>
          </div>

          {/* Step 2 */}
          <div className="bg-card rounded-lg border border-border p-6">
            <div className="flex items-center gap-3 mb-4">
              <span className="text-3xl font-bold text-muted-foreground/30">02</span>
              <h3 className="text-lg font-semibold text-foreground">Analyze</h3>
            </div>
            <div className="bg-muted rounded p-4 font-mono text-sm">
              <div className="text-muted-foreground">Building knowledge graph...</div>
              <div className="mt-2 h-2 bg-background rounded-full overflow-hidden">
                <div className="h-full w-full bg-emerald-500 rounded-full" />
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                <div>
                  <span className="text-muted-foreground">Nodes:</span> <span className="text-emerald-400">12,458</span>
                </div>
                <div>
                  <span className="text-muted-foreground">Edges:</span> <span className="text-emerald-400">47,921</span>
                </div>
              </div>
            </div>
          </div>

          {/* Step 3 */}
          <div className="bg-card rounded-lg border border-border p-6">
            <div className="flex items-center gap-3 mb-4">
              <span className="text-3xl font-bold text-muted-foreground/30">03</span>
              <h3 className="text-lg font-semibold text-foreground">Fix</h3>
            </div>
            <div className="bg-muted rounded p-4 text-sm">
              <div className="flex items-center gap-2 text-red-400 font-medium mb-2">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                  />
                </svg>
                Circular dependency
              </div>
              <div className="text-xs text-muted-foreground mb-3">auth.ts → user.ts → auth.ts</div>
              <button className="w-full bg-emerald-500 hover:bg-emerald-600 text-white text-sm py-2 rounded font-medium transition-colors">
                Apply AI Fix
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
