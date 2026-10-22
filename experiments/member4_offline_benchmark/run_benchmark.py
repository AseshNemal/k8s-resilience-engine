"""Member 4 offline benchmark: every controlled scenario through evaluation, YAML proposal and a simulated validation.

RESEARCH RUN, OFFLINE. Nothing here reads a cluster or a kubeconfig, and nothing is applied. The scenarios are the
hand-written ones in ``tests/support/member4_scenarios.py``; the "test cluster" that stands in for observation is the
in-memory double in ``tests/support/member4_test_cluster.py``, whose scheduler is scripted. The numbers therefore show
how the engine behaves on those inputs and how the prediction-error figures are computed. They are NOT experimental
validation and must not be reported as such: that needs a real test cluster and a real controlled failure.

    python -m experiments.member4_offline_benchmark.run_benchmark            # a table
    python -m experiments.member4_offline_benchmark.run_benchmark --json     # machine-readable
"""

import argparse
import json

from app.engine.recommendation_evaluation_validation.validation import (
    APPLY,
    FAIL_NODE,
    TestTarget,
    ValidationPlan,
    ValidationRun,
    authorize,
)
from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
from tests.support.member4_approvals import as_live, requested_for_test
from tests.support.member4_scenarios import Case, catalogue
from tests.support.member4_test_cluster import FakeTestCluster, spread, stay

TARGET = TestTarget("offline-double", "test")
LABEL = "OFFLINE BENCHMARK: controlled scenarios and a scripted scheduler, not evidence about any cluster"


def _validate(case: Case, scheduler) -> dict:
    """Approve the selected candidate through the real gate, then run the validation against the double."""
    service, live, recommendation_id = requested_for_test(case.scenario)
    approval = service.approve(recommendation_id, "v1", "", decided_by="benchmark", evaluate=lambda _: live)
    plan = ValidationPlan.from_approval(approval, live, case.scenario.workflow, TARGET)
    cluster = FakeTestCluster(case.scenario.deployment, name=TARGET.name, scheduler=scheduler)
    result = ValidationRun(plan, cluster).execute(authorize(plan, APPLY, "benchmark"), authorize(plan, FAIL_NODE, "benchmark"))
    return {"state": result.state.value, "validated": result.validated, "predictionError": result.prediction_error,
            "riskReduction": result.comparison.get("riskReduction")}


def run() -> list[dict]:
    rows = []
    for case in catalogue():
        live = case.scenario.evaluate()
        selected = live.report.selected_recommendation_id
        proposal = build_yaml_proposal(live, selected) if selected else None
        row = {"scenario": case.name, "situation": case.covers, "candidates": len(live.report.recommendations),
               "selected": selected, "yaml": (proposal.status if proposal else "NONE"),
               "yamlApplicable": bool(proposal and proposal.applicable)}
        if proposal is not None and proposal.status == "GENERATED":
            row["validationIfSchedulerSpreads"] = _validate(case, spread)
            row["validationIfSchedulerChangesNothing"] = _validate(case, stay)
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    rows = run()
    if parser.parse_args().json:
        print(json.dumps({"label": LABEL, "rows": rows}, indent=2))
        return
    print(LABEL, "\n")
    print(f"{'scenario':28} {'cands':>5}  {'selected':8} {'yaml':9} {'err if spread':>13} {'err if no change':>16}")
    for row in rows:
        spread_run, stay_run = row.get("validationIfSchedulerSpreads"), row.get("validationIfSchedulerChangesNothing")
        print(f"{row['scenario']:28} {row['candidates']:>5}  {'yes' if row['selected'] else '-':8} {row['yaml']:9} "
              f"{(spread_run['predictionError'] if spread_run else '-')!s:>13} {(stay_run['predictionError'] if stay_run else '-')!s:>16}")


if __name__ == "__main__":
    main()
