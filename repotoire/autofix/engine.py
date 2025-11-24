"""Core auto-fix engine for generating code fixes."""

import ast
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from openai import OpenAI

from repotoire.logging_config import get_logger
from repotoire.models import Finding, Severity
from repotoire.ai.retrieval import GraphRAGRetriever
from repotoire.ai.embeddings import CodeEmbedder
from repotoire.graph import Neo4jClient
from repotoire.autofix.models import (
    FixProposal,
    FixContext,
    CodeChange,
    Evidence,
    FixType,
    FixConfidence,
    FixStatus,
)

logger = get_logger(__name__)


class AutoFixEngine:
    """Generate and validate automatic code fixes."""

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        openai_api_key: Optional[str] = None,
        model: str = "gpt-4o",
    ):
        """Initialize auto-fix engine.

        Args:
            neo4j_client: Neo4j client for RAG context
            openai_api_key: OpenAI API key (or use OPENAI_API_KEY env var)
            model: OpenAI model to use for fix generation
        """
        self.neo4j_client = neo4j_client
        self.model = model

        # Initialize OpenAI client
        api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY environment variable or openai_api_key parameter required"
            )
        self.client = OpenAI(api_key=api_key)

        # Initialize RAG retriever for context gathering
        embedder = CodeEmbedder(api_key=api_key)
        self.rag_retriever = GraphRAGRetriever(neo4j_client, embedder)

        logger.info(f"AutoFixEngine initialized with model={model}")

    async def generate_fix(
        self,
        finding: Finding,
        repository_path: Path,
        context_size: int = 5,
    ) -> Optional[FixProposal]:
        """Generate a fix proposal for a finding.

        Args:
            finding: The code smell or issue to fix
            repository_path: Path to the repository
            context_size: Number of related code snippets to gather

        Returns:
            FixProposal if fix can be generated, None otherwise
        """
        try:
            # Step 1: Gather context using RAG
            logger.info(f"Gathering context for finding: {finding.title}")
            context = await self._gather_context(finding, repository_path, context_size)

            # Step 2: Determine fix type from finding
            fix_type = self._determine_fix_type(finding)

            # Step 3: Generate fix using GPT-4
            logger.info(f"Generating {fix_type.value} fix using {self.model}")
            fix_proposal = await self._generate_fix_with_llm(finding, context, fix_type)

            # Step 4: Validate the fix
            logger.info("Validating generated fix")
            is_valid = self._validate_fix(fix_proposal, repository_path)
            fix_proposal.syntax_valid = is_valid

            # Step 5: Optionally generate tests
            if is_valid and fix_type in [FixType.REFACTOR, FixType.EXTRACT]:
                logger.info("Generating tests for fix")
                test_code = await self._generate_tests(fix_proposal, context)
                if test_code:
                    fix_proposal.test_code = test_code
                    fix_proposal.tests_generated = True

            logger.info(
                f"Fix generated with confidence={fix_proposal.confidence.value}"
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

            # Extract imports from file
            if context.file_content:
                context.imports = self._extract_imports(context.file_content)

        except Exception as e:
            logger.warning(f"Failed to gather full context: {e}")

        return context

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
    ) -> FixProposal:
        """Generate fix using LLM.

        Args:
            finding: The finding to fix
            context: Context for fix generation
            fix_type: Type of fix to generate

        Returns:
            FixProposal with generated changes
        """
        # Build prompt
        prompt = self._build_fix_prompt(finding, context, fix_type)

        # Call GPT-4
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert Python developer specializing in code refactoring and quality improvements.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,  # Lower temperature for more consistent code generation
        )

        # Parse response
        fix_data = self._parse_llm_response(response.choices[0].message.content)

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
    ) -> str:
        """Build prompt for LLM fix generation.

        Args:
            finding: The finding to fix
            context: Context for fix
            fix_type: Type of fix

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

        prompt = f"""# Code Fix Task

## Issue Details
- **Title**: {finding.title}
- **Severity**: {finding.severity.value}
- **Description**: {finding.description or 'No description'}
- **File**: {file_path}
- **Line**: {finding.line_start or 'unknown'}

## Fix Type Required
{fix_type.value}

## Current Code
```python
{code_section}
```

## Related Code Context
{chr(10).join(f"```python{chr(10)}{code}{chr(10)}```" for code in context.related_code[:3])}

## Task
Generate a fix for this issue. Provide your response in the following JSON format:

{{
    "title": "Short fix title (max 100 chars)",
    "description": "Detailed explanation of the fix",
    "rationale": "Why this fix addresses the issue",
    "evidence": {{
        "similar_patterns": ["Example 1 from codebase showing this pattern works", "Example 2..."],
        "documentation_refs": ["PEP 8: ...", "Python docs: ...", "Best practice: ..."],
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
- Follow Python best practices
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

        Args:
            fix_proposal: The fix to validate
            repository_path: Path to repository

        Returns:
            True if fix is valid, False otherwise
        """
        import textwrap

        try:
            for change in fix_proposal.changes:
                # Check syntax of fixed code
                try:
                    # Dedent to handle indented code snippets
                    dedented_code = textwrap.dedent(change.fixed_code)
                    ast.parse(dedented_code)
                except SyntaxError as e:
                    logger.warning(f"Syntax error in fix: {e}")
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

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at writing comprehensive pytest test cases.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )

            test_code = response.choices[0].message.content

            # Extract code from markdown if needed
            import re

            code_match = re.search(r"```python\s*(.*?)\s*```", test_code, re.DOTALL)
            if code_match:
                test_code = code_match.group(1)

            # Validate test syntax
            try:
                ast.parse(test_code)
                return test_code
            except SyntaxError:
                logger.warning("Generated test code has syntax errors")
                return None

        except Exception as e:
            logger.error(f"Failed to generate tests: {e}")
            return None

    def _extract_imports(self, file_content: str) -> List[str]:
        """Extract import statements from Python code.

        Args:
            file_content: Python source code

        Returns:
            List of import statements
        """
        imports = []
        try:
            tree = ast.parse(file_content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(f"import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for alias in node.names:
                        imports.append(f"from {module} import {alias.name}")
        except:
            pass

        return imports
