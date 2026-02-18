"use client"

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"

const faqs = [
  {
    question: "Is Repotoire really free?",
    answer:
      "Yes. Repotoire is MIT-licensed and free forever. All 114 detectors, all 13 languages, all export formats. No freemium, no feature gates, no cloud account required.",
  },
  {
    question: "Do I need an API key?",
    answer:
      "No. All analysis runs locally with zero network calls. API keys are only needed for the optional AI-powered fix feature (bring your own key from Anthropic, OpenAI, or use Ollama for 100% free local AI).",
  },
  {
    question: "What languages are supported?",
    answer:
      "Full graph analysis with tree-sitter: Python, TypeScript, JavaScript, Go, Java, Rust, C, C++, C#. Security and quality scanning via regex: Ruby, PHP, Kotlin, Swift.",
  },
  {
    question: "How does it compare to SonarQube or Semgrep?",
    answer:
      "Repotoire is a single ~24MB binary with zero dependencies. No Docker, no server, no cloud. It builds a full knowledge graph of your codebase to find cross-file issues (circular deps, architectural bottlenecks, taint flow) that file-by-file tools miss.",
  },
  {
    question: "Can I use it in CI/CD?",
    answer:
      "Yes. Use --fail-on high to fail builds when high-severity findings exist. Export as SARIF for GitHub Code Scanning integration. Works in any CI: GitHub Actions, GitLab CI, Jenkins, etc.",
  },
  {
    question: "How fast is it?",
    answer:
      "Most codebases analyze in 1-5 seconds. Large monorepos (~20k functions) take ~15 seconds. Use --lite mode for huge repos. Results are cached — subsequent runs are near-instant for unchanged files.",
  },
  {
    question: "Does my code leave my machine?",
    answer:
      "Never. Everything runs locally. The only optional network calls are: AI fix generation (if you provide an API key) and dependency vulnerability checks against OSV.dev. Both are opt-in.",
  },
  {
    question: "What about enterprise support?",
    answer:
      "Contact us for custom detector development, team training, CI/CD pipeline setup, and priority support agreements.",
  },
]

export function PricingFAQ() {
  return (
    <section className="py-20 px-4 sm:px-6 lg:px-8">
      <div className="max-w-3xl mx-auto">
        <h2 className="text-2xl font-display font-bold text-center text-foreground mb-10">
          Frequently Asked Questions
        </h2>
        <Accordion type="single" collapsible className="w-full space-y-4">
          {faqs.map((faq, index) => (
            <AccordionItem
              key={index}
              value={`item-${index}`}
              className="card-elevated rounded-xl px-5 transition-all duration-300 hover:border-primary/20"
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
      </div>
    </section>
  )
}
