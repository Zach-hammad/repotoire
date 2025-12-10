"""Core auto-fix engine for generating code fixes."""

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Literal

from repotoire.logging_config import get_logger
from repotoire.models import Finding, Severity
from repotoire.ai.retrieval import GraphRAGRetriever
from repotoire.ai.embeddings import CodeEmbedder
from repotoire.ai.llm import LLMClient, LLMConfig, LLMBackend
from repotoire.graph import Neo4jClient
from repotoire.autofix.languages import get_handler, LanguageHandler
from repotoire.autofix.models import (
    FixProposal,
    FixContext,
    CodeChange,
    Evidence,
    FixType,
    FixConfidence,
    FixStatus,
)
from repotoire.autofix.templates import (
    get_registry,
    TemplateRegistry,
    TemplateMatch,
)
from repotoire.autofix.style import StyleAnalyzer, StyleEnforcer, StyleProfile
from repotoire.autofix.learning import DecisionStore, AdaptiveConfidence
from repotoire.sandbox.code_validator import (
    CodeValidator,
    ValidationConfig,
    ValidationResult,
)

logger = get_logger(__name__)

# Cache for style profiles (keyed by repository path)
_style_profile_cache: Dict[str, StyleProfile] = {}


class AutoFixEngine:
    """Generate and validate automatic code fixes."""

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        llm_backend: LLMBackend = "anthropic",
        decision_store: Optional[DecisionStore] = None,
        validation_config: Optional[ValidationConfig] = None,
        skip_runtime_validation: bool = False,
        # Legacy parameter for backwards compatibility
        openai_api_key: Optional[str] = None,
    ):
        """Initialize auto-fix engine.

        Args:
            neo4j_client: Neo4j client for RAG context
            api_key: API key for LLM backend (or use env var)
            model: Model to use for fix generation (uses backend default if not specified)
            llm_backend: LLM backend to use ("anthropic" or "openai")
            decision_store: Store for learning from user decisions
            validation_config: Configuration for runtime validation
            skip_runtime_validation: Skip sandbox-based validation (faster)
            openai_api_key: (Deprecated) Use api_key instead
        """
        self.neo4j_client = neo4j_client
        self.skip_runtime_validation = skip_runtime_validation

        # Handle legacy parameter
        effective_api_key = api_key or openai_api_key

        # Initialize LLM client with backend abstraction
        llm_config = LLMConfig(
            backend=llm_backend,
            model=model,
            temperature=0.2,  # Lower temperature for consistent code generation
        )
        self.llm_client = LLMClient(config=llm_config, api_key=effective_api_key)
        self.model = self.llm_client.model

        # Initialize RAG retriever for context gathering
        # Use OpenAI embeddings for RAG (separate from LLM generation)
        embeddings_api_key = effective_api_key or os.getenv("OPENAI_API_KEY")
        embedder = CodeEmbedder(api_key=embeddings_api_key)
        self.rag_retriever = GraphRAGRetriever(neo4j_client, embedder)

        # Initialize template registry for fast, deterministic fixes
        self.template_registry = get_registry()

        # Style enforcer (lazily initialized per repository)
        self._style_enforcer: Optional[StyleEnforcer] = None
        self._style_repo_path: Optional[Path] = None

        # Learning feedback system
        self.decision_store = decision_store or DecisionStore()
        self.adaptive_confidence = AdaptiveConfidence(self.decision_store)

        # Validation configuration (default: import check only)
        self.validation_config = validation_config or ValidationConfig(
            run_import_check=True,
            run_type_check=False,
            run_smoke_test=False,
        )

        # Code validator (lazily initialized)
        self._code_validator: Optional[CodeValidator] = None

        logger.info(
            f"AutoFixEngine initialized with backend={llm_backend}, model={self.model}, "
            f"skip_runtime_validation={skip_runtime_validation}"
        )

    def get_style_profile(
        self, repository_path: Path, force_refresh: bool = False
    ) -> StyleProfile:
        """Get or analyze style profile for a repository.

        Results are cached per repository path.

        Args:
            repository_path: Path to repository
            force_refresh: Force re-analysis even if cached

        Returns:
            StyleProfile for the repository
        """
        repo_key = str(repository_path.resolve())

        if not force_refresh and repo_key in _style_profile_cache:
            logger.debug(f"Using cached style profile for {repository_path}")
            return _style_profile_cache[repo_key]

        logger.info(f"Analyzing style conventions for {repository_path}")
        analyzer = StyleAnalyzer(repository_path)
        profile = analyzer.analyze()
        _style_profile_cache[repo_key] = profile

        return profile

    def _get_style_instructions(self, repository_path: Path) -> str:
        """Get style instructions for a repository.

        Args:
            repository_path: Path to repository

        Returns:
            Markdown formatted style instructions
        """
        # Check if we need to update the enforcer
        if (
            self._style_enforcer is None
            or self._style_repo_path != repository_path
        ):
            profile = self.get_style_profile(repository_path)
            self._style_enforcer = StyleEnforcer(profile)
            self._style_repo_path = repository_path

        return self._style_enforcer.get_style_instructions()

    async def generate_fix(
        self,
        finding: Finding,
        repository_path: Path,
        context_size: int = 5,
        skip_runtime_validation: Optional[bool] = None,
    ) -> Optional[FixProposal]:
        """Generate a fix proposal for a finding.

        Args:
            finding: The code smell or issue to fix
            repository_path: Path to the repository
            context_size: Number of related code snippets to gather
            skip_runtime_validation: Override instance setting for this call

        Returns:
            FixProposal if fix can be generated, None otherwise
        """
        skip_validation = (
            skip_runtime_validation
            if skip_runtime_validation is not None
            else self.skip_runtime_validation
        )

        try:
            # Step 0: Try template-based fix first (fast, deterministic)
            template_fix = await self._try_template_fix(finding, repository_path)
            if template_fix is not None:
                logger.info(f"Applied template fix: {template_fix.title}")
                return template_fix

            # Step 1: Gather context using RAG
            logger.info(f"Gathering context for finding: {finding.title}")
            context = await self._gather_context(finding, repository_path, context_size)

            # Step 2: Determine fix type from finding
            fix_type = self._determine_fix_type(finding)

            # Step 3: Generate fix using GPT-4
            logger.info(f"Generating {fix_type.value} fix using {self.model}")
            fix_proposal = await self._generate_fix_with_llm(
                finding, context, fix_type, repository_path
            )

            # Step 4: Validate the fix (multi-level)
            logger.info("Validating generated fix")
            validation_result = await self._validate_fix_multilevel(
                fix_proposal, repository_path, skip_validation
            )

            # Populate validation fields
            fix_proposal.syntax_valid = validation_result.syntax_valid
            fix_proposal.import_valid = validation_result.import_valid
            fix_proposal.type_valid = validation_result.type_valid
            fix_proposal.validation_errors = [
                e.to_dict() for e in validation_result.errors
            ]
            fix_proposal.validation_warnings = [
                w.to_dict() for w in validation_result.warnings
            ]

            is_valid = validation_result.is_valid

            # Step 5: Apply adaptive confidence based on historical feedback
            original_confidence = fix_proposal.confidence
            adjusted_confidence = self.adaptive_confidence.adjust_confidence(
                base=original_confidence,
                fix_type=fix_type.value,
                repository=str(repository_path),
            )

            # Further reduce confidence if validation failed
            if not is_valid and adjusted_confidence != FixConfidence.LOW:
                logger.info(
                    f"Reducing confidence to LOW due to validation errors"
                )
                adjusted_confidence = FixConfidence.LOW

            if adjusted_confidence != original_confidence:
                logger.info(
                    f"Adjusted confidence {original_confidence.value} → {adjusted_confidence.value} "
                    f"based on validation and historical feedback"
                )
                fix_proposal.confidence = adjusted_confidence

            # Step 6: Optionally generate tests
            if is_valid and fix_type in [FixType.REFACTOR, FixType.EXTRACT]:
                logger.info("Generating tests for fix")
                test_code = await self._generate_tests(fix_proposal, context)
                if test_code:
                    fix_proposal.test_code = test_code
                    fix_proposal.tests_generated = True

            logger.info(
                f"Fix generated: valid={is_valid}, confidence={fix_proposal.confidence.value}, "
                f"errors={len(fix_proposal.validation_errors)}, "
                f"warnings={len(fix_proposal.validation_warnings)}"
            )
            return fix_proposal

        except Exception as e:
            logger.error(f"Failed to generate fix for finding: {e}", exc_info=True)
            return None

    async def _gather_context(
        self,
        finding: Finding,
        repository_path: Path,
        context_size: int,
    ) -> FixContext:
        """Gather context for fix generation using RAG.

        Args:
            finding: The finding to fix
            repository_path: Path to repository
            context_size: Number of related snippets

        Returns:
            FixContext with related code
        """
        context = FixContext(finding=finding)

        try:
            # Use RAG to find related code
            if finding.affected_files:
                file_path = finding.affected_files[0]  # Use first affected file
                query = f"code related to {finding.title} in {file_path}"
                search_results = self.rag_retriever.retrieve(
                    query=query,
                    top_k=context_size,
                )

                # Extract code snippets from search results
                if search_results:
                    context.related_code = [
                        result.code
                        for result in search_results[:context_size]
                        if result.code
                    ]

            # Read the actual file content
            if finding.affected_files:
                file_path = repository_path / finding.affected_files[0]
                if file_path.exists():
                    with open(file_path, "r", encoding="utf-8") as f:
                        context.file_content = f.read()

            # Extract imports from file using language-specific handler
            if context.file_content and finding.affected_files:
                handler = get_handler(finding.affected_files[0])
                context.imports = handler.extract_imports(context.file_content)

        except Exception as e:
            logger.warning(f"Failed to gather full context: {e}")

        return context

    async def _try_template_fix(
        self,
        finding: Finding,
        repository_path: Path,
    ) -> Optional[FixProposal]:
        """Try to apply a template-based fix.

        Args:
            finding: The finding to fix
            repository_path: Path to repository

        Returns:
            FixProposal if a template matches, None otherwise
        """
        if not finding.affected_files:
            return None

        file_path = finding.affected_files[0]
        full_path = repository_path / file_path

        if not full_path.exists():
            return None

        try:
            # Read the file content
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Determine language from file extension
            handler = get_handler(file_path)
            language = handler.language_name.lower()

            # Try to match a template
            match = self.template_registry.match(
                code=content,
                file_path=file_path,
                language=language,
            )

            if match is None:
                logger.debug(f"No template match for finding: {finding.title}")
                return None

            # Create fix proposal from template match
            return self._create_fix_from_template(
                finding=finding,
                match=match,
                file_path=file_path,
                repository_path=repository_path,
            )

        except Exception as e:
            logger.warning(f"Template fix attempt failed: {e}")
            return None

    def _create_fix_from_template(
        self,
        finding: Finding,
        match: TemplateMatch,
        file_path: str,
        repository_path: Path,
    ) -> FixProposal:
        """Create a FixProposal from a template match.

        Args:
            finding: The original finding
            match: The template match result
            file_path: Path to the affected file
            repository_path: Path to repository

        Returns:
            FixProposal with the template-based fix
        """
        template = match.template

        # Map template fix_type to FixType enum
        fix_type_map = {
            "refactor": FixType.REFACTOR,
            "simplify": FixType.SIMPLIFY,
            "extract": FixType.EXTRACT,
            "rename": FixType.RENAME,
            "remove": FixType.REMOVE,
            "security": FixType.SECURITY,
            "type_hint": FixType.TYPE_HINT,
            "documentation": FixType.DOCUMENTATION,
        }
        fix_type = fix_type_map.get(template.fix_type.lower(), FixType.REFACTOR)

        # Map confidence string to enum
        confidence_map = {
            "HIGH": FixConfidence.HIGH,
            "MEDIUM": FixConfidence.MEDIUM,
            "LOW": FixConfidence.LOW,
        }
        confidence = confidence_map.get(template.confidence, FixConfidence.MEDIUM)

        # Generate fix ID
        fix_id = hashlib.md5(
            f"{file_path}:{match.match_start}:{template.name}".encode()
        ).hexdigest()[:12]

        # Create code change
        change = CodeChange(
            file_path=Path(file_path),
            original_code=match.original_code,
            fixed_code=match.fixed_code,
            start_line=0,  # Will be calculated if needed
            end_line=0,
            description=template.description or f"Apply {template.name}",
        )

        # Create evidence from template
        evidence = Evidence(
            documentation_refs=template.evidence.documentation_refs,
            best_practices=template.evidence.best_practices,
            similar_patterns=[f"Template: {template.name}"],
        )

        # Create the fix proposal
        fix_proposal = FixProposal(
            id=fix_id,
            finding=finding,
            fix_type=fix_type,
            confidence=confidence,
            changes=[change],
            title=f"Template fix: {template.name}",
            description=template.description or f"Applied template '{template.name}'",
            rationale="; ".join(template.evidence.best_practices)
            if template.evidence.best_practices
            else "Template-based fix",
            evidence=evidence,
            branch_name=f"autofix/template/{fix_id}",
            commit_message=f"fix: {template.name}\n\n{template.description or 'Template-based fix'}",
            syntax_valid=True,  # Templates produce deterministic, valid code
        )

        return fix_proposal

    def _determine_fix_type(self, finding: Finding) -> FixType:
        """Determine the type of fix needed based on finding.

        Args:
            finding: The finding to analyze

        Returns:
            Appropriate FixType
        """
        title_lower = finding.title.lower()
        description_lower = finding.description.lower() if finding.description else ""

        # Security issues
        if finding.severity == Severity.CRITICAL or "security" in title_lower:
            return FixType.SECURITY

        # Complexity issues
        if "complex" in title_lower or "cyclomatic" in description_lower:
            return FixType.SIMPLIFY

        # Dead code
        if "unused" in title_lower or "dead code" in title_lower:
            return FixType.REMOVE

        # Documentation
        if "docstring" in title_lower or "documentation" in title_lower:
            return FixType.DOCUMENTATION

        # Type hints
        if "type" in title_lower and "hint" in description_lower:
            return FixType.TYPE_HINT

        # Long methods/functions
        if "long" in title_lower or "too many" in title_lower:
            return FixType.EXTRACT

        # Default to refactor
        return FixType.REFACTOR

    async def _generate_fix_with_llm(
        self,
        finding: Finding,
        context: FixContext,
        fix_type: FixType,
        repository_path: Path,
    ) -> FixProposal:
        """Generate fix using LLM.

        Args:
            finding: The finding to fix
            context: Context for fix generation
            fix_type: Type of fix to generate
            repository_path: Path to repository (for style instructions)

        Returns:
            FixProposal with generated changes
        """
        # Get language-specific handler
        file_path = finding.affected_files[0] if finding.affected_files else "unknown.py"
        handler = get_handler(file_path)

        # Build prompt with language-specific guidance and style instructions
        prompt = self._build_fix_prompt(
            finding, context, fix_type, handler, repository_path
        )

        # Call LLM with language-specific system prompt
        response_text = self.llm_client.generate(
            messages=[{"role": "user", "content": prompt}],
            system=handler.get_system_prompt(),
        )

        # Parse response
        fix_data = self._parse_llm_response(response_text)

        # Add RAG context to evidence
        evidence = fix_data.get("evidence", Evidence())
        if isinstance(evidence, dict):
            evidence = Evidence(**evidence)

        # Add RAG context snippets as additional evidence
        evidence.rag_context = context.related_code[:3]

        # Create fix proposal
        file_path = finding.affected_files[0] if finding.affected_files else "unknown"
        line_num = finding.line_start or 0
        fix_id = hashlib.md5(
            f"{file_path}:{line_num}:{datetime.utcnow().isoformat()}".encode()
        ).hexdigest()[:12]

        fix_proposal = FixProposal(
            id=fix_id,
            finding=finding,
            fix_type=fix_type,
            confidence=self._calculate_confidence(fix_data, context),
            changes=fix_data["changes"],
            title=fix_data.get("title", f"Fix: {finding.title}"),
            description=fix_data.get("description", ""),
            rationale=fix_data.get("rationale", ""),
            evidence=evidence,
            branch_name=f"autofix/{fix_type.value}/{fix_id}",
            commit_message=f"fix: {fix_data.get('title', finding.title)}\n\n{fix_data.get('description', '')}",
        )

        return fix_proposal

    def _build_fix_prompt(
        self,
        finding: Finding,
        context: FixContext,
        fix_type: FixType,
        handler: LanguageHandler,
        repository_path: Path,
    ) -> str:
        """Build prompt for LLM fix generation.

        Args:
            finding: The finding to fix
            context: Context for fix
            fix_type: Type of fix
            handler: Language-specific handler
            repository_path: Path to repository (for style instructions)

        Returns:
            Formatted prompt string
        """
        # Extract relevant code section
        code_section = ""
        if context.file_content and finding.line_start:
            lines = context.file_content.split("\n")
            start = max(0, finding.line_start - 10)
            end = min(len(lines), finding.line_start + 20)
            code_section = "\n".join(lines[start:end])

        file_path = finding.affected_files[0] if finding.affected_files else "unknown"
        language_name = handler.language_name
        code_marker = handler.get_code_block_marker()
        fix_guidance = handler.get_fix_template(fix_type.value)

        # Get style instructions for the repository (only for Python)
        style_section = ""
        if language_name == "Python":
            try:
                style_instructions = self._get_style_instructions(repository_path)
                style_section = f"\n{style_instructions}\n"
            except Exception as e:
                logger.debug(f"Could not get style instructions: {e}")

        # Get historical feedback adjustments
        historical_section = ""
        try:
            prompt_adjustments = self.adaptive_confidence.get_prompt_adjustments(
                repository=str(repository_path)
            )
            if prompt_adjustments:
                historical_section = f"\n{prompt_adjustments}\n"
        except Exception as e:
            logger.debug(f"Could not get prompt adjustments: {e}")

        prompt = f"""# Code Fix Task

## Issue Details
- **Title**: {finding.title}
- **Severity**: {finding.severity.value}
- **Description**: {finding.description or 'No description'}
- **File**: {file_path}
- **Language**: {language_name}
- **Line**: {finding.line_start or 'unknown'}

## Fix Type Required
{fix_type.value}

## Fix Guidelines
{fix_guidance}
{style_section}{historical_section}

## Current Code
```{code_marker}
{code_section}
```

## Related Code Context
{chr(10).join(f"```{code_marker}{chr(10)}{code}{chr(10)}```" for code in context.related_code[:3])}

## Task
Generate a fix for this issue. Provide your response in the following JSON format:

{{
    "title": "Short fix title (max 100 chars)",
    "description": "Detailed explanation of the fix",
    "rationale": "Why this fix addresses the issue",
    "evidence": {{
        "similar_patterns": ["Example 1 from codebase showing this pattern works", "Example 2..."],
        "documentation_refs": ["Relevant style guide or documentation reference", "..."],
        "best_practices": ["Why this approach is recommended", "Industry standard for..."]
    }},
    "changes": [
        {{
            "file_path": "{file_path}",
            "original_code": "exact original code to replace",
            "fixed_code": "new code",
            "start_line": line_number,
            "end_line": line_number,
            "description": "what this change does"
        }}
    ]
}}

**Important**:
- Only fix the specific issue mentioned
- Preserve existing functionality
- Follow {language_name} best practices
- Keep changes minimal and focused
- Ensure the fixed code is syntactically valid
- **Provide evidence**: Include similar patterns, documentation references, and best practices to justify the fix"""

        return prompt

    def _parse_llm_response(self, response_text: str) -> Dict[str, Any]:
        """Parse LLM response into structured data.

        Args:
            response_text: Raw LLM response

        Returns:
            Parsed fix data
        """
        import json
        import re

        # Extract JSON from response (may be wrapped in markdown)
        json_match = re.search(
            r"```json\s*(\{.*?\})\s*```", response_text, re.DOTALL
        )
        if json_match:
            response_text = json_match.group(1)

        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            # Fallback: extract what we can
            logger.warning("Failed to parse JSON response, using fallback")
            data = {
                "title": "Auto-generated fix",
                "description": response_text[:500],
                "rationale": "Fix suggested by AI",
                "evidence": {},
                "changes": [],
            }

        # Convert changes to CodeChange objects
        changes = []
        for change in data.get("changes", []):
            changes.append(
                CodeChange(
                    file_path=Path(change["file_path"]),
                    original_code=change["original_code"],
                    fixed_code=change["fixed_code"],
                    start_line=change.get("start_line", 0),
                    end_line=change.get("end_line", 0),
                    description=change.get("description", ""),
                )
            )

        data["changes"] = changes

        # Parse evidence
        evidence_data = data.get("evidence", {})
        data["evidence"] = Evidence(
            similar_patterns=evidence_data.get("similar_patterns", []),
            documentation_refs=evidence_data.get("documentation_refs", []),
            best_practices=evidence_data.get("best_practices", []),
        )

        return data

    def _calculate_confidence(
        self, fix_data: Dict[str, Any], context: FixContext
    ) -> FixConfidence:
        """Calculate confidence level for generated fix.

        Args:
            fix_data: Parsed fix data
            context: Fix context

        Returns:
            FixConfidence level
        """
        score = 0.5  # Start at 50%

        # Boost confidence if we have good context
        if len(context.related_code) >= 3:
            score += 0.15

        # Boost if we have file content
        if context.file_content:
            score += 0.1

        # Boost if changes are small (less risky)
        if len(fix_data.get("changes", [])) == 1:
            score += 0.1

        # Boost if rationale is detailed
        if len(fix_data.get("rationale", "")) > 100:
            score += 0.1

        # Reduce if finding is critical (needs careful review)
        if context.finding.severity == Severity.CRITICAL:
            score -= 0.2

        # Classify
        if score >= 0.9:
            return FixConfidence.HIGH
        elif score >= 0.7:
            return FixConfidence.MEDIUM
        else:
            return FixConfidence.LOW

    def _validate_fix(
        self, fix_proposal: FixProposal, repository_path: Path
    ) -> bool:
        """Validate that generated fix is syntactically correct.

        DEPRECATED: Use _validate_fix_multilevel for multi-level validation.

        Args:
            fix_proposal: The fix to validate
            repository_path: Path to repository

        Returns:
            True if fix is valid, False otherwise
        """
        try:
            for change in fix_proposal.changes:
                # Get language-specific handler for syntax validation
                handler = get_handler(str(change.file_path))

                # Check syntax of fixed code
                if not handler.validate_syntax(change.fixed_code):
                    logger.warning(
                        f"Syntax error in fix for {change.file_path}"
                    )
                    return False

                # Verify original code exists in file
                file_path = repository_path / change.file_path
                if file_path.exists():
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()

                    if change.original_code.strip() not in content:
                        logger.warning(
                            f"Original code not found in {change.file_path}"
                        )
                        return False

            return True

        except Exception as e:
            logger.error(f"Validation error: {e}")
            return False

    async def _validate_fix_multilevel(
        self,
        fix_proposal: FixProposal,
        repository_path: Path,
        skip_runtime: bool = False,
    ) -> ValidationResult:
        """Validate fix using multi-level validation.

        Validation levels:
        - Level 1 (Syntax): Local ast.parse() - always run
        - Level 2 (Import): Sandbox import test - unless skip_runtime=True
        - Level 3 (Type): Mypy in sandbox - if validation_config.run_type_check
        - Level 4 (Smoke): Function calls - if validation_config.run_smoke_test

        Args:
            fix_proposal: The fix to validate
            repository_path: Path to repository
            skip_runtime: Skip sandbox-based validation levels

        Returns:
            ValidationResult with details about all validation levels
        """
        from repotoire.sandbox.code_validator import (
            ValidationError as ValError,
            ValidationLevel,
            validate_syntax_only,
        )

        # If no changes, return valid
        if not fix_proposal.changes:
            return ValidationResult(
                is_valid=True,
                syntax_valid=True,
            )

        # For skip_runtime mode, just do syntax validation locally
        if skip_runtime:
            all_valid = True
            all_errors: List[ValError] = []

            for change in fix_proposal.changes:
                result = validate_syntax_only(change.fixed_code)
                if not result.syntax_valid:
                    all_valid = False
                    all_errors.extend(result.errors)

                # Also verify original code exists
                file_path = repository_path / change.file_path
                if file_path.exists():
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()

                    if change.original_code.strip() not in content:
                        all_errors.append(
                            ValError(
                                level=ValidationLevel.SYNTAX.value,
                                error_type="MatchError",
                                message=f"Original code not found in {change.file_path}",
                                suggestion="The file may have been modified since analysis",
                            )
                        )
                        all_valid = False

            return ValidationResult(
                is_valid=all_valid,
                syntax_valid=all_valid,
                errors=all_errors,
            )

        # Full validation with sandbox
        try:
            # Get or create validator
            if self._code_validator is None:
                self._code_validator = CodeValidator(self.validation_config)
                await self._code_validator.__aenter__()

            # Validate each change
            combined_result = ValidationResult(
                is_valid=True,
                syntax_valid=True,
            )

            for change in fix_proposal.changes:
                # Collect related project files for import context
                project_files = self._get_related_files(
                    change.file_path, repository_path
                )

                result = await self._code_validator.validate(
                    fixed_code=change.fixed_code,
                    file_path=str(change.file_path),
                    original_code=change.original_code,
                    project_files=project_files,
                    project_root=repository_path,
                )

                # Merge results
                combined_result.is_valid = combined_result.is_valid and result.is_valid
                combined_result.syntax_valid = (
                    combined_result.syntax_valid and result.syntax_valid
                )

                if result.import_valid is not None:
                    if combined_result.import_valid is None:
                        combined_result.import_valid = result.import_valid
                    else:
                        combined_result.import_valid = (
                            combined_result.import_valid and result.import_valid
                        )

                if result.type_valid is not None:
                    if combined_result.type_valid is None:
                        combined_result.type_valid = result.type_valid
                    else:
                        combined_result.type_valid = (
                            combined_result.type_valid and result.type_valid
                        )

                combined_result.errors.extend(result.errors)
                combined_result.warnings.extend(result.warnings)
                combined_result.duration_ms += result.duration_ms

                # Also verify original code exists in file
                file_path = repository_path / change.file_path
                if file_path.exists():
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()

                    if change.original_code.strip() not in content:
                        combined_result.errors.append(
                            ValError(
                                level=ValidationLevel.SYNTAX.value,
                                error_type="MatchError",
                                message=f"Original code not found in {change.file_path}",
                                suggestion="The file may have been modified since analysis",
                            )
                        )
                        combined_result.is_valid = False

            return combined_result

        except Exception as e:
            logger.warning(f"Multi-level validation failed, falling back: {e}")
            # Fall back to syntax-only validation
            return await self._validate_fix_multilevel(
                fix_proposal, repository_path, skip_runtime=True
            )

    def _get_related_files(
        self, file_path: Path, repository_path: Path
    ) -> List[Path]:
        """Get files that the target file depends on.

        Args:
            file_path: Path to the target file
            repository_path: Root of the repository

        Returns:
            List of paths to related files (up to 10)
        """
        related: List[Path] = []

        # Read the file and extract imports
        full_path = repository_path / file_path
        if not full_path.exists():
            return related

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            handler = get_handler(str(file_path))
            imports = handler.extract_imports(content)

            # Try to find local imports in the repository
            for imp in imports[:10]:  # Limit to 10 imports
                # Parse import statement to get module path
                # e.g., "from src.utils import helper" -> "src/utils.py"
                if imp.startswith("from "):
                    parts = imp.split()
                    if len(parts) >= 2:
                        module = parts[1].replace(".", "/") + ".py"
                        candidate = repository_path / module
                        if candidate.exists() and candidate not in related:
                            related.append(candidate)
                elif imp.startswith("import "):
                    parts = imp.split()
                    if len(parts) >= 2:
                        module = parts[1].replace(".", "/") + ".py"
                        candidate = repository_path / module
                        if candidate.exists() and candidate not in related:
                            related.append(candidate)

        except Exception as e:
            logger.debug(f"Error finding related files: {e}")

        return related[:10]  # Limit total files

    async def _generate_tests(
        self,
        fix_proposal: FixProposal,
        context: FixContext,
    ) -> Optional[str]:
        """Generate tests for the fix.

        Args:
            fix_proposal: The fix to test
            context: Fix context

        Returns:
            Test code string if successful, None otherwise
        """
        try:
            # Build test generation prompt
            prompt = f"""Generate pytest test cases for this code fix:

## Fix Description
{fix_proposal.description}

## Changes
{chr(10).join(f"File: {c.file_path}{chr(10)}Fixed Code:{chr(10)}{c.fixed_code}" for c in fix_proposal.changes)}

## Requirements
- Use pytest framework
- Test both original behavior and fixed behavior
- Include edge cases
- Use clear test names

Provide only the test code, no explanations."""

            test_code = self.llm_client.generate(
                messages=[{"role": "user", "content": prompt}],
                system="You are an expert at writing comprehensive pytest test cases.",
                temperature=0.3,
            )

            # Extract code from markdown if needed
            import re

            code_match = re.search(r"```python\s*(.*?)\s*```", test_code, re.DOTALL)
            if code_match:
                test_code = code_match.group(1)

            # Validate test syntax using Python handler (tests are always Python)
            handler = get_handler("test.py")
            if handler.validate_syntax(test_code):
                return test_code
            else:
                logger.warning("Generated test code has syntax errors")
                return None

        except Exception as e:
            logger.error(f"Failed to generate tests: {e}")
            return None
