"""Detector performance benchmarks."""

import pytest


class TestDetectorPerformance:
    """Benchmark detector performance."""

    @pytest.mark.skip(reason="Requires Neo4j connection")
    def test_circular_dependency_detector(self, benchmark, neo4j_client):
        """Benchmark circular dependency detection."""
        from repotoire.detectors.circular_dependency import CircularDependencyDetector

        detector = CircularDependencyDetector(neo4j_client)

        def detect():
            return detector.detect()

        findings = benchmark(detect)
        assert findings is not None

    @pytest.mark.skip(reason="Requires Neo4j connection")
    def test_dead_code_detector(self, benchmark, neo4j_client):
        """Benchmark dead code detection."""
        from repotoire.detectors.dead_code import DeadCodeDetector

        detector = DeadCodeDetector(neo4j_client)

        def detect():
            return detector.detect()

        findings = benchmark(detect)
        assert findings is not None


class TestGraphAlgorithmPerformance:
    """Benchmark Rust graph algorithm performance."""

    def test_pagerank_1000_nodes(self, benchmark, large_graph_data):
        """Benchmark PageRank on 1000 nodes."""
        try:
            from repotoire_fast import graph_algorithms
        except ImportError:
            pytest.skip("repotoire-fast not installed")

        nodes = large_graph_data["nodes"]
        relationships = large_graph_data["relationships"]

        # Build adjacency list
        node_ids = {n["qualifiedName"]: i for i, n in enumerate(nodes)}
        edges = []
        for rel in relationships:
            if rel["source"] in node_ids and rel["target"] in node_ids:
                edges.append((node_ids[rel["source"]], node_ids[rel["target"]]))

        def run_pagerank():
            return graph_algorithms.pagerank(len(nodes), edges)

        result = benchmark(run_pagerank)
        assert len(result) == len(nodes)

    def test_betweenness_centrality_1000_nodes(self, benchmark, large_graph_data):
        """Benchmark betweenness centrality on 1000 nodes."""
        try:
            from repotoire_fast import graph_algorithms
        except ImportError:
            pytest.skip("repotoire-fast not installed")

        nodes = large_graph_data["nodes"]
        relationships = large_graph_data["relationships"]

        # Build adjacency list
        node_ids = {n["qualifiedName"]: i for i, n in enumerate(nodes)}
        edges = []
        for rel in relationships:
            if rel["source"] in node_ids and rel["target"] in node_ids:
                edges.append((node_ids[rel["source"]], node_ids[rel["target"]]))

        def run_betweenness():
            return graph_algorithms.betweenness_centrality(len(nodes), edges)

        result = benchmark(run_betweenness)
        assert len(result) == len(nodes)

    def test_leiden_communities_1000_nodes(self, benchmark, large_graph_data):
        """Benchmark Leiden community detection on 1000 nodes."""
        try:
            from repotoire_fast import graph_algorithms
        except ImportError:
            pytest.skip("repotoire-fast not installed")

        nodes = large_graph_data["nodes"]
        relationships = large_graph_data["relationships"]

        # Build adjacency list
        node_ids = {n["qualifiedName"]: i for i, n in enumerate(nodes)}
        edges = []
        for rel in relationships:
            if rel["source"] in node_ids and rel["target"] in node_ids:
                edges.append((node_ids[rel["source"]], node_ids[rel["target"]]))

        def run_leiden():
            return graph_algorithms.leiden(len(nodes), edges)

        result = benchmark(run_leiden)
        assert len(result) == len(nodes)

    def test_scc_1000_nodes(self, benchmark, large_graph_data):
        """Benchmark strongly connected components on 1000 nodes."""
        try:
            from repotoire_fast import graph_algorithms
        except ImportError:
            pytest.skip("repotoire-fast not installed")

        nodes = large_graph_data["nodes"]
        relationships = large_graph_data["relationships"]

        # Build adjacency list
        node_ids = {n["qualifiedName"]: i for i, n in enumerate(nodes)}
        edges = []
        for rel in relationships:
            if rel["source"] in node_ids and rel["target"] in node_ids:
                edges.append((node_ids[rel["source"]], node_ids[rel["target"]]))

        def run_scc():
            return graph_algorithms.strongly_connected_components(len(nodes), edges)

        result = benchmark(run_scc)
        assert len(result) == len(nodes)
