"""Contextual Retrieval for RAG using Claude.

Implements Anthropic's Contextual Retrieval technique which reduces failed
retrievals by 49-67% by adding semantic context to code entities before embedding.

The Problem: Code chunks embedded in isolation lose context:
    # This alone is ambiguous - what class? what file? what does it do?
    def calculate_score(self): return self.points / self.max_points

The Solution: Use Claude to add context before embedding:
    This method is in the Player class in game/entities.py.
    It calculates the player's score as a percentage of maximum possible points.
    def calculate_score(self): return self.points / self.max_points

Environment variables:
- ANTHROPIC_API_KEY: Required for context generation

Reference: https://www.anthropic.com/news/contextual-retrieval
"""

import asyncio
import os
from dataclasses import dataclass, field
from typing import Optional, List, Callable, TYPE_CHECKING

from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from repotoire.models import Entity

logger = get_logger(__name__)


class CostLimitExceeded(Exception):
    """Raised when context generation cost exceeds configured limit."""
    pass


@dataclass
class ContextualRetrievalConfig:
    """Configuration for contextual retrieval.

    Attributes:
        enabled: Whether contextual retrieval is enabled (disabled by default due to cost)
        model: Claude model for context generation (haiku is cheap and fast)
        max_concurrent: Maximum concurrent API calls for batch processing
        cache_contexts: Whether to store contexts in Neo4j for reuse
        track_costs: Whether to track API costs
        max_cost_usd: Maximum USD to spend on context generation (None = unlimited)
    """

    enabled: bool = False  # Disabled by default (adds cost)
    model: str = "claude-haiku-3-5-20241022"  # Cheap and fast
    max_concurrent: int = 10  # Batch API calls
    cache_contexts: bool = True  # Store in Neo4j
    track_costs: bool = True
    max_cost_usd: Optional[float] = None  # Stop if exceeded


class CostTracker:
    """Track API costs for context generation.

    Supports Claude Haiku and Sonnet models with accurate per-token pricing.

    Example:
        >>> tracker = CostTracker()
        >>> tracker.add(input_tokens=1_000_000, output_tokens=100_000, model="claude-haiku-3-5-20241022")
        >>> print(f"Total cost: ${tracker.total_cost:.4f}")
        Total cost: $0.3750

        >>> summary = tracker.summary()
        >>> print(summary)
        {'input_tokens': 1000000, 'output_tokens': 100000, 'total_cost_usd': 0.375, 'model': 'claude-haiku-3-5-20241022'}
    """

    # Claude pricing (per 1M tokens) as of December 2024
    PRICING = {
        # Haiku 3.5 - optimal for context generation
        "claude-haiku-3-5-20241022": {"input": 0.80, "output": 4.00},
        "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
        # Sonnet 4 - better quality but more expensive
        "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
        # Legacy models
        "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    }

    def __init__(self):
        """Initialize cost tracker."""
        self.input_tokens = 0
        self.output_tokens = 0
        self.model: Optional[str] = None

    def add(self, input_tokens: int, output_tokens: int, model: str) -> None:
        """Add token usage from an API call.

        Args:
            input_tokens: Number of input tokens used
            output_tokens: Number of output tokens generated
            model: Model name used for the call
        """
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.model = model

    @property
    def total_cost(self) -> float:
        """Calculate total cost in USD based on token usage.

        Returns:
            Total cost in USD, or 0.0 if model unknown
        """
        if not self.model or self.model not in self.PRICING:
            return 0.0

        pricing = self.PRICING[self.model]
        input_cost = (self.input_tokens / 1_000_000) * pricing["input"]
        output_cost = (self.output_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost

    def summary(self) -> dict:
        """Get summary of token usage and costs.

        Returns:
            Dictionary with token counts and total cost
        """
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_cost_usd": round(self.total_cost, 4),
            "model": self.model,
        }

    def reset(self) -> None:
        """Reset all counters."""
        self.input_tokens = 0
        self.output_tokens = 0
        self.model = None


class ContextGenerator:
    """Generates semantic context for code entities using Claude.

    Uses Claude to create brief context paragraphs that explain where code lives,
    what it does, and how it relates to the broader codebase. This context is
    prepended to code before embedding to improve retrieval accuracy.

    Example:
        >>> config = ContextualRetrievalConfig(enabled=True)
        >>> generator = ContextGenerator(config)
        >>> context = await generator.generate_context(function_entity)
        >>> print(context)
        This method is in the AuthHandler class in src/auth/handlers.py...

    Attributes:
        config: Configuration for context generation
    """

    CONTEXT_PROMPT = """You are a code documentation expert. Given a code entity and its metadata, write a brief context paragraph (2-3 sentences) that explains:
1. Where this code lives (file, class, module)
2. What it does at a high level
3. How it relates to the broader codebase

Be concise and factual. This context will be prepended to the code for semantic search.

<entity>
Type: {entity_type}
Name: {name}
File: {file_path}
Parent Class: {parent_class}
Docstring: {docstring}
Source Code:
{source_code}
</entity>

Write ONLY the context paragraph, nothing else."""

    def __init__(self, config: Optional[ContextualRetrievalConfig] = None):
        """Initialize context generator.

        Args:
            config: Configuration for context generation. Uses defaults if not provided.

        Raises:
            ValueError: If ANTHROPIC_API_KEY is not set
        """
        self.config = config or ContextualRetrievalConfig()
        self._client = self._init_client()
        self._cost_tracker = CostTracker() if self.config.track_costs else None

    def _init_client(self):
        """Initialize Anthropic client.

        Returns:
            Anthropic client instance

        Raises:
            ValueError: If ANTHROPIC_API_KEY is not set
        """
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "anthropic package required for contextual retrieval. "
                "Install with: pip install anthropic"
            )

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable required for contextual retrieval. "
                "Get your API key at https://console.anthropic.com"
            )
        return anthropic.Anthropic(api_key=api_key)

    def _get_parent_class(self, entity: "Entity") -> str:
        """Extract parent class name from entity qualified name.

        Args:
            entity: Entity to extract parent class from

        Returns:
            Parent class name or "N/A" if not a method
        """
        # qualified_name format: "module.py::ClassName.method_name" or "module.py::function"
        qn = entity.qualified_name
        if "::" in qn:
            after_file = qn.split("::")[-1]
            if "." in after_file:
                # Has parent class
                parts = after_file.split(".")
                if len(parts) >= 2:
                    return parts[-2]  # Class name is second to last
        return "N/A"

    def _get_source_code(self, entity: "Entity") -> str:
        """Get source code from entity metadata if available.

        Args:
            entity: Entity to get source code from

        Returns:
            Source code or empty string if not available
        """
        # Check various places source code might be stored
        if hasattr(entity, "source_code") and entity.source_code:
            return entity.source_code[:1000]  # Limit length
        if entity.metadata and "source_code" in entity.metadata:
            return str(entity.metadata["source_code"])[:1000]
        return ""

    async def generate_context(self, entity: "Entity") -> str:
        """Generate context for a single entity.

        Args:
            entity: Entity to generate context for

        Returns:
            Context paragraph describing the entity

        Raises:
            CostLimitExceeded: If cost limit has been reached
        """
        # Check cost limit before making request
        if self.config.max_cost_usd and self._cost_tracker:
            if self._cost_tracker.total_cost >= self.config.max_cost_usd:
                raise CostLimitExceeded(
                    f"Cost limit ${self.config.max_cost_usd} exceeded "
                    f"(current: ${self._cost_tracker.total_cost:.4f})"
                )

        # Build prompt from entity data
        entity_type = entity.node_type.value if entity.node_type else "unknown"
        prompt = self.CONTEXT_PROMPT.format(
            entity_type=entity_type,
            name=entity.name,
            file_path=entity.file_path,
            parent_class=self._get_parent_class(entity),
            docstring=entity.docstring or "None",
            source_code=self._get_source_code(entity),
        )

        # Make API call in thread pool to avoid blocking
        response = await asyncio.to_thread(
            self._client.messages.create,
            model=self.config.model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )

        context = response.content[0].text

        # Track cost
        if self._cost_tracker:
            self._cost_tracker.add(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model=self.config.model,
            )

        return context

    async def generate_contexts_batch(
        self,
        entities: List["Entity"],
        on_progress: Optional[Callable[[str, int], None]] = None,
    ) -> dict[str, str]:
        """Generate contexts for multiple entities with concurrency control.

        Uses a semaphore to limit concurrent API calls and prevent overwhelming
        the API. Stops early if cost limit is reached.

        Args:
            entities: List of entities to generate contexts for
            on_progress: Optional callback(qualified_name, completed_count) for progress updates

        Returns:
            Dictionary mapping qualified_name to generated context

        Raises:
            CostLimitExceeded: If cost limit is exceeded during processing
        """
        semaphore = asyncio.Semaphore(self.config.max_concurrent)
        results: dict[str, str] = {}
        errors: List[str] = []

        async def process_one(entity: "Entity") -> None:
            async with semaphore:
                # Check cost limit before each request
                if self.config.max_cost_usd and self._cost_tracker:
                    if self._cost_tracker.total_cost >= self.config.max_cost_usd:
                        raise CostLimitExceeded(
                            f"Cost limit ${self.config.max_cost_usd} exceeded"
                        )

                try:
                    context = await self.generate_context(entity)
                    results[entity.qualified_name] = context

                    if on_progress:
                        on_progress(entity.qualified_name, len(results))

                except CostLimitExceeded:
                    raise  # Re-raise to stop processing
                except Exception as e:
                    logger.warning(
                        f"Failed to generate context for {entity.qualified_name}: {e}"
                    )
                    errors.append(entity.qualified_name)

        try:
            await asyncio.gather(*[process_one(e) for e in entities])
        except CostLimitExceeded as e:
            logger.warning(f"Context generation stopped: {e}")
            # Return partial results

        if errors:
            logger.warning(f"Failed to generate context for {len(errors)} entities")

        return results

    def contextualize_text(self, entity: "Entity", context: str) -> str:
        """Combine context with entity data for embedding.

        Creates a rich text representation that includes:
        1. Generated semantic context
        2. Entity name
        3. Docstring (if available)
        4. Source code snippet (if available)

        Args:
            entity: Entity being contextualized
            context: Generated context paragraph

        Returns:
            Combined text ready for embedding
        """
        parts = [context]

        if entity.name:
            parts.append(f"Name: {entity.name}")
        if entity.docstring:
            parts.append(f"Docstring: {entity.docstring}")

        source_code = self._get_source_code(entity)
        if source_code:
            parts.append(f"Code:\n{source_code[:500]}")

        return "\n\n".join(parts)

    @property
    def cost_tracker(self) -> Optional[CostTracker]:
        """Get the cost tracker instance.

        Returns:
            CostTracker if cost tracking is enabled, None otherwise
        """
        return self._cost_tracker


@dataclass
class ContextGenerationResult:
    """Result of batch context generation.

    Attributes:
        contexts: Dictionary mapping qualified_name to generated context
        entities_processed: Number of entities successfully processed
        entities_failed: Number of entities that failed
        cost_summary: Cost tracking summary if available
    """

    contexts: dict[str, str] = field(default_factory=dict)
    entities_processed: int = 0
    entities_failed: int = 0
    cost_summary: Optional[dict] = None


def create_context_generator(
    enabled: bool = False,
    model: str = "claude-haiku-3-5-20241022",
    max_cost_usd: Optional[float] = None,
) -> Optional[ContextGenerator]:
    """Factory function to create a context generator.

    Args:
        enabled: Whether to enable context generation
        model: Claude model to use for generation
        max_cost_usd: Maximum cost limit in USD

    Returns:
        ContextGenerator if enabled and API key available, None otherwise
    """
    if not enabled:
        return None

    try:
        config = ContextualRetrievalConfig(
            enabled=True,
            model=model,
            max_cost_usd=max_cost_usd,
        )
        return ContextGenerator(config)
    except ValueError as e:
        logger.warning(f"Could not create context generator: {e}")
        return None
