"use client"

import { motion } from "framer-motion"
import { 
  GitBranch, 
  Search, 
  Sparkles, 
  Shield, 
  Zap, 
  Code2,
  FileCode,
  AlertTriangle,
  Network
} from "lucide-react"

const features = [
  {
    icon: Network,
    title: "Graph-Powered Analysis",
    description: "Build a knowledge graph of your codebase. See relationships, dependencies, and patterns that static analysis misses.",
  },
  {
    icon: GitBranch,
    title: "Circular Dependencies",
    description: "Detect circular imports and dependency cycles that cause maintenance nightmares and slow builds.",
  },
  {
    icon: AlertTriangle,
    title: "Security Scanning",
    description: "Integrated Bandit, Semgrep, and custom rules catch vulnerabilities before they ship.",
  },
  {
    icon: Search,
    title: "Dead Code Detection",
    description: "Find unused exports, unreachable functions, and dead imports bloating your codebase.",
  },
  {
    icon: Code2,
    title: "Code Quality",
    description: "Ruff, Pylint, Mypy, ESLint — all integrated. One command, comprehensive analysis.",
  },
  {
    icon: Sparkles,
    title: "AI-Powered Fixes",
    description: "Generate fixes with your own API keys (OpenAI, Anthropic). We never see your code or keys.",
  },
]

const containerVariants = {
  hidden: {},
  visible: {
    transition: {
      staggerChildren: 0.1,
    },
  },
}

const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0 },
}

export function CLIFeatures() {
  return (
    <section className="py-20 px-4 sm:px-6 lg:px-8 border-t border-border/50">
      <div className="max-w-6xl mx-auto">
        {/* Section header */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-center mb-16"
        >
          <h2 className="text-3xl sm:text-4xl font-display font-bold text-foreground mb-4">
            Everything you need, locally
          </h2>
          <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
            No cloud required. No data leaves your machine. Full analysis power on your laptop.
          </p>
        </motion.div>

        {/* Features grid */}
        <motion.div
          variants={containerVariants}
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true }}
          className="grid md:grid-cols-2 lg:grid-cols-3 gap-6"
        >
          {features.map((feature) => (
            <motion.div
              key={feature.title}
              variants={itemVariants}
              className="group p-6 rounded-xl bg-muted/30 border border-border/50 hover:border-emerald-500/30 hover:bg-emerald-500/5 transition-all duration-300"
            >
              <div className="flex items-start gap-4">
                <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-500 group-hover:bg-emerald-500/20 transition-colors">
                  <feature.icon className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-lg font-display font-semibold text-foreground mb-2">
                    {feature.title}
                  </h3>
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    {feature.description}
                  </p>
                </div>
              </div>
            </motion.div>
          ))}
        </motion.div>

        {/* Languages supported */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="mt-16 text-center"
        >
          <p className="text-sm text-muted-foreground mb-4">Languages supported</p>
          <div className="flex flex-wrap justify-center gap-3">
            {["Python", "JavaScript", "TypeScript", "Rust", "Go"].map((lang) => (
              <span
                key={lang}
                className="px-4 py-2 rounded-full bg-muted border border-border/50 text-sm text-foreground"
              >
                {lang}
              </span>
            ))}
          </div>
        </motion.div>
      </div>
    </section>
  )
}
