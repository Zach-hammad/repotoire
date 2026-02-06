"""AI naming pattern detector.

Detects AI-typical generic variable naming patterns in code.
Based on research showing AI uses generic variable names much more than humans.

AI-generated code tends to use:
- Single letters: i, j, k, x, y, n, m (outside of loop/math contexts)
- Generic words: result, temp, data, value, item, obj, res, ret, tmp, val
- Numbered generics: var1, temp2, data3

Human-written code tends to use:
- Domain-specific names: user, order, payment, customer
- Action-specific names: validated_email, parsed_response
- Type-hinted names: user_list, config_dict
"""

import ast
import re
import uuid
from dataclasses import dataclass
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


# Generic variable name patterns (AI-typical)
SINGLE_LETTER_GENERICS = frozenset({"i", "j", "k", "x", "y", "n", "m", "a", "b", "c", "d", "e", "f", "g", "h", "l", "o", "p", "q", "r", "s", "t", "u", "v", "w", "z"})

GENERIC_WORDS = frozenset({
    "result", "results", "res", "ret", "retval", "return_value",
    "temp", "tmp", "temporary",
    "data", "dat",
    "value", "val", "values", "vals",
    "item", "items", "elem", "element", "elements",
    "obj", "object", "objects",
    "output", "out",
    "input", "inp",
    "response", "resp", "req", "request",
    "var", "variable",
    "arg", "args", "argument", "arguments",
    "param", "params", "parameter", "parameters",
    "info", "stuff", "thing", "things",
    "content", "contents",
    "entry", "entries",
    "record", "records",
    "node", "nodes",  # Unless in graph context
    "current", "curr",
    "new", "old",
    "first", "last",
    "prev", "next",
    "left", "right",
    "count", "cnt",
    "num", "number",
    "idx", "index",
    "key", "keys",
    "flag", "flags",
    "status",
    "state",
    "type", "kind",
    "name",  # Too generic when used alone
    "id",    # Too generic when used alone
    "str", "string", "text",
    "list", "lst", "array", "arr",
    "dict", "dictionary", "map", "mapping",
    "set", "sets",
    "tuple", "tup",
    "func", "function", "fn",
    "callback", "cb",
    "handler",
    "wrapper",
    "helper",
    "util", "utils", "utility",
})

# Pattern for numbered generics like var1, temp2, data3
NUMBERED_GENERIC_PATTERN = re.compile(r"^(" + "|".join(GENERIC_WORDS) + r")\d+$", re.IGNORECASE)

# Also match single letters followed by numbers like x1, y2
SINGLE_LETTER_NUMBERED_PATTERN = re.compile(r"^[a-z]\d+$", re.IGNORECASE)

# Acceptable single-letter names in specific contexts
LOOP_CONTEXT_NAMES = frozenset({"i", "j", "k", "idx"})
MATH_CONTEXT_NAMES = frozenset({"x", "y", "z", "n", "m", "a", "b", "c", "r", "t"})
LAMBDA_CONTEXT_NAMES = frozenset({"x", "y", "z", "a", "b", "c", "f", "g", "_"})

# Names to always ignore (builtins, conventions)
IGNORED_NAMES = frozenset({
    "self", "cls", "_", "__",
    "True", "False", "None",
    "Exception", "Error",
})


@dataclass
class FunctionNamingAnalysis:
    """Represents naming analysis for a single function."""

    file_path: str
    function_name: str
    qualified_name: str
    total_identifiers: int
    generic_count: int
    generic_ratio: float
    generic_identifiers: List[str]
    line_number: int
    commit_sha: Optional[str] = None
    commit_date: Optional[datetime] = None
    author: Optional[str] = None


class IdentifierExtractor(ast.NodeVisitor):
    """AST visitor to extract all identifiers from a function."""

    def __init__(self):
        self.identifiers: List[str] = []
        self.loop_variables: Set[str] = set()
        self.math_context_depth: int = 0
        self.lambda_depth: int = 0
        self.comprehension_variables: Set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit function definition - extract parameter names."""
        for arg in node.args.args:
            self.identifiers.append(arg.arg)
        for arg in node.args.posonlyargs:
            self.identifiers.append(arg.arg)
        for arg in node.args.kwonlyargs:
            self.identifiers.append(arg.arg)
        if node.args.vararg:
            self.identifiers.append(node.args.vararg.arg)
        if node.args.kwarg:
            self.identifiers.append(node.args.kwarg.arg)
        
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Visit async function definition - extract parameter names."""
        self.visit_FunctionDef(node)  # type: ignore

    def visit_For(self, node: ast.For) -> None:
        """Visit for loop - track loop variables."""
        loop_vars = self._extract_target_names(node.target)
        self.loop_variables.update(loop_vars)
        self.identifiers.extend(loop_vars)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        """Visit async for loop."""
        self.visit_For(node)  # type: ignore

    def visit_comprehension(self, node: ast.comprehension) -> None:
        """Visit comprehension - track comprehension variables."""
        comp_vars = self._extract_target_names(node.target)
        self.comprehension_variables.update(comp_vars)
        self.identifiers.extend(comp_vars)
        
        # Visit iter and ifs
        self.visit(node.iter)
        for if_clause in node.ifs:
            self.visit(if_clause)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        """Visit list comprehension."""
        for generator in node.generators:
            self.visit_comprehension(generator)
        self.visit(node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        """Visit set comprehension."""
        for generator in node.generators:
            self.visit_comprehension(generator)
        self.visit(node.elt)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        """Visit generator expression."""
        for generator in node.generators:
            self.visit_comprehension(generator)
        self.visit(node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        """Visit dict comprehension."""
        for generator in node.generators:
            self.visit_comprehension(generator)
        self.visit(node.key)
        self.visit(node.value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Visit lambda - track lambda context."""
        self.lambda_depth += 1
        for arg in node.args.args:
            self.identifiers.append(arg.arg)
        self.generic_visit(node)
        self.lambda_depth -= 1

    def visit_Assign(self, node: ast.Assign) -> None:
        """Visit assignment - extract assigned names."""
        for target in node.targets:
            names = self._extract_target_names(target)
            self.identifiers.extend(names)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Visit annotated assignment."""
        if node.target:
            names = self._extract_target_names(node.target)
            self.identifiers.extend(names)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """Visit augmented assignment (+=, -=, etc)."""
        names = self._extract_target_names(node.target)
        self.identifiers.extend(names)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        """Visit walrus operator (:=)."""
        self.identifiers.append(node.target.id)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Visit exception handler."""
        if node.name:
            self.identifiers.append(node.name)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        """Visit with statement."""
        for item in node.items:
            if item.optional_vars:
                names = self._extract_target_names(item.optional_vars)
                self.identifiers.extend(names)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        """Visit async with statement."""
        self.visit_With(node)  # type: ignore

    def visit_Import(self, node: ast.Import) -> None:
        """Visit import - extract imported names."""
        for alias in node.names:
            name = alias.asname if alias.asname else alias.name.split(".")[0]
            self.identifiers.append(name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Visit from import - extract imported names."""
        for alias in node.names:
            if alias.name != "*":
                name = alias.asname if alias.asname else alias.name
                self.identifiers.append(name)

    def _extract_target_names(self, target: ast.AST) -> List[str]:
        """Extract names from assignment target (handles tuples, lists)."""
        names = []
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                names.extend(self._extract_target_names(elt))
        elif isinstance(target, ast.Starred):
            names.extend(self._extract_target_names(target.value))
        return names

    def is_loop_variable(self, name: str) -> bool:
        """Check if name is a loop variable."""
        return name in self.loop_variables or name in self.comprehension_variables

    def is_in_lambda(self) -> bool:
        """Check if currently inside a lambda."""
        return self.lambda_depth > 0


class AINamingPatternDetector(CodeSmellDetector):
    """Detects AI-typical generic variable naming patterns.

    This detector identifies functions where a high proportion of identifiers
    use generic, non-descriptive names which is a pattern commonly seen in
    AI-generated code.

    Configuration:
        repository_path: Path to repository root (required)
        generic_ratio_threshold: Ratio above which to flag (default: 0.4 = 40%)
        min_identifiers: Minimum identifiers needed for analysis (default: 5)
        window_days: Only analyze functions from last N days (default: 30)
        max_findings: Maximum findings to report (default: 50)
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict] = None,
    ):
        """Initialize AI naming pattern detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Configuration dictionary
        """
        super().__init__(graph_client, detector_config)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.generic_ratio_threshold = config.get("generic_ratio_threshold", 0.4)
        self.min_identifiers = config.get("min_identifiers", 5)
        self.window_days = config.get("window_days", 30)
        self.max_findings = config.get("max_findings", 50)

        if not self.repository_path.exists():
            raise ValueError(f"Repository path does not exist: {self.repository_path}")

    def detect(self) -> List[Finding]:
        """Run detection for AI naming patterns.

        Returns:
            List of findings for detected generic naming patterns
        """
        if not GIT_AVAILABLE:
            logger.warning("GitPython not available, skipping AI naming pattern detection")
            return []

        logger.info(f"Running AI naming pattern detection on {self.repository_path}")

        # Analyze recently added functions
        analyses = self._analyze_recent_functions()

        # Filter to those exceeding threshold
        flagged = [
            a for a in analyses
            if a.generic_ratio > self.generic_ratio_threshold
            and a.total_identifiers >= self.min_identifiers
        ]

        # Sort by generic ratio descending
        flagged.sort(key=lambda a: -a.generic_ratio)

        # Create findings
        findings = []
        for analysis in flagged[:self.max_findings]:
            finding = self._create_finding(analysis)
            if finding:
                findings.append(finding)

        logger.info(f"Found {len(findings)} AI naming pattern findings")
        return findings

    def _analyze_recent_functions(self) -> List[FunctionNamingAnalysis]:
        """Analyze functions added in the recent window.

        Returns:
            List of FunctionNamingAnalysis objects
        """
        try:
            repo = git.Repo(self.repository_path, search_parent_directories=True)
        except (git.exc.InvalidGitRepositoryError, git.exc.NoSuchPathError):
            logger.warning(f"Not a git repository: {self.repository_path}")
            return []

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.window_days)

        # Track functions we've already analyzed (file_path, function_name, line)
        analyzed: Set[Tuple[str, str, int]] = set()
        results: List[FunctionNamingAnalysis] = []

        # Get recent commits
        try:
            commits = list(repo.iter_commits("HEAD", max_count=500))
        except git.exc.GitCommandError:
            logger.warning("Failed to iterate commits")
            return []

        for commit in commits:
            commit_date = commit.committed_datetime
            if commit_date.tzinfo is None:
                commit_date = commit_date.replace(tzinfo=timezone.utc)

            # Skip commits outside window
            if commit_date < cutoff_date:
                continue

            # Get changed Python files
            changed_files = self._get_changed_python_files(commit)

            for file_path in changed_files:
                try:
                    file_content = self._get_file_at_commit(repo, commit, file_path)
                    if file_content is None:
                        continue

                    # Parse and analyze functions
                    function_analyses = self._analyze_file_functions(
                        file_content, file_path, commit
                    )

                    for analysis in function_analyses:
                        key = (analysis.file_path, analysis.function_name, analysis.line_number)
                        if key not in analyzed:
                            analyzed.add(key)
                            results.append(analysis)

                except Exception as e:
                    logger.debug(f"Failed to analyze {file_path} at {commit.hexsha[:8]}: {e}")
                    continue

        return results

    def _get_changed_python_files(self, commit: "git.Commit") -> List[str]:
        """Get Python files changed in a commit."""
        changed = []

        if commit.parents:
            parent = commit.parents[0]
            try:
                diffs = parent.diff(commit)
                for diff in diffs:
                    path = diff.b_path or diff.a_path
                    if path and path.endswith(".py"):
                        changed.append(path)
            except Exception:
                pass
        else:
            try:
                for path in commit.stats.files.keys():
                    if path.endswith(".py"):
                        changed.append(path)
            except Exception:
                pass

        return changed

    def _get_file_at_commit(
        self, repo: "git.Repo", commit: "git.Commit", file_path: str
    ) -> Optional[str]:
        """Get file content at a specific commit."""
        try:
            blob = commit.tree / file_path
            return blob.data_stream.read().decode("utf-8", errors="replace")
        except (KeyError, AttributeError):
            return None

    def _analyze_file_functions(
        self, source_code: str, file_path: str, commit: "git.Commit"
    ) -> List[FunctionNamingAnalysis]:
        """Analyze all functions in a file for naming patterns.

        Args:
            source_code: Python source code
            file_path: Path to the file
            commit: Git commit

        Returns:
            List of FunctionNamingAnalysis objects
        """
        results = []

        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return results

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                analysis = self._analyze_function(node, file_path, commit)
                if analysis:
                    results.append(analysis)

        return results

    def _analyze_function(
        self,
        func_node: ast.FunctionDef,
        file_path: str,
        commit: "git.Commit",
    ) -> Optional[FunctionNamingAnalysis]:
        """Analyze a single function for naming patterns.

        Args:
            func_node: AST function node
            file_path: Path to the file
            commit: Git commit

        Returns:
            FunctionNamingAnalysis or None if insufficient data
        """
        # Extract all identifiers from function
        extractor = IdentifierExtractor()
        extractor.visit(func_node)

        # Filter ignored names and analyze
        identifiers = [
            name for name in extractor.identifiers
            if name not in IGNORED_NAMES
            and not name.startswith("__")
            and not name.startswith("_")  # Skip private names
        ]

        if len(identifiers) < self.min_identifiers:
            return None

        # Classify identifiers
        generic_identifiers = []

        for name in identifiers:
            if self._is_generic_name(name, extractor):
                generic_identifiers.append(name)

        # Calculate ratio
        total = len(identifiers)
        generic_count = len(generic_identifiers)
        generic_ratio = generic_count / total if total > 0 else 0.0

        # Build qualified name
        qualified_name = f"{file_path}::{func_node.name}"

        commit_date = commit.committed_datetime
        if commit_date.tzinfo is None:
            commit_date = commit_date.replace(tzinfo=timezone.utc)

        return FunctionNamingAnalysis(
            file_path=file_path,
            function_name=func_node.name,
            qualified_name=qualified_name,
            total_identifiers=total,
            generic_count=generic_count,
            generic_ratio=generic_ratio,
            generic_identifiers=list(set(generic_identifiers)),  # Unique names
            line_number=func_node.lineno,
            commit_sha=commit.hexsha,
            commit_date=commit_date,
            author=commit.author.name if commit.author else "Unknown",
        )

    def _is_generic_name(self, name: str, extractor: IdentifierExtractor) -> bool:
        """Determine if a name is generic (AI-typical).

        Args:
            name: Identifier name
            extractor: IdentifierExtractor with context info

        Returns:
            True if the name is considered generic
        """
        name_lower = name.lower()

        # Check single-letter names
        if len(name) == 1:
            # Allow in loop/comprehension context
            if name_lower in LOOP_CONTEXT_NAMES and extractor.is_loop_variable(name):
                return False
            # Allow in lambda context
            if name_lower in LAMBDA_CONTEXT_NAMES and extractor.is_in_lambda():
                return False
            # Otherwise flag single letters
            if name_lower in SINGLE_LETTER_GENERICS:
                return True

        # Check single letter + number (x1, y2, etc)
        if SINGLE_LETTER_NUMBERED_PATTERN.match(name):
            return True

        # Check generic words
        if name_lower in GENERIC_WORDS:
            return True

        # Check numbered generics (var1, temp2, data3)
        if NUMBERED_GENERIC_PATTERN.match(name_lower):
            return True

        return False

    def _create_finding(self, analysis: FunctionNamingAnalysis) -> Finding:
        """Create a Finding from a FunctionNamingAnalysis.

        Args:
            analysis: Function naming analysis

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())

        # Always LOW severity as specified
        severity = Severity.LOW

        # Calculate days ago for display
        days_ago = 0
        if analysis.commit_date:
            days_ago = (datetime.now(timezone.utc) - analysis.commit_date).days

        # Build percentage string
        ratio_pct = f"{analysis.generic_ratio * 100:.0f}%"

        description = self._build_description(analysis, days_ago)
        suggested_fix = self._build_suggested_fix(analysis)

        finding = Finding(
            id=finding_id,
            detector="AINamingPatternDetector",
            severity=severity,
            title=f"Generic naming pattern in '{analysis.function_name}' ({ratio_pct} generic)",
            description=description,
            affected_nodes=[analysis.qualified_name],
            affected_files=[analysis.file_path],
            graph_context={
                "function_name": analysis.function_name,
                "generic_ratio": analysis.generic_ratio,
                "generic_ratio_pct": ratio_pct,
                "total_identifiers": analysis.total_identifiers,
                "generic_count": analysis.generic_count,
                "generic_identifiers": analysis.generic_identifiers,
                "line_number": analysis.line_number,
                "commit_sha": analysis.commit_sha[:8] if analysis.commit_sha else None,
                "commit_date": analysis.commit_date.isoformat() if analysis.commit_date else None,
                "author": analysis.author,
                "days_ago": days_ago,
            },
            suggested_fix=suggested_fix,
            estimated_effort="Small (30 min - 1 hour)",
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        finding.add_collaboration_metadata(
            CollaborationMetadata(
                detector="AINamingPatternDetector",
                confidence=min(0.6 + (analysis.generic_ratio - 0.4) * 2, 0.95),
                evidence=[
                    "generic_naming_pattern",
                    f"ratio_{ratio_pct}",
                    f"generic_count_{analysis.generic_count}",
                    f"total_{analysis.total_identifiers}",
                ],
                tags=["ai-generated", "naming", "readability"],
            )
        )

        return finding

    def _build_description(self, analysis: FunctionNamingAnalysis, days_ago: int) -> str:
        """Build description for naming pattern finding."""
        ratio_pct = f"{analysis.generic_ratio * 100:.0f}%"

        desc = f"Function **{analysis.function_name}** uses a high proportion of generic variable names.\n\n"
        desc += "### Naming Analysis\n"
        desc += f"- **Generic ratio**: {ratio_pct} ({analysis.generic_count}/{analysis.total_identifiers} identifiers)\n"
        desc += f"- **Line**: {analysis.line_number}\n\n"

        desc += "### Generic Identifiers Found\n"
        generic_list = ", ".join(f"`{name}`" for name in sorted(analysis.generic_identifiers)[:15])
        if len(analysis.generic_identifiers) > 15:
            generic_list += f" ... and {len(analysis.generic_identifiers) - 15} more"
        desc += f"{generic_list}\n\n"

        if analysis.commit_sha:
            desc += "### Recent Change\n"
            desc += f"- **When**: {days_ago} days ago"
            if analysis.commit_date:
                desc += f" ({analysis.commit_date.strftime('%Y-%m-%d')})"
            desc += "\n"
            desc += f"- **Commit**: `{analysis.commit_sha[:8]}`\n"
            if analysis.author:
                desc += f"- **Author**: {analysis.author}\n"
            desc += "\n"

        desc += "### Why This Matters\n"
        desc += "High use of generic variable names suggests this code may be AI-generated:\n"
        desc += "- **Reduced readability**: Names like `data`, `result`, `temp` don't convey intent\n"
        desc += "- **Maintenance burden**: Future developers must read more context to understand purpose\n"
        desc += "- **Bug-prone**: Generic names make it easier to use the wrong variable\n"

        return desc

    def _build_suggested_fix(self, analysis: FunctionNamingAnalysis) -> str:
        """Build suggested fix for naming pattern finding."""
        suggestions = [
            "1. **Rename generic variables** to reflect their purpose:",
        ]

        # Provide specific rename suggestions for common generics
        rename_examples = {
            "data": "user_data, response_body, config_values",
            "result": "validated_user, parsed_response, calculation_total",
            "value": "input_amount, config_setting, threshold_value",
            "temp": "swap_holder, intermediate_result, cache_entry",
            "item": "user_record, order_item, menu_entry",
            "obj": "connection_pool, database_client, http_client",
            "res": "api_response, query_result, validation_outcome",
            "ret": "return_value → describe what's being returned",
        }

        for generic in analysis.generic_identifiers[:5]:
            if generic.lower() in rename_examples:
                suggestions.append(f"   - `{generic}` → e.g., {rename_examples[generic.lower()]}")

        suggestions.append("")
        suggestions.append("2. **Use domain-specific terminology** from your problem space")
        suggestions.append("3. **Add type hints** to clarify expected types")
        suggestions.append("4. **Consider the reader**: Would someone unfamiliar with this code understand the purpose?")

        return "\n".join(suggestions)

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a finding."""
        return finding.severity
