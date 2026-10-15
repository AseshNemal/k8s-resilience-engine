"""
Quick CLI Runner for Multi-Service Parametric Ensemble Evaluation.
Run this directly to see the full statistical report in the terminal.
"""
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from experiments.placement_drift.parametric_ensemble_evaluator import ParametricEnsembleEvaluator

if __name__ == "__main__":
    evaluator = ParametricEnsembleEvaluator()
    metrics = evaluator.run_ensemble()
    report = ParametricEnsembleEvaluator.generate_markdown_report(metrics)
    print(report)
