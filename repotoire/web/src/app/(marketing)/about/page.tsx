"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  Github,
  ArrowRight,
  Code2,
  GitBranch,
  Sparkles,
  Target,
  Heart,
} from "lucide-react";

const values: Array<{
  icon: typeof Target;
  title: string;
  description: string;
  link?: string;
}> = [
  {
    icon: Target,
    title: "Developer-First",
    description:
      "We build tools that developers actually want to use. Fast, unobtrusive, and integrated into your workflow.",
  },
  {
    icon: Code2,
    title: "Open Source",
    description:
      "Our core analysis engine is open source. We believe in transparency and community-driven development.",
    link: "https://github.com/repotoire/repotoire",
  },
  {
    icon: Sparkles,
    title: "AI-Powered Fixes",
    description:
      "AI assists humans, not replaces them. Every fix requires human approval before being applied.",
  },
  {
    icon: Heart,
    title: "Quality Over Speed",
    description:
      "We ship when it's ready. No rushed features, no technical debt. We practice what we preach.",
  },
];

export default function AboutPage() {
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    setIsVisible(true);
  }, []);

  return (
    <section className="py-24 px-4 sm:px-6 lg:px-8">
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className={cn("text-center mb-16 opacity-0", isVisible && "animate-fade-up")}>
          <h1 className="text-4xl sm:text-5xl tracking-tight text-foreground mb-6">
            <span className="font-serif italic text-muted-foreground">About</span>{" "}
            <span className="text-gradient font-display font-bold">Repotoire</span>
          </h1>
          <p className="text-xl text-muted-foreground max-w-2xl mx-auto">
            We're building the future of code health analysis with graph-powered
            intelligence and AI-assisted fixes.
          </p>
        </div>

        {/* Mission */}
        <div
          className={cn(
            "card-elevated rounded-xl p-8 mb-16 opacity-0",
            isVisible && "animate-fade-up delay-100"
          )}
        >
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            Our Mission
          </h2>
          <p className="text-lg text-muted-foreground leading-relaxed mb-6">
            Traditional linters examine files in isolation. They catch syntax errors
            and style violations, but miss the architectural issues that truly slow
            teams down: circular dependencies, bottleneck modules, and code that's
            impossible to test.
          </p>
          <p className="text-lg text-muted-foreground leading-relaxed">
            Repotoire builds a knowledge graph of your codebase, combining structural
            analysis (AST), semantic understanding (NLP + AI), and relational patterns
            (graph algorithms). This multi-layered approach detects issues that
            traditional tools miss and provides actionable, AI-powered fixes.
          </p>
        </div>

        {/* Values */}
        <div className={cn("mb-16 opacity-0", isVisible && "animate-fade-up delay-200")}>
          <h2 className="text-2xl font-display font-bold text-foreground mb-8 text-center">
            Our Values
          </h2>
          <div className="grid sm:grid-cols-2 gap-6">
            {values.map((value, i) => {
              const Icon = value.icon;
              return (
                <div
                  key={value.title}
                  className="card-elevated rounded-xl p-6 flex gap-4"
                  style={{ animationDelay: `${200 + i * 50}ms` }}
                >
                  <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                    <Icon className="w-5 h-5 text-primary" />
                  </div>
                  <div>
                    <h3 className="font-display font-bold text-foreground mb-1">
                      {value.title}
                    </h3>
                    <p className="text-sm text-muted-foreground">
                      {value.description}
                      {value.link && (
                        <>
                          {" "}
                          <a
                            href={value.link}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary hover:underline"
                          >
                            View on GitHub &rarr;
                          </a>
                        </>
                      )}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* CTA */}
        <div
          className={cn(
            "card-elevated rounded-xl p-8 text-center opacity-0",
            isVisible && "animate-fade-up delay-300"
          )}
        >
          <GitBranch className="w-12 h-12 text-primary mx-auto mb-4" />
          <h2 className="text-2xl font-display font-bold text-foreground mb-2">
            Ready to improve your code health?
          </h2>
          <p className="text-muted-foreground mb-6">
            Try Repotoire on your codebase and see what your linter is missing.
          </p>
          <div className="flex items-center justify-center gap-4">
            <Link href="/docs/cli">
              <Button size="lg" className="font-display">
                Get Started Free
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </Link>
            <a
              href="https://github.com/repotoire/repotoire"
              target="_blank"
              rel="noopener noreferrer"
            >
              <Button variant="outline" size="lg" className="font-display">
                <Github className="mr-2 h-4 w-4" />
                View on GitHub
              </Button>
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
