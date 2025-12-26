"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Mail, MessageSquare, Github } from "lucide-react";

export default function ContactPage() {
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // TODO: Implement form submission
    setSubmitted(true);
  };

  return (
    <section className="py-24 px-4 sm:px-6 lg:px-8">
      <div className="max-w-4xl mx-auto">
        <div className="text-center mb-12">
          <h1 className="text-4xl sm:text-5xl tracking-tight text-foreground mb-4">
            <span className="font-serif italic text-muted-foreground">Get in</span>{" "}
            <span className="text-gradient font-display font-bold">Touch</span>
          </h1>
          <p className="text-muted-foreground">
            Have questions? We&apos;d love to hear from you.
          </p>
        </div>

        <div className="grid md:grid-cols-2 gap-8">
          {/* Contact Form */}
          <div className="card-elevated rounded-xl p-6">
            {submitted ? (
              <div className="text-center py-8">
                <div className="w-12 h-12 rounded-full bg-emerald-500/20 flex items-center justify-center mx-auto mb-4">
                  <MessageSquare className="w-6 h-6 text-emerald-500" />
                </div>
                <h2 className="text-xl font-display font-bold text-foreground mb-2">
                  Message Sent!
                </h2>
                <p className="text-muted-foreground">
                  We&apos;ll get back to you as soon as possible.
                </p>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="name">Name</Label>
                  <Input id="name" placeholder="Your name" required />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <Input id="email" type="email" placeholder="you@example.com" required />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="message">Message</Label>
                  <Textarea
                    id="message"
                    placeholder="How can we help?"
                    rows={4}
                    required
                  />
                </div>
                <Button type="submit" className="w-full">
                  Send Message
                </Button>
              </form>
            )}
          </div>

          {/* Contact Info */}
          <div className="space-y-6">
            <div className="card-elevated rounded-xl p-6">
              <div className="flex items-start gap-4">
                <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                  <Mail className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <h3 className="font-display font-bold text-foreground mb-1">Email</h3>
                  <p className="text-muted-foreground text-sm">
                    For general inquiries and support
                  </p>
                  <a
                    href="mailto:hello@repotoire.com"
                    className="text-foreground hover:underline text-sm"
                  >
                    hello@repotoire.com
                  </a>
                </div>
              </div>
            </div>

            <div className="card-elevated rounded-xl p-6">
              <div className="flex items-start gap-4">
                <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                  <Github className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <h3 className="font-display font-bold text-foreground mb-1">GitHub</h3>
                  <p className="text-muted-foreground text-sm">
                    Report issues or contribute
                  </p>
                  <a
                    href="https://github.com/repotoire/repotoire"
                    className="text-foreground hover:underline text-sm"
                  >
                    github.com/repotoire/repotoire
                  </a>
                </div>
              </div>
            </div>

            <div className="card-elevated rounded-xl p-6">
              <div className="flex items-start gap-4">
                <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                  <MessageSquare className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <h3 className="font-display font-bold text-foreground mb-1">Twitter / X</h3>
                  <p className="text-muted-foreground text-sm">
                    Follow for updates
                  </p>
                  <a
                    href="https://twitter.com/repotoire"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-foreground hover:underline text-sm"
                  >
                    @repotoire
                  </a>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
