import { Metadata } from "next"
import { PricingCards } from "@/components/marketing/pricing-cards"
import { PricingFAQ } from "@/components/marketing/pricing-faq"

export const metadata: Metadata = {
  title: "Pricing - Repotoire",
  description: "Simple, transparent pricing. Start free, upgrade when you need more.",
  openGraph: {
    title: "Pricing - Repotoire",
    description: "Simple, transparent pricing. Start free, upgrade when you need more.",
    type: "website",
  },
}

export default function PricingPage() {
  return (
    <>
      <section className="pt-32 pb-20 px-4 sm:px-6 lg:px-8">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-16 opacity-0 animate-fade-up">
            <span className="inline-block px-4 py-1.5 mb-6 text-sm font-medium rounded-full bg-primary/10 text-primary border border-primary/20">
              Pricing
            </span>
            <h1 className="text-4xl font-display font-bold tracking-tight sm:text-5xl lg:text-6xl text-foreground">
              Simple, <span className="text-gradient">transparent</span> pricing
            </h1>
            <p className="mt-6 text-lg text-muted-foreground max-w-2xl mx-auto">
              Start free, upgrade when you need more. No hidden fees, no surprises.
            </p>
          </div>

          <div className="opacity-0 animate-fade-up delay-200">
            <PricingCards />
          </div>
        </div>
      </section>

      <section className="py-20 border-t border-border">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8">
          <h2 className="text-3xl font-display font-bold text-center mb-12 text-foreground opacity-0 animate-fade-up">
            Frequently asked questions
          </h2>
          <div className="opacity-0 animate-fade-up delay-100">
            <PricingFAQ />
          </div>
        </div>
      </section>
    </>
  )
}
