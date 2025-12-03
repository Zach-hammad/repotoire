"use client";

import { Github } from "lucide-react";
import { Button } from "@/components/ui/button";

const GITHUB_APP_NAME = process.env.NEXT_PUBLIC_GITHUB_APP_NAME || "repotoireapp";

interface GitHubInstallButtonProps {
  className?: string;
  variant?: "default" | "outline" | "secondary" | "ghost";
  size?: "default" | "sm" | "lg";
}

/**
 * Button that redirects users to install the Repotoire GitHub App.
 * After installation, GitHub redirects back to our callback URL.
 */
export function GitHubInstallButton({
  className,
  variant = "default",
  size = "default",
}: GitHubInstallButtonProps) {
  const installUrl = `https://github.com/apps/${GITHUB_APP_NAME}/installations/new`;

  return (
    <Button asChild variant={variant} size={size} className={className}>
      <a href={installUrl}>
        <Github className="mr-2 h-4 w-4" />
        Connect GitHub
      </a>
    </Button>
  );
}

/**
 * Secondary version for settings pages
 */
export function GitHubInstallButtonSecondary({
  className,
}: {
  className?: string;
}) {
  const installUrl = `https://github.com/apps/${GITHUB_APP_NAME}/installations/new`;

  return (
    <Button asChild variant="outline" className={className}>
      <a href={installUrl}>
        <Github className="mr-2 h-4 w-4" />
        Add Another Installation
      </a>
    </Button>
  );
}
