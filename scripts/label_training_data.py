#!/usr/bin/env python3
"""Supervised labeling of training data for the GBDT classifier.

Applies rule-based TP/FP labels based on detector semantics, finding context,
and known patterns. Much more accurate than bootstrap heuristics.

Usage:
    python3 scripts/label_training_data.py --input scripts/training_data/merged.json --output scripts/training_data/labeled.json
"""

import argparse
import json
import re
import sys
from collections import Counter


# ── Detector-level rules ──────────────────────────────────────────────────
# These encode domain knowledge about when each detector's findings are
# reliably TP or FP based on the finding title/description.

def label_sample(sample: dict) -> tuple[bool, float, str]:
    """Return (is_tp, confidence, reason) for a single finding.

    confidence: 0.0-1.0, where 1.0 = certain
    Returns None for is_tp if ambiguous (needs manual review).
    """
    detector = sample.get("detector", "")
    title = sample.get("title", "")
    desc = sample.get("description", "")
    severity = sample.get("severity", "")
    file_path = sample.get("file", "")

    title_lower = title.lower()
    desc_lower = desc.lower()
    file_lower = file_path.lower()

    # ── Test file findings are usually FP ──
    is_test_file = any(p in file_lower for p in [
        "/test/", "/tests/", "/spec/", "/specs/", "test_", "_test.",
        ".test.", ".spec.", "/testing/", "/fixtures/", "/mocks/",
        "/conftest", "testcase", "/benchmark", "/bench/", "/examples/",
    ])

    # ── Vendor/generated file findings are FP ──
    is_vendor = any(p in file_lower for p in [
        "/vendor/", "/node_modules/", "/dist/", "/build/",
        ".min.", ".generated.", "/gen/", "pb.go", ".pb.",
        "/third_party/", "/external/",
    ])
    if is_vendor:
        return (False, 0.95, "vendor/generated file")

    # ── Security detectors ──
    if detector in ("SQLInjectionDetector", "SqlInjectionDetector"):
        if is_test_file:
            return (False, 0.9, "SQL injection in test file")
        # String concat in query = likely TP
        if any(w in desc_lower for w in ["concatenat", "f-string", "format(", ".format", "f\""]):
            return (True, 0.9, "SQL string concatenation")
        return (True, 0.7, "SQL injection finding")

    if detector == "XssDetector":
        if is_test_file:
            return (False, 0.9, "XSS in test file")
        return (True, 0.7, "XSS finding")

    if detector == "CommandInjectionDetector":
        if is_test_file:
            return (False, 0.9, "command injection in test file")
        if any(w in desc_lower for w in ["subprocess", "os.system", "exec(", "shell=true"]):
            return (True, 0.85, "command injection with shell execution")
        return (True, 0.7, "command injection finding")

    if detector == "PathTraversalDetector":
        if is_test_file:
            return (False, 0.9, "path traversal in test file")
        return (True, 0.7, "path traversal finding")

    if detector == "InsecureCryptoDetector":
        if "md5" in desc_lower or "sha1" in desc_lower:
            # MD5/SHA1 for non-security purposes (checksums, caching) are FP
            if any(w in desc_lower for w in ["hash", "checksum", "cache", "etag", "digest"]):
                return (True, 0.5, "weak hash - possibly non-security use")
            return (True, 0.8, "weak crypto algorithm")
        return (True, 0.7, "insecure crypto")

    if detector == "SecretsDetector":
        if is_test_file:
            return (False, 0.95, "secret in test file (likely test fixture)")
        if "example" in desc_lower or "placeholder" in desc_lower:
            return (False, 0.9, "example/placeholder secret")
        return (True, 0.8, "hardcoded secret")

    # ── Code quality detectors ──
    if detector == "DeadCodeDetector":
        if is_test_file:
            return (False, 0.9, "dead code in test file")
        # Dead code in public API files is often FP (exported for users)
        if any(w in title_lower for w in ["__init__", "index.", "mod.rs", "lib.rs"]):
            return (False, 0.7, "dead code in module entry point")
        return (True, 0.6, "dead code finding")

    if detector == "UnreachableCodeDetector":
        if is_test_file:
            return (False, 0.85, "unreachable code in test")
        return (True, 0.65, "unreachable code")

    if detector == "DeadStoreDetector":
        if is_test_file:
            return (False, 0.85, "dead store in test")
        return (True, 0.7, "dead store")

    if detector == "DeepNestingDetector":
        return (True, 0.8, "deep nesting is structural")

    if detector == "LongMethodsDetector":
        return (True, 0.75, "long method is structural")

    if detector == "MagicNumbersDetector":
        if is_test_file:
            return (False, 0.9, "magic numbers in tests are normal")
        # Common FP patterns
        if any(n in title_lower for n in ["0", "1", "2", "-1", "100", "1000"]):
            return (False, 0.6, "common constant, likely FP")
        return (True, 0.6, "magic number")

    if detector == "CommentedCodeDetector":
        if is_test_file:
            return (False, 0.7, "commented code in test")
        return (True, 0.6, "commented out code")

    if detector == "DebugCodeDetector":
        if is_test_file:
            return (False, 0.9, "debug code in test is normal")
        return (True, 0.8, "debug code in production")

    if detector == "DuplicateCodeDetector":
        if is_test_file:
            return (False, 0.8, "duplicate code in tests is normal")
        return (True, 0.7, "duplicate code")

    if detector == "EmptyCatchDetector":
        return (True, 0.85, "empty catch blocks are almost always bad")

    if detector == "BroadExceptionDetector":
        if is_test_file:
            return (False, 0.7, "broad exception in test")
        return (True, 0.7, "broad exception catch")

    # ── Code smell detectors (graph-based) ──
    if detector == "GodClassDetector":
        return (True, 0.8, "god class is structural")

    if detector == "FeatureEnvyDetector":
        if is_test_file:
            return (False, 0.8, "feature envy in test")
        return (True, 0.65, "feature envy")

    if detector == "DataClumpsDetector":
        return (True, 0.7, "data clumps are structural")

    if detector == "LazyClassDetector":
        if is_test_file:
            return (False, 0.8, "lazy class in test is normal")
        # Single-method classes in Go (interface satisfaction) or Rust (trait impls) are normal
        if file_lower.endswith(".go") or file_lower.endswith(".rs"):
            return (False, 0.6, "lazy class in Go/Rust (trait/interface impl)")
        return (True, 0.55, "lazy class")

    if detector == "LongParameterListDetector":
        if is_test_file:
            return (False, 0.7, "long params in test")
        return (True, 0.75, "long parameter list is structural")

    if detector == "InappropriateIntimacyDetector":
        if is_test_file:
            return (False, 0.85, "intimacy in test (accessing internals)")
        return (True, 0.65, "inappropriate intimacy")

    # ── Architecture detectors ──
    if detector == "CoreUtilityDetector":
        # Core utility is informational, not necessarily bad
        return (True, 0.5, "core utility is informational")

    if detector == "ShotgunSurgeryDetector":
        return (True, 0.7, "shotgun surgery risk")

    if detector == "ModuleCohesionDetector":
        return (True, 0.65, "module cohesion issue")

    if detector in ("DegreeCentralityDetector", "ArchitecturalBottleneckDetector"):
        return (True, 0.6, "architectural metric")

    # ── AI detectors ──
    if detector == "AIDuplicateBlockDetector":
        if is_test_file:
            return (False, 0.85, "AI duplicate in test")
        # High similarity = more likely TP
        pct_match = re.search(r'(\d+)%', title)
        if pct_match and int(pct_match.group(1)) >= 90:
            return (True, 0.85, "very high structural similarity")
        return (True, 0.65, "structural duplicate")

    if detector == "AIMissingTestsDetector":
        if is_test_file:
            return (False, 0.95, "missing tests finding IN test file")
        return (True, 0.6, "missing tests")

    if detector == "AIComplexitySpikeDetector":
        return (True, 0.7, "complexity spike")

    if detector == "AIChurnDetector":
        return (True, 0.55, "AI churn is informational")

    # ── Performance detectors ──
    if detector == "NPlusOneDetector":
        if is_test_file:
            return (False, 0.85, "N+1 in test")
        return (True, 0.6, "potential N+1 query")

    if detector == "RegexInLoopDetector":
        return (True, 0.8, "regex compilation in loop")

    # ── Framework detectors ──
    if detector in ("DjangoSecurityDetector", "ExpressSecurityDetector"):
        if is_test_file:
            return (False, 0.85, "framework security in test")
        return (True, 0.7, "framework security finding")

    if detector == "ReactHooksDetector":
        return (True, 0.8, "React hooks violation")

    # ── Async detectors ──
    if detector == "SyncInAsyncDetector":
        return (True, 0.75, "sync-in-async")

    if detector == "MissingAwaitDetector":
        return (True, 0.8, "missing await")

    # ── Rust-specific ──
    if detector == "UnwrapWithoutContextDetector":
        if is_test_file:
            return (False, 0.9, "unwrap in test is normal")
        return (True, 0.7, "unwrap without context")

    # ── Consensus findings ──
    if "Consensus[" in detector:
        # Multi-detector agreement = higher confidence TP
        return (True, 0.85, "multi-detector consensus")

    # ── Large file detector ──
    if "large file" in title_lower:
        return (True, 0.7, "large file is structural")

    # ── Nesting ──
    if "nesting" in title_lower:
        return (True, 0.75, "excessive nesting is structural")

    # ── Default: use severity as signal ──
    if is_test_file:
        return (False, 0.5, "unknown detector in test file")

    if severity in ("critical", "high"):
        return (True, 0.6, f"unknown detector, {severity} severity")

    return (True, 0.5, f"unknown detector: {detector}")


def main():
    parser = argparse.ArgumentParser(description="Label training data with supervised rules")
    parser.add_argument("--input", required=True, help="Input merged JSON")
    parser.add_argument("--output", required=True, help="Output labeled JSON")
    parser.add_argument("--min-confidence", type=float, default=0.0,
                       help="Only include samples with confidence >= threshold")
    args = parser.parse_args()

    with open(args.input) as f:
        data = json.load(f)

    if not data:
        print("No data found")
        sys.exit(1)

    labeled = []
    stats = Counter()
    detector_stats = {}  # detector -> {tp, fp}
    confidence_dist = Counter()
    reasons = Counter()

    for sample in data:
        is_tp, confidence, reason = label_sample(sample)

        if confidence < args.min_confidence:
            continue

        sample["is_tp"] = is_tp
        sample["weight"] = confidence
        sample["label_source"] = f"supervised:{reason}"
        labeled.append(sample)

        label = "tp" if is_tp else "fp"
        stats[label] += 1
        reasons[reason] += 1
        confidence_dist[f"{confidence:.1f}"] += 1

        det = sample.get("detector", "unknown")
        if det not in detector_stats:
            detector_stats[det] = {"tp": 0, "fp": 0}
        detector_stats[det][label] += 1

    # Write output
    with open(args.output, "w") as f:
        json.dump(labeled, f)

    # Print statistics
    total = len(labeled)
    tp = stats["tp"]
    fp = stats["fp"]

    print(f"=== Supervised Labeling Results ===")
    print(f"Total samples:     {total}")
    print(f"True positives:    {tp} ({100*tp/max(total,1):.1f}%)")
    print(f"False positives:   {fp} ({100*fp/max(total,1):.1f}%)")
    print(f"TP/FP ratio:       {tp/max(fp,1):.1f}:1")
    print()

    print(f"Confidence distribution:")
    for conf, count in sorted(confidence_dist.items()):
        print(f"  {conf}: {count}")
    print()

    print(f"Per-detector TP/FP breakdown:")
    for det, counts in sorted(detector_stats.items(), key=lambda x: -(x[1]["tp"] + x[1]["fp"])):
        total_det = counts["tp"] + counts["fp"]
        fp_rate = counts["fp"] / max(total_det, 1) * 100
        print(f"  {det:45s} TP:{counts['tp']:5d}  FP:{counts['fp']:5d}  ({fp_rate:.0f}% FP)")
    print()

    print(f"Top labeling reasons:")
    for reason, count in reasons.most_common(20):
        print(f"  {reason:45s} {count}")

    print(f"\nLabeled data saved to {args.output}")


if __name__ == "__main__":
    main()
