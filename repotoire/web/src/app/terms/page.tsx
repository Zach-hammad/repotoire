import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Terms of Service | Repotoire",
  description: "Terms and conditions for using Repotoire",
};

export default function TermsPage() {
  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-3xl px-4 py-12 sm:px-6 lg:px-8">
        <article className="prose prose-slate dark:prose-invert max-w-none">
          <h1 className="text-3xl font-bold tracking-tight">Terms of Service</h1>
          <p className="text-muted-foreground">
            Last updated: {new Date().toLocaleDateString("en-US", {
              year: "numeric",
              month: "long",
              day: "numeric",
            })}
          </p>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">1. Agreement to Terms</h2>
            <p>
              By accessing or using Repotoire (&quot;the Service&quot;), you agree to be bound
              by these Terms of Service (&quot;Terms&quot;). If you do not agree to these Terms,
              you may not use the Service.
            </p>
            <p>
              These Terms apply to all visitors, users, and others who access or use
              the Service. By using the Service, you represent that you are at least
              16 years of age.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">2. Description of Service</h2>
            <p>
              Repotoire is a graph-powered code intelligence platform that analyzes
              codebases to detect code smells, architectural issues, and technical debt.
              The Service includes:
            </p>
            <ul className="list-disc pl-6 space-y-2">
              <li>Automated code analysis and health scoring</li>
              <li>AI-powered code fix suggestions</li>
              <li>Integration with GitHub repositories</li>
              <li>Web dashboard and API access</li>
            </ul>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">3. User Accounts</h2>

            <h3 className="text-xl font-medium mt-4">Registration</h3>
            <p>
              To use the Service, you must create an account using our authentication
              provider (Clerk). You agree to provide accurate and complete information
              and to keep this information updated.
            </p>

            <h3 className="text-xl font-medium mt-4">Account Security</h3>
            <p>
              You are responsible for maintaining the security of your account credentials.
              You must immediately notify us of any unauthorized use of your account.
            </p>

            <h3 className="text-xl font-medium mt-4">Organizations</h3>
            <p>
              If you create or join an organization, you agree that the organization
              owner may have access to organization-level data and may manage your
              membership.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">4. Subscriptions and Payment</h2>

            <h3 className="text-xl font-medium mt-4">Free Tier</h3>
            <p>
              The Service offers a free tier with limited features. Free tier usage
              is subject to fair use limits.
            </p>

            <h3 className="text-xl font-medium mt-4">Paid Plans</h3>
            <p>
              Paid subscriptions are billed monthly or annually through Stripe.
              By subscribing, you authorize us to charge your payment method.
            </p>

            <h3 className="text-xl font-medium mt-4">Cancellation</h3>
            <p>
              You may cancel your subscription at any time. Cancellation takes effect
              at the end of your current billing period. No refunds are provided
              for partial months.
            </p>

            <h3 className="text-xl font-medium mt-4">Price Changes</h3>
            <p>
              We may change subscription prices with 30 days&apos; notice. Continued use
              of the Service after price changes constitutes acceptance of new prices.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">5. Acceptable Use</h2>
            <p>You agree not to use the Service to:</p>
            <ul className="list-disc pl-6 space-y-2">
              <li>Violate any applicable laws or regulations</li>
              <li>Infringe on intellectual property rights</li>
              <li>Upload malicious code or attempt to compromise the Service</li>
              <li>Interfere with or disrupt the Service or its infrastructure</li>
              <li>Access the Service through automated means without permission</li>
              <li>Collect or harvest user data without consent</li>
              <li>Use the Service for competitive analysis without permission</li>
            </ul>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">6. Repository Access</h2>

            <h3 className="text-xl font-medium mt-4">GitHub Integration</h3>
            <p>
              The Service requires access to your GitHub repositories to perform
              analysis. By connecting your repositories, you grant us permission to:
            </p>
            <ul className="list-disc pl-6 space-y-2">
              <li>Read repository content for analysis</li>
              <li>Store analysis results and metadata</li>
              <li>Create issues or pull requests (with your explicit permission)</li>
            </ul>

            <h3 className="text-xl font-medium mt-4">Code Privacy</h3>
            <p>
              We do not store your raw source code. We only store structural
              representations (AST) and analysis results. Your code is processed
              in memory and not persisted beyond what is necessary for analysis.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">7. Intellectual Property</h2>

            <h3 className="text-xl font-medium mt-4">Service Ownership</h3>
            <p>
              The Service, including its design, features, and content, is owned
              by Repotoire and protected by intellectual property laws.
            </p>

            <h3 className="text-xl font-medium mt-4">Your Content</h3>
            <p>
              You retain ownership of your code and data. By using the Service,
              you grant us a limited license to process your content solely
              to provide the Service.
            </p>

            <h3 className="text-xl font-medium mt-4">Feedback</h3>
            <p>
              Any feedback, suggestions, or ideas you provide about the Service
              may be used by us without obligation to you.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">8. Disclaimers</h2>

            <h3 className="text-xl font-medium mt-4">No Warranty</h3>
            <p>
              THE SERVICE IS PROVIDED &quot;AS IS&quot; WITHOUT WARRANTIES OF ANY KIND.
              WE DO NOT GUARANTEE THAT THE SERVICE WILL BE ERROR-FREE OR UNINTERRUPTED.
            </p>

            <h3 className="text-xl font-medium mt-4">Analysis Accuracy</h3>
            <p>
              While we strive for accuracy, code analysis results are recommendations
              only. You are responsible for reviewing and verifying any suggestions
              before implementing them.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">9. Limitation of Liability</h2>
            <p>
              TO THE MAXIMUM EXTENT PERMITTED BY LAW, REPOTOIRE SHALL NOT BE LIABLE
              FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES,
              INCLUDING LOSS OF PROFITS, DATA, OR BUSINESS OPPORTUNITIES.
            </p>
            <p>
              Our total liability shall not exceed the greater of (a) the amount you
              paid us in the 12 months preceding the claim, or (b) $100.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">10. Indemnification</h2>
            <p>
              You agree to indemnify and hold harmless Repotoire and its affiliates
              from any claims, damages, or expenses arising from your use of the
              Service or violation of these Terms.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">11. Account Termination</h2>

            <h3 className="text-xl font-medium mt-4">By You</h3>
            <p>
              You may terminate your account at any time through the Settings page.
              Account deletion is subject to a 30-day grace period as described
              in our{" "}
              <Link href="/privacy" className="text-primary hover:underline">
                Privacy Policy
              </Link>
              .
            </p>

            <h3 className="text-xl font-medium mt-4">By Us</h3>
            <p>
              We may suspend or terminate your account if you violate these Terms.
              We will provide notice unless immediate action is required for
              security or legal reasons.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">12. Changes to Terms</h2>
            <p>
              We may modify these Terms at any time. Material changes will be
              communicated via email or through the Service. Continued use after
              changes constitutes acceptance of the modified Terms.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">13. Governing Law</h2>
            <p>
              These Terms are governed by the laws of the State of Delaware,
              without regard to conflict of law principles. Any disputes shall
              be resolved in the courts of Delaware.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">14. General Provisions</h2>

            <h3 className="text-xl font-medium mt-4">Entire Agreement</h3>
            <p>
              These Terms, together with our Privacy Policy, constitute the entire
              agreement between you and Repotoire.
            </p>

            <h3 className="text-xl font-medium mt-4">Severability</h3>
            <p>
              If any provision of these Terms is found unenforceable, the remaining
              provisions shall continue in effect.
            </p>

            <h3 className="text-xl font-medium mt-4">Waiver</h3>
            <p>
              Failure to enforce any right or provision shall not constitute a
              waiver of such right or provision.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">15. Contact</h2>
            <p>
              For questions about these Terms, contact us at:
            </p>
            <ul className="list-none mt-4 space-y-2">
              <li>
                Email:{" "}
                <a href="mailto:legal@repotoire.io" className="text-primary hover:underline">
                  legal@repotoire.io
                </a>
              </li>
            </ul>
          </section>

          <div className="mt-12 border-t pt-8 flex gap-4">
            <Link href="/" className="text-primary hover:underline">
              &larr; Back to Home
            </Link>
            <Link href="/privacy" className="text-primary hover:underline">
              Privacy Policy
            </Link>
          </div>
        </article>
      </div>
    </div>
  );
}
