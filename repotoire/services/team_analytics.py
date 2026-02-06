"""Team analytics service for code ownership and collaboration.

This service provides cloud-only team features:
- Code ownership analysis (git blame)
- Collaboration graph computation
- Developer profile aggregation
- Team insights (bus factor, knowledge silos)

All features require organization membership and are not available
in local/free mode.
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.db.models import (
    CodeOwnership,
    Collaboration,
    Developer,
    OwnershipType,
    Repository,
    TeamInsight,
)
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class TeamAnalyticsService:
    """Service for computing and querying team analytics.
    
    This service is cloud-only and requires:
    - Valid organization membership
    - Repository access permissions
    """

    def __init__(self, session: AsyncSession, organization_id: UUID):
        """Initialize the team analytics service.
        
        Args:
            session: Database session
            organization_id: Organization to scope analytics to
        """
        self.session = session
        self.organization_id = organization_id

    async def analyze_git_ownership(
        self,
        repository_id: UUID,
        git_log: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Analyze code ownership from git history.
        
        Args:
            repository_id: Repository to analyze
            git_log: List of commit dicts with author, files, timestamp
            
        Returns:
            Analysis results with ownership data
        """
        # Track contributions per developer per file
        file_contributions: Dict[str, Dict[str, Dict]] = defaultdict(
            lambda: defaultdict(lambda: {"commits": 0, "lines_added": 0, "lines_removed": 0, "last_modified": None})
        )
        developer_stats: Dict[str, Dict] = defaultdict(
            lambda: {"commits": 0, "lines_added": 0, "lines_removed": 0, "first_commit": None, "last_commit": None, "name": ""}
        )

        for commit in git_log:
            author_email = commit.get("author_email", "").lower()
            author_name = commit.get("author_name", "Unknown")
            timestamp = commit.get("timestamp")
            files = commit.get("files", [])

            if not author_email:
                continue

            # Update developer stats
            dev = developer_stats[author_email]
            dev["commits"] += 1
            dev["name"] = author_name
            if timestamp:
                if dev["first_commit"] is None or timestamp < dev["first_commit"]:
                    dev["first_commit"] = timestamp
                if dev["last_commit"] is None or timestamp > dev["last_commit"]:
                    dev["last_commit"] = timestamp

            # Update file contributions
            for file_info in files:
                file_path = file_info.get("path", "")
                lines_added = file_info.get("lines_added", 0)
                lines_removed = file_info.get("lines_removed", 0)

                if not file_path:
                    continue

                contrib = file_contributions[file_path][author_email]
                contrib["commits"] += 1
                contrib["lines_added"] += lines_added
                contrib["lines_removed"] += lines_removed
                if timestamp and (contrib["last_modified"] is None or timestamp > contrib["last_modified"]):
                    contrib["last_modified"] = timestamp

                dev["lines_added"] += lines_added
                dev["lines_removed"] += lines_removed

        # Create/update Developer records
        developers_created = 0
        developer_id_map: Dict[str, UUID] = {}

        for email, stats in developer_stats.items():
            # Check if developer exists
            result = await self.session.execute(
                select(Developer).where(
                    Developer.organization_id == self.organization_id,
                    Developer.email == email,
                )
            )
            developer = result.scalar_one_or_none()

            if developer:
                # Update existing
                developer.total_commits += stats["commits"]
                developer.total_lines_added += stats["lines_added"]
                developer.total_lines_removed += stats["lines_removed"]
                if stats["name"]:
                    developer.name = stats["name"]
                if stats["first_commit"]:
                    if developer.first_commit_at is None or stats["first_commit"] < developer.first_commit_at:
                        developer.first_commit_at = stats["first_commit"]
                if stats["last_commit"]:
                    if developer.last_commit_at is None or stats["last_commit"] > developer.last_commit_at:
                        developer.last_commit_at = stats["last_commit"]
            else:
                # Create new
                developer = Developer(
                    organization_id=self.organization_id,
                    email=email,
                    name=stats["name"] or email.split("@")[0],
                    total_commits=stats["commits"],
                    total_lines_added=stats["lines_added"],
                    total_lines_removed=stats["lines_removed"],
                    first_commit_at=stats["first_commit"],
                    last_commit_at=stats["last_commit"],
                )
                self.session.add(developer)
                developers_created += 1

            await self.session.flush()
            developer_id_map[email] = developer.id

        # Create/update CodeOwnership records
        ownership_records = 0
        for file_path, contributors in file_contributions.items():
            # Calculate ownership scores (weighted by recency and contribution size)
            total_score = 0
            scores = {}
            now = datetime.now(timezone.utc)

            for email, contrib in contributors.items():
                # Score = commits * lines * recency_factor
                lines = contrib["lines_added"] + contrib["lines_removed"]
                commits = contrib["commits"]

                recency_factor = 1.0
                if contrib["last_modified"]:
                    days_ago = (now - contrib["last_modified"]).days
                    recency_factor = max(0.1, 1.0 - (days_ago / 365))  # Decay over a year

                score = (commits * 10 + lines) * recency_factor
                scores[email] = score
                total_score += score

            # Normalize and create ownership records
            for email, score in scores.items():
                if email not in developer_id_map:
                    continue

                ownership_score = score / total_score if total_score > 0 else 0
                contrib = contributors[email]

                # Check existing
                result = await self.session.execute(
                    select(CodeOwnership).where(
                        CodeOwnership.repository_id == repository_id,
                        CodeOwnership.developer_id == developer_id_map[email],
                        CodeOwnership.ownership_type == OwnershipType.FILE,
                        CodeOwnership.path == file_path,
                    )
                )
                ownership = result.scalar_one_or_none()

                if ownership:
                    ownership.ownership_score = ownership_score
                    ownership.lines_owned = contrib["lines_added"]
                    ownership.commit_count = contrib["commits"]
                    ownership.last_modified_at = contrib["last_modified"]
                else:
                    ownership = CodeOwnership(
                        repository_id=repository_id,
                        developer_id=developer_id_map[email],
                        ownership_type=OwnershipType.FILE,
                        path=file_path,
                        ownership_score=ownership_score,
                        lines_owned=contrib["lines_added"],
                        commit_count=contrib["commits"],
                        last_modified_at=contrib["last_modified"],
                    )
                    self.session.add(ownership)
                    ownership_records += 1

        await self.session.commit()

        return {
            "developers_created": developers_created,
            "developers_updated": len(developer_stats) - developers_created,
            "ownership_records": ownership_records,
            "files_analyzed": len(file_contributions),
        }

    async def compute_collaboration_graph(
        self,
        repository_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
        """Compute collaboration metrics between developers.
        
        Analyzes shared file modifications to determine who works together.
        
        Args:
            repository_id: Optional repository filter (all repos if None)
            
        Returns:
            Collaboration graph statistics
        """
        # Get all ownership records for the org
        query = select(CodeOwnership).where(
            CodeOwnership.repository_id.in_(
                select(Repository.id).where(
                    Repository.organization_id == self.organization_id
                )
            )
        )
        if repository_id:
            query = query.where(CodeOwnership.repository_id == repository_id)

        result = await self.session.execute(query)
        ownership_records = result.scalars().all()

        # Group by file to find collaborators
        file_developers: Dict[str, List[UUID]] = defaultdict(list)
        for record in ownership_records:
            file_developers[record.path].append(record.developer_id)

        # Count shared files between developer pairs
        pair_shared_files: Dict[Tuple[UUID, UUID], int] = defaultdict(int)
        for file_path, developers in file_developers.items():
            # Generate all pairs
            for i, dev_a in enumerate(developers):
                for dev_b in developers[i+1:]:
                    # Ensure consistent ordering
                    pair = (min(dev_a, dev_b), max(dev_a, dev_b))
                    pair_shared_files[pair] += 1

        # Create/update Collaboration records
        collaborations_created = 0
        for (dev_a, dev_b), shared_count in pair_shared_files.items():
            # Calculate collaboration score
            total_files = len(file_developers)
            score = shared_count / total_files if total_files > 0 else 0

            result = await self.session.execute(
                select(Collaboration).where(
                    Collaboration.organization_id == self.organization_id,
                    Collaboration.developer_a_id == dev_a,
                    Collaboration.developer_b_id == dev_b,
                )
            )
            collab = result.scalar_one_or_none()

            if collab:
                collab.shared_files = shared_count
                collab.collaboration_score = score
                collab.last_interaction_at = datetime.now(timezone.utc)
            else:
                collab = Collaboration(
                    organization_id=self.organization_id,
                    developer_a_id=dev_a,
                    developer_b_id=dev_b,
                    shared_files=shared_count,
                    collaboration_score=score,
                    last_interaction_at=datetime.now(timezone.utc),
                )
                self.session.add(collab)
                collaborations_created += 1

        await self.session.commit()

        return {
            "collaborations_created": collaborations_created,
            "collaborations_updated": len(pair_shared_files) - collaborations_created,
            "total_pairs": len(pair_shared_files),
        }

    async def compute_bus_factor(
        self,
        repository_id: UUID,
    ) -> Dict[str, Any]:
        """Compute the bus factor for a repository.
        
        Bus factor = minimum number of developers that would need to leave
        for the project to stall (based on code ownership concentration).
        
        Args:
            repository_id: Repository to analyze
            
        Returns:
            Bus factor analysis results
        """
        # Get ownership records
        result = await self.session.execute(
            select(CodeOwnership).where(
                CodeOwnership.repository_id == repository_id,
                CodeOwnership.ownership_type == OwnershipType.FILE,
            )
        )
        records = result.scalars().all()

        if not records:
            return {"bus_factor": 0, "at_risk_files": [], "top_owners": []}

        # Aggregate ownership by developer
        developer_ownership: Dict[UUID, float] = defaultdict(float)
        file_owners: Dict[str, List[Tuple[UUID, float]]] = defaultdict(list)

        for record in records:
            developer_ownership[record.developer_id] += record.ownership_score
            file_owners[record.path].append((record.developer_id, record.ownership_score))

        # Sort developers by total ownership
        sorted_devs = sorted(
            developer_ownership.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        # Calculate bus factor: how many top developers own 50%+ of the code
        total_ownership = sum(developer_ownership.values())
        cumulative = 0
        bus_factor = 0
        top_owners = []

        for dev_id, ownership in sorted_devs:
            cumulative += ownership
            bus_factor += 1

            # Get developer name
            dev_result = await self.session.execute(
                select(Developer).where(Developer.id == dev_id)
            )
            dev = dev_result.scalar_one_or_none()
            top_owners.append({
                "developer_id": str(dev_id),
                "name": dev.name if dev else "Unknown",
                "email": dev.email if dev else "",
                "ownership_pct": (ownership / total_ownership * 100) if total_ownership > 0 else 0,
            })

            if cumulative >= total_ownership * 0.5:
                break

        # Find at-risk files (owned primarily by one person)
        at_risk_files = []
        for file_path, owners in file_owners.items():
            if owners:
                top_owner = max(owners, key=lambda x: x[1])
                if top_owner[1] > 0.8:  # >80% ownership by one person
                    at_risk_files.append({
                        "path": file_path,
                        "owner_pct": top_owner[1] * 100,
                    })

        # Store as insight
        insight = TeamInsight(
            organization_id=self.organization_id,
            repository_id=repository_id,
            insight_type="bus_factor",
            insight_data={
                "bus_factor": bus_factor,
                "top_owners": top_owners[:5],
                "at_risk_files_count": len(at_risk_files),
            },
        )
        self.session.add(insight)
        await self.session.commit()

        return {
            "bus_factor": bus_factor,
            "at_risk_files": at_risk_files[:10],
            "top_owners": top_owners,
        }

    async def get_developer_profile(
        self,
        developer_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        """Get detailed developer profile.
        
        Args:
            developer_id: Developer to get profile for
            
        Returns:
            Developer profile data or None if not found
        """
        result = await self.session.execute(
            select(Developer).where(
                Developer.id == developer_id,
                Developer.organization_id == self.organization_id,
            )
        )
        developer = result.scalar_one_or_none()

        if not developer:
            return None

        # Get ownership data
        ownership_result = await self.session.execute(
            select(CodeOwnership).where(
                CodeOwnership.developer_id == developer_id,
            ).order_by(CodeOwnership.ownership_score.desc()).limit(10)
        )
        top_files = ownership_result.scalars().all()

        # Get collaboration data
        collab_result = await self.session.execute(
            select(Collaboration, Developer).where(
                and_(
                    Collaboration.organization_id == self.organization_id,
                    (Collaboration.developer_a_id == developer_id) |
                    (Collaboration.developer_b_id == developer_id),
                )
            ).join(
                Developer,
                and_(
                    Developer.id != developer_id,
                    (Developer.id == Collaboration.developer_a_id) |
                    (Developer.id == Collaboration.developer_b_id),
                )
            ).order_by(Collaboration.collaboration_score.desc()).limit(5)
        )
        top_collaborators = []
        for collab, other_dev in collab_result:
            top_collaborators.append({
                "developer_id": str(other_dev.id),
                "name": other_dev.name,
                "email": other_dev.email,
                "shared_files": collab.shared_files,
                "collaboration_score": collab.collaboration_score,
            })

        return {
            "id": str(developer.id),
            "name": developer.name,
            "email": developer.email,
            "total_commits": developer.total_commits,
            "total_lines_added": developer.total_lines_added,
            "total_lines_removed": developer.total_lines_removed,
            "first_commit_at": developer.first_commit_at.isoformat() if developer.first_commit_at else None,
            "last_commit_at": developer.last_commit_at.isoformat() if developer.last_commit_at else None,
            "expertise_areas": developer.expertise_areas or {},
            "top_owned_files": [
                {"path": f.path, "ownership_score": f.ownership_score}
                for f in top_files
            ],
            "top_collaborators": top_collaborators,
        }

    async def get_team_overview(
        self,
        repository_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
        """Get team overview dashboard data.
        
        Args:
            repository_id: Optional repository filter
            
        Returns:
            Team overview statistics
        """
        # Get developer count and stats
        dev_query = select(
            func.count(Developer.id),
            func.sum(Developer.total_commits),
            func.avg(Developer.total_commits),
        ).where(Developer.organization_id == self.organization_id)

        dev_result = await self.session.execute(dev_query)
        dev_count, total_commits, avg_commits = dev_result.one()

        # Get top contributors
        top_devs_result = await self.session.execute(
            select(Developer).where(
                Developer.organization_id == self.organization_id,
            ).order_by(Developer.total_commits.desc()).limit(5)
        )
        top_contributors = [
            {
                "id": str(d.id),
                "name": d.name,
                "email": d.email,
                "commits": d.total_commits,
            }
            for d in top_devs_result.scalars()
        ]

        # Get recent insights
        insights_result = await self.session.execute(
            select(TeamInsight).where(
                TeamInsight.organization_id == self.organization_id,
            ).order_by(TeamInsight.computed_at.desc()).limit(5)
        )
        recent_insights = [
            {
                "type": i.insight_type,
                "data": i.insight_data,
                "computed_at": i.computed_at.isoformat(),
            }
            for i in insights_result.scalars()
        ]

        return {
            "developer_count": dev_count or 0,
            "total_commits": total_commits or 0,
            "avg_commits_per_developer": float(avg_commits or 0),
            "top_contributors": top_contributors,
            "recent_insights": recent_insights,
        }
