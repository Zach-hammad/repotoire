"""Fix application with git integration.

This module provides the FixApplicator class for applying auto-fix proposals
to codebases with git integration and secure test execution.

Security:
    By default, tests are run in isolated E2B sandboxes to prevent malicious
    auto-fix code from accessing host resources. Use --local-tests flag only
    for trusted code in development environments.
"""

import asyncio
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import git

from repotoire.autofix.models import FixProposal, FixStatus, CodeChange
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Try to import Rust-accelerated functions
try:
    from repotoire_fast import (
        apply_changes_parallel as _rust_apply_changes,
        fuzzy_find_in_file as _rust_fuzzy_find,
        batch_verify_originals as _rust_batch_verify,
        code_similarity as _rust_code_similarity,
        CodeChange as RustCodeChange,
    )
    HAS_RUST_APPLICATOR = True
    logger.debug("Rust fix applicator available")
except ImportError:
    HAS_RUST_APPLICATOR = False
    logger.debug("Rust fix applicator not available, using Python fallback")


# =============================================================================
# Test Result Model (for backward compatibility)
# =============================================================================


@dataclass
class TestResult:
    """Result of test execution (legacy compatibility wrapper).

    For full functionality, use repotoire.sandbox.test_executor.TestResult.
    """

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int = 0
    sandbox_id: Optional[str] = None
    timed_out: bool = False


class FixApplicator:
    """Apply approved fixes to codebase with git integration.

    Security:
        Test execution defaults to isolated E2B sandboxes. Set use_sandbox=False
        only for trusted code in development environments. Local execution will
        log a security warning.
    """

    def __init__(
        self,
        repository_path: Path,
        create_branch: bool = True,
        use_sandbox: bool = True,
        test_timeout: int = 300,
        test_env_vars: Optional[Dict[str, str]] = None,
    ):
        """Initialize fix applicator.

        Args:
            repository_path: Path to git repository
            create_branch: Whether to create git branch for fixes
            use_sandbox: Run tests in E2B sandbox (default: True for security)
            test_timeout: Test execution timeout in seconds (default: 300)
            test_env_vars: Environment variables to inject into sandbox tests
        """
        self.repository_path = Path(repository_path)
        self.create_branch = create_branch
        self.use_sandbox = use_sandbox
        self.test_timeout = test_timeout
        self.test_env_vars = test_env_vars or {}

        # Thread-safe locks for file I/O and git operations
        self._file_locks: Dict[Path, threading.Lock] = {}
        self._file_locks_lock = threading.Lock()
        self._git_lock = threading.Lock()

        # Initialize git repo
        try:
            self.repo = git.Repo(repository_path)
        except git.exc.InvalidGitRepositoryError:
            logger.warning(f"{repository_path} is not a git repository")
            self.repo = None

    def apply_fix(
        self, fix: FixProposal, commit: bool = True
    ) -> Tuple[bool, Optional[str]]:
        """Apply a single fix to the codebase.

        Args:
            fix: Fix proposal to apply
            commit: Whether to create git commit

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Create branch if requested
            if self.create_branch and self.repo:
                self._create_branch(fix)

            # Apply each code change
            for change in fix.changes:
                success, error = self._apply_change(change)
                if not success:
                    fix.status = FixStatus.FAILED
                    return False, error

            # Create commit if requested
            if commit and self.repo:
                self._create_commit(fix)

            # Mark as applied
            fix.status = FixStatus.APPLIED
            fix.applied_at = datetime.utcnow()

            logger.info(f"Successfully applied fix {fix.id}")
            return True, None

        except Exception as e:
            error_msg = f"Failed to apply fix: {str(e)}"
            logger.error(error_msg, exc_info=True)
            fix.status = FixStatus.FAILED
            return False, error_msg

    def apply_batch(
        self, fixes: List[FixProposal], commit_each: bool = False
    ) -> Tuple[List[FixProposal], List[Tuple[FixProposal, str]]]:
        """Apply multiple fixes.

        Args:
            fixes: List of approved fixes
            commit_each: Create commit for each fix (vs one commit for all)

        Returns:
            Tuple of (successful_fixes, failed_fixes_with_errors)
        """
        successful = []
        failed = []

        for fix in fixes:
            if fix.status != FixStatus.APPROVED:
                continue

            success, error = self.apply_fix(fix, commit=commit_each)

            if success:
                successful.append(fix)
            else:
                failed.append((fix, error))

        # Create single commit for all if not committing individually
        if successful and not commit_each and self.repo:
            self._create_batch_commit(successful)

        return successful, failed

    def apply_batch_parallel(
        self, fixes: List[FixProposal], commit: bool = True, threshold: float = 0.85
    ) -> Tuple[List[FixProposal], List[Tuple[FixProposal, str]]]:
        """Apply multiple fixes in parallel using Rust.

        This is significantly faster than apply_batch() for large batches
        because it uses Rust + rayon for parallel file I/O and fuzzy matching.

        Args:
            fixes: List of approved fixes
            commit: Create a single commit for all fixes
            threshold: Fuzzy matching threshold (0.85 = 85% similarity)

        Returns:
            Tuple of (successful_fixes, failed_fixes_with_errors)
        """
        if not HAS_RUST_APPLICATOR:
            logger.warning("Rust applicator not available, falling back to sequential apply")
            return self.apply_batch(fixes, commit_each=False)

        # Filter to approved fixes
        approved_fixes = [f for f in fixes if f.status == FixStatus.APPROVED]
        if not approved_fixes:
            return [], []

        # Collect all changes with fix references
        all_changes = []
        change_to_fix: Dict[int, FixProposal] = {}
        
        for fix in approved_fixes:
            for change in fix.changes:
                idx = len(all_changes)
                all_changes.append(
                    RustCodeChange(
                        file_path=str(change.file_path),
                        original_code=change.original_code,
                        fixed_code=change.fixed_code,
                    )
                )
                change_to_fix[idx] = fix

        if not all_changes:
            return [], []

        logger.info(f"Applying {len(all_changes)} changes across {len(approved_fixes)} fixes (parallel)")

        # Apply all changes in parallel via Rust
        results = _rust_apply_changes(
            str(self.repository_path),
            all_changes,
            threshold,
        )

        # Process results
        successful_fixes = set()
        failed_fixes: Dict[str, Tuple[FixProposal, str]] = {}

        for idx, result in enumerate(results):
            fix = change_to_fix.get(idx)
            if not fix:
                continue

            if result.success:
                successful_fixes.add(fix.id)
                if result.similarity < 1.0:
                    logger.info(
                        f"Applied change to {result.file_path} "
                        f"(fuzzy match: {result.similarity:.0%})"
                    )
            else:
                error = result.error or "Unknown error"
                failed_fixes[fix.id] = (fix, f"{result.file_path}: {error}")
                fix.status = FixStatus.FAILED

        # Update successful fix statuses
        successful = []
        for fix in approved_fixes:
            if fix.id in successful_fixes and fix.id not in failed_fixes:
                fix.status = FixStatus.APPLIED
                fix.applied_at = datetime.utcnow()
                successful.append(fix)

        # Create commit for successful fixes
        if successful and commit and self.repo:
            self._create_batch_commit(successful)

        failed = list(failed_fixes.values())
        logger.info(f"Applied {len(successful)} fixes, {len(failed)} failed")
        
        return successful, failed

    def _get_file_lock(self, file_path: Path) -> threading.Lock:
        """Get or create a lock for a specific file.

        Thread-safe: Uses double-checked locking to avoid race conditions.

        Args:
            file_path: Path to the file

        Returns:
            Lock for the file
        """
        # Normalize path for consistent locking
        normalized = file_path.resolve()
        # Fast path: lock exists
        if normalized in self._file_locks:
            return self._file_locks[normalized]
        # Slow path: create lock with protection
        with self._file_locks_lock:
            if normalized not in self._file_locks:
                self._file_locks[normalized] = threading.Lock()
            return self._file_locks[normalized]

    def _apply_change(self, change: CodeChange) -> Tuple[bool, Optional[str]]:
        """Apply a single code change to a file.

        Thread-safe: Uses per-file locking to prevent concurrent modifications.

        Args:
            change: Code change to apply

        Returns:
            Tuple of (success, error_message)
        """
        file_path = self.repository_path / change.file_path

        # Get per-file lock for thread-safe file I/O
        file_lock = self._get_file_lock(file_path)

        try:
            with file_lock:
                # Read current file content
                if not file_path.exists():
                    return False, f"File not found: {file_path}"

                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Verify original code exists (with fuzzy matching for line drift)
                original = change.original_code.strip()
                if original not in content:
                    # Try fuzzy matching to handle line drift
                    match_result = self._fuzzy_find_code(content, original)
                    if match_result:
                        matched_code, similarity = match_result
                        logger.info(f"Fuzzy matched code ({similarity:.0%} similar) despite line drift")
                        original = matched_code
                    else:
                        return (
                            False,
                            f"Original code not found in {change.file_path}. File may have changed.",
                        )

                # Apply the change
                new_content = content.replace(original, change.fixed_code.strip(), 1)

                # Write back to file
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(new_content)

            logger.debug(f"Applied change to {change.file_path}")
            return True, None

        except Exception as e:
            error_msg = f"Error applying change to {change.file_path}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return False, error_msg

    def _fuzzy_find_code(
        self, content: str, target: str, threshold: float = 0.85
    ) -> Optional[Tuple[str, float]]:
        """Find code in content using fuzzy matching to handle line drift.
        
        Uses Rust-accelerated LCS similarity when available, falling back
        to Python difflib for compatibility.
        
        Args:
            content: Full file content to search
            target: Code snippet to find
            threshold: Minimum similarity ratio (0-1) to accept match
            
        Returns:
            Tuple of (matched_code, similarity_ratio) or None if no match
        """
        # Use Rust implementation when available (faster LCS algorithm)
        if HAS_RUST_APPLICATOR:
            return self._fuzzy_find_code_rust(content, target, threshold)
        return self._fuzzy_find_code_python(content, target, threshold)

    def _fuzzy_find_code_rust(
        self, content: str, target: str, threshold: float = 0.85
    ) -> Optional[Tuple[str, float]]:
        """Rust-accelerated fuzzy code matching using LCS similarity."""
        target_lines = target.strip().splitlines()
        content_lines = content.splitlines()
        
        if not target_lines or not content_lines:
            return None
        
        target_len = len(target_lines)
        target_text = target.strip()
        best_match = None
        best_ratio = 0.0
        
        # Try exact window size and ±1,2 lines
        for delta in [0, 1, 2, -1]:
            adjusted_len = target_len + delta
            if adjusted_len < 1 or adjusted_len > len(content_lines):
                continue
            
            for i in range(len(content_lines) - adjusted_len + 1):
                window = content_lines[i:i + adjusted_len]
                window_text = "\n".join(window)
                
                # Use Rust LCS-based similarity
                ratio = _rust_code_similarity(target_text, window_text.strip())
                
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = window_text
                    
                    # Early exit on very high match
                    if ratio > 0.98:
                        return (best_match, best_ratio)
        
        if best_ratio >= threshold and best_match:
            return (best_match, best_ratio)
        
        return None
    
    def _fuzzy_find_code_python(
        self, content: str, target: str, threshold: float = 0.85
    ) -> Optional[Tuple[str, float]]:
        """Python fallback for fuzzy code matching using difflib."""
        import difflib
        
        target_lines = target.strip().splitlines()
        content_lines = content.splitlines()
        
        if not target_lines or not content_lines:
            return None
        
        target_len = len(target_lines)
        best_match = None
        best_ratio = 0.0
        
        # Slide window through content
        for i in range(len(content_lines) - target_len + 1):
            window = content_lines[i:i + target_len]
            window_text = "\n".join(window)
            
            ratio = difflib.SequenceMatcher(
                None, 
                target.strip(), 
                window_text.strip()
            ).ratio()
            
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = window_text
        
        # Also try with some flexibility on window size (±2 lines)
        for delta in [1, 2, -1]:
            adjusted_len = target_len + delta
            if adjusted_len < 1 or adjusted_len > len(content_lines):
                continue
                
            for i in range(len(content_lines) - adjusted_len + 1):
                window = content_lines[i:i + adjusted_len]
                window_text = "\n".join(window)
                
                ratio = difflib.SequenceMatcher(
                    None,
                    target.strip(),
                    window_text.strip()
                ).ratio()
                
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = window_text
        
        if best_ratio >= threshold and best_match:
            return (best_match, best_ratio)
        
        return None

    def _create_branch(self, fix: FixProposal) -> None:
        """Create git branch for fix.

        Thread-safe: Uses git lock to prevent concurrent git operations.

        Args:
            fix: Fix proposal
        """
        if not self.repo:
            return

        branch_name = fix.branch_name or f"autofix/{fix.fix_type.value}/{fix.id}"

        try:
            with self._git_lock:
                # Check if branch exists
                if branch_name in self.repo.heads:
                    logger.warning(f"Branch {branch_name} already exists, checking out")
                    self.repo.heads[branch_name].checkout()
                else:
                    # Create new branch
                    new_branch = self.repo.create_head(branch_name)
                    new_branch.checkout()
                    logger.info(f"Created and checked out branch: {branch_name}")

                fix.branch_name = branch_name

        except Exception as e:
            logger.error(f"Failed to create branch: {e}")
            # Continue without branch

    def _create_commit(self, fix: FixProposal) -> None:
        """Create git commit for fix.

        Thread-safe: Uses git lock to prevent concurrent git operations.

        Args:
            fix: Fix proposal
        """
        if not self.repo:
            return

        try:
            with self._git_lock:
                # Stage all changed files
                for change in fix.changes:
                    file_path = str(change.file_path)
                    self.repo.index.add([file_path])

                # Create commit message
                commit_msg = fix.commit_message or self._generate_commit_message(fix)

                # Commit
                self.repo.index.commit(commit_msg)
                logger.info(f"Created commit for fix {fix.id}")

        except Exception as e:
            logger.error(f"Failed to create commit: {e}")

    def _create_batch_commit(self, fixes: List[FixProposal]) -> None:
        """Create single commit for multiple fixes.

        Thread-safe: Uses git lock to prevent concurrent git operations.

        Args:
            fixes: List of applied fixes
        """
        if not self.repo or not fixes:
            return

        try:
            with self._git_lock:
                # Stage all changed files
                for fix in fixes:
                    for change in fix.changes:
                        file_path = str(change.file_path)
                        self.repo.index.add([file_path])

                # Generate batch commit message
                commit_msg = self._generate_batch_commit_message(fixes)

                # Commit
                self.repo.index.commit(commit_msg)
                logger.info(f"Created batch commit for {len(fixes)} fixes")

        except Exception as e:
            logger.error(f"Failed to create batch commit: {e}")

    def _generate_commit_message(self, fix: FixProposal) -> str:
        """Generate commit message for fix.

        Args:
            fix: Fix proposal

        Returns:
            Commit message string
        """
        # Use fix's commit message if available
        if fix.commit_message:
            return fix.commit_message

        # Generate from fix details
        msg = f"fix: {fix.title}\n\n"
        msg += f"{fix.description}\n\n"
        msg += f"Fix Type: {fix.fix_type.value}\n"
        msg += f"Confidence: {fix.confidence.value}\n"
        msg += f"Files: {', '.join(f.file_path.name for f in fix.changes)}\n\n"
        msg += "🤖 Generated with Repotoire Auto-Fix\n"

        return msg

    def _generate_batch_commit_message(self, fixes: List[FixProposal]) -> str:
        """Generate commit message for batch of fixes.

        Args:
            fixes: List of fixes

        Returns:
            Batch commit message
        """
        msg = f"fix: apply {len(fixes)} auto-fixes\n\n"

        for fix in fixes:
            msg += f"- {fix.title}\n"

        msg += f"\n🤖 Generated with Repotoire Auto-Fix ({len(fixes)} fixes)\n"

        return msg

    def run_tests(
        self,
        test_command: Optional[str] = None,
        use_sandbox: Optional[bool] = None,
    ) -> TestResult:
        """Run tests after applying fixes.

        By default, tests run in an isolated E2B sandbox to prevent malicious
        auto-fix code from accessing host resources.

        Args:
            test_command: Test command to run (default: pytest)
            use_sandbox: Override instance sandbox setting (None = use instance default)

        Returns:
            TestResult with execution details

        Security:
            ALWAYS prefer sandbox execution. Local execution should only be used
            for trusted code in development environments. Local execution logs
            a security warning.
        """
        command = test_command or "pytest"
        should_use_sandbox = use_sandbox if use_sandbox is not None else self.use_sandbox

        if should_use_sandbox:
            return self._run_tests_sandbox(command)
        else:
            return self._run_tests_local(command)

    def _run_tests_sandbox(self, command: str) -> TestResult:
        """Run tests in isolated E2B sandbox.

        Args:
            command: Test command to execute

        Returns:
            TestResult from sandbox execution
        """
        try:
            from repotoire.sandbox.test_executor import (
                TestExecutor,
                TestExecutorConfig,
                TestResult as SandboxTestResult,
            )
        except ImportError:
            logger.error(
                "Sandbox test execution requires E2B: pip install e2b-code-interpreter"
            )
            return TestResult(
                success=False,
                stdout="",
                stderr="Sandbox test execution requires E2B package. "
                "Install with: pip install e2b-code-interpreter\n"
                "Or use --local-tests flag (security warning: runs with host access)",
                exit_code=-1,
                duration_ms=0,
            )

        logger.info(f"Running tests in sandbox: {command}")

        try:
            # Create config with instance settings
            config = TestExecutorConfig.from_env()
            config.test_timeout_seconds = self.test_timeout

            executor = TestExecutor(config)

            # Run tests asynchronously
            sandbox_result: SandboxTestResult = asyncio.run(
                executor.run_tests(
                    repo_path=self.repository_path,
                    command=command,
                    env_vars=self.test_env_vars,
                    timeout=self.test_timeout,
                )
            )

            if sandbox_result.success:
                logger.info(f"Sandbox tests passed: {sandbox_result.summary}")
            else:
                logger.warning(f"Sandbox tests failed: {sandbox_result.summary}")

            # Convert to local TestResult for backward compatibility
            return TestResult(
                success=sandbox_result.success,
                stdout=sandbox_result.stdout,
                stderr=sandbox_result.stderr,
                exit_code=sandbox_result.exit_code,
                duration_ms=sandbox_result.duration_ms,
                sandbox_id=sandbox_result.sandbox_id,
                timed_out=sandbox_result.timed_out,
            )

        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"Sandbox test execution failed ({error_type}): {e}")

            # Check if this is a configuration error
            if "E2B_API_KEY" in str(e) or "not configured" in str(e).lower():
                return TestResult(
                    success=False,
                    stdout="",
                    stderr=f"Sandbox not configured: {e}\n\n"
                    "Set E2B_API_KEY environment variable, or use --local-tests flag "
                    "(warning: runs with full host access)",
                    exit_code=-1,
                    duration_ms=0,
                )

            return TestResult(
                success=False,
                stdout="",
                stderr=f"Sandbox test execution failed: {e}",
                exit_code=-1,
                duration_ms=0,
            )

    def _run_tests_local(self, command: str) -> TestResult:
        """Run tests locally with full host access.

        SECURITY WARNING: This method runs tests with full host access.
        Only use for trusted code in development environments.

        Args:
            command: Test command to execute

        Returns:
            TestResult from local execution
        """
        # Log security warning
        logger.warning(
            "SECURITY: Running tests locally with full host access. "
            "Use sandbox execution (default) for untrusted code."
        )

        import time
        start_time = time.time()

        try:
            result = subprocess.run(
                command.split(),
                cwd=self.repository_path,
                capture_output=True,
                text=True,
                timeout=self.test_timeout,
            )

            duration_ms = int((time.time() - start_time) * 1000)
            success = result.returncode == 0
            output = result.stdout + result.stderr

            if success:
                logger.info("Local tests passed after applying fixes")
            else:
                logger.warning("Local tests failed after applying fixes")

            return TestResult(
                success=success,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                duration_ms=duration_ms,
            )

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start_time) * 1000)
            return TestResult(
                success=False,
                stdout="",
                stderr=f"Test execution timed out after {self.test_timeout} seconds",
                exit_code=-1,
                duration_ms=duration_ms,
                timed_out=True,
            )
        except FileNotFoundError:
            return TestResult(
                success=False,
                stdout="",
                stderr=f"Test command not found: {command}",
                exit_code=-1,
                duration_ms=0,
            )
        except Exception as e:
            return TestResult(
                success=False,
                stdout="",
                stderr=f"Error running tests: {str(e)}",
                exit_code=-1,
                duration_ms=0,
            )

    def run_tests_legacy(self, test_command: Optional[str] = None) -> Tuple[bool, str]:
        """Legacy interface for backward compatibility.

        DEPRECATED: Use run_tests() which returns TestResult.

        Args:
            test_command: Test command to run

        Returns:
            Tuple of (success, output)
        """
        result = self.run_tests(test_command)
        return result.success, result.stdout + result.stderr

    def rollback(self) -> bool:
        """Rollback all changes (reset to HEAD).

        Returns:
            True if successful, False otherwise
        """
        if not self.repo:
            logger.warning("No git repository, cannot rollback")
            return False

        try:
            # Reset to HEAD
            self.repo.head.reset(index=True, working_tree=True)
            logger.info("Rolled back all changes")
            return True

        except Exception as e:
            logger.error(f"Failed to rollback: {e}")
            return False
