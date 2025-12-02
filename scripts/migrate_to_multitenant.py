#!/usr/bin/env python3
"""Migration script for existing single-tenant to multi-tenant graph storage.

This script provisions graph storage for all existing organizations that don't
already have a graph_database_name set.

Usage:
    python scripts/migrate_to_multitenant.py

    # Dry run (show what would be done)
    python scripts/migrate_to_multitenant.py --dry-run

    # Specify backend
    python scripts/migrate_to_multitenant.py --backend falkordb

Environment Variables:
    DATABASE_URL: PostgreSQL connection string for organization data
    REPOTOIRE_DB_TYPE: Graph database backend (neo4j or falkordb)
    REPOTOIRE_NEO4J_URI: Neo4j connection URI
    REPOTOIRE_FALKORDB_HOST: FalkorDB host
"""

import argparse
import asyncio
import logging
import os
import sys
from uuid import UUID

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from repotoire.db.models import Organization
from repotoire.graph.tenant_factory import GraphClientFactory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def get_organizations_without_graph(session: AsyncSession) -> list[Organization]:
    """Get all organizations that don't have graph storage configured."""
    result = await session.execute(
        select(Organization).where(Organization.graph_database_name.is_(None))
    )
    return list(result.scalars().all())


async def get_all_organizations(session: AsyncSession) -> list[Organization]:
    """Get all organizations."""
    result = await session.execute(select(Organization))
    return list(result.scalars().all())


async def provision_organization(
    factory: GraphClientFactory,
    org: Organization,
    dry_run: bool = False,
) -> str | None:
    """Provision graph storage for a single organization.

    Returns the graph name on success, None on failure.
    """
    try:
        if dry_run:
            graph_name = factory._generate_graph_name(org.id, org.slug)
            logger.info(f"  [DRY RUN] Would provision: {org.slug} -> {graph_name}")
            return graph_name

        graph_name = await factory.provision_tenant(org.id, org.slug)
        logger.info(f"  Provisioned: {org.slug} -> {graph_name}")
        return graph_name
    except Exception as e:
        logger.error(f"  Failed to provision {org.slug}: {e}")
        return None


async def migrate_organizations(
    database_url: str,
    backend: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    """Migrate existing organizations to multi-tenant graph storage.

    Args:
        database_url: PostgreSQL connection string
        backend: Graph database backend (neo4j or falkordb)
        dry_run: If True, show what would be done without making changes
        limit: Maximum number of organizations to process

    Returns:
        Dictionary with migration statistics
    """
    # Create async engine
    engine = create_async_engine(database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Create factory
    factory_kwargs = {}
    if backend:
        factory_kwargs["backend"] = backend
    factory = GraphClientFactory(**factory_kwargs)

    stats = {
        "total": 0,
        "already_provisioned": 0,
        "provisioned": 0,
        "failed": 0,
        "skipped": 0,
    }

    async with async_session() as session:
        # Get organizations
        if dry_run:
            orgs = await get_all_organizations(session)
        else:
            orgs = await get_organizations_without_graph(session)

        stats["total"] = len(orgs)

        if limit:
            orgs = orgs[:limit]
            logger.info(f"Processing {len(orgs)} of {stats['total']} organizations (limit: {limit})")
        else:
            logger.info(f"Processing {len(orgs)} organizations")

        for org in orgs:
            # Check if already provisioned
            if org.graph_database_name:
                stats["already_provisioned"] += 1
                logger.info(f"  Already provisioned: {org.slug} -> {org.graph_database_name}")
                continue

            # Provision graph storage
            graph_name = await provision_organization(factory, org, dry_run)

            if graph_name:
                stats["provisioned"] += 1

                # Update organization record
                if not dry_run:
                    await session.execute(
                        update(Organization)
                        .where(Organization.id == org.id)
                        .values(
                            graph_database_name=graph_name,
                            graph_backend=factory.backend,
                        )
                    )
            else:
                stats["failed"] += 1

        # Commit all changes
        if not dry_run:
            await session.commit()
            logger.info("Committed all changes to database")

    # Close factory
    factory.close_all()

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Migrate existing organizations to multi-tenant graph storage"
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL connection URL (default: DATABASE_URL env var)",
    )
    parser.add_argument(
        "--backend",
        choices=["neo4j", "falkordb"],
        default=None,
        help="Graph database backend (default: REPOTOIRE_DB_TYPE or neo4j)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of organizations to process",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.database_url:
        logger.error("DATABASE_URL environment variable or --database-url required")
        sys.exit(1)

    # Convert sync URL to async if needed
    database_url = args.database_url
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    logger.info("=" * 60)
    logger.info("Multi-Tenant Graph Storage Migration")
    logger.info("=" * 60)
    logger.info(f"Backend: {args.backend or os.environ.get('REPOTOIRE_DB_TYPE', 'neo4j')}")
    logger.info(f"Dry run: {args.dry_run}")
    if args.limit:
        logger.info(f"Limit: {args.limit}")
    logger.info("=" * 60)

    try:
        stats = asyncio.run(migrate_organizations(
            database_url=database_url,
            backend=args.backend,
            dry_run=args.dry_run,
            limit=args.limit,
        ))
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)

    # Print summary
    logger.info("=" * 60)
    logger.info("Migration Summary")
    logger.info("=" * 60)
    logger.info(f"Total organizations: {stats['total']}")
    logger.info(f"Already provisioned: {stats['already_provisioned']}")
    logger.info(f"Newly provisioned: {stats['provisioned']}")
    logger.info(f"Failed: {stats['failed']}")
    logger.info("=" * 60)

    if stats["failed"] > 0:
        logger.warning(f"{stats['failed']} organization(s) failed to provision")
        sys.exit(1)

    if args.dry_run:
        logger.info("Dry run complete. Run without --dry-run to apply changes.")
    else:
        logger.info("Migration complete!")


if __name__ == "__main__":
    main()
