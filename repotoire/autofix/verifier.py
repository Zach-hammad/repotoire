"""Parallel sandbox verification for Best-of-N fix candidates.

This module provides high-performance verification of multiple fix candidates
in parallel E2B sandboxes. Each candidate is:
1. Applied to a fresh sandbox environment
2. Validated (syntax, imports, types)
3. Tested with the project's test suite

Example:
    ```python
    from repotoire.autofix.verifier import (
        ParallelVerifier,
        VerificationConfig,
    )

    config = VerificationConfig(
        max_concurrent=5,
        test_timeout=120,
    )

    async with ParallelVerifier(config) as verifier:
        results = await verifier.verify_all(
            fixes=candidates,
            repository_path="/path/to/repo",
            test_command="pytest tests/",
        )

        for fix_id, result in results.items():
            print(f"{fix_id}: {result.tests_passed}/{result.tests_total}")
    ```
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from repotoire.autofix.models import FixProposal, CodeChange
from repotoire.autofix.scorer import VerificationResult
from repotoire.logging_config import get_logger
from repotoire.sandbox.config import SandboxConfig
from repotoire.sandbox.exceptions import (
    SandboxConfigurationError,
    SandboxExecutionError,
    SandboxTimeoutError,
)

if TYPE_CHECKING:
    from repotoire.db.models import PlanTier
    from repotoire.sandbox.enforcement import QuotaEnforcer
    from repotoire.sandbox.usage import SandboxUsageTracker

logger = get_logger(__name__)


@dataclass
class VerificationConfig:
    """Configuration for parallel verification.

    Attributes:
        max_concurrent: Maximum number of concurrent sandbox verifications
        test_timeout: Timeout for test execution in seconds
        validation_timeout: Timeout for validation (syntax/import/type) in seconds
        run_type_check: Whether to run mypy type checking
        test_command: Default test command if not specified per-run
        fail_fast: Stop other verifications when one fails completely
    """

    max_concurrent: int = 5
    test_timeout: int = 120  # 2 minutes for tests
    validation_timeout: int = 30  # 30 seconds for validation
    run_type_check: bool = False  # Type check is slow, off by default
    test_command: str = "pytest"
    fail_fast: bool = False


@dataclass
class VerificationTask:
    """A single verification task for the queue.

    Attributes:
        fix: The fix to verify
        index: Position in the original list
        repository_path: Path to repository
        test_command: Test command to run
    """

    fix: FixProposal
    index: int
    repository_path: str
    test_command: str


class ParallelVerifier:
    """Verify multiple fix candidates in parallel sandboxes.

    Manages sandbox lifecycle, applies fixes, runs validation and tests,
    and collects results. Respects quota limits via the QuotaEnforcer.
    """

    def __init__(
        self,
        config: VerificationConfig,
        sandbox_config: Optional[SandboxConfig] = None,
        customer_id: Optional[str] = None,
        tier: Optional["PlanTier"] = None,
        quota_enforcer: Optional["QuotaEnforcer"] = None,
        usage_tracker: Optional["SandboxUsageTracker"] = None,
    ):
        """Initialize verifier.

        Args:
            config: Verification configuration
            sandbox_config: Sandbox configuration (loads from env if not provided)
            customer_id: Customer ID for quota tracking
            tier: Customer tier for quota limits
            quota_enforcer: Enforcer for checking quotas
            usage_tracker: Tracker for recording usage
        """
        self.config = config
        self.sandbox_config = sandbox_config or SandboxConfig.from_env()
        self.customer_id = customer_id
        self.tier = tier
        self.quota_enforcer = quota_enforcer
        self.usage_tracker = usage_tracker

        self._semaphore: Optional[asyncio.Semaphore] = None
        self._total_cost: float = 0.0

    async def __aenter__(self) -> "ParallelVerifier":
        """Enter async context."""
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context."""
        pass

    async def verify_all(
        self,
        fixes: List[FixProposal],
        repository_path: str,
        test_command: Optional[str] = None,
    ) -> Dict[str, VerificationResult]:
        """Verify all fix candidates in parallel.

        Args:
            fixes: List of fix proposals to verify
            repository_path: Path to the repository
            test_command: Test command to run (uses config default if not provided)

        Returns:
            Dictionary mapping fix_id -> VerificationResult
        """
        if not fixes:
            return {}

        if not self.sandbox_config.is_configured:
            logger.warning("Sandbox not configured, running local validation only")
            return await self._verify_all_local(fixes)

        test_cmd = test_command or self.config.test_command

        # Create tasks for parallel execution
        tasks = [
            VerificationTask(
                fix=fix,
                index=i,
                repository_path=repository_path,
                test_command=test_cmd,
            )
            for i, fix in enumerate(fixes)
        ]

        logger.info(
            f"Starting parallel verification of {len(tasks)} fixes "
            f"(max concurrent: {self.config.max_concurrent})"
        )

        # Run verifications with semaphore for concurrency control
        results = await asyncio.gather(
            *[self._verify_with_semaphore(task) for task in tasks],
            return_exceptions=True,
        )

        # Build result dictionary
        result_dict: Dict[str, VerificationResult] = {}
        for task, result in zip(tasks, results):
            if isinstance(result, Exception):
                logger.error(f"Verification failed for {task.fix.id}: {result}")
                result_dict[task.fix.id] = VerificationResult(
                    fix_id=task.fix.id,
                    error=str(result),
                )
            else:
                result_dict[task.fix.id] = result

        # Log summary
        successful = sum(1 for r in result_dict.values() if r.succeeded)
        logger.info(
            f"Verification complete: {successful}/{len(fixes)} succeeded, "
            f"total cost: ${self._total_cost:.4f}"
        )

        return result_dict

    async def _verify_with_semaphore(
        self,
        task: VerificationTask,
    ) -> VerificationResult:
        """Run verification with semaphore for concurrency control.

        Args:
            task: Verification task

        Returns:
            VerificationResult
        """
        async with self._semaphore:
            return await self._verify_single(task)

    async def _verify_single(
        self,
        task: VerificationTask,
    ) -> VerificationResult:
        """Verify a single fix in a sandbox.

        Args:
            task: Verification task

        Returns:
            VerificationResult with test and validation results
        """
        from repotoire.sandbox.client import SandboxExecutor

        fix = task.fix
        start_time = time.time()

        logger.debug(f"Starting verification for fix {fix.id}")

        try:
            async with SandboxExecutor(
                self.sandbox_config,
                customer_id=self.customer_id,
                tier=self.tier,
                quota_enforcer=self.quota_enforcer,
                usage_tracker=self.usage_tracker,
                operation_type="best_of_n_verification",
            ) as sandbox:
                # 1. Upload repository (or prepare working directory)
                await self._prepare_sandbox(sandbox, task.repository_path)

                # 2. Apply the fix
                await self._apply_fix(sandbox, fix)

                # 3. Run validation
                syntax_valid, import_valid, type_valid = await self._run_validation(
                    sandbox, fix
                )

                # 4. Run tests
                tests_passed, tests_failed, tests_total, test_output = (
                    await self._run_tests(sandbox, task.test_command)
                )

                duration_ms = int((time.time() - start_time) * 1000)

                # Calculate cost (rough estimate based on duration)
                from repotoire.sandbox.metrics import calculate_cost
                cost = calculate_cost(
                    duration_seconds=duration_ms / 1000,
                    cpu_count=self.sandbox_config.cpu_count,
                    memory_gb=self.sandbox_config.memory_mb / 1024,
                )
                self._total_cost += cost

                return VerificationResult(
                    fix_id=fix.id,
                    tests_passed=tests_passed,
                    tests_failed=tests_failed,
                    tests_total=tests_total,
                    test_output=test_output,
                    syntax_valid=syntax_valid,
                    import_valid=import_valid,
                    type_valid=type_valid,
                    duration_ms=duration_ms,
                    sandbox_cost_usd=cost,
                )

        except SandboxConfigurationError as e:
            logger.warning(f"Sandbox not configured: {e}")
            return VerificationResult(
                fix_id=fix.id,
                error=f"Sandbox not configured: {e}",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        except SandboxTimeoutError as e:
            logger.warning(f"Verification timed out for {fix.id}: {e}")
            return VerificationResult(
                fix_id=fix.id,
                error=f"Verification timed out: {e}",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        except Exception as e:
            logger.exception(f"Verification failed for {fix.id}: {e}")
            return VerificationResult(
                fix_id=fix.id,
                error=str(e),
                duration_ms=int((time.time() - start_time) * 1000),
            )

    async def _prepare_sandbox(
        self,
        sandbox,
        repository_path: str,
    ) -> None:
        """Prepare sandbox with repository files.

        Args:
            sandbox: SandboxExecutor instance
            repository_path: Path to the repository
        """
        # For now, we assume the repository is already available or mounted
        # In production, you would:
        # 1. Create a git clone of the repo
        # 2. Or upload relevant files

        # Create working directory
        await sandbox.execute_command("mkdir -p /code")

        # Clone or copy repository (simplified for now)
        logger.debug(f"Preparing sandbox with repo: {repository_path}")

    async def _apply_fix(
        self,
        sandbox,
        fix: FixProposal,
    ) -> None:
        """Apply fix changes to sandbox.

        Args:
            sandbox: SandboxExecutor instance
            fix: Fix to apply
        """
        for change in fix.changes:
            # Write the fixed code to the file
            file_path = f"/code/{change.file_path}"

            # Ensure parent directory exists
            parent = str(Path(file_path).parent)
            await sandbox.execute_command(f"mkdir -p {parent}")

            # Write the fixed content
            # Escape the code for shell
            escaped_code = change.fixed_code.replace("'", "'\\''")
            await sandbox.execute_command(
                f"cat > {file_path} << 'FIXCODE'\n{change.fixed_code}\nFIXCODE"
            )

            logger.debug(f"Applied fix to {file_path}")

    async def _run_validation(
        self,
        sandbox,
        fix: FixProposal,
    ) -> Tuple[bool, Optional[bool], Optional[bool]]:
        """Run validation checks on the fix.

        Args:
            sandbox: SandboxExecutor instance
            fix: Fix to validate

        Returns:
            Tuple of (syntax_valid, import_valid, type_valid)
        """
        syntax_valid = True
        import_valid = None
        type_valid = None

        for change in fix.changes:
            file_path = f"/code/{change.file_path}"

            # Syntax check (AST parse)
            result = await sandbox.execute_command(
                f"python3 -m py_compile {file_path}"
            )
            if not result.success:
                syntax_valid = False
                logger.debug(f"Syntax check failed for {file_path}: {result.stderr}")
                continue

            # Import check
            module_name = Path(change.file_path).stem
            result = await sandbox.execute_command(
                f"python3 -c \"import sys; sys.path.insert(0, '/code'); "
                f"import {module_name}\""
            )
            if import_valid is None:
                import_valid = result.success
            elif not result.success:
                import_valid = False

            # Type check (optional, slow)
            if self.config.run_type_check:
                result = await sandbox.execute_command(
                    f"cd /code && python3 -m mypy {change.file_path} --ignore-missing-imports"
                )
                if type_valid is None:
                    type_valid = result.success
                elif not result.success:
                    type_valid = False

        return syntax_valid, import_valid, type_valid

    async def _run_tests(
        self,
        sandbox,
        test_command: str,
    ) -> Tuple[int, int, int, str]:
        """Run tests in sandbox.

        Args:
            sandbox: SandboxExecutor instance
            test_command: Test command to execute

        Returns:
            Tuple of (passed, failed, total, output)
        """
        logger.debug(f"Running tests: {test_command}")

        # Run pytest with JSON output for parsing
        result = await sandbox.execute_command(
            f"cd /code && {test_command} --tb=short -q 2>&1 || true",
        )

        output = result.stdout + result.stderr

        # Parse pytest output for counts
        passed, failed, total = self._parse_pytest_output(output)

        return passed, failed, total, output

    def _parse_pytest_output(self, output: str) -> Tuple[int, int, int]:
        """Parse pytest output to extract test counts.

        Args:
            output: Raw pytest output

        Returns:
            Tuple of (passed, failed, total)
        """
        import re

        passed = 0
        failed = 0

        # Look for summary line like "5 passed, 2 failed in 1.23s"
        # or "5 passed in 1.23s"
        summary_pattern = r"(\d+)\s+passed"
        match = re.search(summary_pattern, output)
        if match:
            passed = int(match.group(1))

        failed_pattern = r"(\d+)\s+failed"
        match = re.search(failed_pattern, output)
        if match:
            failed = int(match.group(1))

        # Also check for errors
        error_pattern = r"(\d+)\s+error"
        match = re.search(error_pattern, output)
        if match:
            failed += int(match.group(1))

        total = passed + failed

        # If no tests found, check for "no tests ran"
        if total == 0:
            if "no tests ran" in output.lower() or "collected 0 items" in output.lower():
                logger.debug("No tests found in output")

        return passed, failed, total

    async def _verify_all_local(
        self,
        fixes: List[FixProposal],
    ) -> Dict[str, VerificationResult]:
        """Run local-only verification (syntax check only, no sandbox).

        Args:
            fixes: List of fixes to verify

        Returns:
            Dictionary of fix_id -> VerificationResult
        """
        import ast

        results: Dict[str, VerificationResult] = {}

        for fix in fixes:
            syntax_valid = True
            errors = []

            for change in fix.changes:
                try:
                    ast.parse(change.fixed_code)
                except SyntaxError as e:
                    syntax_valid = False
                    errors.append(f"{change.file_path}: {e.msg} (line {e.lineno})")

            results[fix.id] = VerificationResult(
                fix_id=fix.id,
                syntax_valid=syntax_valid,
                error="; ".join(errors) if errors else None,
            )

        return results


async def verify_fixes_parallel(
    fixes: List[FixProposal],
    repository_path: str,
    test_command: str = "pytest",
    max_concurrent: int = 5,
    customer_id: Optional[str] = None,
    tier: Optional["PlanTier"] = None,
) -> Dict[str, VerificationResult]:
    """Convenience function for parallel verification.

    Args:
        fixes: Fixes to verify
        repository_path: Path to repository
        test_command: Test command
        max_concurrent: Max concurrent sandboxes
        customer_id: Customer ID for tracking
        tier: Customer tier

    Returns:
        Dictionary of fix_id -> VerificationResult
    """
    config = VerificationConfig(
        max_concurrent=max_concurrent,
        test_command=test_command,
    )

    async with ParallelVerifier(
        config=config,
        customer_id=customer_id,
        tier=tier,
    ) as verifier:
        return await verifier.verify_all(
            fixes=fixes,
            repository_path=repository_path,
            test_command=test_command,
        )
