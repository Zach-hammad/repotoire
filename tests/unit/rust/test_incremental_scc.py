"""Tests for Rust incremental SCC cache (REPO-412).

These tests verify:
1. Basic SCC computation via Rust
2. Incremental updates (edge addition, removal)
3. Cycle detection with proper node tracking
4. Cache versioning and verification
5. Performance for large graphs
"""

import pytest
import time

# Skip all tests if repotoire_fast is not available
pytest.importorskip("repotoire_fast")

import repotoire_fast


class TestBasicSCCComputation:
    """Tests for basic SCC computation without incremental updates."""

    def test_empty_graph(self):
        """Test empty graph returns no SCCs."""
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize([], 0)
        assert cache.scc_count == 0
        assert cache.get_cycles(2) == []

    def test_single_node(self):
        """Test single node is its own SCC but not a cycle."""
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize([], 1)
        assert cache.scc_count == 1
        assert cache.get_cycles(2) == []  # Single nodes aren't cycles

    def test_linear_chain(self):
        """Test linear chain has no cycles."""
        edges = [(0, 1), (1, 2), (2, 3)]
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, 4)
        assert cache.scc_count == 4  # Each node in its own SCC
        assert cache.get_cycles(2) == []

    def test_triangle_cycle(self):
        """Test 3-node cycle is detected."""
        edges = [(0, 1), (1, 2), (2, 0)]
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, 3)

        cycles = cache.get_cycles(2)
        assert len(cycles) == 1
        assert len(cycles[0]) == 3
        assert set(cycles[0]) == {0, 1, 2}

    def test_two_node_cycle(self):
        """Test 2-node cycle (mutual import)."""
        edges = [(0, 1), (1, 0)]
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, 2)

        cycles = cache.get_cycles(2)
        assert len(cycles) == 1
        assert len(cycles[0]) == 2

    def test_two_separate_cycles(self):
        """Test two independent cycles are both detected."""
        edges = [
            (0, 1), (1, 2), (2, 0),  # Cycle 1
            (3, 4), (4, 5), (5, 3),  # Cycle 2
        ]
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, 6)

        cycles = cache.get_cycles(2)
        assert len(cycles) == 2

    def test_get_scc(self):
        """Test getting SCC ID for individual nodes."""
        edges = [(0, 1), (1, 0)]
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, 3)

        # Nodes 0 and 1 should be in same SCC
        scc_0 = cache.get_scc(0)
        scc_1 = cache.get_scc(1)
        scc_2 = cache.get_scc(2)

        assert scc_0 is not None
        assert scc_1 is not None
        assert scc_2 is not None
        assert scc_0 == scc_1  # Same SCC
        assert scc_0 != scc_2  # Different SCC

    def test_get_scc_members(self):
        """Test getting all members of an SCC."""
        edges = [(0, 1), (1, 2), (2, 0)]
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, 3)

        scc_id = cache.get_scc(0)
        members = cache.get_scc_members(scc_id)

        assert members is not None
        assert set(members) == {0, 1, 2}


class TestIncrementalUpdates:
    """Tests for incremental SCC updates."""

    def test_edge_removal_breaks_cycle(self):
        """Test removing edge breaks a cycle."""
        edges = [(0, 1), (1, 2), (2, 0)]
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, 3)

        assert len(cache.get_cycles(2)) == 1

        # Remove edge that breaks cycle
        new_edges = [(0, 1), (1, 2)]
        result = cache.update([], [(2, 0)], new_edges)

        assert result["type"] in ["updated", "full_recompute"]
        assert len(cache.get_cycles(2)) == 0

    def test_edge_addition_no_cycle(self):
        """Test adding edge that doesn't create cycle returns NoChange."""
        edges = []
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, 2)

        # Add edge that doesn't create cycle (no reverse path)
        new_edges = [(0, 1)]
        result = cache.update([(0, 1)], [], new_edges)

        assert result["type"] == "no_change"
        assert len(cache.get_cycles(2)) == 0

    def test_edge_addition_creates_cycle(self):
        """Test adding edge that creates a cycle."""
        # Start with almost-cycle: 0->1->2
        edges = [(0, 1), (1, 2)]
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, 3)

        assert len(cache.get_cycles(2)) == 0

        # Add edge that completes cycle: 2->0
        new_edges = [(0, 1), (1, 2), (2, 0)]
        result = cache.update([(2, 0)], [], new_edges)

        assert result["type"] in ["updated", "full_recompute"]
        assert len(cache.get_cycles(2)) == 1

    def test_external_edge_no_change(self):
        """Test adding/removing external edge doesn't affect SCCs."""
        # Triangle cycle with external node
        edges = [(0, 1), (1, 2), (2, 0), (3, 0)]
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, 4)

        initial_cycles = cache.get_cycles(2)
        assert len(initial_cycles) == 1

        # Remove external edge
        new_edges = [(0, 1), (1, 2), (2, 0)]
        result = cache.update([], [(3, 0)], new_edges)

        assert result["type"] == "no_change"
        assert len(cache.get_cycles(2)) == 1

    def test_version_increments(self):
        """Test cache version increments on updates."""
        cache = repotoire_fast.PyIncrementalSCC()
        v0 = cache.version

        cache.initialize([(0, 1), (1, 0)], 2)
        v1 = cache.version
        assert v1 > v0

        cache.update([], [(1, 0)], [(0, 1)])
        v2 = cache.version
        assert v2 > v1


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_incremental_scc_new(self):
        """Test one-step cache initialization."""
        edges = [(0, 1), (1, 2), (2, 0)]
        cache = repotoire_fast.incremental_scc_new(edges, 3)

        assert cache.scc_count >= 1
        assert len(cache.get_cycles(2)) == 1

    def test_find_sccs_one_shot(self):
        """Test one-shot SCC computation."""
        edges = [(0, 1), (1, 2), (2, 0)]
        cycles = repotoire_fast.find_sccs_one_shot(edges, 3, 2)

        assert len(cycles) == 1
        assert set(cycles[0]) == {0, 1, 2}

    def test_find_sccs_one_shot_no_cycle(self):
        """Test one-shot with no cycles."""
        edges = [(0, 1), (1, 2)]
        cycles = repotoire_fast.find_sccs_one_shot(edges, 3, 2)

        assert len(cycles) == 0


class TestVerification:
    """Tests for cache verification."""

    def test_verify_after_init(self):
        """Test cache verifies correctly after initialization."""
        edges = [(0, 1), (1, 2), (2, 0)]
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, 3)

        assert cache.verify(edges, 3) is True

    def test_verify_after_update(self):
        """Test cache verifies correctly after incremental update."""
        edges = [(0, 1), (1, 2), (2, 0)]
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, 3)

        # Remove edge
        new_edges = [(0, 1), (1, 2)]
        cache.update([], [(2, 0)], new_edges)

        assert cache.verify(new_edges, 3) is True


class TestErrorHandling:
    """Tests for error handling."""

    def test_node_out_of_bounds(self):
        """Test error on edge with node out of bounds."""
        cache = repotoire_fast.PyIncrementalSCC()

        with pytest.raises(ValueError, match="out of bounds"):
            cache.initialize([(0, 5)], 3)  # Node 5 doesn't exist


class TestPerformance:
    """Performance tests for large graphs."""

    def test_large_cycle_initialization(self):
        """Test initialization performance for large cycle."""
        n = 1000
        edges = [(i, (i + 1) % n) for i in range(n)]

        cache = repotoire_fast.PyIncrementalSCC()
        start = time.time()
        cache.initialize(edges, n)
        elapsed_ms = (time.time() - start) * 1000

        cycles = cache.get_cycles(2)
        assert len(cycles) == 1
        assert len(cycles[0]) == n

        # Should complete in reasonable time
        assert elapsed_ms < 1000, f"Init took {elapsed_ms:.1f}ms, expected <1000ms"

    def test_incremental_update_speed(self):
        """Test incremental update is fast for external edge changes.

        This tests the key optimization: when an edge change doesn't affect
        any SCC's internal structure, the update should be very fast (O(1)
        vs O(V+E) for full recomputation).
        """
        n = 1000
        # Create a cycle of nodes 0..999, plus an external node 1000
        # that points into the cycle
        edges = [(i, (i + 1) % n) for i in range(n)]
        edges.append((n, 0))  # External node 1000 -> 0

        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, n + 1)

        # Initial state: one cycle of n nodes, plus isolated node 1000
        assert len(cache.get_cycles(2)) == 1
        assert len(cache.get_cycles(2)[0]) == n

        # Remove external edge (doesn't break the cycle)
        new_edges = [(i, (i + 1) % n) for i in range(n)]
        start = time.time()
        result = cache.update([], [(n, 0)], new_edges)
        update_time = time.time() - start

        # External edge removal should result in no_change
        assert result["type"] == "no_change", (
            f"Expected no_change for external edge removal, got {result['type']}"
        )

        # Should be very fast (no SCC recomputation needed)
        assert update_time < 0.01, f"External update took too long: {update_time:.3f}s"

        # Cycle should still be intact
        assert len(cache.get_cycles(2)) == 1

    def test_many_small_updates(self):
        """Test many small updates maintain correctness."""
        n = 100
        edges = list((i, (i + 1) % n) for i in range(n))

        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize(edges, n)

        # Remove edges one by one
        for i in range(10):
            removed_edge = edges.pop()
            cache.update([], [removed_edge], edges)
            assert cache.verify(edges, n)


class TestRepr:
    """Tests for string representation."""

    def test_repr(self):
        """Test __repr__ includes useful info."""
        cache = repotoire_fast.PyIncrementalSCC()
        cache.initialize([(0, 1), (1, 0)], 2)

        repr_str = repr(cache)
        assert "version=" in repr_str or "scc_count=" in repr_str
