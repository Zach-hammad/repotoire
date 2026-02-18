import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";
import { dirname } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname_resolved = dirname(__filename);

const nextConfig: NextConfig = {
  turbopack: {
    root: __dirname_resolved,
  },
  // Optimize package imports for faster builds and smaller bundles
  experimental: {
    optimizePackageImports: [
      // UI Components
      "lucide-react",
      "framer-motion",
      "@radix-ui/react-icons",
      "@radix-ui/react-dialog",
      "@radix-ui/react-dropdown-menu",
      "@radix-ui/react-popover",
      "@radix-ui/react-select",
      "@radix-ui/react-tabs",
      "@radix-ui/react-toast",
      "@radix-ui/react-tooltip",
      "@radix-ui/react-slot",
      // Charts
      "recharts",
      // Forms
      "react-hook-form",
      "@hookform/resolvers",
      // Date handling
      "date-fns",
      // Utilities
      "lodash-es",
      "clsx",
      "tailwind-merge",
      // HTTP
      "swr",
      // Animations
      "react-spring",
    ],
  },
  // Enable modular imports for tree-shaking
  modularizeImports: {
    "lodash-es": {
      transform: "lodash-es/{{member}}",
    },
    "date-fns": {
      transform: "date-fns/{{member}}",
    },
  },
  // Compiler optimizations
  compiler: {
    // Remove console.log in production
    removeConsole: process.env.NODE_ENV === "production" ? { exclude: ["error", "warn"] } : false,
  },
  // Output optimization
  output: "standalone",
  // Enable webpack bundle analyzer in analyze mode
  ...(process.env.ANALYZE === "true" && {
    webpack: (config: { plugins: unknown[] }) => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { BundleAnalyzerPlugin } = require("webpack-bundle-analyzer");
      config.plugins.push(
        new BundleAnalyzerPlugin({
          analyzerMode: "static",
          reportFilename: "./analyze/bundle-report.html",
          openAnalyzer: false,
        })
      );
      return config;
    },
  }),
};

export default withSentryConfig(nextConfig, {
  // For all available options, see:
  // https://www.npmjs.com/package/@sentry/webpack-plugin#options

  org: "repotoire",

  project: "repotoire",

  // Only print logs for uploading source maps in CI
  silent: !process.env.CI,

  // For all available options, see:
  // https://docs.sentry.io/platforms/javascript/guides/nextjs/manual-setup/

  // Upload a larger set of source maps for prettier stack traces (increases build time)
  widenClientFileUpload: true,

  // Route browser requests to Sentry through a Next.js rewrite to circumvent ad-blockers.
  // This can increase your server load as well as your hosting bill.
  // Note: Check that the configured route will not match with your Next.js middleware, otherwise reporting of client-
  // side errors will fail.
  tunnelRoute: "/monitoring",

  // Automatically tree-shake Sentry logger statements to reduce bundle size
  disableLogger: true,

  // Enables automatic instrumentation of Vercel Cron Monitors. (Does not yet work with App Router route handlers.)
  // See the following for more information:
  // https://docs.sentry.io/product/crons/
  // https://vercel.com/docs/cron-jobs
  automaticVercelMonitors: true,
});