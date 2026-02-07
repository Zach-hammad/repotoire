"use client"

import { useState } from "react"
import { motion } from "framer-motion"
import { Button } from "@/components/ui/button"
import { Copy, Check, Terminal, Shield, Zap, Database } from "lucide-react"

export function CLIHero() {
  const [copied, setCopied] = useState(false)
  const installCommand = "pip install repotoire"

  const copyToClipboard = () => {
    navigator.clipboard.writeText(installCommand)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <section className="relative pt-32 pb-20 px-4 sm:px-6 lg:px-8 overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 -z-10">
        <div className="absolute inset-0 dot-grid opacity-50" />
        <div className="absolute top-1/4 -left-32 w-96 h-96 rounded-full bg-emerald-500/5 blur-3xl" />
        <div className="absolute bottom-1/4 -right-32 w-96 h-96 rounded-full bg-emerald-500/5 blur-3xl" />
      </div>

      <div className="max-w-4xl mx-auto">
        {/* Badge */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex justify-center mb-8"
        >
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-sm text-emerald-500">
            <Terminal className="w-4 h-4" />
            <span>Free & Open Source</span>
          </div>
        </motion.div>

        {/* Headline */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="text-center mb-12"
        >
          <h1 className="text-4xl sm:text-5xl lg:text-6xl font-display font-bold tracking-tight text-foreground mb-6">
            Analyze your code
            <br />
            <span className="text-emerald-500">locally.</span>
          </h1>
          <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
            47 detectors. Graph-powered analysis. AI fixes with your own keys.
            <br />
            <span className="text-foreground font-medium">Your code never leaves your machine.</span>
          </p>
        </motion.div>

        {/* Install command */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="flex justify-center mb-12"
        >
          <div className="relative group">
            <div className="flex items-center gap-3 px-6 py-4 rounded-xl bg-muted border border-border font-mono text-lg">
              <span className="text-emerald-500">$</span>
              <span className="text-foreground">{installCommand}</span>
              <Button
                variant="ghost"
                size="sm"
                onClick={copyToClipboard}
                className="ml-4 h-8 w-8 p-0 hover:bg-emerald-500/10"
              >
                {copied ? (
                  <Check className="w-4 h-4 text-emerald-500" />
                ) : (
                  <Copy className="w-4 h-4 text-muted-foreground" />
                )}
              </Button>
            </div>
            {/* Glow effect */}
            <div className="absolute -inset-1 rounded-xl bg-emerald-500/20 blur-xl opacity-0 group-hover:opacity-50 transition-opacity -z-10" />
          </div>
        </motion.div>

        {/* Quick stats */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="grid grid-cols-3 gap-6 max-w-2xl mx-auto"
        >
          <div className="text-center p-4 rounded-xl bg-muted/50 border border-border/50">
            <div className="flex justify-center mb-2">
              <Zap className="w-6 h-6 text-emerald-500" />
            </div>
            <div className="text-2xl font-bold text-foreground">42</div>
            <div className="text-sm text-muted-foreground">Detectors</div>
          </div>
          <div className="text-center p-4 rounded-xl bg-muted/50 border border-border/50">
            <div className="flex justify-center mb-2">
              <Database className="w-6 h-6 text-emerald-500" />
            </div>
            <div className="text-2xl font-bold text-foreground">0</div>
            <div className="text-sm text-muted-foreground">Data Uploaded</div>
          </div>
          <div className="text-center p-4 rounded-xl bg-muted/50 border border-border/50">
            <div className="flex justify-center mb-2">
              <Shield className="w-6 h-6 text-emerald-500" />
            </div>
            <div className="text-2xl font-bold text-foreground">100%</div>
            <div className="text-sm text-muted-foreground">Local</div>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
