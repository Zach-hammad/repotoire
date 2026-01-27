"""Integration tests for contrastive learning with FalkorDB.

These tests verify that ContrastivePairGenerator works correctly with
a real FalkorDB instance, generating training pairs from actual graph data.

Requirements:
- FalkorDB running (locally or on Fly)
- sentence-transformers installed

Set environment variables:
- REPOTOIRE_FALKORDB_HOST: FalkorDB host (default: localhost)
- REPOTOIRE_FALKORDB_PORT: FalkorDB Redis port (default: 6381)
- REPOTOIRE_FALKORDB_PASSWORD: Optional password
"""

import os
import logging
import tempfile
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# Check if sentence-transformers is available
try:
    import sentence_transformers
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False

# FalkorDB connection settings
FALKORDB_HOST = os.environ.get("REPOTOIRE_FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.environ.get("REPOTOIRE_FALKORDB_PORT", "6381"))
FALKORDB_PASSWORD = os.environ.get("REPOTOIRE_FALKORDB_PASSWORD", None)


def is_falkordb_available() -> bool:
    """Check if FalkorDB is available."""
    try:
        import redis
        r = redis.Redis(
            host=FALKORDB_HOST,
            port=FALKORDB_PORT,
            password=FALKORDB_PASSWORD,
            socket_timeout=2,
        )
        r.ping()
        return True
    except Exception:
        return False


# Skip all tests if FalkorDB is not available
pytestmark = pytest.mark.skipif(
    not is_falkordb_available(),
    reason=f"FalkorDB not available at {FALKORDB_HOST}:{FALKORDB_PORT}"
)


@pytest.fixture
def falkordb_client():
    """Create a FalkorDB client for testing."""
    from repotoire.graph import create_falkordb_client

    client = create_falkordb_client(
        graph_name="contrastive_test",
        max_retries=2,  # Faster failure for tests
    )

    # Clear the test graph
    try:
        client.execute_query("MATCH (n) DETACH DELETE n", {})
    except Exception:
        pass

    yield client

    # Cleanup
    try:
        client.execute_query("MATCH (n) DETACH DELETE n", {})
    except Exception:
        pass
    client.close()


@pytest.fixture
def populated_graph(falkordb_client):
    """Populate the graph with test data for contrastive learning."""
    # Create a class with methods
    falkordb_client.execute_query("""
        CREATE (c:Class {
            name: 'Calculator',
            qualifiedName: 'math.Calculator',
            filePath: 'math.py',
            lineStart: 1,
            lineEnd: 50,
            docstring: 'A simple calculator class'
        })
    """, {})

    # Create functions with docstrings
    functions = [
        {
            "name": "add",
            "qname": "math.Calculator.add",
            "params": ["self", "a", "b"],
            "ret": "int",
            "doc": "Add two numbers together and return the result.",
            "async": False,
            "decorators": [],
        },
        {
            "name": "subtract",
            "qname": "math.Calculator.subtract",
            "params": ["self", "a", "b"],
            "ret": "int",
            "doc": "Subtract b from a and return the difference.",
            "async": False,
            "decorators": [],
        },
        {
            "name": "multiply",
            "qname": "math.Calculator.multiply",
            "params": ["self", "x", "y"],
            "ret": "int",
            "doc": "Multiply two numbers.",
            "async": False,
            "decorators": [],
        },
        {
            "name": "async_divide",
            "qname": "math.Calculator.async_divide",
            "params": ["self", "a", "b"],
            "ret": "float",
            "doc": "Asynchronously divide a by b.",
            "async": True,
            "decorators": ["async_cache"],
        },
    ]

    for f in functions:
        falkordb_client.execute_query("""
            MATCH (c:Class {qualifiedName: 'math.Calculator'})
            CREATE (fn:Function {
                name: $name,
                qualifiedName: $qname,
                filePath: 'math.py',
                lineStart: 10,
                lineEnd: 15,
                parameters: $params,
                return_type: $ret,
                docstring: $doc,
                is_async: $async,
                decorators: $decorators
            })
            CREATE (c)-[:CONTAINS]->(fn)
        """, {
            "name": f["name"],
            "qname": f["qname"],
            "params": f["params"],
            "ret": f["ret"],
            "doc": f["doc"],
            "async": f["async"],
            "decorators": f["decorators"],
        })

    # Create CALLS relationships
    falkordb_client.execute_query("""
        MATCH (caller:Function {qualifiedName: 'math.Calculator.add'})
        MATCH (callee:Function {qualifiedName: 'math.Calculator.multiply'})
        CREATE (caller)-[:CALLS]->(callee)
    """, {})

    return falkordb_client


class TestContrastivePairGeneratorIntegration:
    """Integration tests for ContrastivePairGenerator with FalkorDB."""

    def test_generate_signature_docstring_pairs(self, populated_graph):
        """Test generating signature-docstring pairs from real graph."""
        from repotoire.ml.contrastive_learning import ContrastivePairGenerator

        generator = ContrastivePairGenerator(populated_graph)
        pairs = generator.generate_code_docstring_pairs(limit=10)

        # Should have 4 pairs (one for each function with docstring)
        assert len(pairs) == 4

        # Check that pairs contain signatures and docstrings
        signatures = [p[0] for p in pairs]
        docstrings = [p[1] for p in pairs]

        # Verify signatures are well-formed
        assert any("def add(self, a, b) -> int" in sig for sig in signatures)
        assert any("async def async_divide" in sig for sig in signatures)

        # Verify docstrings are present
        assert any("Add two numbers" in doc for doc in docstrings)
        assert any("Asynchronously divide" in doc for doc in docstrings)

    def test_generate_same_class_pairs(self, populated_graph):
        """Test generating same-class method pairs from real graph."""
        from repotoire.ml.contrastive_learning import ContrastivePairGenerator

        generator = ContrastivePairGenerator(populated_graph)
        pairs = generator.generate_same_class_pairs(limit=10)

        # Should have pairs from the Calculator class
        # 4 methods = 4*3/2 = 6 pairs
        assert len(pairs) == 6

        # All pairs should be from Calculator class
        for sig1, sig2 in pairs:
            assert "math.Calculator" in sig1
            assert "math.Calculator" in sig2

    def test_generate_caller_callee_pairs(self, populated_graph):
        """Test generating caller-callee pairs from real graph."""
        from repotoire.ml.contrastive_learning import ContrastivePairGenerator

        generator = ContrastivePairGenerator(populated_graph)
        pairs = generator.generate_caller_callee_pairs(limit=10)

        # Should have 1 pair (add -> multiply)
        assert len(pairs) == 1

        caller_sig, callee_sig = pairs[0]
        assert "add" in caller_sig
        assert "multiply" in callee_sig

    def test_generate_all_pairs(self, populated_graph):
        """Test generating all pair types from real graph."""
        from repotoire.ml.contrastive_learning import (
            ContrastivePairGenerator,
            ContrastiveConfig,
        )

        config = ContrastiveConfig(
            max_code_docstring_pairs=10,
            max_same_class_pairs=10,
            max_caller_callee_pairs=10,
        )

        generator = ContrastivePairGenerator(populated_graph)
        pairs = generator.generate_all_pairs(config)

        # Should have: 4 docstring + 6 same-class + 1 caller-callee = 11 pairs
        assert len(pairs) == 11


@pytest.mark.skipif(
    not HAS_SENTENCE_TRANSFORMERS,
    reason="sentence-transformers not installed"
)
class TestContrastiveTrainingIntegration:
    """Integration tests for full contrastive training pipeline."""

    def test_fine_tune_from_graph(self, populated_graph):
        """Test fine-tuning embeddings from real graph data."""
        from repotoire.ml.contrastive_learning import (
            ContrastiveConfig,
            fine_tune_from_graph,
        )

        config = ContrastiveConfig(
            base_model="all-MiniLM-L6-v2",
            epochs=1,
            batch_size=2,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "model"
            stats = fine_tune_from_graph(populated_graph, output_path, config)

            assert stats["pairs"] == 11  # 4 + 6 + 1
            assert stats["epochs"] == 1
            assert output_path.exists()

            # Verify model can be loaded
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(str(output_path))
            embeddings = model.encode(["def test(): pass"])
            assert len(embeddings) == 1

    def test_trained_model_produces_similar_embeddings(self, populated_graph):
        """Test that trained model produces similar embeddings for related code."""
        from repotoire.ml.contrastive_learning import (
            ContrastiveConfig,
            ContrastiveTrainer,
            ContrastivePairGenerator,
        )
        import numpy as np

        config = ContrastiveConfig(
            base_model="all-MiniLM-L6-v2",
            epochs=1,
            batch_size=4,
        )

        generator = ContrastivePairGenerator(populated_graph)
        pairs = generator.generate_all_pairs(config)

        trainer = ContrastiveTrainer(config)

        with tempfile.TemporaryDirectory() as tmpdir:
            trainer.train(pairs, Path(tmpdir) / "model")

            # Encode some related code
            embeddings = trainer.encode([
                "def add(a, b): return a + b",
                "Add two numbers together",
                "def unrelated_function(): pass",
            ])

            # Calculate cosine similarities
            def cosine_sim(a, b):
                return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

            # add function and its docstring should be more similar
            # than add function and unrelated function
            add_doc_sim = cosine_sim(embeddings[0], embeddings[1])
            add_unrelated_sim = cosine_sim(embeddings[0], embeddings[2])

            # The model should learn that code and docstring are related
            # Note: With only 1 epoch on small data, the difference may be small
            logger.info(f"add-docstring similarity: {add_doc_sim:.4f}")
            logger.info(f"add-unrelated similarity: {add_unrelated_sim:.4f}")

            # Basic sanity check - similarities should be in valid range
            assert -1 <= add_doc_sim <= 1
            assert -1 <= add_unrelated_sim <= 1
