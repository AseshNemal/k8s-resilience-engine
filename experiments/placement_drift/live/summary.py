"""Aggregates live run records per (scenario, configuration).

Statistics are over repetitions of the same condition (proposal section 11.4):
mean, sample standard deviation (n-1) and a two-sided 95% Student-t confidence interval.
Failed runs are counted and listed, never dropped.
"""
import json
import math
from pathlib import Path
from typing import Any

from experiments.placement_drift.parametric_ensemble_evaluator import t_critical_95


def describe(values: list[float], rate: bool = True) -> dict[str, Any]:
    """rate=True clips the interval to [0, 1] (shares); rate=False leaves it unbounded (e.g. ms)."""
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "ci95": None}
    mean = sum(values) / n
    if n < 2:
        return {"n": 1, "mean": round(mean, 4), "sd": None, "ci95": None}
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    half = t_critical_95(n - 1) * sd / math.sqrt(n)
    low, high = mean - half, mean + half
    if rate:
        low, high = max(0.0, low), min(1.0, high)
    return {"n": n, "mean": round(mean, 4), "sd": round(sd, 4), "ci95": [round(low, 4), round(high, 4)]}


def _values(rows: list[dict], key: str) -> list[float]:
    return [r[key] for r in rows if r.get(key) is not None]


def summarise(results_dir: Path) -> dict[str, Any]:
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(results_dir.glob("*__run*.json"))]
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["scenario_id"], row["config"]), []).append(row)

    conditions = []
    for (scenario, config), group in sorted(groups.items()):
        ok = [r for r in group if not r.get("failed")]
        conditions.append({
            "scenario_id": scenario,
            "config": config,
            "runs": len(group),
            "failed_runs": len(group) - len(ok),
            "unstable_runs": sum(1 for r in ok if not r["stable_after_disruption"]),
            "d_rate": describe(_values(ok, "d_rate")),
            "exposure_rate_t0": describe(_values(ok, "exposure_rate_t0")),
            "exposure_rate_t1": describe(_values(ok, "exposure_rate_t1")),
            "compliance_rate_t1": describe(_values(ok, "compliance_rate_t1")),
            "target_became_exposed": sum(1 for r in ok if r["target_status_t0"] != "EXPOSED" and r["target_status_t1"] == "EXPOSED"),
            "target_status_t1_counts": {s: sum(1 for r in ok if r["target_status_t1"] == s)
                                        for s in sorted({r["target_status_t1"] for r in ok})},
            "observation_ms": describe(_values(ok, "observation_ms_t0") + _values(ok, "observation_ms_t1"), rate=False),
        })
    # Folder name only: the absolute path would expose the local machine's directory layout.
    summary = {"results_dir": results_dir.name, "total_runs": len(rows), "conditions": conditions}
    (results_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (results_dir / "summary.md").write_text(to_markdown(summary), encoding="utf-8")
    return summary


def _fmt(stat: dict[str, Any], pct: bool = True) -> str:
    if stat["mean"] is None:
        return "n/a"
    f = (lambda v: f"{v * 100:.1f}%") if pct else (lambda v: f"{v:.1f}")
    text = f(stat["mean"])
    if stat["ci95"]:
        text += f" [{f(stat['ci95'][0])}, {f(stat['ci95'][1])}]"
    return text


def to_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Live Placement Drift Experiment Results",
        "",
        "Observed on a local KinD cluster (nodes are containers on one host). Values are mean "
        "[95% Student-t CI] over repetitions of each condition.",
        "",
        "| Scenario | Config | Runs (failed / unsettled) | D_rate | Exposure T0 | Exposure T1 | Compliance T1 | Target became exposed | Target status at T1 |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ]
    for c in summary["conditions"]:
        lines.append(
            f"| `{c['scenario_id']}` | {c['config']} | {c['runs']} ({c['failed_runs']} / {c['unstable_runs']}) | "
            f"{_fmt(c['d_rate'])} | {_fmt(c['exposure_rate_t0'])} | {_fmt(c['exposure_rate_t1'])} | "
            f"{_fmt(c['compliance_rate_t1'])} | {c['target_became_exposed']} | "
            f"{', '.join(f'{k}: {v}' for k, v in c['target_status_t1_counts'].items())} |"
        )
    return "\n".join(lines) + "\n"
