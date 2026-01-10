"use client";

import { useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Mail, MessageSquare, Github, Loader2, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

interface FormErrors {
  name?: string;
  email?: string;
  message?: string;
}

// Email validation regex
const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function validateField(name: string, value: string): string | undefined {
  switch (name) {
    case "name":
      if (!value.trim()) return "Name is required";
      if (value.trim().length < 2) return "Name must be at least 2 characters";
      return undefined;
    case "email":
      if (!value.trim()) return "Email is required";
      if (!emailRegex.test(value)) return "Please enter a valid email address";
      return undefined;
    case "message":
      if (!value.trim()) return "Message is required";
      if (value.trim().length < 10) return "Message must be at least 10 characters";
      return undefined;
    default:
      return undefined;
  }
}

export default function ContactPage() {
  const [submitted, setSubmitted] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errors, setErrors] = useState<FormErrors>({});
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [formData, setFormData] = useState({
    name: "",
    email: "",
    message: "",
  });

  const validateForm = (): boolean => {
    const newErrors: FormErrors = {};

    const nameError = validateField("name", formData.name);
    const emailError = validateField("email", formData.email);
    const messageError = validateField("message", formData.message);

    if (nameError) newErrors.name = nameError;
    if (emailError) newErrors.email = emailError;
    if (messageError) newErrors.message = messageError;

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    // Mark all fields as touched
    setTouched({ name: true, email: true, message: true });

    if (!validateForm()) {
      // Focus on the first field with an error
      const firstErrorField = Object.keys(errors).find(
        (key) => errors[key as keyof FormErrors]
      );
      if (firstErrorField) {
        const element = document.getElementById(firstErrorField);
        element?.focus();
      }
      return;
    }

    setIsLoading(true);

    // Simulate API call
    await new Promise((resolve) => setTimeout(resolve, 1000));

    // TODO: Implement actual form submission
    setIsLoading(false);
    setSubmitted(true);
  };

  const handleBlur = useCallback((e: React.FocusEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const { id, value } = e.target;
    setTouched((prev) => ({ ...prev, [id]: true }));
    const error = validateField(id, value);
    setErrors((prev) => ({ ...prev, [id]: error }));
  }, []);

  const handleInputChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => {
    const { id, value } = e.target;
    setFormData((prev) => ({ ...prev, [id]: value }));

    // Real-time validation for touched fields
    if (touched[id]) {
      const error = validateField(id, value);
      setErrors((prev) => ({ ...prev, [id]: error }));
    }
  };

  const hasFieldError = (field: keyof FormErrors) => touched[field] && errors[field];

  return (
    <section className="py-24 px-4 sm:px-6 lg:px-8" aria-labelledby="contact-heading">
      <div className="max-w-4xl mx-auto">
        <div className="text-center mb-12">
          <h1 id="contact-heading" className="text-4xl sm:text-5xl tracking-tight text-foreground mb-4">
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
              <div className="text-center py-8" role="status" aria-live="polite">
                <div className="w-12 h-12 rounded-full bg-emerald-500/20 flex items-center justify-center mx-auto mb-4">
                  <MessageSquare className="w-6 h-6 text-emerald-500" aria-hidden="true" />
                </div>
                <h2 className="text-xl font-display font-bold text-foreground mb-2">
                  Message Sent!
                </h2>
                <p className="text-muted-foreground">
                  We&apos;ll get back to you as soon as possible.
                </p>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4" noValidate>
                <div className="space-y-2">
                  <Label htmlFor="name">Name</Label>
                  <Input
                    id="name"
                    placeholder="Your name"
                    value={formData.name}
                    onChange={handleInputChange}
                    onBlur={handleBlur}
                    aria-invalid={hasFieldError("name") ? "true" : undefined}
                    aria-describedby={hasFieldError("name") ? "name-error" : undefined}
                    aria-required="true"
                    className={cn(hasFieldError("name") && "border-destructive focus-visible:ring-destructive/50")}
                    disabled={isLoading}
                  />
                  {hasFieldError("name") && (
                    <p id="name-error" className="flex items-center gap-1.5 text-sm text-destructive" role="alert">
                      <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
                      {errors.name}
                    </p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <Input
                    id="email"
                    type="email"
                    placeholder="you@example.com"
                    value={formData.email}
                    onChange={handleInputChange}
                    onBlur={handleBlur}
                    aria-invalid={hasFieldError("email") ? "true" : undefined}
                    aria-describedby={hasFieldError("email") ? "email-error" : undefined}
                    aria-required="true"
                    className={cn(hasFieldError("email") && "border-destructive focus-visible:ring-destructive/50")}
                    disabled={isLoading}
                  />
                  {hasFieldError("email") && (
                    <p id="email-error" className="flex items-center gap-1.5 text-sm text-destructive" role="alert">
                      <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
                      {errors.email}
                    </p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="message">Message</Label>
                  <Textarea
                    id="message"
                    placeholder="How can we help?"
                    rows={4}
                    value={formData.message}
                    onChange={handleInputChange}
                    onBlur={handleBlur}
                    aria-invalid={hasFieldError("message") ? "true" : undefined}
                    aria-describedby={hasFieldError("message") ? "message-error" : undefined}
                    aria-required="true"
                    className={cn(hasFieldError("message") && "border-destructive focus-visible:ring-destructive/50")}
                    disabled={isLoading}
                  />
                  {hasFieldError("message") && (
                    <p id="message-error" className="flex items-center gap-1.5 text-sm text-destructive" role="alert">
                      <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
                      {errors.message}
                    </p>
                  )}
                </div>
                <Button type="submit" className="w-full" disabled={isLoading} aria-disabled={isLoading}>
                  {isLoading ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                      Sending...
                    </>
                  ) : (
                    "Send Message"
                  )}
                </Button>
              </form>
            )}
          </div>

          {/* Contact Info */}
          <div className="space-y-6">
            <div className="card-elevated rounded-xl p-6">
              <div className="flex items-start gap-4">
                <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                  <Mail className="w-5 h-5 text-primary" aria-hidden="true" />
                </div>
                <div>
                  <h2 className="font-display font-bold text-foreground mb-1">Email</h2>
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
                  <Github className="w-5 h-5 text-primary" aria-hidden="true" />
                </div>
                <div>
                  <h2 className="font-display font-bold text-foreground mb-1">GitHub</h2>
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
                  <MessageSquare className="w-5 h-5 text-primary" aria-hidden="true" />
                </div>
                <div>
                  <h2 className="font-display font-bold text-foreground mb-1">Twitter / X</h2>
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
