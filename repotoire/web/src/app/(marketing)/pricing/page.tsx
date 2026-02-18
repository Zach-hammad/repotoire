import { Metadata } from "next"
import { PricingCards } from "@/components/marketing/pricing-cards"
import { PricingFAQ } from "@/components/marketing/pricing-faq"

export const metadata: Metadata = {
  title: "Pricing - Repotoire",
  description: "Repotoire is free, open source, and always will be. 114 detectors, 13 languages, zero cost.",
  openGraph: {
    title: "Pricing - Repotoire",
    description: "Repotoire is free, open source, and always will be.",
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
              Free. <span className="text-gradient">Open source.</span> Always.
            </h1>
            <p className="mt-6 text-lg text-muted-foreground max-w-2xl mx-auto">
              114 detectors. 13 languages. Graph-powered analysis. No API keys, no accounts, no cloud required.
            </p>
          </div>

          <PricingCards />
        </div>
      </section>

      <PricingFAQ />
    </>
  )
}
