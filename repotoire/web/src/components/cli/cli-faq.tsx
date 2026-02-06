"use client"

import { motion } from "framer-motion"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Users } from "lucide-react"

const faqs = [
  {
    question: "Is my code sent anywhere?",
    answer: "No. The CLI runs 100% locally. Your code is analyzed on your machine using Kuzu, an embedded graph database. Nothing is uploaded unless you explicitly use the cloud dashboard.",
  },
  {
    question: "How do AI fixes work with BYOK?",
    answer: "Bring Your Own Keys (BYOK) means you provide your OpenAI, Anthropic, or other API keys. When generating fixes, we send code snippets directly to your chosen provider using your keys. We never see your code or keys.",
  },
  {
    question: "What's the difference between CLI and Cloud?",
    answer: "The CLI is free forever and runs locally — perfect for individual developers. The Cloud dashboard adds team features: code ownership, bus factor analysis, cross-repo insights, and PR integration. Teams need the cloud; individuals can use CLI forever.",
  },
  {
    question: "What languages are supported?",
    answer: "Python and JavaScript/TypeScript have full graph analysis. Rust and Go are supported with limited graph features. More languages coming soon.",
  },
  {
    question: "Can I use this in CI/CD?",
    answer: "Yes! Use `repotoire analyze . --ci` for CI-friendly output. Add `--fail-on critical` to fail builds on critical issues. See docs for GitHub Actions examples.",
  },
  {
    question: "Is it really free?",
    answer: "The CLI is free forever under Apache 2.0. No usage limits. No time limits. Team features require a paid plan, but solo developers can use the CLI indefinitely.",
  },
]

export function CLIFAQ() {
  return (
    <section className="py-20 px-4 sm:px-6 lg:px-8">
      <div className="max-w-3xl mx-auto">
        {/* Section header */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-center mb-12"
        >
          <h2 className="text-3xl sm:text-4xl font-display font-bold text-foreground mb-4">
            Questions?
          </h2>
        </motion.div>

        {/* FAQ */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
        >
          <Accordion type="single" collapsible className="space-y-3">
            {faqs.map((faq, index) => (
              <AccordionItem
                key={index}
                value={`item-${index}`}
                className="border border-border/50 rounded-xl px-6 bg-muted/30 hover:bg-muted/50 transition-colors"
              >
                <AccordionTrigger className="text-left font-display font-medium text-foreground hover:no-underline py-4">
                  {faq.question}
                </AccordionTrigger>
                <AccordionContent className="text-muted-foreground pb-4">
                  {faq.answer}
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </motion.div>

        {/* Teams upsell */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="mt-16 p-8 rounded-2xl bg-primary/5 border border-primary/20 text-center"
        >
          <div className="flex justify-center mb-4">
            <div className="p-3 rounded-full bg-primary/10">
              <Users className="w-6 h-6 text-primary" />
            </div>
          </div>
          <h3 className="text-xl font-display font-semibold text-foreground mb-2">
            Building with a team?
          </h3>
          <p className="text-muted-foreground mb-6 max-w-md mx-auto">
            See who owns what code, identify knowledge silos, and block risky PRs before they merge.
          </p>
          <Link href="/teams">
            <Button className="font-display bg-primary hover:bg-primary/90 text-primary-foreground">
              Explore Team Features
            </Button>
          </Link>
        </motion.div>
      </div>
    </section>
  )
}
