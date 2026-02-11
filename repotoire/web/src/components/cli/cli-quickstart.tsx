"use client"

import { motion } from "framer-motion"
import { Button } from "@/components/ui/button"
import { Copy, Check, ChevronRight } from "lucide-react"
import Link from "next/link"
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard"

const steps = [
  {
    title: "Install",
    command: "cargo install repotoire",
    output: "Installed package `repotoire v0.3.36`",
  },
  {
    title: "Analyze",
    command: "repotoire analyze .",
    output: `Scanning repository...
✓ Built code graph (847 nodes, 2,341 edges)
✓ Running 81 detectors...

╭───────────────────────────────────────╮
│  Health Score: 87/100                 │
├───────────────────────────────────────┤
│  Structure:     92%  ████████████░░   │
│  Quality:       85%  ██████████░░░░   │
│  Architecture:  78%  █████████░░░░░   │
╰───────────────────────────────────────╯

Found 23 issues (3 critical, 8 high, 12 medium)`,
  },
  {
    title: "Fix",
    command: "repotoire fix 1 --model gpt-4",
    output: `Generating fix for: Circular dependency detected
Using model: gpt-4 (BYOK)

╭─ Fix Preview ─────────────────────────╮
│  Move shared types to new module      │
│  src/types.py (new file)              │
│  - 3 files modified                   │
╰───────────────────────────────────────╯

Apply fix? [y/N]`,
  },
]

function CodeBlock({ command, output }: { command: string; output: string }) {
  const { copied, copy } = useCopyToClipboard()

  const copyCommand = () => {
    copy(command)
  }

  return (
    <div className="rounded-xl overflow-hidden border border-border bg-background">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-muted/50 border-b border-border">
        <div className="flex items-center gap-2">
          <div className="flex gap-1.5">
            <span className="w-3 h-3 rounded-full bg-red-500/80" />
            <span className="w-3 h-3 rounded-full bg-amber-500/80" />
            <span className="w-3 h-3 rounded-full bg-green-500/80" />
          </div>
          <span className="text-xs text-muted-foreground font-mono">terminal</span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={copyCommand}
          className="h-6 px-2 text-xs"
        >
          {copied ? (
            <Check className="w-3 h-3 mr-1 text-primary" />
          ) : (
            <Copy className="w-3 h-3 mr-1" />
          )}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>

      {/* Content */}
      <div className="p-4 font-mono text-sm">
        <div className="flex items-center gap-2 text-foreground mb-3">
          <span className="text-primary">$</span>
          <span>{command}</span>
        </div>
        <pre className="text-muted-foreground whitespace-pre-wrap text-xs leading-relaxed">
          {output}
        </pre>
      </div>
    </div>
  )
}

export function CLIQuickStart() {
  return (
    <section className="py-20 px-4 sm:px-6 lg:px-8 bg-muted/30 border-y border-border/50">
      <div className="max-w-4xl mx-auto">
        {/* Section header */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-center mb-12"
        >
          <h2 className="text-3xl sm:text-4xl font-display font-bold text-foreground mb-4">
            Get started in 30 seconds
          </h2>
          <p className="text-lg text-muted-foreground">
            Three commands. That's it.
          </p>
        </motion.div>

        {/* Steps */}
        <div className="space-y-8">
          {steps.map((step, index) => (
            <motion.div
              key={step.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: index * 0.1 }}
            >
              <div className="flex items-center gap-3 mb-3">
                <span className="flex items-center justify-center w-8 h-8 rounded-full bg-primary/10 text-primary font-mono font-bold text-sm">
                  {index + 1}
                </span>
                <span className="text-lg font-display font-semibold text-foreground">
                  {step.title}
                </span>
              </div>
              <CodeBlock command={step.command} output={step.output} />
            </motion.div>
          ))}
        </div>

        {/* CTA */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="mt-12 text-center"
        >
          <Link href="/docs/cli">
            <Button
              size="lg"
              variant="outline"
              className="group h-12 px-6 text-base font-display border-primary/30 hover:border-primary/50 hover:bg-primary/5"
            >
              <span>Read the docs</span>
              <ChevronRight className="w-4 h-4 ml-2 group-hover:translate-x-1 transition-transform" />
            </Button>
          </Link>
        </motion.div>
      </div>
    </section>
  )
}
