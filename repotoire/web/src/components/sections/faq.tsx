"use client"

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"

const faqs = [
  {
    question: "How long does setup take?",
    answer:
      "Initial analysis takes 5-15 minutes for most codebases. After that, incremental scans run in under 8 seconds.",
  },
  {
    question: "What languages do you support?",
    answer:
      "TypeScript, JavaScript, Python, Java, Go, Rust, C#, and C++. The graph analysis works across polyglot codebases.",
  },
  {
    question: "Is the AI auto-fix safe?",
    answer: "Yes. Every fix shows a side-by-side diff before you apply it. You're always in control.",
  },
  {
    question: "Does it work with our CI/CD?",
    answer:
      "Yes. We have native integrations for GitHub Actions, GitLab CI, and any system that supports custom scripts.",
  },
  {
    question: "How do you handle security?",
    answer:
      "SOC2-compliant cloud with encryption. Enterprise customers can self-host with zero code leaving their network.",
  },
]

export function FAQ() {
  return (
    <section id="faq" className="py-20 px-4 sm:px-6 lg:px-8">
      <div className="max-w-2xl mx-auto">
        <div className="text-center mb-12">
          <h2 className="text-3xl sm:text-4xl font-bold text-foreground mb-4">FAQ</h2>
        </div>

        <Accordion type="single" collapsible className="space-y-3">
          {faqs.map((faq, index) => (
            <AccordionItem key={index} value={`item-${index}`} className="bg-card border border-border rounded-lg px-4">
              <AccordionTrigger className="text-left text-foreground hover:no-underline">
                {faq.question}
              </AccordionTrigger>
              <AccordionContent className="text-muted-foreground">{faq.answer}</AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </div>
    </section>
  )
}
