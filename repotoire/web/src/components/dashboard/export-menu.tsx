'use client';

import { useState } from 'react';
import { Download, FileText, FileSpreadsheet, FileJson, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { toast } from 'sonner';

export type ExportFormat = 'pdf' | 'csv' | 'json';

interface ExportData {
  repoName: string;
  score: number;
  structure: number;
  quality: number;
  architecture: number;
  findings: Array<{
    id: string;
    severity: string;
    title: string;
    file: string;
    line?: number;
    detector: string;
    description?: string;
  }>;
  analyzedAt: Date;
}

interface ExportMenuProps {
  data: ExportData;
  className?: string;
}

// Mock data for demonstration
const mockData: ExportData = {
  repoName: 'repotoire/web',
  score: 87,
  structure: 89,
  quality: 84,
  architecture: 88,
  findings: [
    {
      id: '1',
      severity: 'critical',
      title: 'Potential SQL Injection',
      file: 'src/api/users.ts',
      line: 42,
      detector: 'Security',
      description: 'User input is concatenated directly into SQL query',
    },
    {
      id: '2',
      severity: 'high',
      title: 'Cyclomatic complexity of 25',
      file: 'src/utils/parser.ts',
      line: 156,
      detector: 'Complexity',
      description: 'Function exceeds complexity threshold of 15',
    },
    {
      id: '3',
      severity: 'medium',
      title: 'Duplicate code block',
      file: 'src/components/Dashboard.tsx',
      line: 89,
      detector: 'Duplication',
      description: '85% similarity with src/components/Admin.tsx:45',
    },
  ],
  analyzedAt: new Date(),
};

function generateCSV(data: ExportData): string {
  const headers = ['Severity', 'Title', 'File', 'Line', 'Detector', 'Description'];
  const rows = data.findings.map((f) => [
    f.severity,
    `"${f.title.replace(/"/g, '""')}"`,
    f.file,
    f.line || '',
    f.detector,
    `"${(f.description || '').replace(/"/g, '""')}"`,
  ]);

  const summary = [
    [''],
    ['Summary'],
    ['Repository', data.repoName],
    ['Health Score', data.score],
    ['Structure Score', data.structure],
    ['Quality Score', data.quality],
    ['Architecture Score', data.architecture],
    ['Total Findings', data.findings.length],
    ['Analyzed At', data.analyzedAt.toISOString()],
  ];

  return [
    headers.join(','),
    ...rows.map((r) => r.join(',')),
    ...summary.map((r) => r.join(',')),
  ].join('\n');
}

function generateJSON(data: ExportData): string {
  return JSON.stringify(
    {
      repository: data.repoName,
      analyzedAt: data.analyzedAt.toISOString(),
      scores: {
        overall: data.score,
        structure: data.structure,
        quality: data.quality,
        architecture: data.architecture,
      },
      findings: data.findings,
      summary: {
        total: data.findings.length,
        bySeverity: {
          critical: data.findings.filter((f) => f.severity === 'critical').length,
          high: data.findings.filter((f) => f.severity === 'high').length,
          medium: data.findings.filter((f) => f.severity === 'medium').length,
          low: data.findings.filter((f) => f.severity === 'low').length,
        },
      },
    },
    null,
    2
  );
}

async function generatePDF(data: ExportData): Promise<Blob> {
  // In a real implementation, this would use a library like jsPDF or
  // call a server endpoint to generate the PDF
  // For now, we'll create a simple HTML-to-PDF approach

  const html = `
    <!DOCTYPE html>
    <html>
    <head>
      <title>Health Report - ${data.repoName}</title>
      <style>
        body { font-family: system-ui, -apple-system, sans-serif; padding: 40px; max-width: 800px; margin: 0 auto; }
        h1 { color: #1a1a1a; border-bottom: 2px solid #e5e5e5; padding-bottom: 10px; }
        h2 { color: #404040; margin-top: 30px; }
        .score-card { background: #f5f5f5; padding: 20px; border-radius: 8px; margin: 20px 0; }
        .score-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; text-align: center; }
        .score-item { background: white; padding: 15px; border-radius: 6px; }
        .score-value { font-size: 32px; font-weight: bold; color: #22c55e; }
        .score-label { font-size: 12px; color: #666; text-transform: uppercase; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #e5e5e5; }
        th { background: #f5f5f5; font-weight: 600; }
        .severity-critical { color: #ef4444; }
        .severity-high { color: #f97316; }
        .severity-medium { color: #eab308; }
        .severity-low { color: #22c55e; }
        .footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #e5e5e5; color: #666; font-size: 12px; }
      </style>
    </head>
    <body>
      <h1>Code Health Report</h1>
      <p><strong>Repository:</strong> ${data.repoName}</p>
      <p><strong>Generated:</strong> ${data.analyzedAt.toLocaleString()}</p>

      <div class="score-card">
        <div class="score-grid">
          <div class="score-item">
            <div class="score-value">${data.score}</div>
            <div class="score-label">Overall</div>
          </div>
          <div class="score-item">
            <div class="score-value">${data.structure}</div>
            <div class="score-label">Structure</div>
          </div>
          <div class="score-item">
            <div class="score-value">${data.quality}</div>
            <div class="score-label">Quality</div>
          </div>
          <div class="score-item">
            <div class="score-value">${data.architecture}</div>
            <div class="score-label">Architecture</div>
          </div>
        </div>
      </div>

      <h2>Findings (${data.findings.length})</h2>
      <table>
        <thead>
          <tr>
            <th>Severity</th>
            <th>Title</th>
            <th>Location</th>
            <th>Detector</th>
          </tr>
        </thead>
        <tbody>
          ${data.findings
            .map(
              (f) => `
            <tr>
              <td class="severity-${f.severity}">${f.severity.toUpperCase()}</td>
              <td>${f.title}</td>
              <td>${f.file}${f.line ? `:${f.line}` : ''}</td>
              <td>${f.detector}</td>
            </tr>
          `
            )
            .join('')}
        </tbody>
      </table>

      <div class="footer">
        <p>Generated by Repotoire • https://repotoire.com</p>
      </div>
    </body>
    </html>
  `;

  // For actual PDF generation, you would use a library
  // This returns the HTML as a blob for demonstration
  return new Blob([html], { type: 'text/html' });
}

function downloadFile(content: string | Blob, filename: string, mimeType: string) {
  const blob = content instanceof Blob ? content : new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export function ExportMenu({ data = mockData, className }: Partial<ExportMenuProps>) {
  const [exporting, setExporting] = useState<ExportFormat | null>(null);

  const handleExport = async (format: ExportFormat) => {
    setExporting(format);

    try {
      const timestamp = new Date().toISOString().split('T')[0];
      const baseFilename = `${data.repoName.replace('/', '-')}-health-report-${timestamp}`;

      switch (format) {
        case 'csv': {
          const csv = generateCSV(data);
          downloadFile(csv, `${baseFilename}.csv`, 'text/csv');
          toast.success('CSV exported successfully');
          break;
        }
        case 'json': {
          const json = generateJSON(data);
          downloadFile(json, `${baseFilename}.json`, 'application/json');
          toast.success('JSON exported successfully');
          break;
        }
        case 'pdf': {
          const pdf = await generatePDF(data);
          // For actual PDF, change extension to .pdf and type to application/pdf
          downloadFile(pdf, `${baseFilename}.html`, 'text/html');
          toast.success('Report exported successfully', {
            description: 'Open the HTML file in your browser and use Print > Save as PDF',
          });
          break;
        }
      }
    } catch (error) {
      toast.error('Export failed', {
        description: 'An error occurred while exporting the report',
      });
    } finally {
      setExporting(null);
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className={className}>
          {exporting ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Download className="mr-2 h-4 w-4" />
          )}
          Export
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>Export Format</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={() => handleExport('pdf')}
          disabled={exporting !== null}
        >
          <FileText className="mr-2 h-4 w-4" />
          PDF Report
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={() => handleExport('csv')}
          disabled={exporting !== null}
        >
          <FileSpreadsheet className="mr-2 h-4 w-4" />
          CSV Spreadsheet
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={() => handleExport('json')}
          disabled={exporting !== null}
        >
          <FileJson className="mr-2 h-4 w-4" />
          JSON Data
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
