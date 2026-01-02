"use client"

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"

const faqs = [
  {
    question: "What counts as an analysis?",
    answer:
      "An analysis is triggered each time we scan your repository. This happens automatically on push events (if enabled) or manually when you click \"Analyze Now\". Incremental analysis of unchanged files doesn't count toward your limit.",
  },
  {
    question: "Can I upgrade or downgrade anytime?",
    answer:
      "Yes! You can change your plan at any time. When upgrading, you'll be charged the prorated difference. When downgrading, the new rate applies at your next billing cycle.",
  },
  {
    question: "What's your cancellation and refund policy?",
    answer:
      "You can cancel your subscription at any time from your account settings. Cancellation takes effect at the end of your current billing period. We don't offer refunds for partial months, but you'll retain access until your paid period ends.",
  },
  {
    question: "Do you offer annual billing?",
    answer:
      "Yes, we offer annual billing with a 20% discount. You can switch between monthly and annual billing at any time from your account settings.",
  },
  {
    question: "What payment methods do you accept?",
    answer:
      "We accept all major credit cards (Visa, Mastercard, American Express) through Stripe. Enterprise customers can pay via invoice.",
  },
  {
    question: "Is there a free trial?",
    answer:
      "Yes! Pro comes with a 7-day free trial. You'll get full access to all Pro features during the trial. After 7 days, you can subscribe or your access will pause until you do.",
  },
  {
    question: "Do I need a credit card to start the trial?",
    answer:
      "No credit card is required to start your 7-day trial. You'll only be asked for payment details if you decide to continue after the trial ends.",
  },
  {
    question: "How does the AI auto-fix work?",
    answer:
      "Every issue detected comes with an AI-generated fix suggestion using GPT-4o and RAG over your codebase. You review the suggested changes with a side-by-side diff and approve them with one click. You're always in control.",
  },
  {
    question: "Can I self-host Repotoire?",
    answer:
      "Yes, Enterprise customers can deploy Repotoire on their own infrastructure. This ensures your code never leaves your network. Contact sales for setup assistance.",
  },
  {
    question: "What languages do you support?",
    answer:
      "We currently support Python with full graph analysis. TypeScript, JavaScript, Java, Go, Rust, C#, and C++ are on the roadmap. The hybrid detectors work across polyglot codebases.",
  },
  {
    question: "How do you handle security?",
    answer:
      "We're SOC2-compliant with end-to-end encryption. Your code is processed in isolated containers and never stored after analysis. Enterprise customers can use our self-hosted option for zero code leaving their network.",
  },
]

export function PricingFAQ() {
  return (
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
  )
}
