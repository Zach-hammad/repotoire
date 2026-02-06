"""AI Churn Pattern Detector.

Detects code with high modification frequency shortly after creation - a pattern
commonly seen with AI-generated code that gets quickly revised or corrected.

The detector analyzes git history to find:
- Code added and then modified within 24-48 hours
- High churn ratio (lines_modified / lines_added) within the first week
- Functions/files that undergo rapid iterative changes

This is an indicator of "AI slop" - code that was generated without deep understanding
and requires significant human correction to function properly.
"""

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)

# Try to import GitPython
try:
    import git
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    git = None  # type: ignore


@dataclass
class FileChurnData:
    """Track churn statistics for a file."""
    file_path: str
    created_at: Optional[datetime] = None
    first_commit_sha: str = ""
    lines_added_initially: int = 0
    lines_modified_first_week: int = 0
    modification_count_first_48h: int = 0
    modification_count_first_week: int = 0
    commits: List[Tuple[datetime, int, int]] = field(default_factory=list)  # (timestamp, insertions, deletions)
    
    @property
    def churn_ratio(self) -> float:
        """Calculate churn ratio: lines modified / lines initially added."""
        if self.lines_added_initially == 0:
            return 0.0
        return self.lines_modified_first_week / self.lines_added_initially
    
    @property
    def rapid_revision_score(self) -> float:
        """Score indicating rapid revision pattern (0-1)."""
        # Weight modifications in first 48h more heavily
        score = 0.0
        if self.modification_count_first_48h > 0:
            score += min(self.modification_count_first_48h * 0.3, 0.6)
        if self.modification_count_first_week > 0:
            score += min(self.modification_count_first_week * 0.1, 0.4)
        return min(score, 1.0)


@dataclass
class FunctionChurnData:
    """Track churn statistics for a function."""
    qualified_name: str
    file_path: str
    function_name: str
    line_start: int
    line_end: int
    created_at: Optional[datetime] = None
    lines_added_initially: int = 0
    lines_modified_first_week: int = 0
    modification_count_first_48h: int = 0
    modification_count_first_week: int = 0
    
    @property
    def churn_ratio(self) -> float:
        """Calculate churn ratio: lines modified / lines initially added."""
        if self.lines_added_initially == 0:
            return 0.0
        return self.lines_modified_first_week / self.lines_added_initially


class AIChurnDetector(CodeSmellDetector):
    """Detects AI-generated code patterns through rapid churn analysis.
    
    AI-generated code often exhibits a distinctive "write-then-fix" pattern:
    1. Large initial commits with generated code
    2. Rapid follow-up modifications within 24-48 hours
    3. High line churn as humans correct AI mistakes
    
    This detector flags code with:
    - Churn ratio > 0.5 (modified > 50% of initial lines in first week)
    - Multiple modifications within 48 hours of creation
    - Functions that were heavily revised shortly after being added
    
    Severity levels:
    - CRITICAL: Churn ratio > 1.0 (more lines changed than initially added)
    - HIGH: Churn ratio > 0.5 or 3+ modifications in first 48h
    - MEDIUM: Churn ratio > 0.3 or 2 modifications in first 48h
    - LOW: Early signs of churn pattern
    """
    
    # Thresholds for churn detection
    CRITICAL_CHURN_RATIO = 1.0  # More lines changed than initially added
    HIGH_CHURN_RATIO = 0.5
    MEDIUM_CHURN_RATIO = 0.3
    
    # Time windows
    RAPID_REVISION_WINDOW_HOURS = 48
    FIRST_WEEK_DAYS = 7
    
    # Analysis window (how far back to look)
    ANALYSIS_WINDOW_DAYS = 90
    
    # Minimum lines to consider (ignore tiny files/functions)
    MIN_LINES_THRESHOLD = 10
    
    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
    ):
        """Initialize the AI Churn detector.
        
        Args:
            graph_client: FalkorDB database client
            detector_config: Optional configuration dict. May include:
                - repo_id: Repository UUID for filtering queries
                - repo_path: Path to git repository for history analysis
                - analysis_window_days: How far back to analyze (default: 90)
                - churn_ratio_threshold: Threshold for HIGH severity (default: 0.5)
        """
        super().__init__(graph_client, detector_config)
        self.repo_path = self.config.get("repo_path")
        self.analysis_window_days = self.config.get("analysis_window_days", self.ANALYSIS_WINDOW_DAYS)
        self.churn_threshold = self.config.get("churn_ratio_threshold", self.HIGH_CHURN_RATIO)
        self._git_repo: Optional["git.Repo"] = None
    
    @property
    def git_repo(self) -> Optional["git.Repo"]:
        """Lazy-load git repository."""
        if not GIT_AVAILABLE:
            return None
        if self._git_repo is None and self.repo_path:
            try:
                self._git_repo = git.Repo(self.repo_path, search_parent_directories=True)
            except Exception as e:
                logger.warning(f"Failed to open git repository at {self.repo_path}: {e}")
        return self._git_repo
    
    def detect(self) -> List[Finding]:
        """Detect AI churn patterns in the codebase.
        
        Returns:
            List of findings for high-churn code
        """
        findings = []
        
        if not GIT_AVAILABLE:
            logger.warning("GitPython not available, skipping AI churn detection")
            return findings
        
        if not self.git_repo:
            logger.warning("No git repository available for churn analysis")
            return findings
        
        try:
            # Analyze file-level churn from git history
            file_churn_data = self._analyze_file_churn()
            
            # Create findings for high-churn files
            for file_path, churn_data in file_churn_data.items():
                finding = self._create_file_churn_finding(churn_data)
                if finding:
                    findings.append(finding)
            
            # If we have graph data, also analyze function-level churn
            function_findings = self._detect_function_churn(file_churn_data)
            findings.extend(function_findings)
            
            logger.info(f"AIChurnDetector found {len(findings)} high-churn patterns")
            return findings
            
        except Exception as e:
            logger.error(f"Error in AI churn detection: {e}", exc_info=True)
            return findings
    
    def _analyze_file_churn(self) -> Dict[str, FileChurnData]:
        """Analyze git history for file-level churn patterns.
        
        Returns:
            Dictionary mapping file paths to churn data
        """
        file_churn: Dict[str, FileChurnData] = {}
        
        if not self.git_repo:
            return file_churn
        
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.analysis_window_days)
        
        try:
            # Get commits in reverse chronological order
            commits = list(self.git_repo.iter_commits(
                'HEAD',
                since=cutoff_date.isoformat()
            ))
            
            # Process commits from oldest to newest to track file creation
            for commit in reversed(commits):
                commit_time = commit.committed_datetime
                if commit_time.tzinfo is None:
                    commit_time = commit_time.replace(tzinfo=timezone.utc)
                
                # Get changed files with stats
                if commit.parents:
                    diffs = commit.parents[0].diff(commit)
                else:
                    # Initial commit - all files are new
                    diffs = commit.diff(git.NULL_TREE)
                
                for diff in diffs:
                    file_path = diff.b_path or diff.a_path
                    if not file_path:
                        continue
                    
                    # Only analyze code files
                    if not self._is_code_file(file_path):
                        continue
                    
                    # Get line statistics
                    try:
                        stats = commit.stats.files.get(file_path, {})
                        insertions = stats.get('insertions', 0)
                        deletions = stats.get('deletions', 0)
                    except Exception:
                        insertions = 0
                        deletions = 0
                    
                    if file_path not in file_churn:
                        # New file - track creation
                        file_churn[file_path] = FileChurnData(
                            file_path=file_path,
                            created_at=commit_time,
                            first_commit_sha=commit.hexsha,
                            lines_added_initially=insertions,
                        )
                    else:
                        # Existing file - track modifications
                        churn_data = file_churn[file_path]
                        churn_data.commits.append((commit_time, insertions, deletions))
                        
                        if churn_data.created_at:
                            time_since_creation = commit_time - churn_data.created_at
                            
                            # Track modifications in first 48 hours
                            if time_since_creation <= timedelta(hours=self.RAPID_REVISION_WINDOW_HOURS):
                                churn_data.modification_count_first_48h += 1
                                churn_data.lines_modified_first_week += insertions + deletions
                            
                            # Track modifications in first week
                            elif time_since_creation <= timedelta(days=self.FIRST_WEEK_DAYS):
                                churn_data.modification_count_first_week += 1
                                churn_data.lines_modified_first_week += insertions + deletions
            
        except Exception as e:
            logger.error(f"Failed to analyze git history: {e}", exc_info=True)
        
        return file_churn
    
    def _is_code_file(self, file_path: str) -> bool:
        """Check if file is a code file we should analyze."""
        code_extensions = {
            '.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.go', '.rs',
            '.rb', '.php', '.c', '.cpp', '.h', '.hpp', '.cs', '.swift',
            '.kt', '.scala', '.ex', '.exs'
        }
        return Path(file_path).suffix.lower() in code_extensions
    
    def _create_file_churn_finding(self, churn_data: FileChurnData) -> Optional[Finding]:
        """Create a finding for a high-churn file.
        
        Args:
            churn_data: File churn statistics
            
        Returns:
            Finding if churn exceeds threshold, None otherwise
        """
        # Skip files that are too small or haven't been modified
        if churn_data.lines_added_initially < self.MIN_LINES_THRESHOLD:
            return None
        
        if churn_data.modification_count_first_48h == 0 and churn_data.modification_count_first_week == 0:
            return None
        
        churn_ratio = churn_data.churn_ratio
        
        # Determine severity
        severity = self._calculate_file_severity(churn_data)
        if severity == Severity.INFO:
            return None  # Don't report low-impact findings
        
        # Build description
        description_parts = [
            f"File `{churn_data.file_path}` shows signs of rapid revision after creation.",
            f"",
            f"**Churn Statistics:**",
            f"- Initial lines added: {churn_data.lines_added_initially}",
            f"- Lines modified in first week: {churn_data.lines_modified_first_week}",
            f"- Churn ratio: {churn_ratio:.2f} ({churn_ratio * 100:.0f}% of original code modified)",
            f"- Modifications in first 48h: {churn_data.modification_count_first_48h}",
            f"- Modifications in first week: {churn_data.modification_count_first_week}",
        ]
        
        if churn_ratio > self.CRITICAL_CHURN_RATIO:
            description_parts.append("")
            description_parts.append("⚠️ This file has been modified more than it was initially written, "
                                    "suggesting the original code required extensive correction.")
        
        # Suggested fix based on severity
        if severity in (Severity.CRITICAL, Severity.HIGH):
            suggested_fix = (
                "Review this code carefully for correctness issues. Consider:\n"
                "1. Adding comprehensive unit tests\n"
                "2. Reviewing for logical errors or edge cases\n"
                "3. Ensuring proper error handling\n"
                "4. Documenting complex logic that may have been auto-generated"
            )
        else:
            suggested_fix = (
                "Consider adding tests and documentation to stabilize this code. "
                "Monitor for continued high churn."
            )
        
        return Finding(
            id=str(uuid4()),
            detector="AIChurnDetector",
            severity=severity,
            title=f"High churn detected in {Path(churn_data.file_path).name}",
            description="\n".join(description_parts),
            affected_nodes=[churn_data.file_path],
            affected_files=[churn_data.file_path],
            graph_context={
                "churn_ratio": churn_ratio,
                "lines_added_initially": churn_data.lines_added_initially,
                "lines_modified_first_week": churn_data.lines_modified_first_week,
                "modifications_48h": churn_data.modification_count_first_48h,
                "modifications_first_week": churn_data.modification_count_first_week,
                "created_at": churn_data.created_at.isoformat() if churn_data.created_at else None,
                "first_commit": churn_data.first_commit_sha[:8] if churn_data.first_commit_sha else None,
            },
            suggested_fix=suggested_fix,
            estimated_effort="Small (2-4 hours)" if severity == Severity.MEDIUM else "Medium (1-2 days)",
            collaboration_metadata=[
                CollaborationMetadata(
                    detector="AIChurnDetector",
                    confidence=min(0.5 + churn_ratio * 0.3, 0.95),
                    evidence=[
                        f"churn_ratio={churn_ratio:.2f}",
                        f"mods_48h={churn_data.modification_count_first_48h}",
                        f"mods_week={churn_data.modification_count_first_week}",
                    ],
                    tags=["ai-churn", "rapid-revision", "code-quality"],
                )
            ],
            why_it_matters=(
                "Code with high early churn often indicates AI-generated content that required "
                "significant human correction. This pattern is associated with hidden bugs, "
                "incomplete error handling, and logic that may not be fully understood by the team."
            ),
        )
    
    def _calculate_file_severity(self, churn_data: FileChurnData) -> Severity:
        """Calculate severity based on churn metrics.
        
        Args:
            churn_data: File churn statistics
            
        Returns:
            Severity level
        """
        churn_ratio = churn_data.churn_ratio
        
        # Critical: Very high churn ratio or many rapid revisions
        if churn_ratio > self.CRITICAL_CHURN_RATIO:
            return Severity.CRITICAL
        if churn_data.modification_count_first_48h >= 4:
            return Severity.CRITICAL
        
        # High: Significant churn
        if churn_ratio > self.HIGH_CHURN_RATIO:
            return Severity.HIGH
        if churn_data.modification_count_first_48h >= 3:
            return Severity.HIGH
        
        # Medium: Moderate churn
        if churn_ratio > self.MEDIUM_CHURN_RATIO:
            return Severity.MEDIUM
        if churn_data.modification_count_first_48h >= 2:
            return Severity.MEDIUM
        
        # Low: Some churn but not concerning
        if churn_data.modification_count_first_48h >= 1:
            return Severity.LOW
        
        return Severity.INFO
    
    def _detect_function_churn(self, file_churn: Dict[str, FileChurnData]) -> List[Finding]:
        """Detect function-level churn using graph data.
        
        Args:
            file_churn: File-level churn data for context
            
        Returns:
            List of function-level findings
        """
        findings = []
        
        # Query graph for functions in high-churn files
        high_churn_files = [
            path for path, data in file_churn.items()
            if data.churn_ratio > self.MEDIUM_CHURN_RATIO or data.modification_count_first_48h >= 2
        ]
        
        if not high_churn_files:
            return findings
        
        # Query functions in these files
        repo_filter = self._get_isolation_filter("f")
        query = f"""
        MATCH (f:Function)
        WHERE f.filePath IN $file_paths {repo_filter}
        RETURN 
            f.qualifiedName AS qualified_name,
            f.name AS name,
            f.filePath AS file_path,
            f.lineNumber AS line_start,
            coalesce(f.lineEnd, f.lineNumber + 10) AS line_end,
            coalesce(f.complexity, 0) AS complexity,
            coalesce(f.loc, 0) AS loc
        """
        
        try:
            results = self.db.execute_query(query, {
                **self._get_query_params(),
                "file_paths": high_churn_files[:100]  # Limit for performance
            })
            
            for result in results:
                file_path = result.get("file_path")
                if file_path and file_path in file_churn:
                    file_data = file_churn[file_path]
                    
                    # Create function-level finding if the function is significant
                    loc = result.get("loc", 0)
                    complexity = result.get("complexity", 0)
                    
                    # Only flag functions that are both in high-churn files AND complex
                    if loc >= 20 or complexity >= 10:
                        finding = self._create_function_finding(result, file_data)
                        if finding:
                            findings.append(finding)
            
        except Exception as e:
            logger.debug(f"Could not query function data: {e}")
        
        return findings
    
    def _create_function_finding(
        self,
        func_data: Dict,
        file_churn: FileChurnData
    ) -> Optional[Finding]:
        """Create a finding for a function in a high-churn file.
        
        Args:
            func_data: Function data from graph query
            file_churn: File-level churn data
            
        Returns:
            Finding or None
        """
        qualified_name = func_data.get("qualified_name", "")
        function_name = func_data.get("name", "unknown")
        file_path = func_data.get("file_path", "")
        line_start = func_data.get("line_start")
        complexity = func_data.get("complexity", 0)
        
        # Determine if this function needs a separate finding
        # Only create if complexity suggests it's worth calling out
        if complexity < 10 and file_churn.churn_ratio < self.HIGH_CHURN_RATIO:
            return None
        
        severity = self._calculate_file_severity(file_churn)
        if severity == Severity.INFO:
            return None
        
        # Adjust severity based on complexity
        if complexity >= 15:
            # Bump up severity for complex functions in churning files
            if severity == Severity.MEDIUM:
                severity = Severity.HIGH
            elif severity == Severity.LOW:
                severity = Severity.MEDIUM
        
        return Finding(
            id=str(uuid4()),
            detector="AIChurnDetector",
            severity=severity,
            title=f"Complex function `{function_name}` in high-churn file",
            description=(
                f"Function `{function_name}` in `{file_path}` is complex "
                f"(complexity: {complexity}) and located in a file with high early churn "
                f"(churn ratio: {file_churn.churn_ratio:.2f}).\n\n"
                "Complex functions in rapidly-revised files are more likely to contain "
                "subtle bugs or incomplete logic."
            ),
            affected_nodes=[qualified_name] if qualified_name else [file_path],
            affected_files=[file_path],
            line_start=line_start,
            graph_context={
                "complexity": complexity,
                "file_churn_ratio": file_churn.churn_ratio,
                "modifications_48h": file_churn.modification_count_first_48h,
            },
            suggested_fix=(
                "Prioritize review and testing of this function. Consider:\n"
                "1. Adding unit tests with edge cases\n"
                "2. Breaking into smaller functions if possible\n"
                "3. Adding detailed comments explaining the logic"
            ),
            estimated_effort="Small (2-4 hours)",
            collaboration_metadata=[
                CollaborationMetadata(
                    detector="AIChurnDetector",
                    confidence=0.7,
                    evidence=[
                        f"complexity={complexity}",
                        f"file_churn_ratio={file_churn.churn_ratio:.2f}",
                    ],
                    tags=["ai-churn", "complex-function", "review-priority"],
                )
            ],
        )
    
    def severity(self, finding: Finding) -> Severity:
        """Get severity for a finding.
        
        Args:
            finding: Finding to assess
            
        Returns:
            Severity level from the finding
        """
        return finding.severity
