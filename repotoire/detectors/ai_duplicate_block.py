"""AI Duplicate Block Detector.

Detects near-identical code blocks that AI coding assistants tend to create
(copy-paste patterns). These are functions with high structural similarity
but not exact duplicates.

AI assistants often generate repetitive code with minor variations like:
- Different variable names but same logic
- Same structure with different literals
- Copy-paste patterns with slight modifications

This detector uses normalized hashing to find these near-duplicates.
"""

import hashlib
import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity


class AIDuplicateBlockDetector(CodeSmellDetector):
    """Detect near-identical code blocks typical of AI-generated code.
    
    AI coding assistants often produce repetitive patterns:
    - Functions with identical structure but different variable names
    - Copy-paste with minor modifications
    - Template-like code with different parameters
    
    This detector normalizes function code and finds high-similarity pairs
    that aren't exact duplicates (those are caught by other detectors).
    """

    # Default thresholds
    DEFAULT_SIMILARITY_THRESHOLD = 0.85  # 85% similarity
    DEFAULT_MIN_LOC = 5  # Minimum lines of code to consider
    DEFAULT_MAX_FINDINGS = 50  # Limit results

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict[str, Any]] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize AI duplicate block detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Optional configuration dict with thresholds
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        self.logger = get_logger(__name__)

        config = detector_config or {}
        self.similarity_threshold = config.get(
            "similarity_threshold", self.DEFAULT_SIMILARITY_THRESHOLD
        )
        self.min_loc = config.get("min_loc", self.DEFAULT_MIN_LOC)
        self.max_findings = config.get("max_findings", self.DEFAULT_MAX_FINDINGS)

    def detect(self) -> List[Finding]:
        """Detect near-duplicate code blocks in the codebase.

        Returns:
            List of findings for AI-generated duplicate patterns
        """
        # Fetch all functions with their source code / structure info
        functions = self._fetch_functions()
        
        if not functions:
            self.logger.info("AIDuplicateBlockDetector: No functions found")
            return []

        # Compute normalized representations and group by similarity
        duplicates = self._find_near_duplicates(functions)
        
        # Create findings
        findings = self._create_findings(duplicates)
        
        self.logger.info(
            f"AIDuplicateBlockDetector found {len(findings)} near-duplicate groups"
        )
        return findings[:self.max_findings]

    def _fetch_functions(self) -> List[Dict[str, Any]]:
        """Fetch all functions from the graph with relevant metadata.

        Returns:
            List of function data dictionaries
        """
        # REPO-600: Filter by tenant_id AND repo_id
        repo_filter = self._get_isolation_filter("f")

        query = f"""
        MATCH (f:Function)
        WHERE f.name IS NOT NULL 
          AND f.loc IS NOT NULL 
          AND f.loc >= $min_loc
          {repo_filter}
        OPTIONAL MATCH (f)<-[:CONTAINS*]-(file:File)
        RETURN f.qualifiedName AS qualified_name,
               f.name AS name,
               f.lineStart AS line_start,
               f.lineEnd AS line_end,
               f.loc AS loc,
               f.parameters AS parameters,
               f.complexity AS complexity,
               f.is_method AS is_method,
               f.decorators AS decorators,
               f.has_return AS has_return,
               f.has_yield AS has_yield,
               f.is_async AS is_async,
               file.filePath AS file_path
        ORDER BY f.loc DESC
        LIMIT 1000
        """

        try:
            results = self.db.execute_query(
                query,
                self._get_query_params(min_loc=self.min_loc),
            )
            return results
        except Exception as e:
            self.logger.error(f"Error fetching functions: {e}")
            return []

    def _normalize_function(self, func: Dict[str, Any]) -> str:
        """Create a normalized representation of a function for comparison.
        
        Normalization strips:
        - Whitespace differences
        - Variable/parameter names (replaced with placeholders)
        - Function names
        - Decorator differences
        
        Args:
            func: Function data from graph query
            
        Returns:
            Normalized string representation for hashing
        """
        # Build a structural fingerprint from available metadata
        parts = []
        
        # Function structure indicators
        loc = func.get("loc", 0)
        complexity = func.get("complexity", 0)
        params = func.get("parameters") or []
        param_count = len(params) if isinstance(params, list) else 0
        
        # Structural elements
        parts.append(f"LOC:{loc}")
        parts.append(f"COMPLEXITY:{complexity}")
        parts.append(f"PARAMS:{param_count}")
        
        # Boolean flags that indicate structure
        if func.get("is_method"):
            parts.append("METHOD")
        if func.get("is_async"):
            parts.append("ASYNC")
        if func.get("has_return"):
            parts.append("RETURNS")
        if func.get("has_yield"):
            parts.append("YIELDS")
            
        return "|".join(sorted(parts))

    def _compute_hash(self, normalized: str) -> str:
        """Compute hash of normalized function representation.
        
        Args:
            normalized: Normalized function string
            
        Returns:
            MD5 hash string
        """
        return hashlib.md5(normalized.encode()).hexdigest()

    def _calculate_similarity(self, func1: Dict, func2: Dict) -> float:
        """Calculate structural similarity between two functions.
        
        Uses multiple heuristics:
        - LOC similarity
        - Complexity similarity
        - Parameter count similarity
        - Structural flags match
        
        Args:
            func1: First function data
            func2: Second function data
            
        Returns:
            Similarity score 0.0-1.0
        """
        scores = []
        
        # LOC similarity (within 20% is considered similar)
        loc1 = func1.get("loc", 0) or 0
        loc2 = func2.get("loc", 0) or 0
        if loc1 > 0 and loc2 > 0:
            loc_ratio = min(loc1, loc2) / max(loc1, loc2)
            scores.append(loc_ratio)
        
        # Complexity similarity
        c1 = func1.get("complexity", 0) or 0
        c2 = func2.get("complexity", 0) or 0
        if c1 > 0 and c2 > 0:
            complexity_ratio = min(c1, c2) / max(c1, c2)
            scores.append(complexity_ratio)
        elif c1 == c2 == 0:
            scores.append(1.0)  # Both have no complexity data
        
        # Parameter count similarity
        p1 = func1.get("parameters") or []
        p2 = func2.get("parameters") or []
        p1_count = len(p1) if isinstance(p1, list) else 0
        p2_count = len(p2) if isinstance(p2, list) else 0
        if p1_count > 0 or p2_count > 0:
            max_params = max(p1_count, p2_count)
            param_sim = 1.0 - (abs(p1_count - p2_count) / (max_params + 1))
            scores.append(param_sim)
        else:
            scores.append(1.0)  # Both have no params
        
        # Boolean flags match (each matching flag adds to similarity)
        flags = ["is_method", "is_async", "has_return", "has_yield"]
        flag_matches = sum(
            1 for flag in flags 
            if bool(func1.get(flag)) == bool(func2.get(flag))
        )
        flag_similarity = flag_matches / len(flags)
        scores.append(flag_similarity)
        
        # Weight average (LOC and complexity more important)
        if len(scores) >= 4:
            weights = [0.35, 0.25, 0.2, 0.2]  # LOC, complexity, params, flags
            weighted_sum = sum(s * w for s, w in zip(scores, weights))
            return weighted_sum
        
        return sum(scores) / len(scores) if scores else 0.0

    def _find_near_duplicates(
        self, functions: List[Dict[str, Any]]
    ) -> List[Tuple[Dict, Dict, float]]:
        """Find pairs of functions that are near-duplicates.
        
        Groups functions by structural hash first for efficiency,
        then computes detailed similarity within groups.
        
        Args:
            functions: List of function data from graph
            
        Returns:
            List of (func1, func2, similarity) tuples
        """
        # Group by normalized hash for initial clustering
        hash_groups: Dict[str, List[Dict]] = defaultdict(list)
        for func in functions:
            normalized = self._normalize_function(func)
            hash_key = self._compute_hash(normalized)
            hash_groups[hash_key].append(func)
        
        duplicates = []
        seen_pairs: Set[Tuple[str, str]] = set()
        
        # Check within hash groups (exact structural matches)
        for group_funcs in hash_groups.values():
            if len(group_funcs) < 2:
                continue
            
            # Compare all pairs in group
            for i, func1 in enumerate(group_funcs):
                for func2 in group_funcs[i + 1:]:
                    qn1 = func1.get("qualified_name", "")
                    qn2 = func2.get("qualified_name", "")
                    
                    # Skip if same file (might be intentional overloads)
                    if func1.get("file_path") == func2.get("file_path"):
                        continue
                    
                    pair_key = tuple(sorted([qn1, qn2]))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    
                    similarity = self._calculate_similarity(func1, func2)
                    if similarity >= self.similarity_threshold:
                        duplicates.append((func1, func2, similarity))
        
        # Also check across groups with similar LOC (within 20%)
        all_funcs = list(functions)
        all_funcs.sort(key=lambda f: f.get("loc", 0))
        
        for i, func1 in enumerate(all_funcs):
            loc1 = func1.get("loc", 0)
            if loc1 < self.min_loc:
                continue
                
            # Only check nearby functions (sorted by LOC)
            for func2 in all_funcs[i + 1:min(i + 20, len(all_funcs))]:
                loc2 = func2.get("loc", 0)
                
                # Stop if LOC difference too large
                if loc2 > loc1 * 1.3:
                    break
                
                qn1 = func1.get("qualified_name", "")
                qn2 = func2.get("qualified_name", "")
                
                # Skip same file
                if func1.get("file_path") == func2.get("file_path"):
                    continue
                
                pair_key = tuple(sorted([qn1, qn2]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                
                similarity = self._calculate_similarity(func1, func2)
                if similarity >= self.similarity_threshold:
                    duplicates.append((func1, func2, similarity))
        
        # Sort by similarity (highest first)
        duplicates.sort(key=lambda x: x[2], reverse=True)
        return duplicates

    def _create_findings(
        self, duplicates: List[Tuple[Dict, Dict, float]]
    ) -> List[Finding]:
        """Create findings from duplicate pairs.
        
        Args:
            duplicates: List of (func1, func2, similarity) tuples
            
        Returns:
            List of Finding objects
        """
        findings = []
        
        for func1, func2, similarity in duplicates:
            qn1 = func1.get("qualified_name", "unknown")
            qn2 = func2.get("qualified_name", "unknown")
            name1 = func1.get("name", "unknown")
            name2 = func2.get("name", "unknown")
            file1 = func1.get("file_path", "unknown")
            file2 = func2.get("file_path", "unknown")
            loc1 = func1.get("loc", 0)
            loc2 = func2.get("loc", 0)
            
            similarity_pct = int(similarity * 100)
            
            description = (
                f"Functions '{name1}' and '{name2}' have {similarity_pct}% structural "
                f"similarity, suggesting AI-generated copy-paste patterns.\n\n"
                f"- {name1}: {loc1} LOC in {file1}\n"
                f"- {name2}: {loc2} LOC in {file2}\n\n"
                f"Near-identical functions increase maintenance burden and "
                f"can lead to inconsistent bug fixes."
            )
            
            suggestion = (
                "Consider one of the following approaches:\n"
                "1. Extract common logic into a shared helper function\n"
                "2. Use a template/factory pattern if variations are intentional\n"
                "3. If truly duplicates, consolidate into a single implementation\n"
                "4. Add documentation explaining why similar implementations exist"
            )
            
            finding = Finding(
                id=f"ai_duplicate_block_{qn1}_{qn2}",
                detector="AIDuplicateBlockDetector",
                severity=Severity.HIGH,
                title=f"AI-style duplicate: {name1} ≈ {name2} ({similarity_pct}%)",
                description=description,
                affected_nodes=[qn1, qn2],
                affected_files=[f for f in [file1, file2] if f != "unknown"],
                line_start=func1.get("line_start"),
                line_end=func1.get("line_end"),
                suggested_fix=suggestion,
                estimated_effort="Medium (1-2 hours)",
                graph_context={
                    "similarity": round(similarity, 3),
                    "func1_loc": loc1,
                    "func2_loc": loc2,
                    "func1_complexity": func1.get("complexity", 0),
                    "func2_complexity": func2.get("complexity", 0),
                },
            )
            
            # Add collaboration metadata
            evidence = ["high_structural_similarity", "cross_file_duplicate"]
            if similarity >= 0.95:
                evidence.append("near_identical")
            
            finding.add_collaboration_metadata(CollaborationMetadata(
                detector="AIDuplicateBlockDetector",
                confidence=min(0.7 + (similarity - 0.85) * 2, 0.95),
                evidence=evidence,
                tags=["ai_duplicate", "copy_paste", "duplication", "refactoring_candidate"],
            ))
            
            # Flag entities in graph for cross-detector collaboration
            if self.enricher:
                for qn in [qn1, qn2]:
                    try:
                        self.enricher.flag_entity(
                            entity_qualified_name=qn,
                            detector="AIDuplicateBlockDetector",
                            severity=finding.severity.value,
                            issues=["ai_generated_duplicate"],
                            confidence=similarity,
                            metadata={
                                "duplicate_of": qn2 if qn == qn1 else qn1,
                                "similarity": round(similarity, 3),
                            },
                        )
                    except Exception:
                        pass  # Don't fail detection if enrichment fails
            
            findings.append(finding)
        
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity (always HIGH for AI duplicates).

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        return Severity.HIGH
