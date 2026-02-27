#!/usr/bin/env python3
"""Train the seed GBDT model for repotoire's FP classifier.

Usage:
    uv run scripts/train_model.py --data labeled_findings.json --output repotoire-cli/models/seed_model.json

Trains XGBoost on labeled findings with 80/20 train/test split,
reports AUC/accuracy/precision/recall, and exports to JSON format
compatible with gbdt-rs (XGBoost dump format).
"""

import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="Train seed GBDT model")
    parser.add_argument("--data", required=True, help="Path to labeled findings JSON")
    parser.add_argument("--output", required=True, help="Output model JSON path")
    parser.add_argument("--trees", type=int, default=100, help="Number of trees")
    parser.add_argument("--depth", type=int, default=6, help="Max tree depth")
    parser.add_argument("--lr", type=float, default=0.1, help="Learning rate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--test-split", type=float, default=0.2, help="Test set fraction (default: 0.2)"
    )
    args = parser.parse_args()

    try:
        import xgboost as xgb
        import numpy as np
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import (
            accuracy_score,
            precision_score,
            recall_score,
            roc_auc_score,
        )
    except ImportError:
        print("Install dependencies: uv pip install xgboost numpy scikit-learn")
        sys.exit(1)

    # Load labeled data
    with open(args.data) as f:
        data = json.load(f)

    if not data:
        print("No training data found")
        sys.exit(1)

    # Extract features and labels
    X = np.array([d["features"] for d in data])
    y = np.array([1 if d["is_tp"] else 0 for d in data])

    # Get feature names from first sample
    feature_names = data[0].get("feature_names", [f"f{i}" for i in range(X.shape[1])])

    print("=== Dataset Statistics ===")
    print(f"Total samples:     {len(X)}")
    print(f"True positives:    {sum(y)} ({100 * sum(y) / len(y):.1f}%)")
    print(f"False positives:   {len(y) - sum(y)} ({100 * (len(y) - sum(y)) / len(y):.1f}%)")
    print(f"Feature dimensions: {X.shape[1]}")

    # Detector distribution
    detectors = {}
    for d in data:
        det = d.get("detector", "unknown")
        detectors[det] = detectors.get(det, 0) + 1
    print(f"Unique detectors:  {len(detectors)}")

    # Label source distribution
    sources = {}
    for d in data:
        src = d.get("label_source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    print(f"Label sources:     {sources}")
    print()

    # 80/20 train/test split (stratified)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=args.test_split, random_state=args.seed, stratify=y
    )

    print(f"=== Train/Test Split ===")
    print(f"Train: {len(X_train)} ({sum(y_train)} TP, {len(y_train) - sum(y_train)} FP)")
    print(f"Test:  {len(X_test)} ({sum(y_test)} TP, {len(y_test) - sum(y_test)} FP)")
    print()

    # Build sample weights from the data
    weight_map = {}
    for i, d in enumerate(data):
        weight_map[i] = d.get("weight", 1.0)

    # Reconstruct weights for train/test (use indices from split)
    # Since train_test_split doesn't preserve original indices directly,
    # we pass weights through the same split
    weights = np.array([d.get("weight", 1.0) for d in data])
    w_train, w_test = train_test_split(
        weights, test_size=args.test_split, random_state=args.seed, stratify=y
    )

    # Train
    dtrain = xgb.DMatrix(X_train, label=y_train, weight=w_train, feature_names=feature_names)
    dtest = xgb.DMatrix(X_test, label=y_test, weight=w_test, feature_names=feature_names)

    params = {
        "max_depth": args.depth,
        "eta": args.lr,
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "nthread": 4,
        "verbosity": 1,
        "seed": args.seed,
    }

    print("=== Training ===")
    model = xgb.train(
        params,
        dtrain,
        num_boost_round=args.trees,
        evals=[(dtrain, "train"), (dtest, "test")],
        verbose_eval=10,
    )

    # Evaluate on test set
    y_pred_proba = model.predict(dtest)
    y_pred = (y_pred_proba >= 0.5).astype(int)

    auc = roc_auc_score(y_test, y_pred_proba)
    accuracy = accuracy_score(y_test, y_pred)
    precision = precision_score(y_test, y_pred, zero_division=0)
    recall = recall_score(y_test, y_pred, zero_division=0)

    print()
    print("=== Test Set Metrics ===")
    print(f"AUC:       {auc:.4f}")
    print(f"Accuracy:  {accuracy:.4f} ({accuracy * 100:.1f}%)")
    print(f"Precision: {precision:.4f}")
    print(f"Recall:    {recall:.4f}")
    print()

    # Check quality gate
    if auc < 0.60:
        print(f"WARNING: AUC ({auc:.4f}) is below 0.60 — model may not be useful")
    elif auc < 0.70:
        print(f"NOTE: AUC ({auc:.4f}) is below 0.70 — acceptable for seed model")
    else:
        print(f"PASS: AUC ({auc:.4f}) meets quality threshold (>= 0.70)")

    # Export to JSON dump (gbdt-rs compatible)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    model.dump_model(args.output, dump_format="json")
    print(f"\nModel saved to {args.output}")

    # Also save gbdt-rs native format (binary model)
    native_path = args.output.replace(".json", "_native.json")
    model.save_model(native_path)
    print(f"XGBoost native model saved to {native_path}")

    # Save model stats alongside
    stats = {
        "total_samples": len(X),
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "tp_count": int(sum(y)),
        "fp_count": int(len(y) - sum(y)),
        "num_features": int(X.shape[1]),
        "feature_names": feature_names,
        "num_trees": args.trees,
        "max_depth": args.depth,
        "learning_rate": args.lr,
        "test_auc": float(auc),
        "test_accuracy": float(accuracy),
        "test_precision": float(precision),
        "test_recall": float(recall),
        "unique_detectors": len(detectors),
        "detector_distribution": detectors,
        "label_source_distribution": sources,
    }

    stats_path = args.output.replace(".json", "_stats.json")
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"Model stats saved to {stats_path}")

    # Feature importance
    importance = model.get_score(importance_type="gain")
    if importance:
        print("\nTop 10 features by gain:")
        sorted_imp = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:10]
        for feat, gain in sorted_imp:
            print(f"  {feat}: {gain:.4f}")

    # Verify all features are used
    used_features = len(importance) if importance else 0
    total_features = X.shape[1]
    print(f"\nFeatures used: {used_features}/{total_features}")
    if used_features < total_features:
        unused = set(feature_names) - set(importance.keys()) if importance else set(feature_names)
        print(f"Unused features: {unused}")


if __name__ == "__main__":
    main()
