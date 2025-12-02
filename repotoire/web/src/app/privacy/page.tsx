import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy Policy | Repotoire",
  description: "How Repotoire collects, uses, and protects your data",
};

export default function PrivacyPage() {
  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-3xl px-4 py-12 sm:px-6 lg:px-8">
        <article className="prose prose-slate dark:prose-invert max-w-none">
          <h1 className="text-3xl font-bold tracking-tight">Privacy Policy</h1>
          <p className="text-muted-foreground">
            Last updated: {new Date().toLocaleDateString("en-US", {
              year: "numeric",
              month: "long",
              day: "numeric",
            })}
          </p>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">1. Introduction</h2>
            <p>
              Repotoire (&quot;we&quot;, &quot;our&quot;, or &quot;us&quot;) is committed to protecting your privacy.
              This Privacy Policy explains how we collect, use, disclose, and safeguard
              your information when you use our graph-powered code intelligence platform.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">2. Data We Collect</h2>

            <h3 className="text-xl font-medium mt-4">Account Information</h3>
            <ul className="list-disc pl-6 space-y-2">
              <li>Email address and name (via Clerk authentication)</li>
              <li>Organization membership and roles</li>
              <li>Profile preferences and settings</li>
              <li>Authentication tokens (securely encrypted)</li>
            </ul>

            <h3 className="text-xl font-medium mt-4">Repository Data</h3>
            <ul className="list-disc pl-6 space-y-2">
              <li>Repository metadata (name, URL, branch information)</li>
              <li>Code structure (AST representation, not raw source code)</li>
              <li>Analysis results and health scores</li>
              <li>Detected issues and suggested fixes</li>
            </ul>

            <h3 className="text-xl font-medium mt-4">Usage Data</h3>
            <ul className="list-disc pl-6 space-y-2">
              <li>Pages visited and features used (with consent)</li>
              <li>Analysis run history and timestamps</li>
              <li>API usage patterns for billing purposes</li>
            </ul>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">3. How We Use Your Data</h2>
            <ul className="list-disc pl-6 space-y-2">
              <li>To provide and maintain our code analysis service</li>
              <li>To authenticate and authorize access to your repositories</li>
              <li>To generate code health reports and insights</li>
              <li>To process payments and manage subscriptions</li>
              <li>To communicate important updates about your account</li>
              <li>To improve our service (aggregated, anonymized data only)</li>
            </ul>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">4. Third-Party Services</h2>
            <p>We use the following third-party services to operate Repotoire:</p>

            <div className="mt-4 overflow-x-auto">
              <table className="min-w-full divide-y divide-border">
                <thead>
                  <tr>
                    <th className="px-4 py-2 text-left font-semibold">Service</th>
                    <th className="px-4 py-2 text-left font-semibold">Purpose</th>
                    <th className="px-4 py-2 text-left font-semibold">Data Shared</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  <tr>
                    <td className="px-4 py-2">Clerk</td>
                    <td className="px-4 py-2">Authentication</td>
                    <td className="px-4 py-2">Email, name, profile</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">Stripe</td>
                    <td className="px-4 py-2">Payment processing</td>
                    <td className="px-4 py-2">Billing information</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">GitHub</td>
                    <td className="px-4 py-2">Repository access</td>
                    <td className="px-4 py-2">Repository metadata</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">Vercel</td>
                    <td className="px-4 py-2">Hosting</td>
                    <td className="px-4 py-2">IP address, usage logs</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">PostHog</td>
                    <td className="px-4 py-2">Analytics (with consent)</td>
                    <td className="px-4 py-2">Usage events, anonymized</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">5. Your Rights (GDPR)</h2>
            <p>
              Under the General Data Protection Regulation (GDPR) and similar privacy laws,
              you have the following rights:
            </p>

            <ul className="list-disc pl-6 space-y-2 mt-4">
              <li>
                <strong>Right to Access</strong> - Export all your data from{" "}
                <Link href="/dashboard/settings/privacy" className="text-primary hover:underline">
                  Settings &rarr; Privacy
                </Link>
              </li>
              <li>
                <strong>Right to Erasure</strong> - Delete your account with a 30-day grace period
              </li>
              <li>
                <strong>Right to Rectification</strong> - Update your profile information anytime
              </li>
              <li>
                <strong>Right to Data Portability</strong> - Download your data in JSON format
              </li>
              <li>
                <strong>Right to Object</strong> - Opt out of analytics tracking
              </li>
              <li>
                <strong>Right to Restrict Processing</strong> - Contact us to limit data use
              </li>
            </ul>

            <p className="mt-4">
              To exercise any of these rights, visit your{" "}
              <Link href="/dashboard/settings/privacy" className="text-primary hover:underline">
                Privacy Settings
              </Link>{" "}
              or contact us at{" "}
              <a href="mailto:privacy@repotoire.io" className="text-primary hover:underline">
                privacy@repotoire.io
              </a>
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">6. Data Retention</h2>

            <div className="mt-4 overflow-x-auto">
              <table className="min-w-full divide-y divide-border">
                <thead>
                  <tr>
                    <th className="px-4 py-2 text-left font-semibold">Data Type</th>
                    <th className="px-4 py-2 text-left font-semibold">Retention Period</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  <tr>
                    <td className="px-4 py-2">User profile</td>
                    <td className="px-4 py-2">While account active + 30 days</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">Analysis results</td>
                    <td className="px-4 py-2">1 year</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">Repository metadata</td>
                    <td className="px-4 py-2">While connected + 30 days</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">Audit logs</td>
                    <td className="px-4 py-2">2 years (anonymized)</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2">Billing records</td>
                    <td className="px-4 py-2">7 years (legal requirement)</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">7. Data Security</h2>
            <p>We implement industry-standard security measures to protect your data:</p>
            <ul className="list-disc pl-6 space-y-2 mt-4">
              <li>Encryption in transit (TLS 1.3) and at rest (AES-256)</li>
              <li>Secure authentication via Clerk with MFA support</li>
              <li>Regular security audits and penetration testing</li>
              <li>Access controls and audit logging</li>
              <li>Encrypted backups with geographic redundancy</li>
            </ul>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">8. Cookies and Tracking</h2>
            <p>
              We use cookies and similar technologies for essential functionality
              and, with your consent, for analytics. You can manage your preferences
              using the cookie banner or in your browser settings.
            </p>

            <h3 className="text-xl font-medium mt-4">Cookie Types</h3>
            <ul className="list-disc pl-6 space-y-2">
              <li>
                <strong>Essential</strong> - Required for authentication and security
              </li>
              <li>
                <strong>Analytics</strong> - Help us understand how you use our service (opt-in)
              </li>
              <li>
                <strong>Marketing</strong> - Personalized content (opt-in)
              </li>
            </ul>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">9. International Data Transfers</h2>
            <p>
              Your data may be processed in the United States and other countries
              where our service providers operate. We ensure adequate data protection
              through Standard Contractual Clauses and other approved mechanisms.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">10. Children&apos;s Privacy</h2>
            <p>
              Repotoire is not intended for use by individuals under 16 years of age.
              We do not knowingly collect personal information from children.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">11. Changes to This Policy</h2>
            <p>
              We may update this Privacy Policy from time to time. We will notify you
              of any material changes by email or through a prominent notice on our website.
            </p>
          </section>

          <section className="mt-8">
            <h2 className="text-2xl font-semibold">12. Contact Us</h2>
            <p>
              For privacy-related inquiries or to exercise your data rights, contact us at:
            </p>
            <ul className="list-none mt-4 space-y-2">
              <li>
                Email:{" "}
                <a href="mailto:privacy@repotoire.io" className="text-primary hover:underline">
                  privacy@repotoire.io
                </a>
              </li>
              <li>
                Data Protection Officer:{" "}
                <a href="mailto:dpo@repotoire.io" className="text-primary hover:underline">
                  dpo@repotoire.io
                </a>
              </li>
            </ul>
          </section>

          <div className="mt-12 border-t pt-8">
            <Link href="/" className="text-primary hover:underline">
              &larr; Back to Home
            </Link>
          </div>
        </article>
      </div>
    </div>
  );
}
