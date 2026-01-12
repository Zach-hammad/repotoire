"""AI Narrative Generation Service.

This module provides natural language narrative generation for code health data.
It uses the LLM client to transform raw health metrics into human-readable stories.

Key capabilities:
- Generate executive summaries of repository health
- Create contextual insights for specific metrics
- Produce weekly changelog narratives
- Stream narrative generation for real-time UX
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import AsyncGenerator, List, Optional, Dict, Any

from repotoire.ai.llm import LLMClient, LLMConfig, create_llm_client
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class HealthContext:
    """Context data for narrative generation."""

    score: int
    grade: str
    structure_score: int
    quality_score: int
    architecture_score: int
    issues_score: Optional[int] = None
    previous_score: Optional[int] = None
    score_trend: Optional[str] = None  # 'up', 'down', 'stable'
    findings_count: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    files_analyzed: int = 0
    repo_name: Optional[str] = None
    analysis_date: Optional[datetime] = None


@dataclass
class WeeklyContext:
    """Context data for weekly narrative generation."""

    current_health: HealthContext
    previous_health: Optional[HealthContext] = None
    new_findings: List[Dict[str, Any]] = None
    resolved_findings: List[Dict[str, Any]] = None
    top_hotspots: List[Dict[str, Any]] = None
    files_changed: int = 0
    commits_count: int = 0
    week_start: Optional[datetime] = None
    week_end: Optional[datetime] = None


@dataclass
class NarrativeResult:
    """Result of a narrative generation."""

    text: str
    tokens_used: int = 0
    model: str = ""
    generated_at: datetime = None

    def __post_init__(self):
        if self.generated_at is None:
            self.generated_at = datetime.utcnow()


# System prompts for different narrative types
SUMMARY_SYSTEM_PROMPT = """You are an expert code health analyst providing executive summaries.
Write concise, actionable summaries that:
- Lead with the most important insight
- Use natural, conversational language
- Avoid jargon where possible
- Focus on trends and patterns, not just numbers
- Provide specific, actionable recommendations
- Keep responses under 200 words

Format your response as a single paragraph of natural prose, not bullet points."""

INSIGHT_SYSTEM_PROMPT = """You are a helpful code health assistant providing quick insights.
Generate a single, focused insight about the specific metric or data point.
Keep responses to 1-2 sentences maximum.
Be specific and actionable.
Use conversational language."""

WEEKLY_SYSTEM_PROMPT = """You are a technical writer creating weekly code health reports.
Write a narrative changelog that:
- Summarizes the week's health changes in plain language
- Highlights significant improvements or regressions
- Calls out any new critical issues
- Celebrates wins and improvements
- Provides context for trends
- Ends with clear next steps

Structure as 2-3 short paragraphs. Use "we" to include the reader.
Keep the total under 300 words."""

HOVER_SYSTEM_PROMPT = """You are a helpful code assistant providing brief explanations.
Generate a single tooltip-style explanation for the given metric.
Keep it to one short sentence. Be helpful and clear."""


class NarrativeGenerator:
    """Generates natural language narratives from code health data.

    Uses LLM to transform raw metrics into human-readable stories and insights.

    Example:
        >>> generator = NarrativeGenerator()
        >>> context = HealthContext(score=78, grade="C", ...)
        >>> result = await generator.generate_summary(context)
        >>> print(result.text)
        "Your codebase health is fair at 78%. The main areas..."
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        backend: str = "openai",
        model: Optional[str] = None,
    ):
        """Initialize the narrative generator.

        Args:
            llm_client: Pre-configured LLM client (creates one if not provided)
            backend: LLM backend to use ('openai' or 'anthropic')
            model: Specific model to use (uses backend default if not provided)
        """
        if llm_client:
            self._llm = llm_client
        else:
            # Use a fast/cheap model for narrative generation
            config = LLMConfig(
                backend=backend,
                model=model or ("gpt-4o-mini" if backend == "openai" else "claude-3-5-haiku-20241022"),
                max_tokens=512,
                temperature=0.7,  # Slightly creative for narratives
            )
            self._llm = LLMClient(config)

    async def generate_summary(self, context: HealthContext) -> NarrativeResult:
        """Generate an executive summary of the health score.

        Args:
            context: Health metrics context

        Returns:
            NarrativeResult with the generated summary
        """
        prompt = self._build_summary_prompt(context)

        try:
            response = await self._llm.agenerate(
                messages=[{"role": "user", "content": prompt}],
                system=SUMMARY_SYSTEM_PROMPT,
            )

            return NarrativeResult(
                text=response.strip(),
                model=self._llm.model,
            )

        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            # Return a graceful fallback
            return NarrativeResult(
                text=self._generate_fallback_summary(context),
                model="fallback",
            )

    async def generate_insight(
        self,
        metric_name: str,
        metric_value: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> NarrativeResult:
        """Generate a quick insight for a specific metric.

        Args:
            metric_name: Name of the metric (e.g., 'structure_score', 'critical_findings')
            metric_value: The metric value
            context: Optional additional context

        Returns:
            NarrativeResult with the generated insight
        """
        prompt = self._build_insight_prompt(metric_name, metric_value, context)

        try:
            response = await self._llm.agenerate(
                messages=[{"role": "user", "content": prompt}],
                system=INSIGHT_SYSTEM_PROMPT,
                max_tokens=100,
            )

            return NarrativeResult(
                text=response.strip(),
                model=self._llm.model,
            )

        except Exception as e:
            logger.error(f"Error generating insight: {e}")
            return NarrativeResult(
                text=f"{metric_name}: {metric_value}",
                model="fallback",
            )

    async def generate_weekly_narrative(self, context: WeeklyContext) -> NarrativeResult:
        """Generate a weekly health changelog narrative.

        Args:
            context: Weekly health context with comparisons

        Returns:
            NarrativeResult with the generated narrative
        """
        prompt = self._build_weekly_prompt(context)

        try:
            response = await self._llm.agenerate(
                messages=[{"role": "user", "content": prompt}],
                system=WEEKLY_SYSTEM_PROMPT,
            )

            return NarrativeResult(
                text=response.strip(),
                model=self._llm.model,
            )

        except Exception as e:
            logger.error(f"Error generating weekly narrative: {e}")
            return NarrativeResult(
                text=self._generate_fallback_weekly(context),
                model="fallback",
            )

    async def generate_hover_insight(
        self,
        element_type: str,
        element_data: Dict[str, Any],
    ) -> NarrativeResult:
        """Generate a hover tooltip insight.

        Args:
            element_type: Type of element being hovered (e.g., 'severity_badge', 'health_score')
            element_data: Data about the element

        Returns:
            NarrativeResult with the tooltip text
        """
        prompt = f"Element type: {element_type}\nData: {json.dumps(element_data)}\nGenerate a brief, helpful tooltip explanation."

        try:
            response = await self._llm.agenerate(
                messages=[{"role": "user", "content": prompt}],
                system=HOVER_SYSTEM_PROMPT,
                max_tokens=50,
            )

            return NarrativeResult(
                text=response.strip(),
                model=self._llm.model,
            )

        except Exception as e:
            logger.error(f"Error generating hover insight: {e}")
            return NarrativeResult(
                text="",
                model="fallback",
            )

    async def stream_summary(
        self,
        context: HealthContext,
    ) -> AsyncGenerator[str, None]:
        """Stream the summary generation for real-time UX.

        Note: This is a placeholder for streaming support.
        Currently yields the full response in chunks to simulate streaming.
        """
        result = await self.generate_summary(context)

        # Simulate streaming by yielding in chunks
        words = result.text.split()
        chunk_size = 3

        for i in range(0, len(words), chunk_size):
            chunk = " ".join(words[i : i + chunk_size])
            if i > 0:
                chunk = " " + chunk
            yield chunk

    def _build_summary_prompt(self, context: HealthContext) -> str:
        """Build the prompt for summary generation."""
        trend_text = ""
        if context.previous_score is not None:
            diff = context.score - context.previous_score
            if diff > 0:
                trend_text = f"This is a {diff} point improvement from the previous analysis."
            elif diff < 0:
                trend_text = f"This is a {abs(diff)} point decline from the previous analysis."
            else:
                trend_text = "The score is unchanged from the previous analysis."

        prompt = f"""Generate an executive summary for this code health analysis:

Repository: {context.repo_name or 'Unknown'}
Overall Health Score: {context.score}/100 (Grade: {context.grade})
{trend_text}

Category Scores:
- Structure: {context.structure_score}/100
- Quality: {context.quality_score}/100
- Architecture: {context.architecture_score}/100
{"- Issues Impact: " + str(context.issues_score) + "/100" if context.issues_score else ""}

Findings Summary:
- Critical: {context.critical_count}
- High: {context.high_count}
- Medium: {context.medium_count}
- Low: {context.low_count}
- Total: {context.findings_count}

Files Analyzed: {context.files_analyzed}
Analysis Date: {context.analysis_date or 'Today'}

Write a natural, conversational summary focusing on the most important insights and actionable recommendations."""

        return prompt

    def _build_insight_prompt(
        self,
        metric_name: str,
        metric_value: Any,
        context: Optional[Dict[str, Any]],
    ) -> str:
        """Build the prompt for insight generation."""
        context_str = ""
        if context:
            context_str = f"\nAdditional context: {json.dumps(context)}"

        return f"""Generate a brief insight for this metric:

Metric: {metric_name}
Value: {metric_value}
{context_str}

Provide one actionable insight in 1-2 sentences."""

    def _build_weekly_prompt(self, context: WeeklyContext) -> str:
        """Build the prompt for weekly narrative generation."""
        health = context.current_health

        # Calculate changes
        score_change = ""
        if context.previous_health:
            diff = health.score - context.previous_health.score
            score_change = f"Score changed by {diff:+d} points (from {context.previous_health.score} to {health.score})."

        new_findings_text = ""
        if context.new_findings:
            new_findings_text = f"New findings this week: {len(context.new_findings)}"

        resolved_text = ""
        if context.resolved_findings:
            resolved_text = f"Resolved findings: {len(context.resolved_findings)}"

        hotspots_text = ""
        if context.top_hotspots:
            files = [h.get("file_path", "unknown") for h in context.top_hotspots[:3]]
            hotspots_text = f"Top hotspots: {', '.join(files)}"

        return f"""Generate a weekly code health narrative:

Week: {context.week_start} to {context.week_end}
{score_change}

Current Health:
- Score: {health.score}/100 (Grade: {health.grade})
- Structure: {health.structure_score}
- Quality: {health.quality_score}
- Architecture: {health.architecture_score}

Activity:
- Files changed: {context.files_changed}
- Commits: {context.commits_count}
{new_findings_text}
{resolved_text}
{hotspots_text}

Write a narrative summary of the week's code health changes."""

    def _generate_fallback_summary(self, context: HealthContext) -> str:
        """Generate a fallback summary without LLM."""
        grade_desc = {
            "A": "excellent",
            "B": "good",
            "C": "fair",
            "D": "poor",
            "F": "critical",
        }.get(context.grade, "unknown")

        return (
            f"Your codebase health is {grade_desc} with a score of {context.score}%. "
            f"The analysis found {context.findings_count} issues across {context.files_analyzed} files. "
            f"Focus on addressing the {context.critical_count} critical and {context.high_count} high severity issues first."
        )

    def _generate_fallback_weekly(self, context: WeeklyContext) -> str:
        """Generate a fallback weekly narrative without LLM."""
        health = context.current_health
        return (
            f"This week's code health score is {health.score}% (Grade: {health.grade}). "
            f"We analyzed {health.files_analyzed} files and found {health.findings_count} total issues. "
            f"Continue focusing on reducing critical and high severity findings."
        )


def create_narrative_generator(
    backend: str = "openai",
    model: Optional[str] = None,
) -> NarrativeGenerator:
    """Factory function to create a NarrativeGenerator.

    Args:
        backend: LLM backend ('openai' or 'anthropic')
        model: Specific model override

    Returns:
        Configured NarrativeGenerator instance
    """
    return NarrativeGenerator(backend=backend, model=model)
