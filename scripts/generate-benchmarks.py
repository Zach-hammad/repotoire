#!/usr/bin/env python3
"""Generate ecosystem benchmark JSON files from PostHog data.

Queries PostHog HogQL API for analysis_complete events, computes percentile
distributions per segment (global, per-language, per-language+size), and writes
JSON files to benchmark-output/v1/.

Usage:
    POSTHOG_API_KEY=phk_... POSTHOG_PROJECT_ID=12345 python scripts/generate-benchmarks.py

    # Dry-run with fixture data (no PostHog needed):
    python scripts/generate-benchmarks.py --dry-run
"""

import json
import os
import sys
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# PostHog API config
POSTHOG_API_URL = "https://app.posthog.com/api/projects/{project_id}/query"
MIN_SAMPLE_SIZE = 5
OUTPUT_DIR = Path("benchmark-output/v1")
SCHEMA_VERSION = 1

SIZE_BUCKETS = [
    ("0-5k", 0, 5),
    ("5-10k", 5, 10),
    ("10-50k", 10, 50),
    ("50-100k", 50, 100),
    ("100k+", 100, float("inf")),
]


def kloc_to_bucket(kloc):
    for name, low, high in SIZE_BUCKETS:
        if low <= kloc < high:
            return name
    return "100k+"


def percentiles(values, ps=(25, 50, 75, 90)):
    if not values:
        return {f"p{p}": 0 for p in ps}
    sorted_v = sorted(values)
    result = {}
    for p in ps:
        k = (len(sorted_v) - 1) * p / 100
        f = int(k)
        c = f + 1
        if c >= len(sorted_v):
            result[f"p{p}"] = sorted_v[-1]
        else:
            result[f"p{p}"] = sorted_v[f] + (k - f) * (sorted_v[c] - sorted_v[f])
    return result


def query_posthog(api_key, project_id):
    """Query PostHog for analysis_complete events (last 90 days)."""
    import requests

    url = POSTHOG_API_URL.format(project_id=project_id)
    payload = {
        "query": {
            "kind": "HogQLQuery",
            "query": """
                SELECT
                    properties.repo_id as repo_id,
                    properties.score as score,
                    properties.grade as grade,
                    properties.pillar_structure as pillar_structure,
                    properties.pillar_quality as pillar_quality,
                    properties.pillar_architecture as pillar_architecture,
                    properties.primary_language as primary_language,
                    properties.total_kloc as total_kloc,
                    properties.graph_modularity as graph_modularity,
                    properties.graph_avg_degree as graph_avg_degree,
                    properties.graph_scc_count as graph_scc_count,
                    timestamp
                FROM events
                WHERE event = 'analysis_complete'
                  AND timestamp > now() - interval 90 day
                ORDER BY timestamp DESC
            """
        }
    }

    resp = requests.post(url, json=payload, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
    resp.raise_for_status()
    return resp.json().get("results", [])


def deduplicate_by_repo(rows):
    """Keep only the latest event per repo_id."""
    seen = {}
    for row in rows:
        repo_id = row[0]
        if repo_id and repo_id not in seen:
            seen[repo_id] = row
    return list(seen.values())


def build_segment(rows, language=None, kloc_bucket=None):
    """Build a benchmark segment JSON from a set of rows."""
    scores = [r[1] for r in rows if r[1] is not None]
    structures = [r[3] for r in rows if r[3] is not None]
    qualities = [r[4] for r in rows if r[4] is not None]
    architectures = [r[5] for r in rows if r[5] is not None]
    modularities = [r[8] for r in rows if r[8] is not None]
    avg_degrees = [r[9] for r in rows if r[9] is not None]
    scc_counts = [r[10] for r in rows if r[10] is not None]
    grades = [r[2] for r in rows if r[2] is not None]

    grade_dist = defaultdict(int)
    for g in grades:
        grade_dist[g] += 1
    total = len(grades) or 1
    grade_distribution = {g: round(c / total, 3) for g, c in grade_dist.items()}

    pct_zero_scc = len([s for s in scc_counts if s == 0]) / max(len(scc_counts), 1)

    return {
        "schema_version": SCHEMA_VERSION,
        "segment": {
            "language": language,
            "kloc_bucket": kloc_bucket,
        },
        "sample_size": len(rows),
        "sample_size_note": "unique repos (deduplicated by repo_id, latest event per repo)",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "score": percentiles(scores),
        "pillar_structure": percentiles(structures),
        "pillar_quality": percentiles(qualities),
        "pillar_architecture": percentiles(architectures),
        "graph_modularity": percentiles(modularities),
        "graph_avg_degree": percentiles(avg_degrees),
        "graph_scc_count": {
            "pct_zero": round(pct_zero_scc, 3),
            **percentiles([s for s in scc_counts if s > 0] or [0]),
        },
        "grade_distribution": grade_distribution,
        "top_detectors": [],  # TODO: query from detector findings
        "detector_accuracy": [],  # TODO: query from feedback events
        "avg_improvement_per_analysis": 0.0,  # TODO: compute from sequential events
    }


def generate_fixture_data():
    """Generate fixture data for dry-run testing."""
    import random
    random.seed(42)
    rows = []
    languages = ["rust", "python", "typescript", "go"]
    grades = ["A", "A-", "B+", "B", "B-", "C+", "C"]
    for i in range(200):
        lang = random.choice(languages)
        kloc = random.uniform(1, 150)
        score = random.gauss(65, 15)
        rows.append([
            f"repo_{i}",  # repo_id
            max(0, min(100, score)),  # score
            random.choice(grades),  # grade
            max(0, min(100, score + random.gauss(0, 5))),  # pillar_structure
            max(0, min(100, score + random.gauss(0, 8))),  # pillar_quality
            max(0, min(100, score + random.gauss(0, 6))),  # pillar_architecture
            lang,  # primary_language
            kloc,  # total_kloc
            random.uniform(0.3, 0.9),  # graph_modularity
            random.uniform(2, 15),  # graph_avg_degree
            random.choice([0, 0, 1, 2, 3, 5]),  # graph_scc_count
            f"2026-03-{random.randint(1,20):02d}T00:00:00Z",  # timestamp
        ])
    return rows


def main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("DRY RUN: using fixture data")
        rows = generate_fixture_data()
    else:
        api_key = os.environ.get("POSTHOG_API_KEY")
        project_id = os.environ.get("POSTHOG_PROJECT_ID")
        if not api_key or not project_id:
            print("Error: POSTHOG_API_KEY and POSTHOG_PROJECT_ID must be set", file=sys.stderr)
            sys.exit(1)
        rows = query_posthog(api_key, project_id)

    # Deduplicate by repo_id
    rows = deduplicate_by_repo(rows)
    print(f"Total unique repos: {len(rows)}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Global segment
    if len(rows) >= MIN_SAMPLE_SIZE:
        segment = build_segment(rows)
        path = OUTPUT_DIR / "global.json"
        path.write_text(json.dumps(segment, indent=2))
        print(f"Wrote: {path} ({len(rows)} repos)")

    # Per-language segments
    by_language = defaultdict(list)
    for row in rows:
        lang = row[6]
        if lang:
            by_language[lang].append(row)

    for lang, lang_rows in by_language.items():
        if len(lang_rows) >= MIN_SAMPLE_SIZE:
            segment = build_segment(lang_rows, language=lang)
            lang_dir = OUTPUT_DIR / "lang"
            lang_dir.mkdir(parents=True, exist_ok=True)
            path = lang_dir / f"{lang}.json"
            path.write_text(json.dumps(segment, indent=2))
            print(f"Wrote: {path} ({len(lang_rows)} repos)")

        # Per-language + size bucket
        by_size = defaultdict(list)
        for row in lang_rows:
            kloc = row[7]
            if kloc is not None:
                bucket = kloc_to_bucket(kloc)
                by_size[bucket].append(row)

        for bucket, bucket_rows in by_size.items():
            if len(bucket_rows) >= MIN_SAMPLE_SIZE:
                segment = build_segment(bucket_rows, language=lang, kloc_bucket=bucket)
                bucket_dir = OUTPUT_DIR / "lang" / lang
                bucket_dir.mkdir(parents=True, exist_ok=True)
                path = bucket_dir / f"{bucket}.json"
                path.write_text(json.dumps(segment, indent=2))
                print(f"Wrote: {path} ({len(bucket_rows)} repos)")

    print("Done!")


if __name__ == "__main__":
    main()
