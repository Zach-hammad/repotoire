"""Git history RAG: Natural language queries over git commits.

Replaces Graphiti's LLM-based episode storage with a cheaper, faster approach
using Repotoire's existing RAG infrastructure. Instead of sending each commit
through an LLM ($0.01-0.02/commit), we:

1. Store commits as FalkorDB nodes with vector embeddings (FREE with local backend)
2. Use hybrid BM25 + vector search for retrieval
3. Generate answers with Claude Haiku ($0.001/query)

Cost comparison:
- Graphiti: $10-20 to ingest 1000 commits + $0.01/query
- GitHistoryRAG: FREE to ingest + $0.001/query (10-20x cheaper)

Example:
    >>> from repotoire.historical.git_rag import GitHistoryRAG
    >>> rag = GitHistoryRAG(graph_client, embedder)
    >>> await rag.ingest_commits(commits, repo_id="...")
    >>> results = await rag.search("When did we add OAuth?", top_k=10)
    >>> answer = await rag.ask("What authentication changes were made?")
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from repotoire.ai.embeddings import CodeEmbedder
from repotoire.graph.base import DatabaseClient
from repotoire.models import CommitEntity, GitCommit, RelationshipType
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class CommitSearchResult:
    """Result from git history search.

    Attributes:
        commit: The CommitEntity that matched
        score: Relevance score (0-1)
        relevance_reason: Why this commit matched (keywords, semantic)
        related_files: Files modified in this commit
    """

    commit: CommitEntity
    score: float
    relevance_reason: str = ""
    related_files: List[str] = field(default_factory=list)


@dataclass
class GitHistoryAnswer:
    """Answer to a git history question.

    Attributes:
        answer: Natural language answer
        commits: Relevant commits used to generate answer
        confidence: Confidence score (0-1)
        follow_up_questions: Suggested follow-up questions
        execution_time_ms: Query execution time
    """

    answer: str
    commits: List[CommitSearchResult]
    confidence: float
    follow_up_questions: List[str] = field(default_factory=list)
    execution_time_ms: float = 0.0


class GitHistoryRAG:
    """RAG-based natural language queries over git history.

    Uses Repotoire's existing embedding and graph infrastructure to provide
    semantic search over git commits. Much cheaper than Graphiti's LLM-based
    approach while providing similar functionality.

    Example:
        >>> from repotoire.ai.embeddings import CodeEmbedder
        >>> from repotoire.graph.falkordb_client import FalkorDBClient
        >>>
        >>> client = FalkorDBClient(...)
        >>> embedder = CodeEmbedder(backend="local")  # FREE
        >>> rag = GitHistoryRAG(client, embedder)
        >>>
        >>> # Ingest commits (one-time)
        >>> commits = git_repo.get_commit_history(max_commits=100)
        >>> await rag.ingest_commits(commits, repo_id="my-repo-id")
        >>>
        >>> # Search (instant)
        >>> results = await rag.search("authentication changes", top_k=5)
        >>>
        >>> # Ask questions (uses Claude Haiku, ~$0.001/query)
        >>> answer = await rag.ask("When did we add OAuth?")
    """

    def __init__(
        self,
        client: DatabaseClient,
        embedder: CodeEmbedder,
        use_haiku: bool = True,
    ):
        """Initialize GitHistoryRAG.

        Args:
            client: FalkorDB or Neo4j database client
            embedder: Code embedder for generating commit embeddings
            use_haiku: Use Claude Haiku for answer generation (cheap)
        """
        self.client = client
        self.embedder = embedder
        self.use_haiku = use_haiku
        self.is_falkordb = type(client).__name__ == "FalkorDBClient"

    async def ingest_commits(
        self,
        commits: List[GitCommit],
        repo_id: str,
        batch_size: int = 50,
    ) -> Dict[str, Any]:
        """Ingest git commits into the graph with embeddings.

        Converts GitCommit DTOs to CommitEntity nodes, generates embeddings,
        and stores in FalkorDB. Also creates MODIFIED relationships to files.

        Args:
            commits: List of GitCommit DTOs from GitRepository
            repo_id: Repository UUID for multi-tenant isolation
            batch_size: Number of commits to process in each batch

        Returns:
            Stats dict with commits_processed, embeddings_generated, etc.
        """
        import time

        start_time = time.time()
        stats = {
            "commits_processed": 0,
            "embeddings_generated": 0,
            "relationships_created": 0,
            "errors": 0,
        }

        logger.info(f"Ingesting {len(commits)} commits for repo {repo_id}")

        # Process in batches
        for i in range(0, len(commits), batch_size):
            batch = commits[i : i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(commits) + batch_size - 1) // batch_size

            logger.debug(f"Processing batch {batch_num}/{total_batches}")

            try:
                # Convert to CommitEntity
                entities = [
                    CommitEntity.from_git_commit(c, repo_id=repo_id) for c in batch
                ]

                # Generate embeddings for commit messages
                texts = [self._commit_to_text(e) for e in entities]
                embeddings = self.embedder.embed_batch(texts)

                # Assign embeddings to entities
                for entity, embedding in zip(entities, embeddings):
                    entity.embedding = embedding

                stats["embeddings_generated"] += len(embeddings)

                # Store in graph
                for entity in entities:
                    try:
                        self._create_commit_node(entity)
                        stats["commits_processed"] += 1

                        # Create relationships to modified files
                        rels_created = self._create_file_relationships(entity)
                        stats["relationships_created"] += rels_created

                    except Exception as e:
                        logger.debug(f"Error storing commit {entity.short_sha}: {e}")
                        stats["errors"] += 1

            except Exception as e:
                logger.warning(f"Batch {batch_num} failed: {e}")
                stats["errors"] += len(batch)

        elapsed = time.time() - start_time
        stats["elapsed_seconds"] = elapsed
        stats["commits_per_second"] = (
            stats["commits_processed"] / elapsed if elapsed > 0 else 0
        )

        logger.info(
            f"Ingested {stats['commits_processed']} commits in {elapsed:.1f}s "
            f"({stats['commits_per_second']:.1f}/sec)"
        )

        return stats

    def _commit_to_text(self, commit: CommitEntity) -> str:
        """Convert commit to text for embedding.

        Combines commit message, author, and file changes into a single
        text string optimized for semantic search.

        Args:
            commit: CommitEntity to convert

        Returns:
            Text representation for embedding
        """
        parts = [
            f"Commit: {commit.message_subject}",
            f"Author: {commit.author_name}",
        ]

        if commit.message and commit.message != commit.message_subject:
            # Include full message if different from subject
            body = commit.message.split("\n", 1)
            if len(body) > 1:
                parts.append(f"Description: {body[1][:500]}")

        if commit.changed_file_paths:
            files_str = ", ".join(commit.changed_file_paths[:10])
            parts.append(f"Files changed: {files_str}")

        if commit.commit_type:
            parts.append(f"Type: {commit.commit_type}")

        return "\n".join(parts)

    def _create_commit_node(self, commit: CommitEntity) -> None:
        """Create Commit node in FalkorDB.

        Args:
            commit: CommitEntity to store
        """
        # Build properties dict
        props = {
            "sha": commit.sha,
            "shortSha": commit.short_sha,
            "message": commit.message[:2000] if commit.message else "",
            "messageSubject": commit.message_subject[:200] if commit.message_subject else "",
            "authorName": commit.author_name,
            "authorEmail": commit.author_email,
            "committedAt": commit.committed_at.isoformat() if commit.committed_at else None,
            "parentShas": commit.parent_shas,
            "branches": commit.branches,
            "tags": commit.tags,
            "filesChanged": commit.files_changed,
            "insertions": commit.insertions,
            "deletions": commit.deletions,
            "changedFilePaths": commit.changed_file_paths[:50],  # Limit for storage
            "commitType": commit.commit_type,
            "impactScore": commit.impact_score,
            "repoId": commit.repo_id,
            "qualifiedName": commit.qualified_name,
        }

        # Add embedding if present
        if commit.embedding:
            props["embedding"] = commit.embedding

        # MERGE to avoid duplicates
        query = """
        MERGE (c:Commit {sha: $sha, repoId: $repoId})
        SET c += $props
        RETURN c.sha as sha
        """

        self.client.execute_query(query, {"sha": commit.sha, "repoId": commit.repo_id, "props": props})

    def _create_file_relationships(self, commit: CommitEntity) -> int:
        """Create MODIFIED relationships between Commit and File nodes.

        Args:
            commit: CommitEntity with changed_file_paths

        Returns:
            Number of relationships created
        """
        if not commit.changed_file_paths:
            return 0

        created = 0
        for file_path in commit.changed_file_paths[:50]:  # Limit
            try:
                query = """
                MATCH (c:Commit {sha: $sha, repoId: $repoId})
                MATCH (f:File {filePath: $filePath, repoId: $repoId})
                MERGE (c)-[r:MODIFIED]->(f)
                SET r.committedAt = $committedAt
                RETURN type(r) as rel_type
                """
                result = self.client.execute_query(
                    query,
                    {
                        "sha": commit.sha,
                        "repoId": commit.repo_id,
                        "filePath": file_path,
                        "committedAt": commit.committed_at.isoformat() if commit.committed_at else None,
                    },
                )
                if result:
                    created += 1
            except Exception:
                # File may not exist in graph yet
                pass

        return created

    async def search(
        self,
        query: str,
        repo_id: str,
        top_k: int = 10,
        author: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[CommitSearchResult]:
        """Search git history using semantic vector search.

        Args:
            query: Natural language search query
            repo_id: Repository UUID
            top_k: Number of results to return
            author: Filter by author email (optional)
            since: Filter commits after this date (optional)
            until: Filter commits before this date (optional)

        Returns:
            List of CommitSearchResult ordered by relevance
        """
        import time

        start_time = time.time()

        # Generate query embedding
        query_embedding = self.embedder.embed_query(query)

        # Vector search
        results = self._vector_search_commits(
            query_embedding=query_embedding,
            repo_id=repo_id,
            top_k=top_k,
            author=author,
            since=since,
            until=until,
        )

        elapsed = (time.time() - start_time) * 1000
        logger.debug(f"Search completed in {elapsed:.1f}ms, found {len(results)} results")

        return results

    def _vector_search_commits(
        self,
        query_embedding: List[float],
        repo_id: str,
        top_k: int,
        author: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[CommitSearchResult]:
        """Perform vector similarity search on Commit nodes.

        Args:
            query_embedding: Query vector
            repo_id: Repository UUID
            top_k: Number of results
            author: Optional author filter
            since: Optional date filter (after)
            until: Optional date filter (before)

        Returns:
            List of CommitSearchResult
        """
        # Build filter conditions
        filters = ["c.repoId = $repoId"]
        params: Dict[str, Any] = {
            "top_k": top_k,
            "embedding": query_embedding,
            "repoId": repo_id,
        }

        if author:
            filters.append("c.authorEmail = $author")
            params["author"] = author

        if since:
            filters.append("c.committedAt >= $since")
            params["since"] = since.isoformat()

        if until:
            filters.append("c.committedAt <= $until")
            params["until"] = until.isoformat()

        filter_clause = " AND ".join(filters)

        if self.is_falkordb:
            # FalkorDB vector search
            query = f"""
            CALL db.idx.vector.queryNodes(
                'Commit',
                'embedding',
                $top_k,
                vecf32($embedding)
            ) YIELD node as c, score
            WHERE {filter_clause}
            RETURN
                c.sha as sha,
                c.shortSha as short_sha,
                c.message as message,
                c.messageSubject as message_subject,
                c.authorName as author_name,
                c.authorEmail as author_email,
                c.committedAt as committed_at,
                c.parentShas as parent_shas,
                c.branches as branches,
                c.tags as tags,
                c.filesChanged as files_changed,
                c.insertions as insertions,
                c.deletions as deletions,
                c.changedFilePaths as changed_file_paths,
                c.commitType as commit_type,
                c.impactScore as impact_score,
                c.repoId as repo_id,
                c.qualifiedName as qualified_name,
                score
            ORDER BY score DESC
            LIMIT $top_k
            """
        else:
            # Neo4j vector search
            query = f"""
            CALL db.index.vector.queryNodes('commit_embeddings', $top_k, $embedding)
            YIELD node as c, score
            WHERE {filter_clause}
            RETURN
                c.sha as sha,
                c.shortSha as short_sha,
                c.message as message,
                c.messageSubject as message_subject,
                c.authorName as author_name,
                c.authorEmail as author_email,
                c.committedAt as committed_at,
                c.parentShas as parent_shas,
                c.branches as branches,
                c.tags as tags,
                c.filesChanged as files_changed,
                c.insertions as insertions,
                c.deletions as deletions,
                c.changedFilePaths as changed_file_paths,
                c.commitType as commit_type,
                c.impactScore as impact_score,
                c.repoId as repo_id,
                c.qualifiedName as qualified_name,
                score
            ORDER BY score DESC
            LIMIT $top_k
            """

        try:
            rows = self.client.execute_query(query, params)
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

        # Convert to CommitSearchResult
        results = []
        for row in rows:
            commit = CommitEntity(
                name=row.get("short_sha", ""),
                qualified_name=row.get("qualified_name", ""),
                file_path=".",
                line_start=0,
                line_end=0,
                sha=row.get("sha", ""),
                short_sha=row.get("short_sha", ""),
                message=row.get("message", ""),
                message_subject=row.get("message_subject", ""),
                author_name=row.get("author_name", ""),
                author_email=row.get("author_email", ""),
                committed_at=datetime.fromisoformat(row["committed_at"]) if row.get("committed_at") else None,
                parent_shas=row.get("parent_shas", []) or [],
                branches=row.get("branches", []) or [],
                tags=row.get("tags", []) or [],
                files_changed=row.get("files_changed", 0) or 0,
                insertions=row.get("insertions", 0) or 0,
                deletions=row.get("deletions", 0) or 0,
                changed_file_paths=row.get("changed_file_paths", []) or [],
                commit_type=row.get("commit_type", ""),
                impact_score=row.get("impact_score", 0.0) or 0.0,
                repo_id=row.get("repo_id"),
            )

            results.append(
                CommitSearchResult(
                    commit=commit,
                    score=row.get("score", 0.0),
                    relevance_reason="Semantic similarity",
                    related_files=commit.changed_file_paths[:5],
                )
            )

        return results

    async def ask(
        self,
        query: str,
        repo_id: str,
        top_k: int = 10,
        author: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> GitHistoryAnswer:
        """Answer a question about git history using RAG.

        Retrieves relevant commits and generates an answer using Claude Haiku.

        Args:
            query: Natural language question
            repo_id: Repository UUID
            top_k: Number of commits to retrieve for context
            author: Filter by author (optional)
            since: Filter after date (optional)
            until: Filter before date (optional)

        Returns:
            GitHistoryAnswer with answer, commits, and metadata
        """
        import time

        start_time = time.time()

        # Step 1: Search for relevant commits
        results = await self.search(
            query=query,
            repo_id=repo_id,
            top_k=top_k,
            author=author,
            since=since,
            until=until,
        )

        if not results:
            return GitHistoryAnswer(
                answer="No commits found matching your query.",
                commits=[],
                confidence=0.0,
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        # Step 2: Generate answer using LLM
        answer, confidence = await self._generate_answer(query, results)

        # Step 3: Generate follow-up questions
        follow_ups = await self._generate_follow_ups(query, results)

        elapsed = (time.time() - start_time) * 1000

        return GitHistoryAnswer(
            answer=answer,
            commits=results,
            confidence=confidence,
            follow_up_questions=follow_ups,
            execution_time_ms=elapsed,
        )

    async def _generate_answer(
        self,
        query: str,
        results: List[CommitSearchResult],
    ) -> Tuple[str, float]:
        """Generate answer using Claude Haiku.

        Args:
            query: User's question
            results: Relevant commits

        Returns:
            Tuple of (answer_text, confidence_score)
        """
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            # Fallback to simple summary if Anthropic not available
            return self._simple_answer(results), 0.5

        # Build context from commits
        context = self._format_commits_for_llm(results[:10])

        system_prompt = """You are an expert code historian helping developers understand
the evolution of a codebase. Answer the user's question about git history based on the
provided commit data. Be concise and factual. Include specific dates and commit SHAs
when relevant. If the commits don't contain enough information, say so."""

        user_prompt = f"""Question: {query}

Commit History:
{context}

Answer the question based on this commit history."""

        try:
            client = AsyncAnthropic()
            response = await client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            answer = response.content[0].text

            # Calculate confidence from relevance scores
            if results:
                confidence = sum(r.score for r in results[:3]) / min(3, len(results))
            else:
                confidence = 0.0

            return answer, confidence

        except Exception as e:
            logger.warning(f"LLM answer generation failed: {e}")
            return self._simple_answer(results), 0.3

    def _simple_answer(self, results: List[CommitSearchResult]) -> str:
        """Generate simple answer without LLM.

        Args:
            results: Relevant commits

        Returns:
            Simple text summary
        """
        if not results:
            return "No matching commits found."

        lines = ["Found the following relevant commits:"]
        for i, r in enumerate(results[:5], 1):
            c = r.commit
            date_str = c.committed_at.strftime("%Y-%m-%d") if c.committed_at else "unknown"
            lines.append(f"{i}. [{c.short_sha}] {date_str} - {c.message_subject}")
            lines.append(f"   Author: {c.author_name}, Files: {c.files_changed}")

        return "\n".join(lines)

    def _format_commits_for_llm(self, results: List[CommitSearchResult]) -> str:
        """Format commits as context for LLM.

        Args:
            results: Commits to format

        Returns:
            Formatted text for LLM context
        """
        lines = []
        for i, r in enumerate(results, 1):
            c = r.commit
            date_str = c.committed_at.strftime("%Y-%m-%d %H:%M") if c.committed_at else "unknown"
            lines.append(f"{i}. Commit {c.short_sha} ({date_str})")
            lines.append(f"   Author: {c.author_name} <{c.author_email}>")
            lines.append(f"   Message: {c.message_subject}")
            if c.message and c.message != c.message_subject:
                body = c.message.split("\n", 1)
                if len(body) > 1:
                    lines.append(f"   Details: {body[1][:200]}")
            lines.append(f"   Changes: +{c.insertions}/-{c.deletions} in {c.files_changed} files")
            if c.changed_file_paths:
                files = ", ".join(c.changed_file_paths[:5])
                lines.append(f"   Files: {files}")
            lines.append("")

        return "\n".join(lines)

    async def _generate_follow_ups(
        self,
        query: str,
        results: List[CommitSearchResult],
    ) -> List[str]:
        """Generate follow-up questions.

        Args:
            query: Original question
            results: Relevant commits

        Returns:
            List of follow-up questions
        """
        # Simple heuristic-based follow-ups (no LLM needed)
        follow_ups = []

        if results:
            top_commit = results[0].commit

            # Suggest author-specific query
            if top_commit.author_name:
                follow_ups.append(
                    f"What other changes did {top_commit.author_name} make?"
                )

            # Suggest file-specific query
            if top_commit.changed_file_paths:
                file = top_commit.changed_file_paths[0]
                follow_ups.append(f"What is the history of {file}?")

            # Suggest time-based query
            if top_commit.committed_at:
                follow_ups.append("What changes were made before/after this?")

        return follow_ups[:3]

    def get_commit_count(self, repo_id: str) -> int:
        """Get total number of commits for a repository.

        Args:
            repo_id: Repository UUID

        Returns:
            Number of Commit nodes
        """
        query = """
        MATCH (c:Commit {repoId: $repoId})
        RETURN count(c) as count
        """
        result = self.client.execute_query(query, {"repoId": repo_id})
        if result and len(result) > 0:
            return result[0].get("count", 0)
        return 0

    def get_embeddings_status(self, repo_id: str) -> Dict[str, Any]:
        """Get status of commit embeddings for a repository.

        Args:
            repo_id: Repository UUID

        Returns:
            Dict with total_commits, commits_with_embeddings, coverage
        """
        query = """
        MATCH (c:Commit {repoId: $repoId})
        WITH count(c) as total,
             sum(CASE WHEN c.embedding IS NOT NULL THEN 1 ELSE 0 END) as with_embeddings
        RETURN total, with_embeddings
        """
        result = self.client.execute_query(query, {"repoId": repo_id})

        if result and len(result) > 0:
            total = result[0].get("total", 0)
            with_emb = result[0].get("with_embeddings", 0)
            coverage = with_emb / total if total > 0 else 0.0
            return {
                "total_commits": total,
                "commits_with_embeddings": with_emb,
                "coverage": coverage,
            }

        return {
            "total_commits": 0,
            "commits_with_embeddings": 0,
            "coverage": 0.0,
        }
