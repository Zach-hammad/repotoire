import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "CLI Telemetry | Repotoire",
  description: "What the Repotoire CLI collects when you opt in to telemetry",
};

export default function TelemetryPage() {
  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-3xl px-4 py-12 sm:px-6 lg:px-8">
        <article className="prose prose-slate dark:prose-invert max-w-none">
          <h1 className="text-3xl font-bold tracking-tight">
            CLI Telemetry
          </h1>
          <p className="text-muted-foreground">
            What the Repotoire CLI collects when you opt in
          </p>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">Overview</h2>
            <p>
              Repotoire collects anonymous usage data from CLI users who
              explicitly opt in. This data helps us improve the tool and powers
              ecosystem benchmarks &mdash; so you can see how your project
              compares to others.
            </p>
            <p>
              <strong>Telemetry is off by default.</strong> You must explicitly
              enable it. You can disable it at any time.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">What we collect</h2>

            <h3 className="text-xl font-medium mt-4">Analysis data</h3>
            <ul className="list-disc pl-6 space-y-2">
              <li>
                Score, grade, and pillar breakdowns (structure, quality,
                architecture)
              </li>
              <li>
                Finding counts by severity, detector, and category &mdash; not
                the findings themselves
              </li>
              <li>
                Graph metrics: node/edge counts, modularity, circular
                dependencies, coupling
              </li>
              <li>
                Repo shape: workspace/monorepo detection, language breakdown,
                lines of code
              </li>
              <li>
                Detected frameworks (e.g. &quot;django&quot;,
                &quot;actix-web&quot;)
              </li>
              <li>Calibration threshold divergence from defaults</li>
              <li>Analysis duration and mode (cold/incremental/cached)</li>
            </ul>

            <h3 className="text-xl font-medium mt-4">Usage data</h3>
            <ul className="list-disc pl-6 space-y-2">
              <li>Which commands you run and how long they take</li>
              <li>Fix acceptance/rejection per detector</li>
              <li>True positive/false positive feedback labels</li>
              <li>Watch session duration and reanalysis counts</li>
              <li>Diff score deltas</li>
            </ul>

            <h3 className="text-xl font-medium mt-4">System info</h3>
            <ul className="list-disc pl-6 space-y-2">
              <li>Operating system (linux/macos/windows)</li>
              <li>Repotoire version</li>
              <li>Whether running in CI</li>
            </ul>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">What we never collect</h2>
            <ul className="list-disc pl-6 space-y-2">
              <li>Repository names or URLs</li>
              <li>File paths or code content</li>
              <li>Git author names or emails</li>
              <li>API keys or credentials</li>
              <li>IP-based geolocation (disabled in our analytics provider)</li>
            </ul>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">How it works</h2>
            <p>
              Each CLI install gets a random anonymous ID (UUID). Each
              repository gets a one-way hash (SHA-256 of the root commit)
              &mdash; we can track trends for the same repo without knowing
              what repo it is.
            </p>
            <p>
              Events are sent to our analytics backend. A scheduled job
              computes aggregate benchmarks and publishes them as static JSON.
              Your CLI fetches these to show ecosystem comparisons.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">Managing telemetry</h2>
            <pre className="bg-muted rounded-lg p-4 text-sm overflow-x-auto">
              <code>{`repotoire config telemetry on      # enable
repotoire config telemetry off     # disable
repotoire config telemetry status  # check current state`}</code>
            </pre>
            <p className="mt-4">Environment variable overrides:</p>
            <ul className="list-disc pl-6 space-y-2">
              <li>
                <code>REPOTOIRE_TELEMETRY=off</code> &mdash; disable
                per-invocation
              </li>
              <li>
                <code>DO_NOT_TRACK=1</code> &mdash; industry standard, always
                honored
              </li>
            </ul>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">What you get back</h2>
            <p>With telemetry enabled, after every analysis you see:</p>
            <ul className="list-disc pl-6 space-y-2">
              <li>Your score percentile among similar projects</li>
              <li>
                Pillar comparisons (structure, quality, architecture)
              </li>
              <li>Graph health benchmarks (modularity, coupling)</li>
              <li>Detector accuracy rates from community feedback</li>
            </ul>
            <p className="mt-4">
              Run <code>repotoire benchmark</code> for the full comparison.
            </p>
          </section>
        </article>
      </div>
    </div>
  );
}
