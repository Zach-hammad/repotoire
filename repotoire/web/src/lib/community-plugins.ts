/**
 * Community plugins sync from claude-code-marketplace GitHub repo.
 * These are all free, open-source plugins contributed by the community.
 *
 * Source: https://github.com/ananddtyagi/claude-code-marketplace
 */

import { AssetSummary, AssetType } from '@/types/marketplace';

const GITHUB_API_BASE = 'https://api.github.com/repos/ananddtyagi/claude-code-marketplace';
const RAW_CONTENT_BASE = 'https://raw.githubusercontent.com/ananddtyagi/claude-code-marketplace/main';

// Static fallback data - community plugins from claudecodecommands.directory
// No install/rating data available - these are synced from the community repo
const STATIC_COMMUNITY_PLUGINS: AssetSummary[] = [
  {
    id: 'community-code-review-assistant',
    slug: 'code-review-assistant',
    name: 'Code Review Assistant',
    description: 'Get comprehensive code reviews with suggestions for improvements, best practices, and potential issues.',
    type: 'command',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'community',
    publisher_name: 'Community',
    publisher_verified: false,
    latest_version: '1.0.0',
    install_count: 0,
    rating_avg: 0,
    rating_count: 0,
    verified: false,
    featured: true,
    tags: ['code-review'],
    source: 'community',
    homepage: 'https://claudecodecommands.directory/commands/code-review-assistant',
  },
  {
    id: 'community-accessibility-expert',
    slug: 'accessibility-expert',
    name: 'Accessibility Expert',
    description: 'Accessibility Expert specializing in enterprise B2B applications and international compliance standards.',
    type: 'skill',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'community',
    publisher_name: 'Alysson Franklin',
    publisher_verified: false,
    latest_version: '1.0.0',
    install_count: 0,
    rating_avg: 0,
    rating_count: 0,
    verified: false,
    featured: false,
    tags: ['subagent', 'accessibility', 'wcag'],
    source: 'community',
    homepage: 'https://claudecodecommands.directory/agents/accessibility-expert',
  },
  {
    id: 'community-backend-architect',
    slug: 'backend-architect',
    name: 'Backend Architect',
    description: 'Expert backend architecture agent for designing scalable systems and APIs.',
    type: 'skill',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'community',
    publisher_name: 'Community',
    publisher_verified: false,
    latest_version: '1.0.0',
    install_count: 0,
    rating_avg: 0,
    rating_count: 0,
    verified: false,
    featured: true,
    tags: ['backend', 'architecture', 'api'],
    source: 'community',
    homepage: 'https://claudecodecommands.directory/agents/backend-architect',
  },
  {
    id: 'community-frontend-developer',
    slug: 'frontend-developer',
    name: 'Frontend Developer',
    description: 'Expert frontend development agent for building modern web applications.',
    type: 'skill',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'community',
    publisher_name: 'Community',
    publisher_verified: false,
    latest_version: '1.0.0',
    install_count: 0,
    rating_avg: 0,
    rating_count: 0,
    verified: false,
    featured: true,
    tags: ['frontend', 'react', 'web'],
    source: 'community',
    homepage: 'https://claudecodecommands.directory/agents/frontend-developer',
  },
  {
    id: 'community-devops-automator',
    slug: 'devops-automator',
    name: 'DevOps Automator',
    description: 'Automate DevOps tasks including CI/CD pipelines, infrastructure, and deployments.',
    type: 'skill',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'community',
    publisher_name: 'Community',
    publisher_verified: false,
    latest_version: '1.0.0',
    install_count: 0,
    rating_avg: 0,
    rating_count: 0,
    verified: false,
    featured: false,
    tags: ['devops', 'ci-cd', 'automation'],
    source: 'community',
    homepage: 'https://claudecodecommands.directory/agents/devops-automator',
  },
  {
    id: 'community-data-scientist',
    slug: 'data-scientist',
    name: 'Data Scientist',
    description: 'Expert data science agent for analysis, machine learning, and data visualization.',
    type: 'skill',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'community',
    publisher_name: 'Community',
    publisher_verified: false,
    latest_version: '1.0.0',
    install_count: 0,
    rating_avg: 0,
    rating_count: 0,
    verified: false,
    featured: false,
    tags: ['data-science', 'ml', 'analytics'],
    source: 'community',
    homepage: 'https://claudecodecommands.directory/agents/data-scientist',
  },
  {
    id: 'community-security-reviewer',
    slug: 'security-reviewer',
    name: 'Security Reviewer',
    description: 'Security expert agent for code audits, vulnerability detection, and security best practices.',
    type: 'skill',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'community',
    publisher_name: 'Community',
    publisher_verified: false,
    latest_version: '1.0.0',
    install_count: 0,
    rating_avg: 0,
    rating_count: 0,
    verified: false,
    featured: false,
    tags: ['security', 'audit', 'vulnerability'],
    source: 'community',
    homepage: 'https://claudecodecommands.directory/agents/security-reviewer',
  },
  {
    id: 'community-ux-researcher',
    slug: 'ux-researcher',
    name: 'UX Researcher',
    description: 'Expert UX research agent for user research, usability testing, and design recommendations.',
    type: 'skill',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'community',
    publisher_name: 'Community',
    publisher_verified: false,
    latest_version: '1.0.0',
    install_count: 0,
    rating_avg: 0,
    rating_count: 0,
    verified: false,
    featured: false,
    tags: ['ux', 'research', 'design'],
    source: 'community',
    homepage: 'https://claudecodecommands.directory/agents/ux-researcher',
  },
  {
    id: 'community-python-expert',
    slug: 'python-expert',
    name: 'Python Expert',
    description: 'Expert Python development agent for writing clean, efficient Python code.',
    type: 'skill',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'community',
    publisher_name: 'Community',
    publisher_verified: false,
    latest_version: '1.0.0',
    install_count: 0,
    rating_avg: 0,
    rating_count: 0,
    verified: false,
    featured: true,
    tags: ['python', 'development'],
    source: 'community',
    homepage: 'https://claudecodecommands.directory/agents/python-expert',
  },
  {
    id: 'community-ui-designer',
    slug: 'ui-designer',
    name: 'UI Designer',
    description: 'Expert UI design agent for creating beautiful, functional user interfaces.',
    type: 'skill',
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'community',
    publisher_name: 'Community',
    publisher_verified: false,
    latest_version: '1.0.0',
    install_count: 0,
    rating_avg: 0,
    rating_count: 0,
    verified: false,
    featured: false,
    tags: ['ui', 'design', 'interface'],
    source: 'community',
    homepage: 'https://claudecodecommands.directory/agents/ui-designer',
  },
];

interface PluginJson {
  name: string;
  description: string;
  version: string;
  author: {
    name: string;
  };
  homepage?: string;
  keywords?: string[];
  commands?: string; // Path to commands directory (indicates it's a command)
}

interface GithubContent {
  name: string;
  path: string;
  type: 'file' | 'dir';
}

// Cache for community plugins (15 min TTL)
let pluginsCache: AssetSummary[] | null = null;
let cacheTimestamp: number = 0;
const CACHE_TTL = 15 * 60 * 1000; // 15 minutes

/**
 * Determine asset type from plugin metadata and structure.
 */
function determineAssetType(plugin: PluginJson, hasAgentsDir: boolean): AssetType {
  if (plugin.commands) {
    return 'command';
  }
  if (hasAgentsDir || plugin.keywords?.includes('subagent') || plugin.keywords?.includes('agent')) {
    return 'skill'; // Map agents to skills in our taxonomy
  }
  if (plugin.keywords?.includes('hook')) {
    return 'hook';
  }
  if (plugin.keywords?.includes('style')) {
    return 'style';
  }
  if (plugin.keywords?.includes('prompt')) {
    return 'prompt';
  }
  // Default to skill for agent-like plugins
  return 'skill';
}

/**
 * Fetch a single plugin's metadata.
 */
async function fetchPluginMetadata(pluginName: string): Promise<PluginJson | null> {
  try {
    const response = await fetch(
      `${RAW_CONTENT_BASE}/plugins/${pluginName}/.claude-plugin/plugin.json`,
      { next: { revalidate: 3600 } } // Cache for 1 hour
    );
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

/**
 * Check if plugin has agents directory.
 */
async function hasAgentsDirectory(pluginName: string): Promise<boolean> {
  try {
    const response = await fetch(
      `${GITHUB_API_BASE}/contents/plugins/${pluginName}/agents`,
      {
        headers: { 'Accept': 'application/vnd.github.v3+json' },
        next: { revalidate: 3600 }
      }
    );
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Convert plugin metadata to our AssetSummary format.
 */
function toAssetSummary(
  plugin: PluginJson,
  pluginName: string,
  hasAgents: boolean
): AssetSummary {
  const type = determineAssetType(plugin, hasAgents);

  return {
    id: `community-${pluginName}`,
    slug: pluginName,
    name: plugin.name
      .split('-')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' '),
    description: plugin.description && plugin.description !== 'Examples:'
      ? plugin.description
      : `Community ${type} for Claude Code`,
    type,
    pricing_type: 'free',
    price_cents: 0,
    publisher_slug: 'community',
    publisher_name: plugin.author?.name || 'Community',
    publisher_verified: false,
    latest_version: plugin.version || '1.0.0',
    install_count: 0, // We don't have this data
    rating_avg: 0,
    rating_count: 0,
    verified: false,
    featured: false,
    tags: plugin.keywords || [],
    source: 'community' as const,
    homepage: plugin.homepage,
  };
}

/**
 * Fetch all community plugins.
 * Currently returns static data. GitHub API integration can be re-enabled
 * when we have authentication to avoid rate limits.
 */
export async function fetchCommunityPlugins(): Promise<AssetSummary[]> {
  // For now, always use static data to avoid GitHub API rate limits
  // TODO: Re-enable GitHub API with authentication token
  return STATIC_COMMUNITY_PLUGINS;
}

/**
 * Get a single community plugin by slug.
 */
export async function getCommunityPlugin(slug: string): Promise<AssetSummary | null> {
  return STATIC_COMMUNITY_PLUGINS.find(p => p.slug === slug) || null;
}

/**
 * Clear the plugins cache (useful for forcing refresh).
 */
export function clearCommunityPluginsCache(): void {
  pluginsCache = null;
  cacheTimestamp = 0;
}
