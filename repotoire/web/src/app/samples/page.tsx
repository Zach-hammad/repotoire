import Link from 'next/link';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ArrowRight, Github, Star } from 'lucide-react';

// Sample repositories with pre-generated reports
const sampleRepos = [
  {
    name: 'facebook/react',
    description: 'A declarative, efficient, and flexible JavaScript library for building user interfaces.',
    healthScore: 87,
    grade: 'A',
    stars: '227k',
    language: 'JavaScript',
    slug: 'react',
  },
  {
    name: 'vercel/next.js',
    description: 'The React Framework for the Web.',
    healthScore: 82,
    grade: 'B',
    stars: '126k',
    language: 'JavaScript',
    slug: 'nextjs',
  },
  {
    name: 'python/cpython',
    description: 'The Python programming language.',
    healthScore: 79,
    grade: 'B',
    stars: '63k',
    language: 'Python',
    slug: 'cpython',
  },
];

const gradeColors: Record<string, string> = {
  A: 'bg-success-muted text-success border-success',
  B: 'bg-lime-500/10 text-lime-600 border-lime-500/30',
  C: 'bg-warning-muted text-warning border-warning',
  D: 'bg-warning-muted text-warning border-warning',
  F: 'bg-error-muted text-error border-error',
};

export const metadata = {
  title: 'Sample Reports | Repotoire',
  description: 'Explore sample code health reports from popular open source repositories.',
};

export default function SamplesPage() {
  return (
    <div className="min-h-screen bg-background">
      <div className="container max-w-5xl py-16 px-4">
        {/* Header */}
        <div className="text-center mb-12">
          <Badge variant="outline" className="mb-4">Sample Reports</Badge>
          <h1 className="text-4xl font-bold tracking-tight mb-4">
            See Repotoire in Action
          </h1>
          <p className="text-xl text-muted-foreground max-w-2xl mx-auto">
            Explore code health reports from popular open source projects.
            See the insights you'll get when you analyze your own repositories.
          </p>
        </div>

        {/* Sample repos grid */}
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3 mb-12">
          {sampleRepos.map((repo) => (
            <Link key={repo.slug} href={`/samples/${repo.slug}`}>
              <Card className="h-full hover:shadow-lg transition-shadow cursor-pointer group">
                <CardHeader>
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <Github className="h-5 w-5 text-muted-foreground" />
                      <Badge variant="secondary">{repo.language}</Badge>
                    </div>
                    <Badge className={gradeColors[repo.grade]}>
                      {repo.grade}
                    </Badge>
                  </div>
                  <CardTitle className="group-hover:text-primary transition-colors">
                    {repo.name}
                  </CardTitle>
                  <CardDescription className="line-clamp-2">
                    {repo.description}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4 text-sm text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <Star className="h-4 w-4" />
                        {repo.stars}
                      </span>
                      <span>
                        Score: <span className="font-semibold text-foreground">{repo.healthScore}</span>
                      </span>
                    </div>
                    <ArrowRight className="h-4 w-4 text-muted-foreground group-hover:text-primary group-hover:translate-x-1 transition-all" />
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>

        {/* CTA */}
        <div className="text-center">
          <p className="text-muted-foreground mb-4">
            Ready to analyze your own repositories?
          </p>
          <div className="flex items-center justify-center gap-4">
            <Link href="/docs/cli">
              <Button size="lg">
                Get Started Free
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </Link>
            <Link href="/">
              <Button variant="outline" size="lg">
                Learn More
              </Button>
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
