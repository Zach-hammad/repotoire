"use client"

import { motion } from "framer-motion"
import { 
  Users, 
  AlertTriangle, 
  GitPullRequest, 
  TrendingUp,
  Network,
  Shield
} from "lucide-react"

const features = [
  {
    icon: Users,
    title: "Code Ownership",
    description: "See who owns what. Identify knowledge silos before they become problems. Auto-suggest reviewers based on expertise.",
    highlight: "Know who to ask",
  },
  {
    icon: AlertTriangle,
    title: "Bus Factor Analysis",
    description: "Find critical code owned by single developers. Get alerts when key contributors leave or go inactive.",
    highlight: "Reduce risk",
  },
  {
    icon: Network,
    title: "Collaboration Graph",
    description: "Visualize how your team works together. Find isolated developers and strengthen cross-team collaboration.",
    highlight: "Build bridges",
  },
  {
    icon: TrendingUp,
    title: "Health Trends",
    description: "Track code quality over time across all repos. Spot regressions early and celebrate improvements.",
    highlight: "Measure progress",
  },
  {
    icon: GitPullRequest,
    title: "PR Quality Gates",
    description: "Block merges that introduce circular dependencies or critical issues. Automated checks, no manual review fatigue.",
    highlight: "Ship safely",
  },
  {
    icon: Shield,
    title: "Security Dashboard",
    description: "Aggregate security findings across all repos. Prioritize by impact. Track remediation progress.",
    highlight: "Stay secure",
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

export function TeamsFeatures() {
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
            Insights you can't get locally
          </h2>
          <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
            Multi-repo analysis, team dynamics, historical trends.
            <br />
            The stuff that requires a persistent backend.
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
              className="group relative p-6 rounded-xl bg-muted/30 border border-border/50 hover:border-primary/30 hover:bg-primary/5 transition-all duration-300"
            >
              {/* Highlight badge */}
              <div className="absolute -top-2 right-4">
                <span className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
                  {feature.highlight}
                </span>
              </div>

              <div className="flex items-start gap-4">
                <div className="p-2 rounded-lg bg-primary/10 text-primary group-hover:bg-primary/20 transition-colors">
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

        {/* Why cloud callout */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="mt-16 p-8 rounded-2xl bg-muted/30 border border-border/50"
        >
          <div className="max-w-3xl mx-auto text-center">
            <h3 className="text-xl font-display font-semibold text-foreground mb-4">
              Why can't I do this locally?
            </h3>
            <p className="text-muted-foreground leading-relaxed">
              These features require aggregating data across multiple repositories over time.
              The CLI analyzes one repo at a time—great for individuals. Teams need persistent
              storage, scheduled analysis, and a shared dashboard. That's what the cloud provides.
            </p>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
