'use client';

import { useState } from 'react';
import { Check, Copy, FileCode2, ExternalLink } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

interface CodeSnippetProps {
  code: string;
  language?: string;
  fileName?: string;
  startLine?: number;
  endLine?: number;
  highlightLines?: number[];
  maxHeight?: string;
  showLineNumbers?: boolean;
  githubUrl?: string;
}

/**
 * Syntax-highlighted code snippet component with line numbers.
 * Highlights specific lines to draw attention to issues.
 */
export function CodeSnippet({
  code,
  language = 'python',
  fileName,
  startLine = 1,
  endLine,
  highlightLines = [],
  maxHeight = '400px',
  showLineNumbers = true,
  githubUrl,
}: CodeSnippetProps) {
  const [copied, setCopied] = useState(false);

  const lines = code.split('\n');
  const actualEndLine = endLine || startLine + lines.length - 1;

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Map language names to Prism-compatible class names
  const languageClass = getLanguageClass(language);

  return (
    <div className="rounded-lg border bg-muted/30 overflow-hidden">
      {/* Header */}
      {(fileName || githubUrl) && (
        <div className="flex items-center justify-between px-4 py-2 border-b bg-muted/50">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <FileCode2 className="h-4 w-4" />
            <span className="font-mono">{fileName}</span>
            {startLine && (
              <span className="text-xs">
                (lines {startLine}-{actualEndLine})
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {githubUrl && (
              <Button
                variant="ghost"
                size="sm"
                asChild
                className="h-7 px-2"
              >
                <a href={githubUrl} target="_blank" rel="noopener noreferrer">
                  <ExternalLink className="h-3.5 w-3.5 mr-1" />
                  View on GitHub
                </a>
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={handleCopy}
              className="h-7 px-2"
            >
              {copied ? (
                <>
                  <Check className="h-3.5 w-3.5 mr-1" />
                  Copied
                </>
              ) : (
                <>
                  <Copy className="h-3.5 w-3.5 mr-1" />
                  Copy
                </>
              )}
            </Button>
          </div>
        </div>
      )}

      {/* Code content */}
      <div
        className="overflow-auto"
        style={{ maxHeight }}
      >
        <pre className={cn('p-4 text-sm', !showLineNumbers && 'pl-4')}>
          <code className={languageClass}>
            {lines.map((line, index) => {
              const lineNumber = startLine + index;
              const isHighlighted = highlightLines.includes(lineNumber);

              return (
                <div
                  key={index}
                  className={cn(
                    'flex',
                    isHighlighted && 'bg-warning-muted -mx-4 px-4 border-l-2 border-warning'
                  )}
                >
                  {showLineNumbers && (
                    <span
                      className={cn(
                        'select-none pr-4 text-muted-foreground text-right min-w-[3rem]',
                        isHighlighted && 'text-warning font-medium'
                      )}
                    >
                      {lineNumber}
                    </span>
                  )}
                  <span className="flex-1 whitespace-pre">{line || ' '}</span>
                </div>
              );
            })}
          </code>
        </pre>
      </div>
    </div>
  );
}

/**
 * Map common language names to syntax highlighting classes.
 */
function getLanguageClass(language: string): string {
  const languageMap: Record<string, string> = {
    python: 'language-python',
    py: 'language-python',
    javascript: 'language-javascript',
    js: 'language-javascript',
    typescript: 'language-typescript',
    ts: 'language-typescript',
    tsx: 'language-tsx',
    jsx: 'language-jsx',
    rust: 'language-rust',
    go: 'language-go',
    java: 'language-java',
    c: 'language-c',
    cpp: 'language-cpp',
    csharp: 'language-csharp',
    cs: 'language-csharp',
    ruby: 'language-ruby',
    php: 'language-php',
    sql: 'language-sql',
    yaml: 'language-yaml',
    yml: 'language-yaml',
    json: 'language-json',
    html: 'language-html',
    css: 'language-css',
    scss: 'language-scss',
    bash: 'language-bash',
    shell: 'language-bash',
    sh: 'language-bash',
    markdown: 'language-markdown',
    md: 'language-markdown',
  };

  return languageMap[language.toLowerCase()] || `language-${language}`;
}

/**
 * Extract language from file path based on extension.
 */
export function getLanguageFromPath(filePath: string): string {
  const extension = filePath.split('.').pop()?.toLowerCase() || '';
  const extensionMap: Record<string, string> = {
    py: 'python',
    js: 'javascript',
    ts: 'typescript',
    tsx: 'typescript',
    jsx: 'javascript',
    rs: 'rust',
    go: 'go',
    java: 'java',
    c: 'c',
    cpp: 'cpp',
    h: 'c',
    hpp: 'cpp',
    cs: 'csharp',
    rb: 'ruby',
    php: 'php',
    sql: 'sql',
    yaml: 'yaml',
    yml: 'yaml',
    json: 'json',
    html: 'html',
    css: 'css',
    scss: 'scss',
    sh: 'bash',
    bash: 'bash',
    md: 'markdown',
  };

  return extensionMap[extension] || extension;
}

interface CodeDiffProps {
  originalCode: string;
  fixedCode: string;
  fileName?: string;
  startLine?: number;
  language?: string;
}

/**
 * Side-by-side or unified diff view for code changes.
 */
export function CodeDiff({
  originalCode,
  fixedCode,
  fileName,
  startLine = 1,
  language = 'python',
}: CodeDiffProps) {
  const [view, setView] = useState<'split' | 'unified'>('split');

  const originalLines = originalCode.split('\n');
  const fixedLines = fixedCode.split('\n');

  return (
    <div className="rounded-lg border overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b bg-muted/50">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <FileCode2 className="h-4 w-4" />
          {fileName && <span className="font-mono">{fileName}</span>}
          <span className="text-xs">
            (line {startLine})
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant={view === 'split' ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setView('split')}
            className="h-7 px-2 text-xs"
          >
            Split
          </Button>
          <Button
            variant={view === 'unified' ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setView('unified')}
            className="h-7 px-2 text-xs"
          >
            Unified
          </Button>
        </div>
      </div>

      {view === 'split' ? (
        <div className="grid grid-cols-2 divide-x">
          {/* Original */}
          <div>
            <div className="px-3 py-1.5 bg-error-muted border-b text-xs font-medium text-error">
              Original
            </div>
            <pre className="p-3 text-sm overflow-auto max-h-[300px]">
              <code className={getLanguageClass(language)}>
                {originalLines.map((line, index) => (
                  <div key={index} className="flex">
                    <span className="select-none pr-3 text-muted-foreground text-right min-w-[2.5rem]">
                      {startLine + index}
                    </span>
                    <span className="whitespace-pre text-error/80">{line || ' '}</span>
                  </div>
                ))}
              </code>
            </pre>
          </div>

          {/* Fixed */}
          <div>
            <div className="px-3 py-1.5 bg-success-muted border-b text-xs font-medium text-success">
              Fixed
            </div>
            <pre className="p-3 text-sm overflow-auto max-h-[300px]">
              <code className={getLanguageClass(language)}>
                {fixedLines.map((line, index) => (
                  <div key={index} className="flex">
                    <span className="select-none pr-3 text-muted-foreground text-right min-w-[2.5rem]">
                      {startLine + index}
                    </span>
                    <span className="whitespace-pre text-success/80">{line || ' '}</span>
                  </div>
                ))}
              </code>
            </pre>
          </div>
        </div>
      ) : (
        <pre className="p-3 text-sm overflow-auto max-h-[400px]">
          <code className={getLanguageClass(language)}>
            {/* Show removed lines */}
            {originalLines.map((line, index) => (
              <div key={`old-${index}`} className="flex bg-error-muted">
                <span className="select-none pr-2 text-error min-w-[1rem]">-</span>
                <span className="select-none pr-3 text-muted-foreground text-right min-w-[2.5rem]">
                  {startLine + index}
                </span>
                <span className="whitespace-pre text-error/80">{line || ' '}</span>
              </div>
            ))}
            {/* Show added lines */}
            {fixedLines.map((line, index) => (
              <div key={`new-${index}`} className="flex bg-success-muted">
                <span className="select-none pr-2 text-success min-w-[1rem]">+</span>
                <span className="select-none pr-3 text-muted-foreground text-right min-w-[2.5rem]">
                  {startLine + index}
                </span>
                <span className="whitespace-pre text-success/80">{line || ' '}</span>
              </div>
            ))}
          </code>
        </pre>
      )}
    </div>
  );
}
