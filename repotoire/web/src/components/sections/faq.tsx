"use client"

import { useEffect, useRef, useState } from "react"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"

const faqs = [
  {
    question: "How long does setup take?",
    answer:
      "Initial analysis takes 5-15 minutes for most codebases. After that, incremental scans are significantly faster since only changed files are processed.",
  },
  {
    question: "What languages do you support?",
    answer:
      "We support 9 languages: Python, TypeScript, JavaScript, Go, Java, Rust, C/C++, C#, and Kotlin — all with full graph analysis.",
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
  const sectionRef = useRef<HTMLElement>(null)
  const [isVisible, setIsVisible] = useState(false)

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true)
        }
      },
      { threshold: 0.1 },
    )

    if (sectionRef.current) {
      observer.observe(sectionRef.current)
    }

    return () => observer.disconnect()
  }, [])

  return (
    <section
      ref={sectionRef}
      id="faq"
      className="py-24 px-4 sm:px-6 lg:px-8 dot-grid"
      aria-labelledby="faq-heading"
    >
      <div className="max-w-2xl mx-auto">
        <div className="text-center mb-10">
          <h2
            id="faq-heading"
            className={`text-3xl sm:text-4xl tracking-tight text-foreground mb-4 opacity-0 ${
              isVisible ? "animate-fade-up" : ""
            }`}
          >
            <span className="font-serif italic text-muted-foreground">Frequently asked</span>{" "}
            <span className="text-gradient font-display font-semibold">questions</span>
          </h2>
        </div>

        <Accordion type="single" collapsible className="space-y-3">
          {faqs.map((faq, index) => (
            <AccordionItem
              key={index}
              value={`item-${index}`}
              className={`card-elevated rounded-xl px-5 border opacity-0 ${isVisible ? "animate-scale-in" : ""}`}
              style={{ animationDelay: `${150 + index * 75}ms` }}
            >
              <AccordionTrigger className="text-left text-foreground hover:no-underline py-4 text-sm font-medium">
                {faq.question}
              </AccordionTrigger>
              <AccordionContent className="text-muted-foreground pb-4 text-sm">{faq.answer}</AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </div>
    </section>
  )
}
