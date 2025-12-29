"""Contrastive learning for code embeddings.

Uses sentence-transformers' built-in contrastive losses to fine-tune
embeddings on code-specific positive pairs.

Positive pair strategies:
1. Code-Docstring: (function_source, docstring) - semantic alignment
2. Same-Class: (func1, func2) from same class - structural relatedness
3. Caller-Callee: (caller, callee) - call graph proximity

Uses MultipleNegativesRankingLoss (InfoNCE with in-batch negatives) for
efficient training without explicit negative mining.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ContrastiveConfig:
    """Configuration for contrastive fine-tuning."""

    base_model: str = "all-MiniLM-L6-v2"
    epochs: int = 3
    batch_size: int = 32
    warmup_ratio: float = 0.1
    learning_rate: float = 2e-5
    output_path: Optional[str] = None
    # Pair generation limits
    max_code_docstring_pairs: int = 5000
    max_same_class_pairs: int = 2000
    max_caller_callee_pairs: int = 2000


class ContrastivePairGenerator:
    """Generate positive pairs from code graph for contrastive learning.

    Extracts semantically related code pairs from FalkorDB/Neo4j knowledge graph:
    - Signature-docstring pairs (semantic alignment)
    - Same-class method pairs (structural relatedness)
    - Caller-callee pairs (call graph proximity)

    Note: Since source_code is not stored in the graph, we generate rich
    text representations from function signatures and metadata.
    """

    def __init__(self, client: Any, repo_path: Optional[Path] = None):
        """Initialize pair generator.

        Args:
            client: Database client (FalkorDB or Neo4j) for querying the graph
            repo_path: Optional path to repository for reading source files
        """
        self.client = client
        self.repo_path = repo_path

    def _build_function_signature(self, func: Dict[str, Any]) -> str:
        """Build a text representation of a function from its properties.

        Args:
            func: Function properties from graph query

        Returns:
            Text representation of the function signature
        """
        parts = []

        # Add decorators
        decorators = func.get("decorators") or []
        for dec in decorators:
            parts.append(f"@{dec}")

        # Build signature
        name = func.get("name", "unknown")
        params = func.get("parameters") or []
        return_type = func.get("return_type")

        if func.get("is_async"):
            sig = f"async def {name}({', '.join(params)})"
        else:
            sig = f"def {name}({', '.join(params)})"

        if return_type:
            sig += f" -> {return_type}"

        parts.append(sig)

        # Add qualified name for context
        qname = func.get("qualifiedName", "")
        if qname:
            parts.append(f"# {qname}")

        return "\n".join(parts)

    def generate_code_docstring_pairs(
        self, limit: int = 5000
    ) -> List[Tuple[str, str]]:
        """Generate (signature, docstring) positive pairs.

        Functions with docstrings provide natural positive pairs where
        the signature and its documentation should have similar embeddings.

        Args:
            limit: Maximum number of pairs to generate

        Returns:
            List of (signature_text, docstring) tuples
        """
        query = """
        MATCH (f:Function)
        WHERE f.docstring IS NOT NULL AND f.docstring <> ''
          AND size(f.docstring) > 10
        RETURN f.name AS name,
               f.qualifiedName AS qualifiedName,
               f.parameters AS parameters,
               f.return_type AS return_type,
               f.is_async AS is_async,
               f.decorators AS decorators,
               f.docstring AS docstring
        LIMIT $limit
        """
        results = self.client.execute_query(query, {"limit": limit})
        pairs = []
        for r in results:
            signature = self._build_function_signature(r)
            docstring = r.get("docstring", "")
            if signature and docstring:
                pairs.append((signature, docstring))
        logger.info(f"Generated {len(pairs)} signature-docstring pairs")
        return pairs

    def generate_same_class_pairs(self, limit: int = 2000) -> List[Tuple[str, str]]:
        """Generate (func1, func2) pairs from same class.

        Methods within the same class are structurally related and should
        have similar embeddings in the latent space.

        Args:
            limit: Maximum number of pairs to generate

        Returns:
            List of (signature1, signature2) tuples
        """
        query = """
        MATCH (c:Class)-[:CONTAINS]->(f1:Function)
        MATCH (c)-[:CONTAINS]->(f2:Function)
        WHERE f1.qualifiedName < f2.qualifiedName
        RETURN f1.name AS name1, f1.qualifiedName AS qname1,
               f1.parameters AS params1, f1.return_type AS ret1,
               f1.is_async AS async1, f1.decorators AS dec1,
               f2.name AS name2, f2.qualifiedName AS qname2,
               f2.parameters AS params2, f2.return_type AS ret2,
               f2.is_async AS async2, f2.decorators AS dec2
        LIMIT $limit
        """
        results = self.client.execute_query(query, {"limit": limit})
        pairs = []
        for r in results:
            sig1 = self._build_function_signature({
                "name": r.get("name1"),
                "qualifiedName": r.get("qname1"),
                "parameters": r.get("params1"),
                "return_type": r.get("ret1"),
                "is_async": r.get("async1"),
                "decorators": r.get("dec1"),
            })
            sig2 = self._build_function_signature({
                "name": r.get("name2"),
                "qualifiedName": r.get("qname2"),
                "parameters": r.get("params2"),
                "return_type": r.get("ret2"),
                "is_async": r.get("async2"),
                "decorators": r.get("dec2"),
            })
            if sig1 and sig2:
                pairs.append((sig1, sig2))
        logger.info(f"Generated {len(pairs)} same-class pairs")
        return pairs

    def generate_caller_callee_pairs(
        self, limit: int = 2000
    ) -> List[Tuple[str, str]]:
        """Generate (caller, callee) pairs from call graph.

        Functions that call each other are semantically related and should
        have similar embeddings.

        Args:
            limit: Maximum number of pairs to generate

        Returns:
            List of (caller_signature, callee_signature) tuples
        """
        query = """
        MATCH (caller:Function)-[:CALLS]->(callee:Function)
        WHERE callee.external IS NULL OR callee.external = false
        RETURN caller.name AS caller_name, caller.qualifiedName AS caller_qname,
               caller.parameters AS caller_params, caller.return_type AS caller_ret,
               caller.is_async AS caller_async, caller.decorators AS caller_dec,
               callee.name AS callee_name, callee.qualifiedName AS callee_qname,
               callee.parameters AS callee_params, callee.return_type AS callee_ret,
               callee.is_async AS callee_async, callee.decorators AS callee_dec
        LIMIT $limit
        """
        results = self.client.execute_query(query, {"limit": limit})
        pairs = []
        for r in results:
            caller_sig = self._build_function_signature({
                "name": r.get("caller_name"),
                "qualifiedName": r.get("caller_qname"),
                "parameters": r.get("caller_params"),
                "return_type": r.get("caller_ret"),
                "is_async": r.get("caller_async"),
                "decorators": r.get("caller_dec"),
            })
            callee_sig = self._build_function_signature({
                "name": r.get("callee_name"),
                "qualifiedName": r.get("callee_qname"),
                "parameters": r.get("callee_params"),
                "return_type": r.get("callee_ret"),
                "is_async": r.get("callee_async"),
                "decorators": r.get("callee_dec"),
            })
            if caller_sig and callee_sig:
                pairs.append((caller_sig, callee_sig))
        logger.info(f"Generated {len(pairs)} caller-callee pairs")
        return pairs

    def generate_all_pairs(
        self, config: Optional[ContrastiveConfig] = None
    ) -> List[Tuple[str, str]]:
        """Generate all positive pairs using configured limits.

        Args:
            config: Configuration with limits for each pair type

        Returns:
            Combined list of all positive pairs
        """
        config = config or ContrastiveConfig()
        all_pairs: List[Tuple[str, str]] = []

        # Generate each type of pair
        code_doc_pairs = self.generate_code_docstring_pairs(
            limit=config.max_code_docstring_pairs
        )
        all_pairs.extend(code_doc_pairs)

        same_class_pairs = self.generate_same_class_pairs(
            limit=config.max_same_class_pairs
        )
        all_pairs.extend(same_class_pairs)

        caller_callee_pairs = self.generate_caller_callee_pairs(
            limit=config.max_caller_callee_pairs
        )
        all_pairs.extend(caller_callee_pairs)

        logger.info(f"Generated {len(all_pairs)} total positive pairs")
        return all_pairs


class ContrastiveTrainer:
    """Fine-tune embeddings using contrastive learning.

    Uses sentence-transformers' built-in losses:
    - MultipleNegativesRankingLoss: InfoNCE with in-batch negatives (default)
    - Efficient: no explicit negative mining needed
    - Scales well with batch size (more negatives = better gradients)
    """

    def __init__(self, config: Optional[ContrastiveConfig] = None):
        """Initialize trainer with configuration.

        Args:
            config: Training configuration (uses defaults if not provided)
        """
        self.config = config or ContrastiveConfig()
        self._model = None
        self._initialized = False

    def _init_model(self) -> None:
        """Lazily initialize the sentence transformer model."""
        if self._initialized:
            return

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers required for contrastive learning. "
                "Install with: pip install sentence-transformers"
            )

        logger.info(f"Loading base model: {self.config.base_model}")
        self._model = SentenceTransformer(self.config.base_model)
        self._initialized = True

    @property
    def model(self):
        """Get the sentence transformer model (lazy initialization)."""
        self._init_model()
        return self._model

    def train(
        self,
        pairs: List[Tuple[str, str]],
        output_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Fine-tune model on positive pairs using MultipleNegativesRankingLoss.

        The loss treats all other pairs in the batch as negatives (in-batch
        negative sampling). This is equivalent to InfoNCE/NT-Xent loss.

        Args:
            pairs: List of (text_a, text_b) positive pairs
            output_path: Where to save the fine-tuned model

        Returns:
            Training statistics dict
        """
        if not pairs:
            raise ValueError("No training pairs provided")

        try:
            from sentence_transformers import InputExample, losses
            from torch.utils.data import DataLoader
        except ImportError:
            raise ImportError(
                "sentence-transformers required. "
                "Install with: pip install sentence-transformers"
            )

        # Initialize model
        self._init_model()

        # Convert pairs to InputExamples
        train_examples = [InputExample(texts=[a, b]) for a, b in pairs]

        # Create DataLoader
        train_dataloader = DataLoader(
            train_examples, shuffle=True, batch_size=self.config.batch_size
        )

        # Built-in contrastive loss (in-batch negatives, equivalent to InfoNCE)
        train_loss = losses.MultipleNegativesRankingLoss(self._model)

        # Calculate warmup steps
        total_steps = len(train_dataloader) * self.config.epochs
        warmup_steps = int(total_steps * self.config.warmup_ratio)

        logger.info(
            f"Training with {len(pairs)} pairs, "
            f"{self.config.epochs} epochs, "
            f"batch_size={self.config.batch_size}, "
            f"warmup_steps={warmup_steps}"
        )

        # Determine output path
        save_path = output_path or (
            Path(self.config.output_path) if self.config.output_path else None
        )

        # Train the model
        self._model.fit(
            train_objectives=[(train_dataloader, train_loss)],
            epochs=self.config.epochs,
            warmup_steps=warmup_steps,
            output_path=str(save_path) if save_path else None,
            show_progress_bar=True,
        )

        stats = {
            "pairs": len(pairs),
            "epochs": self.config.epochs,
            "batch_size": self.config.batch_size,
            "warmup_steps": warmup_steps,
            "total_steps": total_steps,
            "base_model": self.config.base_model,
            "output": str(save_path) if save_path else None,
        }

        logger.info(f"Training complete. Stats: {stats}")
        return stats

    def save(self, output_path: Path) -> None:
        """Save the fine-tuned model.

        Args:
            output_path: Directory to save the model
        """
        self._init_model()
        output_path.mkdir(parents=True, exist_ok=True)
        self._model.save(str(output_path))
        logger.info(f"Model saved to {output_path}")

    def encode(self, texts: List[str]) -> List[List[float]]:
        """Encode texts using the fine-tuned model.

        Args:
            texts: List of texts to encode

        Returns:
            List of embedding vectors
        """
        self._init_model()
        embeddings = self._model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()


def fine_tune_from_graph(
    client: Any,
    output_path: Path,
    config: Optional[ContrastiveConfig] = None,
) -> Dict[str, Any]:
    """Convenience function to fine-tune embeddings from a code graph.

    Args:
        client: Neo4jClient for querying the knowledge graph
        output_path: Where to save the fine-tuned model
        config: Training configuration

    Returns:
        Training statistics
    """
    config = config or ContrastiveConfig()

    # Generate pairs from graph
    generator = ContrastivePairGenerator(client)
    pairs = generator.generate_all_pairs(config)

    if not pairs:
        raise ValueError(
            "No training pairs found in graph. "
            "Ensure codebase is ingested with source_code and docstrings."
        )

    # Train model
    trainer = ContrastiveTrainer(config)
    stats = trainer.train(pairs, output_path)

    return stats
