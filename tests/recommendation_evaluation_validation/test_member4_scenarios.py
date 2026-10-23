"""The scenario matrix: every situation of the research brief, carried through feasibility, ranking and the YAML proposal."""

import pytest
import yaml

from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
from tests.support.member4_scenarios import Case, catalogue

CASES = catalogue()


def candidate_for(live, case):
    """The candidate the case is about: the pair for a combination, else the service's candidate of that strategy."""
    return next((c for c in live.report.recommendations
                 if (c.type == "COMBINATION" if case.strategy == "COMBINATION"
                     else (c.service == case.service and c.type == case.strategy))), None)


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_the_chain_says_what_the_situation_calls_for(case: Case):
    live = case.scenario.evaluate()
    candidate = candidate_for(live, case)

    if case.feasibility is None:
        assert candidate is None and live.report.recommendations == []
        proposal = build_yaml_proposal(live, f"rec-productpage-view-{case.service}-topology-spread")
    else:
        assert candidate is not None, "the engine produced no candidate for this situation"
        assert live.evidence["feasibility"][candidate.id]["status"] == case.feasibility
        assert (live.report.selected_recommendation_id == candidate.id) == case.selected
        proposal = build_yaml_proposal(live, candidate.id)

    assert proposal.code == (None if case.yaml == "GENERATED" else case.yaml)
    if case.yaml == "GENERATED":
        assert proposal.status == "GENERATED" and proposal.yaml
        assert len([d for d in yaml.safe_load_all(proposal.yaml) if d]) == case.documents
    else:
        assert proposal.status == "REFUSED" and proposal.yaml is None and proposal.patches == ()


@pytest.mark.parametrize("case", [c for c in CASES if c.yaml != "GENERATED"], ids=lambda c: c.name)
def test_no_situation_that_is_not_verified_feasible_ever_yields_applicable_yaml(case: Case):
    live = case.scenario.evaluate()

    for candidate in live.report.recommendations:
        proposal = build_yaml_proposal(live, candidate.id)
        if proposal.status == "GENERATED":
            # only a verified-feasible, RANKED candidate may get text (not only the selected one: a lower-ranked candidate can be
            # approved with a written reason), and only offline text is non-applicable
            assert candidate.feasible and live.evidence["ranking"]["candidates"][candidate.id]["rank"] is not None
        assert proposal.applicable is False      # offline evidence is never applicable


def test_the_benchmark_is_deterministic():
    first = [(c.name, [(r.id, r.feasible, r.score) for r in c.scenario.evaluate().report.recommendations]) for c in catalogue()]
    second = [(c.name, [(r.id, r.feasible, r.score) for r in c.scenario.evaluate().report.recommendations]) for c in catalogue()]

    assert first == second


def test_the_catalogue_covers_every_situation_of_the_brief():
    covers = " | ".join(c.covers for c in CASES)

    for needle in ("concentrated", "correctly distributed", "single-replica", "CPU", "memory", "unschedulable", "taints",
                   "conflicting", "several mandatory", "no feasible", "measurable improvement", "anti-affinity",
                   "one more replica", "mixing a spread and a replica increase"):
        assert needle in covers, needle


def test_the_research_runner_reports_the_engines_selection_and_labels_itself_offline():
    from experiments.member4_offline_benchmark.run_benchmark import LABEL, run

    rows = {row["scenario"]: row for row in run()}

    assert "not evidence about any cluster" in LABEL
    assert {c.name for c in CASES} == set(rows)
    assert all(row["yamlApplicable"] is False for row in rows.values())      # offline text is never applicable
    for case in CASES:
        row = rows[case.name]
        # the row is about the engine's SELECTION: a selected candidate is eligible, so it always has a proposal
        assert (row["selected"] is not None) == (row["yaml"] == "GENERATED")
        if case.selected:
            assert row["selected"] is not None
        if row["yaml"] == "GENERATED":     # whatever was selected (a pair included) is validated as one proposal
            assert row["validationIfSchedulerSpreads"]["validated"] is True, case.name
            assert row["validationIfSchedulerChangesNothing"]["validated"] is False, case.name
