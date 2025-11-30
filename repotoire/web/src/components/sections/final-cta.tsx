"use client"

import { Button } from "@/components/ui/button"

function ZapIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </svg>
  )
}

export function FinalCTA() {
  return (
    <section className="py-20 px-4 sm:px-6 lg:px-8 bg-background">
      <div className="max-w-2xl mx-auto text-center">
        <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm mb-8">
          <ZapIcon className="w-4 h-4" />
          Join 500+ engineering teams
        </div>

        <h2 className="text-3xl sm:text-4xl font-bold text-foreground mb-4 text-balance">
          Stop shipping bugs linters miss
        </h2>

        <p className="text-lg text-muted-foreground mb-8">
          Find architectural issues in seconds. Fix them with AI. Start free.
        </p>

        <div className="flex flex-col sm:flex-row items-center justify-center gap-3 mb-6">
          <Button size="lg" className="bg-emerald-500 hover:bg-emerald-600 text-white h-12 px-8 text-base font-medium">
            Start Free Trial
          </Button>
          <Button size="lg" variant="outline" className="h-12 px-8 text-base bg-transparent">
            See Demo
          </Button>
        </div>

        <p className="text-sm text-muted-foreground">No credit card required. 14-day free trial.</p>
      </div>
    </section>
  )
}
