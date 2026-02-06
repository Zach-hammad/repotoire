"""AI Churn Pattern Detector.

Detects code with high modification frequency shortly after creation - a pattern
commonly seen with AI-generated code that gets quickly revised or corrected.

The detector uses git blame + diff to analyze function-level changes and identify:
- Functions created and modified within 48 hours ("fix velocity")
- High churn ratio (lines_modified / lines_original) in first week
- Rapid iterative corrections typical of AI-generated code

Research insight: AI-generated code often gets corrected quickly after generation,
exhibiting a distinctive "generate-then-fix" pattern.
"""

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
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
class FunctionChurnRecord:
    """Track churn statistics for a function."""
    qualified_name: str
    file_path: str
    function_name: str
    
    # Creation info
    created_at: Optional[datetime] = None
    creation_commit: str = ""
    lines_original: int = 0
    
    # Modification tracking
    first_modification_at: Optional[datetime] = None
    first_modification_commit: str = ""
    modifications: List[Tuple[datetime, str, int, int]] = field(default_factory=list)  # (time, sha, +lines, -lines)
    
    # Computed metrics
    @property
    def time_to_first_fix(self) -> Optional[timedelta]:
        """Time between creation and first modification."""
        if self.created_at and self.first_modification_at:
            return self.first_modification_at - self.created_at
        return None
    
    @property
    def time_to_first_fix_hours(self) -> Optional[float]:
        """Time to first fix in hours."""
        ttf = self.time_to_first_fix
        if ttf:
            return ttf.total_seconds() / 3600
        return None
    
    @property
    def modifications_first_week(self) -> int:
        """Count modifications within first week of creation."""
        if not self.created_at:
            return 0
        week_cutoff = self.created_at + timedelta(days=7)
        return sum(1 for mod_time, _, _, _ in self.modifications if mod_time <= week_cutoff)
    
    @property
    def lines_changed_first_week(self) -> int:
        """Total lines changed (added + deleted) in first week."""
        if not self.created_at:
            return 0
        week_cutoff = self.created_at + timedelta(days=7)
        return sum(
            added + deleted 
            for mod_time, _, added, deleted in self.modifications 
            if mod_time <= week_cutoff
        )
    
    @property
    def churn_ratio(self) -> float:
        """Ratio of lines changed to original lines in first week."""
        if self.lines_original == 0:
            return 0.0
        return self.lines_changed_first_week / self.lines_original
    
    @property
    def is_high_velocity_fix(self) -> bool:
        """Key signal: fixed within 48h AND multiple modifications."""
        ttf_hours = self.time_to_first_fix_hours
        if ttf_hours is None:
            return False
        return ttf_hours < 48 and len(self.modifications) >= 2
    
    @property
    def ai_churn_score(self) -> float:
        """Combined score indicating AI churn pattern (0-1)."""
        score = 0.0
        
        # Fast fix velocity is strong signal
        ttf_hours = self.time_to_first_fix_hours
        if ttf_hours is not None:
            if ttf_hours < 24:
                score += 0.4
            elif ttf_hours < 48:
                score += 0.25
            elif ttf_hours < 72:
                score += 0.1
        
        # Multiple early modifications
        mods = len(self.modifications)
        if mods >= 4:
            score += 0.3
        elif mods >= 2:
            score += 0.2
        elif mods >= 1:
            score += 0.1
        
        # High churn ratio
        if self.churn_ratio > 1.0:
            score += 0.3
        elif self.churn_ratio > 0.5:
            score += 0.2
        elif self.churn_ratio > 0.3:
            score += 0.1
        
        return min(score, 1.0)


class AIChurnDetector(CodeSmellDetector):
    """Detects AI-generated code patterns through fix velocity and churn analysis.
    
    AI-generated code often exhibits a distinctive "generate-then-fix" pattern:
    1. Large initial commits with generated code
    2. Quick follow-up fixes within 24-48 hours (high fix velocity)
    3. Multiple modifications as humans correct AI mistakes
    
    Key detection signals:
    - **Fix velocity**: time_to_first_fix < 48 hours with 2+ modifications → HIGH signal
    - **Churn ratio**: lines_changed / lines_original > 0.5 in first week → significant rewrite
    
    Severity levels:
    - CRITICAL: Fix within 24h with 4+ modifications, or churn ratio > 1.0
    - HIGH: Fix within 48h with 2+ modifications, or churn ratio > 0.5
    - MEDIUM: Fix within 72h, or churn ratio > 0.3
    - LOW: Early signs of churn pattern
    """
    
    # Time thresholds (hours)
    CRITICAL_FIX_VELOCITY_HOURS = 24
    HIGH_FIX_VELOCITY_HOURS = 48
    MEDIUM_FIX_VELOCITY_HOURS = 72
    
    # Modification count thresholds
    CRITICAL_MOD_COUNT = 4
    HIGH_MOD_COUNT = 2
    
    # Churn ratio thresholds
    CRITICAL_CHURN_RATIO = 1.0  # More changed than originally written
    HIGH_CHURN_RATIO = 0.5
    MEDIUM_CHURN_RATIO = 0.3
    
    # Analysis window (how far back to look)
    ANALYSIS_WINDOW_DAYS = 90
    
    # Minimum function size to analyze
    MIN_FUNCTION_LINES = 5
    
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
        """
        super().__init__(graph_client, detector_config)
        self.repo_path = self._find_git_root(self.config.get("repo_path"))
        self.analysis_window_days = self.config.get("analysis_window_days", self.ANALYSIS_WINDOW_DAYS)
        self._git_repo: Optional["git.Repo"] = None
    
    def _find_git_root(self, start_path: Optional[str]) -> Optional[str]:
        """Find the git repository root by looking for .git directory.
        
        Args:
            start_path: Path to start searching from. If None, uses current directory.
            
        Returns:
            Path to git root, or None if not found.
        """
        if start_path:
            search_path = Path(start_path).resolve()
        else:
            search_path = Path.cwd()
        
        # Check the path itself and all parents
        for path in [search_path, *search_path.parents]:
            git_dir = path / ".git"
            if git_dir.exists():
                logger.debug(f"Found git repository at {path}")
                return str(path)
        
        logger.debug(f"No git repository found starting from {search_path}")
        return None
    
    @property
    def git_repo(self) -> Optional["git.Repo"]:
        """Lazy-load git repository."""
        if not GIT_AVAILABLE:
            return None
        if self._git_repo is None:
            if self.repo_path:
                try:
                    self._git_repo = git.Repo(self.repo_path)
                except Exception as e:
                    logger.warning(f"Failed to open git repository at {self.repo_path}: {e}")
            else:
                # Try current directory as fallback
                try:
                    self._git_repo = git.Repo(Path.cwd(), search_parent_directories=True)
                    logger.debug(f"Found git repository via cwd: {self._git_repo.working_dir}")
                except Exception as e:
                    logger.debug(f"No git repository found from cwd: {e}")
        return self._git_repo
    
    def detect(self) -> List[Finding]:
        """Detect AI churn patterns using function-level git analysis.
        
        Returns:
            List of findings for high-churn functions
        """
        findings = []
        
        if not GIT_AVAILABLE:
            logger.warning("GitPython not available, skipping AI churn detection. Install with: pip install gitpython")
            return findings
        
        if not self.git_repo:
            search_path = self.config.get("repo_path") or Path.cwd()
            logger.info(
                f"No git repository found (searched from {search_path}). "
                "AI churn detection requires git history. Returning empty findings."
            )
            return findings
        
        try:
            # Analyze function-level churn from git history
            function_records = self._analyze_function_churn()
            
            # Create findings for high-churn functions
            for qualified_name, record in function_records.items():
                finding = self._create_finding(record)
                if finding:
                    findings.append(finding)
            
            logger.info(f"AIChurnDetector found {len(findings)} high-churn patterns")
            return findings
            
        except Exception as e:
            logger.error(f"Error in AI churn detection: {e}", exc_info=True)
            return findings
    
    def _analyze_function_churn(self) -> Dict[str, FunctionChurnRecord]:
        """Analyze git history for function-level churn patterns.
        
        Uses git log with patches to track:
        1. When functions were created
        2. When they were first modified
        3. How many times they were modified in the first week
        
        Returns:
            Dictionary mapping qualified names to churn records
        """
        records: Dict[str, FunctionChurnRecord] = {}
        
        if not self.git_repo:
            return records
        
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.analysis_window_days)
        
        try:
            # Get commits with patches to analyze function changes
            commits = list(self.git_repo.iter_commits(
                'HEAD',
                since=cutoff_date.isoformat()
            ))
            
            # Process from oldest to newest to track creation -> modification
            for commit in reversed(commits):
                commit_time = commit.committed_datetime
                if commit_time.tzinfo is None:
                    commit_time = commit_time.replace(tzinfo=timezone.utc)
                
                # Analyze each file changed in this commit
                if commit.parents:
                    diffs = commit.parents[0].diff(commit, create_patch=True)
                else:
                    diffs = commit.diff(git.NULL_TREE, create_patch=True)
                
                for diff in diffs:
                    file_path = diff.b_path or diff.a_path
                    if not file_path or not self._is_code_file(file_path):
                        continue
                    
                    # Parse diff to find function changes
                    self._process_diff(
                        diff,
                        file_path,
                        commit.hexsha,
                        commit_time,
                        records
                    )
            
        except Exception as e:
            logger.error(f"Failed to analyze git history: {e}", exc_info=True)
        
        return records
    
    def _process_diff(
        self,
        diff: "git.Diff",
        file_path: str,
        commit_sha: str,
        commit_time: datetime,
        records: Dict[str, FunctionChurnRecord]
    ) -> None:
        """Process a diff to extract function-level changes.
        
        Args:
            diff: Git diff object with patch
            file_path: Path to the file
            commit_sha: Commit SHA
            commit_time: Commit timestamp
            records: Dictionary to update with findings
        """
        if not diff.diff:
            return
        
        try:
            diff_text = diff.diff.decode("utf-8", errors="ignore")
        except Exception:
            return
        
        # Detect language and use appropriate patterns
        lang = self._detect_language(file_path)
        func_patterns = self._get_function_patterns(lang)
        
        # Track functions added in this diff
        added_functions: Dict[str, int] = {}  # name -> line count
        modified_functions: Set[str] = set()
        
        # Parse the diff hunks
        current_func: Optional[str] = None
        lines_in_func = 0
        in_addition = False
        
        for line in diff_text.split('\n'):
            # Check for function definition (new or modified)
            for pattern in func_patterns:
                match = re.search(pattern, line)
                if match:
                    func_name = match.group(1)
                    
                    if line.startswith('+') and not line.startswith('+++'):
                        # New function being added
                        current_func = func_name
                        in_addition = True
                        lines_in_func = 0
                        added_functions[func_name] = 0
                    elif line.startswith('-') and not line.startswith('---'):
                        # Function being removed/modified
                        modified_functions.add(func_name)
                    elif line.startswith(' ') or line.startswith('@'):
                        # Context line with function def - likely modification
                        modified_functions.add(func_name)
                    break
            
            # Count lines in current function being added
            if current_func and in_addition:
                if line.startswith('+') and not line.startswith('+++'):
                    lines_in_func += 1
                    added_functions[current_func] = lines_in_func
                elif line.startswith('-') or line.startswith(' '):
                    # Still in function, mixed changes
                    if current_func not in added_functions:
                        added_functions[current_func] = lines_in_func
                elif line.startswith('@@'):
                    # New hunk, might be leaving function
                    in_addition = False
                    current_func = None
        
        # Update records for added functions
        for func_name, line_count in added_functions.items():
            if line_count < self.MIN_FUNCTION_LINES:
                continue
            
            qualified_name = f"{file_path}::{func_name}"
            
            if qualified_name not in records:
                # New function creation
                records[qualified_name] = FunctionChurnRecord(
                    qualified_name=qualified_name,
                    file_path=file_path,
                    function_name=func_name,
                    created_at=commit_time,
                    creation_commit=commit_sha[:8],
                    lines_original=line_count,
                )
            else:
                # Function already exists, this is a modification
                record = records[qualified_name]
                if not record.first_modification_at:
                    record.first_modification_at = commit_time
                    record.first_modification_commit = commit_sha[:8]
                
                # Estimate lines changed (rough: added lines in this diff)
                record.modifications.append((commit_time, commit_sha[:8], line_count, 0))
        
        # Update records for modified functions (not new additions)
        for func_name in modified_functions - set(added_functions.keys()):
            # Try to find existing record for this function
            qualified_name = f"{file_path}::{func_name}"
            
            if qualified_name in records:
                record = records[qualified_name]
                if not record.first_modification_at:
                    record.first_modification_at = commit_time
                    record.first_modification_commit = commit_sha[:8]
                
                # Rough line change estimate from diff
                record.modifications.append((commit_time, commit_sha[:8], 1, 1))
    
    def _detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension."""
        ext = Path(file_path).suffix.lower()
        lang_map = {
            '.py': 'python',
            '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript',
            '.ts': 'typescript', '.tsx': 'typescript',
            '.java': 'java',
            '.go': 'go',
            '.rs': 'rust',
            '.rb': 'ruby',
            '.php': 'php',
            '.c': 'c', '.h': 'c',
            '.cpp': 'cpp', '.hpp': 'cpp', '.cc': 'cpp',
            '.cs': 'csharp',
            '.swift': 'swift',
            '.kt': 'kotlin',
        }
        return lang_map.get(ext, 'unknown')
    
    def _get_function_patterns(self, lang: str) -> List[str]:
        """Get regex patterns for function definitions by language."""
        patterns = {
            'python': [
                r'(?:async\s+)?def\s+(\w+)\s*\(',
            ],
            'javascript': [
                r'(?:async\s+)?function\s+(\w+)\s*\(',
                r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(',
                r'(\w+)\s*:\s*(?:async\s+)?function\s*\(',
                r'(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>',
            ],
            'typescript': [
                r'(?:async\s+)?function\s+(\w+)\s*[<(]',
                r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(',
                r'(\w+)\s*:\s*(?:async\s+)?function\s*\(',
                r'(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>',
            ],
            'java': [
                r'(?:public|private|protected)?\s*(?:static)?\s*\w+\s+(\w+)\s*\(',
            ],
            'go': [
                r'func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(',
            ],
            'rust': [
                r'(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]',
            ],
            'ruby': [
                r'def\s+(\w+)',
            ],
            'php': [
                r'(?:public|private|protected)?\s*(?:static)?\s*function\s+(\w+)\s*\(',
            ],
            'c': [
                r'\w+\s+(\w+)\s*\([^)]*\)\s*\{',
            ],
            'cpp': [
                r'\w+\s+(?:\w+::)?(\w+)\s*\([^)]*\)\s*(?:const)?\s*(?:override)?\s*\{',
            ],
            'csharp': [
                r'(?:public|private|protected)?\s*(?:static)?\s*\w+\s+(\w+)\s*\(',
            ],
            'swift': [
                r'func\s+(\w+)\s*[<(]',
            ],
            'kotlin': [
                r'fun\s+(\w+)\s*[<(]',
            ],
        }
        return patterns.get(lang, [r'(?:def|function|func|fn)\s+(\w+)'])
    
    def _is_code_file(self, file_path: str) -> bool:
        """Check if file is a code file we should analyze."""
        code_extensions = {
            '.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.go', '.rs',
            '.rb', '.php', '.c', '.cpp', '.h', '.hpp', '.cc', '.cs',
            '.swift', '.kt', '.scala', '.ex', '.exs'
        }
        return Path(file_path).suffix.lower() in code_extensions
    
    def _create_finding(self, record: FunctionChurnRecord) -> Optional[Finding]:
        """Create a finding for a high-churn function.
        
        Args:
            record: Function churn statistics
            
        Returns:
            Finding if churn pattern detected, None otherwise
        """
        # Calculate severity
        severity = self._calculate_severity(record)
        if severity == Severity.INFO:
            return None
        
        # Build description with key metrics
        ttf_hours = record.time_to_first_fix_hours
        ttf_str = f"{ttf_hours:.1f} hours" if ttf_hours else "N/A"
        
        description_parts = [
            f"Function `{record.function_name}` in `{record.file_path}` shows signs of rapid post-creation revision.",
            "",
            "**Fix Velocity Metrics:**",
            f"- Created: {record.created_at.strftime('%Y-%m-%d %H:%M') if record.created_at else 'Unknown'} (commit `{record.creation_commit}`)",
            f"- Time to first fix: **{ttf_str}**",
            f"- Total modifications in first week: **{record.modifications_first_week}**",
            "",
            "**Churn Analysis:**",
            f"- Original size: {record.lines_original} lines",
            f"- Lines changed in first week: {record.lines_changed_first_week}",
            f"- Churn ratio: **{record.churn_ratio:.2f}** ({record.churn_ratio * 100:.0f}% of original code)",
            f"- AI churn score: {record.ai_churn_score:.2f}",
        ]
        
        if record.is_high_velocity_fix:
            description_parts.append("")
            description_parts.append(
                "⚠️ **High fix velocity detected**: This function was modified within 48 hours of creation "
                "with multiple follow-up changes - a pattern strongly associated with AI-generated code "
                "that required human correction."
            )
        
        if record.churn_ratio > self.CRITICAL_CHURN_RATIO:
            description_parts.append("")
            description_parts.append(
                "⚠️ **Critical churn ratio**: More code was changed than originally written, "
                "indicating significant rewriting was needed."
            )
        
        # Modification timeline
        if record.modifications:
            description_parts.append("")
            description_parts.append("**Modification Timeline:**")
            for i, (mod_time, sha, added, _) in enumerate(record.modifications[:5]):
                time_str = mod_time.strftime('%Y-%m-%d %H:%M')
                description_parts.append(f"- {time_str}: commit `{sha}` (+{added} lines)")
            if len(record.modifications) > 5:
                description_parts.append(f"- ... and {len(record.modifications) - 5} more modifications")
        
        # Suggested fix based on severity
        if severity == Severity.CRITICAL:
            suggested_fix = (
                "This function shows strong signs of AI-generated code that required extensive correction. "
                "Consider:\n"
                "1. **Review thoroughly** for hidden bugs or incomplete logic\n"
                "2. **Add comprehensive tests** - the rapid changes suggest edge cases may be missed\n"
                "3. **Document the logic** - ensure the team understands what this code does\n"
                "4. **Consider rewriting** if the churn continues"
            )
        elif severity == Severity.HIGH:
            suggested_fix = (
                "Review this function for correctness issues. Consider:\n"
                "1. Adding unit tests with edge cases\n"
                "2. Reviewing for logical errors\n"
                "3. Ensuring proper error handling"
            )
        else:
            suggested_fix = (
                "Monitor this function for continued churn. Consider adding tests "
                "to stabilize the implementation."
            )
        
        return Finding(
            id=str(uuid4()),
            detector="AIChurnDetector",
            severity=severity,
            title=f"AI churn pattern in `{record.function_name}`",
            description="\n".join(description_parts),
            affected_nodes=[record.qualified_name],
            affected_files=[record.file_path],
            line_start=None,  # Could extract from git blame if needed
            graph_context={
                "time_to_first_fix_hours": ttf_hours,
                "modifications_first_week": record.modifications_first_week,
                "churn_ratio": record.churn_ratio,
                "lines_original": record.lines_original,
                "lines_changed_first_week": record.lines_changed_first_week,
                "is_high_velocity_fix": record.is_high_velocity_fix,
                "ai_churn_score": record.ai_churn_score,
                "creation_commit": record.creation_commit,
                "first_fix_commit": record.first_modification_commit,
            },
            suggested_fix=suggested_fix,
            estimated_effort="Small (2-4 hours)" if severity in (Severity.LOW, Severity.MEDIUM) else "Medium (1-2 days)",
            collaboration_metadata=[
                CollaborationMetadata(
                    detector="AIChurnDetector",
                    confidence=min(0.5 + record.ai_churn_score * 0.5, 0.95),
                    evidence=[
                        f"ttf={ttf_str}",
                        f"mods={record.modifications_first_week}",
                        f"churn={record.churn_ratio:.2f}",
                    ],
                    tags=["ai-churn", "fix-velocity", "rapid-revision"],
                )
            ],
            why_it_matters=(
                "Code that requires rapid fixing after creation often indicates AI-generated content "
                "that wasn't fully understood or tested before commit. This pattern is associated with "
                "hidden bugs, incomplete error handling, and logic that may not be fully correct."
            ),
        )
    
    def _calculate_severity(self, record: FunctionChurnRecord) -> Severity:
        """Calculate severity based on fix velocity and churn metrics.
        
        Key signal: time_to_first_fix < 48h AND modifications >= 2 → HIGH
        
        Args:
            record: Function churn statistics
            
        Returns:
            Severity level
        """
        ttf_hours = record.time_to_first_fix_hours
        mods = len(record.modifications)
        churn = record.churn_ratio
        
        # CRITICAL conditions
        if churn > self.CRITICAL_CHURN_RATIO:
            return Severity.CRITICAL
        if ttf_hours is not None and ttf_hours < self.CRITICAL_FIX_VELOCITY_HOURS and mods >= self.CRITICAL_MOD_COUNT:
            return Severity.CRITICAL
        
        # HIGH conditions (key signal)
        if ttf_hours is not None and ttf_hours < self.HIGH_FIX_VELOCITY_HOURS and mods >= self.HIGH_MOD_COUNT:
            return Severity.HIGH
        if churn > self.HIGH_CHURN_RATIO:
            return Severity.HIGH
        
        # MEDIUM conditions
        if ttf_hours is not None and ttf_hours < self.MEDIUM_FIX_VELOCITY_HOURS:
            return Severity.MEDIUM
        if churn > self.MEDIUM_CHURN_RATIO:
            return Severity.MEDIUM
        if mods >= 2:
            return Severity.MEDIUM
        
        # LOW - some signal but not strong
        if mods >= 1:
            return Severity.LOW
        
        return Severity.INFO
    
    def severity(self, finding: Finding) -> Severity:
        """Get severity for a finding.
        
        Args:
            finding: Finding to assess
            
        Returns:
            Severity level from the finding
        """
        return finding.severity
