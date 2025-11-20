"""spaCy-based semantic clue generator (MVP)."""

from typing import List, Optional, Dict
from datetime import datetime
import re

from repotoire.models import Entity, ClueEntity, FunctionEntity, ClassEntity, FileEntity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class SpacyClueGenerator:
    """Generate semantic clues using spaCy NLP (local, no API calls).

    This is the MVP version that works without external APIs.
    It uses spaCy for:
    - Keyword extraction from docstrings
    - Named entity recognition
    - Noun phrase extraction
    - Basic summarization from docstrings

    For better quality, use GPT-based generator (requires OpenAI API).
    """

    def __init__(self, model_name: str = "en_core_web_sm"):
        """Initialize spaCy clue generator.

        Args:
            model_name: spaCy model to use (default: en_core_web_sm)
                       For better results, use en_core_web_lg
        """
        self.model_name = model_name
        self.nlp = None  # Lazy-load spaCy
        logger.info(f"SpacyClueGenerator initialized (model: {model_name})")

    def _load_spacy(self):
        """Lazy-load spaCy model on first use."""
        if self.nlp is not None:
            return

        try:
            import spacy
            self.nlp = spacy.load(self.model_name)
            logger.debug(f"Loaded spaCy model: {self.model_name}")
        except Exception as e:
            logger.warning(
                f"Could not load spaCy model '{self.model_name}': {e}\n"
                f"Install with: python -m spacy download {self.model_name}"
            )
            # Create a basic NLP with minimal components for testing
            import spacy
            self.nlp = spacy.blank("en")
            # Add minimal components for basic functionality
            self.nlp.add_pipe("sentencizer")

    def generate_clues(self, entity: Entity) -> List[ClueEntity]:
        """Generate semantic clues for a code entity.

        Args:
            entity: Code entity (function, class, file, etc.)

        Returns:
            List of ClueEntity objects with extracted insights
        """
        self._load_spacy()

        clues = []

        # Generate purpose clue from docstring
        if entity.docstring:
            purpose_clue = self._generate_purpose_clue(entity)
            if purpose_clue:
                clues.append(purpose_clue)

        # Generate keyword clue
        keywords_clue = self._generate_keywords_clue(entity)
        if keywords_clue:
            clues.append(keywords_clue)

        # Entity-specific clues
        if isinstance(entity, FunctionEntity):
            complexity_clue = self._generate_complexity_clue(entity)
            if complexity_clue:
                clues.append(complexity_clue)

        elif isinstance(entity, ClassEntity):
            class_purpose_clue = self._generate_class_purpose_clue(entity)
            if class_purpose_clue:
                clues.append(class_purpose_clue)

        logger.debug(f"Generated {len(clues)} clues for {entity.qualified_name}")
        return clues

    def _generate_purpose_clue(self, entity: Entity) -> Optional[ClueEntity]:
        """Extract purpose from docstring using spaCy."""
        if not entity.docstring:
            return None

        doc = self.nlp(entity.docstring)

        # Extract first sentence as summary
        sentences = list(doc.sents)
        if not sentences:
            return None

        summary = str(sentences[0]).strip()
        detailed = entity.docstring if len(sentences) > 1 else None

        # Extract named entities for keywords
        keywords = [ent.text.lower() for ent in doc.ents if len(ent.text) > 2]

        # Extract noun chunks as additional keywords (if available)
        try:
            noun_chunks = [chunk.text.lower() for chunk in doc.noun_chunks if len(chunk.text) > 3]
            keywords.extend(noun_chunks[:5])  # Limit to top 5
        except ValueError:
            # Blank models don't have noun_chunks
            pass

        # Remove duplicates while preserving order
        keywords = list(dict.fromkeys(keywords))

        # Calculate confidence based on docstring quality
        confidence = self._calculate_docstring_confidence(entity.docstring)

        return ClueEntity(
            name=f"{entity.name}_purpose",
            qualified_name=f"clue::{entity.qualified_name}::purpose",
            file_path=entity.file_path,
            line_start=entity.line_start,
            line_end=entity.line_end,
            clue_type="purpose",
            summary=summary,
            detailed_explanation=detailed,
            confidence=confidence,
            generated_by="spacy",
            generated_at=datetime.utcnow(),
            keywords=keywords[:10],  # Top 10 keywords
            target_entity=entity.qualified_name,
        )

    def _generate_keywords_clue(self, entity: Entity) -> Optional[ClueEntity]:
        """Extract keywords from docstring and entity name."""
        text = entity.docstring or entity.name

        doc = self.nlp(text)

        # Extract keywords from different sources
        keywords = []

        # 1. Named entities
        keywords.extend([ent.text.lower() for ent in doc.ents])

        # 2. Noun chunks (if available)
        try:
            keywords.extend([chunk.root.text.lower() for chunk in doc.noun_chunks])
        except ValueError:
            # Blank models don't have noun_chunks
            pass

        # 3. Important tokens (nouns, proper nouns, verbs)
        important_pos = {"NOUN", "PROPN", "VERB"}
        keywords.extend([
            token.lemma_.lower()
            for token in doc
            if token.pos_ in important_pos and not token.is_stop and len(token.text) > 2
        ])

        # Remove duplicates and filter
        keywords = [kw for kw in set(keywords) if len(kw) > 2 and kw.isalnum()]

        # If no keywords found, try simple word splitting as fallback
        if not keywords:
            # Split entity name on underscores/camelCase
            import re
            name_words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)', entity.name)
            name_words.extend(entity.name.split('_'))
            keywords = [w.lower() for w in name_words if len(w) > 2]
            keywords = list(set(keywords))  # Remove duplicates

        if not keywords:
            return None

        # Create summary from top keywords
        top_keywords = keywords[:5]
        summary = f"Key concepts: {', '.join(top_keywords)}"

        confidence = min(len(keywords) / 10.0, 1.0)  # More keywords = higher confidence

        return ClueEntity(
            name=f"{entity.name}_keywords",
            qualified_name=f"clue::{entity.qualified_name}::keywords",
            file_path=entity.file_path,
            line_start=entity.line_start,
            line_end=entity.line_end,
            clue_type="concept",
            summary=summary,
            confidence=confidence,
            generated_by="spacy",
            generated_at=datetime.utcnow(),
            keywords=keywords[:15],
            target_entity=entity.qualified_name,
        )

    def _generate_complexity_clue(self, entity: FunctionEntity) -> Optional[ClueEntity]:
        """Generate complexity insight for function."""
        complexity = getattr(entity, 'complexity', None)
        if complexity is None or complexity < 5:
            return None

        if complexity >= 15:
            level = "very high"
            suggestion = "Consider refactoring into smaller functions"
        elif complexity >= 10:
            level = "high"
            suggestion = "May benefit from simplification"
        else:
            level = "moderate"
            suggestion = "Complexity is manageable but monitor growth"

        summary = f"Cyclomatic complexity is {level} ({complexity})"

        return ClueEntity(
            name=f"{entity.name}_complexity",
            qualified_name=f"clue::{entity.qualified_name}::complexity",
            file_path=entity.file_path,
            line_start=entity.line_start,
            line_end=entity.line_end,
            clue_type="insight",
            summary=summary,
            detailed_explanation=suggestion,
            confidence=1.0,  # Complexity is objective
            generated_by="spacy",
            generated_at=datetime.utcnow(),
            keywords=["complexity", "refactoring", level],
            target_entity=entity.qualified_name,
        )

    def _generate_class_purpose_clue(self, entity: ClassEntity) -> Optional[ClueEntity]:
        """Generate purpose insight for class based on name and structure."""
        # Analyze class name patterns
        patterns = {
            r".*Manager$": "Manages and coordinates operations",
            r".*Service$": "Provides service functionality",
            r".*Factory$": "Creates instances of other objects",
            r".*Repository$": "Handles data persistence and retrieval",
            r".*Controller$": "Handles request routing and responses",
            r".*Adapter$": "Adapts one interface to another",
            r".*Builder$": "Constructs complex objects step by step",
            r".*Handler$": "Processes specific events or requests",
            r".*Client$": "Communicates with external services",
            r".*Parser$": "Parses and processes structured data",
        }

        detected_pattern = None
        for pattern, purpose in patterns.items():
            if re.match(pattern, entity.name):
                detected_pattern = purpose
                break

        if not detected_pattern:
            return None

        return ClueEntity(
            name=f"{entity.name}_pattern",
            qualified_name=f"clue::{entity.qualified_name}::pattern",
            file_path=entity.file_path,
            line_start=entity.line_start,
            line_end=entity.line_end,
            clue_type="pattern",
            summary=f"Follows design pattern: {detected_pattern}",
            confidence=0.7,  # Pattern matching has medium confidence
            generated_by="spacy",
            generated_at=datetime.utcnow(),
            keywords=["design-pattern", entity.name.lower()],
            target_entity=entity.qualified_name,
        )

    def _calculate_docstring_confidence(self, docstring: str) -> float:
        """Calculate confidence score based on docstring quality.

        Args:
            docstring: Docstring text

        Returns:
            Confidence score 0.0-1.0
        """
        if not docstring:
            return 0.0

        score = 0.0

        # Length score (0.0-0.4)
        length = len(docstring)
        if length > 200:
            score += 0.4
        elif length > 100:
            score += 0.3
        elif length > 50:
            score += 0.2
        else:
            score += 0.1

        # Structure score (0.0-0.3)
        has_sections = any(marker in docstring for marker in ["Args:", "Returns:", "Raises:", "Example:"])
        if has_sections:
            score += 0.3
        elif ":" in docstring:
            score += 0.1

        # Completeness score (0.0-0.3)
        num_sentences = len(list(self.nlp(docstring).sents))
        if num_sentences >= 3:
            score += 0.3
        elif num_sentences == 2:
            score += 0.2
        else:
            score += 0.1

        return min(score, 1.0)
