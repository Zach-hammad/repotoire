"use client";

import { useState } from "react";
import { Check, Download, Loader2, Trash2, RefreshCw, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useInstallAsset, useUninstallAsset, useUpdateAsset } from "@/lib/marketplace-hooks";
import { mutate } from "swr";

interface InstallButtonProps {
  publisherSlug: string;
  assetSlug: string;
  isInstalled?: boolean;
  hasUpdate?: boolean;
  isCommunity?: boolean;
  homepage?: string;
  onInstalled?: () => void;
  onUninstalled?: () => void;
  onUpdated?: () => void;
  className?: string;
  size?: "default" | "sm" | "lg";
  variant?: "default" | "outline";
}

export function InstallButton({
  publisherSlug,
  assetSlug,
  isInstalled = false,
  hasUpdate = false,
  isCommunity = false,
  homepage,
  onInstalled,
  onUninstalled,
  onUpdated,
  className,
  size = "default",
  variant = "default",
}: InstallButtonProps) {
  const [action, setAction] = useState<"idle" | "installing" | "uninstalling" | "updating">("idle");
  const { trigger: install } = useInstallAsset();
  const { trigger: uninstall } = useUninstallAsset();
  const { trigger: update } = useUpdateAsset();

  // Community plugins - show link to their homepage instead of install
  if (isCommunity) {
    return (
      <Button
        asChild
        size={size}
        variant={variant}
        className={cn(
          "font-display font-medium",
          variant === "default" && "bg-primary hover:bg-primary/90 text-primary-foreground",
          className
        )}
      >
        <a
          href={homepage || `https://github.com/ananddtyagi/claude-code-marketplace`}
          target="_blank"
          rel="noopener noreferrer"
        >
          <ExternalLink className="w-4 h-4 mr-2" />
          View Source
        </a>
      </Button>
    );
  }

  const handleInstall = async () => {
    setAction("installing");
    try {
      await install({ publisherSlug, assetSlug });
      // Revalidate installed assets
      await mutate("marketplace-installed");
      onInstalled?.();
    } catch (error) {
      console.error("Failed to install:", error);
    } finally {
      setAction("idle");
    }
  };

  const handleUninstall = async () => {
    setAction("uninstalling");
    try {
      await uninstall({ publisherSlug, assetSlug });
      // Revalidate installed assets
      await mutate("marketplace-installed");
      onUninstalled?.();
    } catch (error) {
      console.error("Failed to uninstall:", error);
    } finally {
      setAction("idle");
    }
  };

  const handleUpdate = async () => {
    setAction("updating");
    try {
      await update({ publisherSlug, assetSlug });
      // Revalidate installed assets
      await mutate("marketplace-installed");
      onUpdated?.();
    } catch (error) {
      console.error("Failed to update:", error);
    } finally {
      setAction("idle");
    }
  };

  const isLoading = action !== "idle";

  // Update available - show update button
  if (isInstalled && hasUpdate) {
    return (
      <Button
        onClick={handleUpdate}
        disabled={isLoading}
        size={size}
        variant={variant}
        className={cn(
          "font-display font-medium",
          variant === "default" && "bg-primary hover:bg-primary/90 text-primary-foreground",
          className
        )}
      >
        {action === "updating" ? (
          <Loader2 className="w-4 h-4 mr-2 animate-spin" />
        ) : (
          <RefreshCw className="w-4 h-4 mr-2" />
        )}
        Update
      </Button>
    );
  }

  // Already installed - show uninstall button
  if (isInstalled) {
    return (
      <Button
        onClick={handleUninstall}
        disabled={isLoading}
        size={size}
        variant="outline"
        className={cn("font-display font-medium", className)}
      >
        {action === "uninstalling" ? (
          <Loader2 className="w-4 h-4 mr-2 animate-spin" />
        ) : (
          <Trash2 className="w-4 h-4 mr-2" />
        )}
        Uninstall
      </Button>
    );
  }

  // Not installed - show install button
  return (
    <Button
      onClick={handleInstall}
      disabled={isLoading}
      size={size}
      variant={variant}
      className={cn(
        "font-display font-medium",
        variant === "default" && "bg-primary hover:bg-primary/90 text-primary-foreground",
        className
      )}
    >
      {action === "installing" ? (
        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
      ) : (
        <Download className="w-4 h-4 mr-2" />
      )}
      Install
    </Button>
  );
}
