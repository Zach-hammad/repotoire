#!/usr/bin/env python3
"""Train the seed GBDT model for repotoire's FP classifier.

Usage:
    uv run scripts/train_model.py --data labeled_findings.json --output repotoire-cli/models/seed_model.json

Trains XGBoost on manually labeled findings, exports to JSON format
compatible with gbdt-rs (XGBoost dump format).
"""

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description="Train seed GBDT model")
    parser.add_argument("--data", required=True, help="Path to labeled findings JSON")
    parser.add_argument("--output", required=True, help="Output model JSON path")
    parser.add_argument("--trees", type=int, default=100, help="Number of trees")
    parser.add_argument("--depth", type=int, default=6, help="Max tree depth")
    parser.add_argument("--lr", type=float, default=0.1, help="Learning rate")
    args = parser.parse_args()

    try:
        import xgboost as xgb
        import numpy as np
    except ImportError:
        print("Install dependencies: uv pip install xgboost numpy")
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

    print(f"Training on {len(X)} examples ({sum(y)} TP, {len(y) - sum(y)} FP)")
    print(f"Feature dimensions: {X.shape[1]}")

    # Train
    dtrain = xgb.DMatrix(X, label=y)
    params = {
        "max_depth": args.depth,
        "eta": args.lr,
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "nthread": 4,
        "verbosity": 1,
    }

    model = xgb.train(
        params,
        dtrain,
        num_boost_round=args.trees,
        evals=[(dtrain, "train")],
        verbose_eval=10,
    )

    # Export to JSON dump (gbdt-rs compatible)
    model.dump_model(args.output, dump_format="json")
    print(f"Model saved to {args.output}")

    # Print feature importance
    importance = model.get_score(importance_type="gain")
    if importance:
        print("\nTop 10 features by gain:")
        sorted_imp = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:10]
        for feat, gain in sorted_imp:
            print(f"  {feat}: {gain:.4f}")


if __name__ == "__main__":
    main()
