"""AI complexity spike detector (research-backed baseline comparison).

Detects sudden complexity increases in previously simple functions using
statistical outlier detection based on codebase-wide complexity baselines.

The research-backed approach:
1. Calculate cyclomatic complexity for ALL functions using radon
2. Compute codebase baseline: median and standard deviation
3. For functions modified in last 30 days, calculate z-scores
4. Flag functions where z_score > 2.0 (statistical outlier)
5. Cross-reference with git history to detect actual SPIKES
   (previous < 5 AND current > 15 → confirmed spike)

This approach avoids arbitrary thresholds by grounding detection in the
actual complexity distribution of the codebase.
"""

import statistics
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)

# Check for GitPython
try:
    import git
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    git = None  # type: ignore

# Check for radon
try:
    from radon.complexity import cc_visit
    RADON_AVAILABLE = True
except ImportError:
    RADON_AVAILABLE = False
    cc_visit = None  # type: ignore


@dataclass
class FunctionComplexity:
    """Complexity data for a single function."""
    
    file_path: str
    function_name: str
    qualified_name: str
    complexity: int
    line_number: int
    
    # Optional historical data
    previous_complexity: Optional[int] = None
    spike_commit_sha: Optional[str] = None
    spike_commit_message: Optional[str] = None
    spike_commit_author: Optional[str] = None
    spike_date: Optional[datetime] = None


@dataclass
class CodebaseBaseline:
    """Statistical baseline for codebase complexity."""
    
    total_functions: int
    median_complexity: float
    mean_complexity: float
    stddev_complexity: float
    min_complexity: int
    max_complexity: int
    p75_complexity: float  # 75th percentile
    p90_complexity: float  # 90th percentile
    
    def z_score(self, complexity: int) -> float:
        """Calculate z-score for a given complexity.
        
        Args:
            complexity: Function complexity value
            
        Returns:
            Number of standard deviations from median
        """
        if self.stddev_complexity == 0:
            return 0.0
        return (complexity - self.median_complexity) / self.stddev_complexity
    
    def is_outlier(self, complexity: int, threshold: float = 2.0) -> bool:
        """Check if complexity is a statistical outlier.
        
        Args:
            complexity: Function complexity value
            threshold: Z-score threshold (default 2.0 = ~95th percentile)
            
        Returns:
            True if complexity is an outlier
        """
        return self.z_score(complexity) > threshold


@dataclass
class ComplexitySpike:
    """Represents a detected complexity spike in a function."""
    
    file_path: str
    function_name: str
    qualified_name: str
    current_complexity: int
    previous_complexity: int
    complexity_delta: int
    z_score: float
    spike_date: datetime
    commit_sha: str
    commit_message: str
    author: str
    line_number: int
    baseline_median: float
    baseline_stddev: float


class AIComplexitySpikeDetector(CodeSmellDetector):
    """Detects complexity spikes using research-backed baseline comparison.
    
    This detector uses statistical outlier detection to identify functions
    with abnormally high complexity relative to the codebase baseline, then
    cross-references with git history to confirm actual complexity spikes.
    
    Algorithm:
    1. Scan all Python files and calculate cyclomatic complexity for every function
    2. Compute codebase baseline: median and standard deviation
    3. Query git for functions modified in the last N days
    4. For each recently modified function:
       - Calculate z_score = (complexity - median) / stddev
       - If z_score > 2.0 → potential outlier
    5. For outliers, check git history:
       - Get previous version of the function
       - If previous_complexity < 5 AND current_complexity > 15 → SPIKE confirmed
    
    Configuration:
        repository_path: Path to repository root (required)
        window_days: Only check functions modified within this window (default: 30)
        z_score_threshold: Z-score threshold for outlier detection (default: 2.0)
        spike_before_max: Max complexity to qualify as "previously simple" (default: 5)
        spike_after_min: Min complexity to qualify as spike (default: 15)
        max_findings: Maximum findings to report (default: 50)
        file_extensions: File extensions to analyze (default: [".py"])
    """
    
    # Default configuration
    DEFAULT_WINDOW_DAYS = 30
    DEFAULT_Z_SCORE_THRESHOLD = 2.0
    DEFAULT_SPIKE_BEFORE_MAX = 5
    DEFAULT_SPIKE_AFTER_MIN = 15
    DEFAULT_MAX_FINDINGS = 50
    DEFAULT_FILE_EXTENSIONS = [".py"]
    
    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
    ):
        """Initialize AI complexity spike detector.
        
        Args:
            graph_client: FalkorDB database client
            detector_config: Configuration dictionary
        """
        super().__init__(graph_client, detector_config)
        
        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.window_days = config.get("window_days", self.DEFAULT_WINDOW_DAYS)
        self.z_score_threshold = config.get("z_score_threshold", self.DEFAULT_Z_SCORE_THRESHOLD)
        self.spike_before_max = config.get("spike_before_max", self.DEFAULT_SPIKE_BEFORE_MAX)
        self.spike_after_min = config.get("spike_after_min", self.DEFAULT_SPIKE_AFTER_MIN)
        self.max_findings = config.get("max_findings", self.DEFAULT_MAX_FINDINGS)
        self.file_extensions = config.get("file_extensions", self.DEFAULT_FILE_EXTENSIONS)
        
        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")
    
    def detect(self) -> List[Finding]:
        """Run detection for AI complexity spikes.
        
        Returns:
            List of findings for detected complexity spikes
        """
        if not GIT_AVAILABLE:
            logger.warning("GitPython not available, skipping AI complexity spike detection")
            return []
        
        if not RADON_AVAILABLE:
            logger.warning("radon not available, skipping AI complexity spike detection")
            return []
        
        logger.info(f"Running AI complexity spike detection on {self.repository_path}")
        
        # Step 1: Calculate complexity for ALL functions in codebase
        all_complexities = self._scan_codebase_complexity()
        
        if not all_complexities:
            logger.warning("No functions found in codebase")
            return []
        
        # Step 2: Compute baseline statistics
        baseline = self._compute_baseline(all_complexities)
        logger.info(
            f"Baseline: median={baseline.median_complexity:.1f}, "
            f"stddev={baseline.stddev_complexity:.1f}, "
            f"total_functions={baseline.total_functions}"
        )
        
        # Step 3: Get recently modified functions
        recently_modified = self._get_recently_modified_functions()
        logger.info(f"Found {len(recently_modified)} recently modified files")
        
        # Step 4 & 5: Find outliers and confirm spikes
        spikes = self._find_complexity_spikes(all_complexities, baseline, recently_modified)
        logger.info(f"Detected {len(spikes)} complexity spikes")
        
        # Create findings
        findings = []
        for spike in spikes[:self.max_findings]:
            finding = self._create_finding(spike, baseline)
            if finding:
                findings.append(finding)
        
        logger.info(f"Created {len(findings)} AI complexity spike findings")
        return findings
    
    def _scan_codebase_complexity(self) -> Dict[str, FunctionComplexity]:
        """Scan entire codebase and calculate complexity for all functions.
        
        Returns:
            Dict mapping qualified_name to FunctionComplexity
        """
        complexities: Dict[str, FunctionComplexity] = {}
        
        for ext in self.file_extensions:
            for file_path in self.repository_path.rglob(f"*{ext}"):
                # Skip common non-source directories
                path_str = str(file_path)
                if any(skip in path_str for skip in [
                    "__pycache__", ".git", "node_modules", ".venv", 
                    "venv", ".tox", ".eggs", "dist", "build"
                ]):
                    continue
                
                try:
                    relative_path = file_path.relative_to(self.repository_path)
                    source_code = file_path.read_text(encoding="utf-8", errors="replace")
                    
                    for func_name, complexity, line_num in self._calculate_function_complexities(source_code):
                        qualified_name = f"{relative_path}::{func_name}"
                        complexities[qualified_name] = FunctionComplexity(
                            file_path=str(relative_path),
                            function_name=func_name,
                            qualified_name=qualified_name,
                            complexity=complexity,
                            line_number=line_num,
                        )
                        
                except Exception as e:
                    logger.debug(f"Failed to analyze {file_path}: {e}")
                    continue
        
        return complexities
    
    def _calculate_function_complexities(
        self, source_code: str
    ) -> List[Tuple[str, int, int]]:
        """Calculate cyclomatic complexity for all functions in source code.
        
        Args:
            source_code: Python source code
            
        Returns:
            List of (function_name, complexity, line_number) tuples
        """
        results = []
        
        try:
            blocks = cc_visit(source_code)
            for block in blocks:
                # radon returns Function, Method, and Class blocks
                if block.letter in ("F", "M"):
                    results.append((block.name, block.complexity, block.lineno))
                elif block.letter == "C":
                    # For classes, include methods with qualified names
                    for method in getattr(block, "methods", []):
                        full_name = f"{block.name}.{method.name}"
                        results.append((full_name, method.complexity, method.lineno))
        except SyntaxError:
            pass
        except Exception as e:
            logger.debug(f"Failed to calculate complexity: {e}")
        
        return results
    
    def _compute_baseline(self, complexities: Dict[str, FunctionComplexity]) -> CodebaseBaseline:
        """Compute statistical baseline from all function complexities.
        
        Args:
            complexities: Dict of all function complexities
            
        Returns:
            CodebaseBaseline with statistics
        """
        values = [fc.complexity for fc in complexities.values()]
        
        if not values:
            return CodebaseBaseline(
                total_functions=0,
                median_complexity=0,
                mean_complexity=0,
                stddev_complexity=1,  # Avoid division by zero
                min_complexity=0,
                max_complexity=0,
                p75_complexity=0,
                p90_complexity=0,
            )
        
        sorted_values = sorted(values)
        n = len(sorted_values)
        
        # Calculate percentiles
        p75_idx = int(n * 0.75)
        p90_idx = int(n * 0.90)
        
        return CodebaseBaseline(
            total_functions=n,
            median_complexity=statistics.median(values),
            mean_complexity=statistics.mean(values),
            stddev_complexity=statistics.stdev(values) if n > 1 else 1.0,
            min_complexity=min(values),
            max_complexity=max(values),
            p75_complexity=sorted_values[p75_idx] if p75_idx < n else sorted_values[-1],
            p90_complexity=sorted_values[p90_idx] if p90_idx < n else sorted_values[-1],
        )
    
    def _get_recently_modified_functions(self) -> Dict[str, Dict[str, Any]]:
        """Get functions modified in the last N days from git.
        
        Returns:
            Dict mapping file_path to commit info for recently modified files
        """
        recently_modified: Dict[str, Dict[str, Any]] = {}
        
        try:
            repo = git.Repo(self.repository_path, search_parent_directories=True)
        except (git.exc.InvalidGitRepositoryError, git.exc.NoSuchPathError):
            logger.warning(f"Not a git repository: {self.repository_path}")
            return recently_modified
        
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.window_days)
        
        try:
            # Get commits within window
            for commit in repo.iter_commits("HEAD", max_count=1000):
                commit_date = commit.committed_datetime
                if commit_date.tzinfo is None:
                    commit_date = commit_date.replace(tzinfo=timezone.utc)
                
                if commit_date < cutoff_date:
                    break
                
                # Get changed files
                if commit.parents:
                    diffs = commit.parents[0].diff(commit)
                    for diff in diffs:
                        path = diff.b_path or diff.a_path
                        if path and any(path.endswith(ext) for ext in self.file_extensions):
                            if path not in recently_modified:
                                recently_modified[path] = {
                                    "commit_sha": commit.hexsha,
                                    "commit_date": commit_date,
                                    "commit_message": commit.message.strip().split("\n")[0][:100],
                                    "author": commit.author.name if commit.author else "Unknown",
                                }
        except Exception as e:
            logger.warning(f"Failed to query git history: {e}")
        
        return recently_modified
    
    def _find_complexity_spikes(
        self,
        all_complexities: Dict[str, FunctionComplexity],
        baseline: CodebaseBaseline,
        recently_modified: Dict[str, Dict[str, Any]],
    ) -> List[ComplexitySpike]:
        """Find functions with complexity spikes using baseline comparison.
        
        Args:
            all_complexities: All function complexities in codebase
            baseline: Computed baseline statistics
            recently_modified: Recently modified files with commit info
            
        Returns:
            List of confirmed complexity spikes
        """
        spikes: List[ComplexitySpike] = []
        
        try:
            repo = git.Repo(self.repository_path, search_parent_directories=True)
        except Exception:
            return spikes
        
        # Filter to functions in recently modified files
        candidate_functions = [
            fc for fc in all_complexities.values()
            if fc.file_path in recently_modified
        ]
        
        logger.debug(f"Checking {len(candidate_functions)} functions in recently modified files")
        
        for func in candidate_functions:
            # Step 4: Calculate z-score
            z_score = baseline.z_score(func.complexity)
            
            # Only consider outliers
            if z_score <= self.z_score_threshold:
                continue
            
            # Step 5: Cross-reference with git history to confirm spike
            previous_complexity = self._get_previous_complexity(
                repo, func.file_path, func.function_name, recently_modified[func.file_path]
            )
            
            if previous_complexity is None:
                # New function, check if it's complex enough to flag
                if func.complexity >= self.spike_after_min:
                    commit_info = recently_modified[func.file_path]
                    spikes.append(ComplexitySpike(
                        file_path=func.file_path,
                        function_name=func.function_name,
                        qualified_name=func.qualified_name,
                        current_complexity=func.complexity,
                        previous_complexity=0,  # New function
                        complexity_delta=func.complexity,
                        z_score=z_score,
                        spike_date=commit_info["commit_date"],
                        commit_sha=commit_info["commit_sha"],
                        commit_message=commit_info["commit_message"],
                        author=commit_info["author"],
                        line_number=func.line_number,
                        baseline_median=baseline.median_complexity,
                        baseline_stddev=baseline.stddev_complexity,
                    ))
                continue
            
            # Confirm spike: previous < 5 AND current > 15
            is_spike = (
                previous_complexity <= self.spike_before_max
                and func.complexity >= self.spike_after_min
            )
            
            if is_spike:
                commit_info = recently_modified[func.file_path]
                spikes.append(ComplexitySpike(
                    file_path=func.file_path,
                    function_name=func.function_name,
                    qualified_name=func.qualified_name,
                    current_complexity=func.complexity,
                    previous_complexity=previous_complexity,
                    complexity_delta=func.complexity - previous_complexity,
                    z_score=z_score,
                    spike_date=commit_info["commit_date"],
                    commit_sha=commit_info["commit_sha"],
                    commit_message=commit_info["commit_message"],
                    author=commit_info["author"],
                    line_number=func.line_number,
                    baseline_median=baseline.median_complexity,
                    baseline_stddev=baseline.stddev_complexity,
                ))
        
        # Sort by z-score (highest outliers first), then by delta
        spikes.sort(key=lambda s: (-s.z_score, -s.complexity_delta))
        
        return spikes
    
    def _get_previous_complexity(
        self,
        repo: "git.Repo",
        file_path: str,
        function_name: str,
        commit_info: Dict[str, Any],
    ) -> Optional[int]:
        """Get the complexity of a function before the recent modification.
        
        Args:
            repo: Git repository object
            file_path: Path to the file
            function_name: Name of the function
            commit_info: Info about the modifying commit
            
        Returns:
            Previous complexity or None if function didn't exist
        """
        try:
            # Get the commit that modified this file
            commit = repo.commit(commit_info["commit_sha"])
            
            if not commit.parents:
                return None  # Initial commit
            
            parent = commit.parents[0]
            
            # Get file content at parent commit
            try:
                blob = parent.tree / file_path
                previous_content = blob.data_stream.read().decode("utf-8", errors="replace")
            except KeyError:
                return None  # File didn't exist
            
            # Find the function in previous content
            for func_name, complexity, _ in self._calculate_function_complexities(previous_content):
                if func_name == function_name:
                    return complexity
            
            return None  # Function didn't exist in previous version
            
        except Exception as e:
            logger.debug(f"Failed to get previous complexity for {function_name}: {e}")
            return None
    
    def _create_finding(self, spike: ComplexitySpike, baseline: CodebaseBaseline) -> Finding:
        """Create a Finding from a ComplexitySpike.
        
        Args:
            spike: Detected complexity spike
            baseline: Codebase baseline for context
            
        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())
        
        # Severity based on z-score and delta
        if spike.z_score >= 3.0 or spike.complexity_delta >= 20:
            severity = Severity.HIGH
        elif spike.z_score >= 2.5 or spike.complexity_delta >= 15:
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM
        
        # Days since spike
        days_ago = (datetime.now(timezone.utc) - spike.spike_date).days
        
        # Build title showing the spike
        if spike.previous_complexity > 0:
            title = f"Function {spike.function_name} jumped from complexity {spike.previous_complexity} to {spike.current_complexity} in commit {spike.commit_sha[:7]}"
        else:
            title = f"New function {spike.function_name} has outlier complexity {spike.current_complexity} (z-score: {spike.z_score:.1f})"
        
        description = self._build_description(spike, baseline, days_ago)
        suggested_fix = self._build_suggested_fix(spike)
        
        finding = Finding(
            id=finding_id,
            detector="AIComplexitySpikeDetector",
            severity=severity,
            title=title,
            description=description,
            affected_nodes=[spike.qualified_name],
            affected_files=[spike.file_path],
            line_start=spike.line_number,
            graph_context={
                "current_complexity": spike.current_complexity,
                "previous_complexity": spike.previous_complexity,
                "complexity_delta": spike.complexity_delta,
                "z_score": round(spike.z_score, 2),
                "baseline_median": round(spike.baseline_median, 1),
                "baseline_stddev": round(spike.baseline_stddev, 2),
                "spike_date": spike.spike_date.isoformat(),
                "commit_sha": spike.commit_sha[:8],
                "commit_message": spike.commit_message,
                "author": spike.author,
                "days_ago": days_ago,
            },
            suggested_fix=suggested_fix,
            estimated_effort=self._estimate_effort(spike),
            why_it_matters=(
                f"This function's complexity ({spike.current_complexity}) is {spike.z_score:.1f} "
                f"standard deviations above the codebase median ({spike.baseline_median:.1f}). "
                "Such sudden complexity spikes often indicate AI-generated code that needs "
                "refactoring, or features added without proper decomposition."
            ),
            created_at=datetime.now(),
        )
        
        # Add collaboration metadata
        confidence = min(0.7 + (spike.z_score - 2.0) * 0.1, 0.95)
        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="AIComplexitySpikeDetector",
                confidence=confidence,
                evidence=[
                    f"z_score_{spike.z_score:.2f}",
                    f"previous_{spike.previous_complexity}",
                    f"current_{spike.current_complexity}",
                    f"delta_{spike.complexity_delta}",
                    "baseline_comparison",
                ],
                tags=["ai-generated", "complexity-spike", "statistical-outlier", "refactoring-needed"],
            )
        )
        
        return finding
    
    def _build_description(
        self, spike: ComplexitySpike, baseline: CodebaseBaseline, days_ago: int
    ) -> str:
        """Build description for complexity spike finding."""
        desc_parts = [
            f"Function **{spike.function_name}** experienced a significant complexity spike.\n",
            "### Complexity Analysis (Baseline Comparison)\n",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Previous complexity | {spike.previous_complexity} |",
            f"| Current complexity | {spike.current_complexity} |",
            f"| Delta | +{spike.complexity_delta} |",
            f"| Codebase median | {spike.baseline_median:.1f} |",
            f"| Codebase stddev | {spike.baseline_stddev:.1f} |",
            f"| **Z-score** | **{spike.z_score:.2f}** (>{self.z_score_threshold} = outlier) |",
            "",
            "### Commit Details\n",
            f"- **When**: {days_ago} days ago ({spike.spike_date.strftime('%Y-%m-%d')})",
            f"- **Commit**: `{spike.commit_sha[:8]}`",
            f"- **Message**: {spike.commit_message}",
            f"- **Author**: {spike.author}",
            f"- **Location**: `{spike.file_path}` line {spike.line_number}",
            "",
            "### Why This Matters\n",
            f"This function's complexity is {spike.z_score:.1f}σ above the codebase average. ",
            "Statistical outliers in complexity often indicate:",
            "- AI-generated code that was accepted without proper refactoring",
            "- Features added without decomposing into smaller functions",
            "- Technical debt that will compound over time",
            "- Reduced testability and higher bug risk",
        ]
        
        return "\n".join(desc_parts)
    
    def _build_suggested_fix(self, spike: ComplexitySpike) -> str:
        """Build suggested fix for complexity spike."""
        suggestions = [
            f"1. **Review commit `{spike.commit_sha[:8]}`** to understand what changed",
            "",
            "2. **Decompose the function** using these patterns:",
            "   - Extract Method: Move logical blocks into separate functions",
            "   - Replace Conditional with Polymorphism (for branching logic)",
            "   - Introduce Parameter Object (for many parameters)",
            "",
            f"3. **Target complexity**: Reduce from {spike.current_complexity} to below {int(spike.baseline_median + spike.baseline_stddev):.0f} (1σ above median)",
            "",
            "4. **Add tests** before refactoring to catch regressions",
        ]
        
        return "\n".join(suggestions)
    
    def _estimate_effort(self, spike: ComplexitySpike) -> str:
        """Estimate effort to fix complexity spike."""
        if spike.current_complexity < 20:
            return "Small (1-2 hours)"
        elif spike.current_complexity < 30:
            return "Medium (half day)"
        elif spike.current_complexity < 50:
            return "Large (1 day)"
        else:
            return "Extra Large (2+ days)"
    
    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a finding."""
        return finding.severity
