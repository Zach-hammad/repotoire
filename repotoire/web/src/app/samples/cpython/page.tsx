import Link from 'next/link';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { Breadcrumb } from '@/components/ui/breadcrumb';
import type { LucideIcon } from 'lucide-react';
import {
  ArrowRight,
  Github,
  ExternalLink,
  AlertTriangle,
  XCircle,
  Clock,
  CheckCircle2,
  FileCode2,
  GitBranch,
  Users,
  Activity,
  Shield,
  Zap,
  Info,
} from 'lucide-react';

// Mock data for CPython health report
const healthData = {
  repo: {
    name: 'python/cpython',
    description: 'The Python programming language.',
    url: 'https://github.com/python/cpython',
    stars: 63000,
    forks: 30200,
    contributors: 2400,
    language: 'Python',
  },
  score: {
    total: 79,
    grade: 'B',
    structure: 85,
    quality: 76,
    architecture: 74,
  },
  findings: {
    total: 892,
    critical: 8,
    high: 34,
    medium: 215,
    low: 478,
    info: 157,
  },
  topIssues: [
    {
      severity: 'critical',
      title: 'Buffer overflow in Unicode string handling',
      file: 'Objects/unicodeobject.c',
      detector: 'Security',
    },
    {
      severity: 'critical',
      title: 'Integer overflow in array allocation',
      file: 'Modules/arraymodule.c',
      detector: 'Security',
    },
    {
      severity: 'high',
      title: 'Cyclomatic complexity of 127 exceeds threshold (15)',
      file: 'Python/compile.c',
      detector: 'Complexity',
    },
    {
      severity: 'high',
      title: 'Function exceeds 500 lines (max: 50)',
      file: 'Python/ceval.c',
      detector: 'Code Style',
    },
    {
      severity: 'medium',
      title: 'Missing error handling for memory allocation',
      file: 'Objects/listobject.c',
      detector: 'Error Handling',
    },
  ],
  fileHotspots: [
    { file: 'ceval.c', count: 89, path: 'Python/' },
    { file: 'compile.c', count: 67, path: 'Python/' },
    { file: 'unicodeobject.c', count: 54, path: 'Objects/' },
    { file: 'typeobject.c', count: 48, path: 'Objects/' },
    { file: 'dictobject.c', count: 42, path: 'Objects/' },
  ],
  detectorBreakdown: [
    { name: 'Complexity', count: 234, color: '#8b5cf6' },
    { name: 'Code Style', count: 189, color: '#3b82f6' },
    { name: 'Duplication', count: 145, color: '#10b981' },
    { name: 'Security', count: 42, color: '#ef4444' },
    { name: 'Error Handling', count: 98, color: '#f59e0b' },
    { name: 'Dead Code', count: 112, color: '#6b7280' },
    { name: 'Memory Safety', count: 72, color: '#ec4899' },
  ],
};

const gradeColors: Record<string, string> = {
  A: 'bg-[var(--color-success)]',
  B: 'bg-lime-500',
  C: 'bg-[var(--color-warning)]',
  D: 'bg-orange-500',
  F: 'bg-[var(--color-error)]',
};

const severityColors: Record<string, { bg: string; text: string; icon: LucideIcon }> = {
  critical: { bg: 'bg-error-muted', text: 'text-error', icon: AlertTriangle },
  high: { bg: 'bg-warning-muted', text: 'text-warning', icon: XCircle },
  medium: { bg: 'bg-warning-muted', text: 'text-warning', icon: Clock },
  low: { bg: 'bg-success-muted', text: 'text-success', icon: CheckCircle2 },
  info: { bg: 'bg-info-muted', text: 'text-info', icon: Info },
};

export const metadata = {
  title: 'CPython Health Report | Repotoire',
  description: 'Code health analysis report for python/cpython - The Python programming language.',
};

export default function CpythonSamplePage() {
  return (
    <div className="min-h-screen bg-background dot-grid">
      <div className="container max-w-7xl py-8 px-4">
        {/* Breadcrumb */}
        <Breadcrumb
          items={[
            { label: 'Samples', href: '/samples' },
            { label: 'python/cpython' },
          ]}
          showHome={false}
          className="mb-6"
        />

        {/* Header */}
        <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-6 mb-8">
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <Github className="h-8 w-8" />
              <h1 className="text-3xl font-bold">{healthData.repo.name}</h1>
              <Badge variant="secondary">Sample Report</Badge>
            </div>
            <p className="text-muted-foreground max-w-2xl">
              {healthData.repo.description}
            </p>
            <div className="flex items-center gap-4 text-sm text-muted-foreground">
              <span className="flex items-center gap-1">
                <Activity className="h-4 w-4" />
                {(healthData.repo.stars / 1000).toFixed(0)}k stars
              </span>
              <span className="flex items-center gap-1">
                <GitBranch className="h-4 w-4" />
                {(healthData.repo.forks / 1000).toFixed(1)}k forks
              </span>
              <span className="flex items-center gap-1">
                <Users className="h-4 w-4" />
                {healthData.repo.contributors}+ contributors
              </span>
            </div>
          </div>
          <div className="flex gap-3">
            <a
              href={healthData.repo.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <Button variant="outline">
                <Github className="mr-2 h-4 w-4" />
                View on GitHub
                <ExternalLink className="ml-2 h-3 w-3" />
              </Button>
            </a>
            <Link href="/docs/cli">
              <Button>
                Analyze Your Repo
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </Link>
          </div>
        </div>

        {/* Health Score Overview */}
        <div className="grid gap-6 lg:grid-cols-[300px_1fr] mb-8">
          {/* Score Card */}
          <Card className="lg:row-span-2">
            <CardHeader>
              <CardTitle>Health Score</CardTitle>
              <CardDescription>Overall code quality assessment</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Big score */}
              <div className="text-center">
                <div className="relative inline-flex items-center justify-center">
                  <div className={`w-32 h-32 rounded-full ${gradeColors[healthData.score.grade]} flex items-center justify-center`}>
                    <span className="text-5xl font-bold text-white">{healthData.score.total}</span>
                  </div>
                </div>
                <div className="mt-3">
                  <Badge className={`${gradeColors[healthData.score.grade]} text-white text-lg px-4 py-1`}>
                    Grade {healthData.score.grade}
                  </Badge>
                </div>
              </div>

              {/* Category breakdown */}
              <div className="space-y-4">
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span>Structure</span>
                    <span className="font-medium">{healthData.score.structure}%</span>
                  </div>
                  <Progress value={healthData.score.structure} className="h-2" />
                </div>
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span>Quality</span>
                    <span className="font-medium">{healthData.score.quality}%</span>
                  </div>
                  <Progress value={healthData.score.quality} className="h-2" />
                </div>
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span>Architecture</span>
                    <span className="font-medium">{healthData.score.architecture}%</span>
                  </div>
                  <Progress value={healthData.score.architecture} className="h-2" />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Severity Cards */}
          <div className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-5">
            {Object.entries(healthData.findings).filter(([key]) => key !== 'total').map(([severity, count]) => {
              const { bg, text, icon: Icon } = severityColors[severity];
              return (
                <Card key={severity} size="compact" className={`border-l-4 ${text.replace('text-', 'border-l-')}`}>
                  <CardContent className="pt-4">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                        {severity}
                      </span>
                      <Icon className={`h-4 w-4 ${text}`} />
                    </div>
                    <p className={`text-2xl font-bold ${text}`}>{count}</p>
                  </CardContent>
                </Card>
              );
            })}
          </div>

          {/* Top Issues */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Zap className="h-5 w-5" />
                Top Issues
              </CardTitle>
              <CardDescription>Most critical findings requiring attention</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {healthData.topIssues.map((issue, i) => {
                  const { bg, text, icon: Icon } = severityColors[issue.severity];
                  return (
                    <div key={i} className={`flex items-start gap-3 p-3 rounded-lg ${bg}`}>
                      <Icon className={`h-5 w-5 ${text} shrink-0 mt-0.5`} />
                      <div className="min-w-0 flex-1">
                        <p className="font-medium text-sm">{issue.title}</p>
                        <p className="text-xs text-muted-foreground truncate">{issue.file}</p>
                        <Badge variant="outline" className="mt-1 text-xs">
                          {issue.detector}
                        </Badge>
                      </div>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Second row */}
        <div className="grid gap-6 lg:grid-cols-2 mb-8">
          {/* File Hotspots */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FileCode2 className="h-5 w-5" />
                File Hotspots
              </CardTitle>
              <CardDescription>Files with the most issues</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {healthData.fileHotspots.map((file, i) => (
                  <div key={i} className="flex items-center gap-3">
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-sm truncate">{file.file}</p>
                      <p className="text-xs text-muted-foreground truncate">{file.path}</p>
                    </div>
                    <Badge variant="secondary">{file.count} issues</Badge>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Detector Breakdown */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Shield className="h-5 w-5" />
                Detector Breakdown
              </CardTitle>
              <CardDescription>Issues by category</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {healthData.detectorBreakdown.map((detector, i) => (
                  <div key={i} className="space-y-1">
                    <div className="flex items-center justify-between text-sm">
                      <span>{detector.name}</span>
                      <span className="font-medium">{detector.count}</span>
                    </div>
                    <div className="h-2 bg-muted rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all"
                        style={{
                          width: `${(detector.count / healthData.findings.total) * 100}%`,
                          backgroundColor: detector.color,
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* CTA Banner */}
        <Card className="border-2 border-primary/20 bg-gradient-to-br from-primary/5 via-background to-primary/5">
          <CardContent className="py-8 text-center">
            <h2 className="text-2xl font-bold mb-2">
              Get insights like these for your codebase
            </h2>
            <p className="text-muted-foreground mb-6 max-w-lg mx-auto">
              Repotoire analyzes your code structure, detects issues, and provides actionable recommendations
              to improve your code health.
            </p>
            <div className="flex items-center justify-center gap-4">
              <Link href="/docs/cli">
                <Button size="lg">
                  Start Free Analysis
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </Link>
              <Link href="/samples">
                <Button variant="outline" size="lg">
                  View More Samples
                </Button>
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
