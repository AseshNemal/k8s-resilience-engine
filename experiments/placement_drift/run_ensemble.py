"""
CLI runner for the scenario ensemble summary.

Usage: python experiments/placement_drift/run_ensemble.py
Prints the Markdown summary computed from executed scenarios (dry-run fixtures by default).
"""
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from experiments.placement_drift.parametric_ensemble_evaluator import ParametricEnsembleEvaluator  # noqa: E402


if __name__ == "__main__":
    metrics = ParametricEnsembleEvaluator().run_ensemble()
    print(ParametricEnsembleEvaluator.generate_markdown_report(metrics))
