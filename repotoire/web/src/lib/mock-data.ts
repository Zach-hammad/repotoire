import {
  AnalyticsSummary,
  CodeChange,
  Evidence,
  FileHotspot,
  FixComment,
  FixConfidence,
  FixProposal,
  FixStatus,
  FixType,
  HealthScore,
  PaginatedResponse,
  TrendDataPoint,
} from '@/types';

// Sample evidence data
const sampleEvidence: Evidence = {
  similar_patterns: [
    'Found 5 similar functions using the same pattern in utils/*.py',
    'Repository contains 3 other refactored examples of this pattern',
  ],
  documentation_refs: [
    'PEP 8 Style Guide - Function complexity guidelines',
    'Clean Code - Single Responsibility Principle',
  ],
  best_practices: [
    'Functions should have a single responsibility',
    'Complex conditionals should be extracted into named functions',
    'Early returns reduce nesting and improve readability',
  ],
  rag_context_count: 12,
};

// Sample code changes
const sampleChanges: CodeChange[] = [
  {
    file_path: 'repotoire/analyzers/complexity.py',
    original_code: `def analyze_complexity(self, code: str, threshold: int = 10) -> List[Finding]:
    findings = []
    if code:
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.FunctionDef):
                complexity = self._calculate_complexity(node)
                if complexity > threshold:
                    findings.append(Finding(
                        message=f"Function {node.name} has complexity {complexity}",
                        severity="high",
                        line=node.lineno
                    ))
    return findings`,
    fixed_code: `def analyze_complexity(self, code: str, threshold: int = 10) -> List[Finding]:
    """Analyze code complexity and return findings for complex functions."""
    if not code:
        return []

    findings = []
    for node in self._get_function_nodes(code):
        finding = self._check_function_complexity(node, threshold)
        if finding:
            findings.append(finding)
    return findings

def _get_function_nodes(self, code: str) -> Iterator[ast.FunctionDef]:
    """Extract function definition nodes from code."""
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.FunctionDef):
            yield node

def _check_function_complexity(
    self, node: ast.FunctionDef, threshold: int
) -> Optional[Finding]:
    """Check if a function exceeds complexity threshold."""
    complexity = self._calculate_complexity(node)
    if complexity <= threshold:
        return None
    return Finding(
        message=f"Function {node.name} has complexity {complexity}",
        severity="high",
        line=node.lineno
    )`,
    start_line: 45,
    end_line: 58,
    description: 'Extracted nested logic into helper methods for better readability and testability',
  },
];

// Generate mock fixes
function generateMockFixes(count: number = 50): FixProposal[] {
  const fixes: FixProposal[] = [];
  const types: FixType[] = ['refactor', 'simplify', 'extract', 'security', 'type_hint', 'documentation'];
  const confidences: FixConfidence[] = ['high', 'medium', 'low'];
  const statuses: FixStatus[] = ['pending', 'approved', 'rejected', 'applied', 'failed'];

  const titles = [
    'Extract complex method into smaller functions',
    'Simplify nested conditionals',
    'Add type hints to function parameters',
    'Fix potential SQL injection vulnerability',
    'Remove unused imports',
    'Add docstring to public function',
    'Refactor long method',
    'Extract duplicate code into shared utility',
    'Fix insecure random number generation',
    'Simplify boolean expression',
  ];

  const files = [
    'repotoire/analyzers/complexity.py',
    'repotoire/detectors/security.py',
    'repotoire/pipeline/ingestion.py',
    'repotoire/graph/client.py',
    'repotoire/api/routes.py',
    'repotoire/utils/helpers.py',
    'repotoire/models/entities.py',
    'repotoire/reporters/html.py',
  ];

  for (let i = 0; i < count; i++) {
    const type = types[Math.floor(Math.random() * types.length)];
    const confidence = confidences[Math.floor(Math.random() * confidences.length)];
    // Ensure first 5 fixes are pending for testing
    const status = i < 5 ? 'pending' : statuses[Math.floor(Math.random() * statuses.length)];
    const title = titles[Math.floor(Math.random() * titles.length)];
    const file = files[Math.floor(Math.random() * files.length)];

    const createdAt = new Date();
    createdAt.setDate(createdAt.getDate() - Math.floor(Math.random() * 30));

    fixes.push({
      id: `fix-${i + 1}`,
      fix_type: type,
      confidence,
      changes: [
        {
          ...sampleChanges[0],
          file_path: file,
        },
      ],
      title,
      description: `This fix addresses a code quality issue detected in ${file}. The AI recommends ${type} to improve maintainability.`,
      rationale: `Based on analysis of the codebase and best practices, this ${type} will improve code quality by reducing complexity and improving readability. The change follows established patterns found in similar files.`,
      evidence: sampleEvidence,
      status,
      created_at: createdAt.toISOString(),
      applied_at: status === 'applied' ? new Date().toISOString() : null,
      syntax_valid: Math.random() > 0.1,
      tests_generated: Math.random() > 0.6,
      test_code: Math.random() > 0.6 ? `def test_${title.toLowerCase().replace(/\s+/g, '_')}():
    # Arrange
    analyzer = ComplexityAnalyzer()

    # Act
    result = analyzer.analyze_complexity(sample_code)

    # Assert
    assert len(result) == 1
    assert result[0].severity == "high"` : null,
      branch_name: status === 'applied' ? `fix/${i + 1}-${type}` : null,
      commit_message: status === 'applied' ? `fix: ${title.toLowerCase()}` : null,
    });
  }

  return fixes;
}

// Mock API functions
const mockFixes = generateMockFixes(50);

export function getMockFixes(
  page: number = 1,
  pageSize: number = 20,
  status?: FixStatus[],
  confidence?: FixConfidence[],
  fixType?: FixType[],
  search?: string
): PaginatedResponse<FixProposal> {
  let filtered = [...mockFixes];

  if (status?.length) {
    filtered = filtered.filter((f) => status.includes(f.status));
  }
  if (confidence?.length) {
    filtered = filtered.filter((f) => confidence.includes(f.confidence));
  }
  if (fixType?.length) {
    filtered = filtered.filter((f) => fixType.includes(f.fix_type));
  }
  if (search) {
    const searchLower = search.toLowerCase();
    filtered = filtered.filter(
      (f) =>
        f.title.toLowerCase().includes(searchLower) ||
        f.description.toLowerCase().includes(searchLower)
    );
  }

  const start = (page - 1) * pageSize;
  const items = filtered.slice(start, start + pageSize);

  return {
    items,
    total: filtered.length,
    page,
    page_size: pageSize,
    has_more: start + pageSize < filtered.length,
  };
}

export function getMockFix(id: string): FixProposal | undefined {
  return mockFixes.find((f) => f.id === id);
}

export function getMockAnalyticsSummary(): AnalyticsSummary {
  // Mock findings by severity
  const critical = 5;
  const high = 15;
  const medium = 25;
  const low = 30;
  const info = 10;

  return {
    total_findings: critical + high + medium + low + info,
    critical,
    high,
    medium,
    low,
    info,
    by_severity: { critical, high, medium, low, info },
    by_detector: {
      ruff: 25,
      mypy: 15,
      bandit: 10,
      pylint: 20,
      complexity: 15,
    },
  };
}

export function getMockTrends(days: number = 14): TrendDataPoint[] {
  const trends: TrendDataPoint[] = [];
  const today = new Date();

  for (let i = days - 1; i >= 0; i--) {
    const date = new Date(today);
    date.setDate(date.getDate() - i);

    const critical = Math.floor(Math.random() * 3);
    const high = Math.floor(Math.random() * 5) + 2;
    const medium = Math.floor(Math.random() * 10) + 5;
    const low = Math.floor(Math.random() * 15) + 5;
    const info = Math.floor(Math.random() * 5);

    trends.push({
      date: date.toISOString().split('T')[0],
      critical,
      high,
      medium,
      low,
      info,
      total: critical + high + medium + low + info,
    });
  }

  return trends;
}

export function getMockFileHotspots(limit: number = 5): FileHotspot[] {
  const fileCount: Record<string, number> = {};

  for (const fix of mockFixes) {
    for (const change of fix.changes) {
      fileCount[change.file_path] = (fileCount[change.file_path] || 0) + 1;
    }
  }

  return Object.entries(fileCount)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([file_path, finding_count]) => ({
      file_path,
      finding_count,
      severity_breakdown: { critical: 0, high: 2, medium: 3, low: 1, info: 0 },
    }));
}

export function getMockComments(fixId: string): FixComment[] {
  return [
    {
      id: 'comment-1',
      fix_id: fixId,
      author: 'Developer',
      content: 'This looks good, but can we add a test for edge cases?',
      created_at: new Date(Date.now() - 86400000).toISOString(),
    },
    {
      id: 'comment-2',
      fix_id: fixId,
      author: 'AI Assistant',
      content: 'I\'ve added test cases for edge conditions including empty input and maximum values.',
      created_at: new Date(Date.now() - 3600000).toISOString(),
    },
  ];
}

export function getMockHealthScore(): HealthScore {
  const summary = getMockAnalyticsSummary();
  const total = summary.total_findings;
  const critical = summary.critical;
  const high = summary.high;

  // Calculate score based on finding metrics (fewer critical/high = better score)
  let score: number;
  if (total > 0) {
    const criticalPenalty = (critical / total) * 30;
    const highPenalty = (high / total) * 15;
    score = Math.max(0, Math.min(100, Math.round(100 - criticalPenalty - highPenalty)));
  } else {
    score = 100;
  }

  // Calculate grade
  let grade: 'A' | 'B' | 'C' | 'D' | 'F';
  if (score >= 90) grade = 'A';
  else if (score >= 80) grade = 'B';
  else if (score >= 70) grade = 'C';
  else if (score >= 60) grade = 'D';
  else grade = 'F';

  return {
    score,
    grade,
    trend: 'improving',
    categories: {
      structure: 85,
      quality: 78,
      architecture: 82,
    },
  };
}
