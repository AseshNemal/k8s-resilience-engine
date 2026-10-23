"""A validation run built on a declared workflow is recorded as conditional on that declaration, and kept apart from the rest."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.core.database import get_engine
from app.engine.recommendation_evaluation_validation import live_pipeline
from app.engine.recommendation_evaluation_validation.approval import ResearchApprovalService
from app.engine.recommendation_evaluation_validation.declared_workflow import get_declared_workflow
from app.engine.recommendation_evaluation_validation.validation import (
    APPLY,
    FAIL_NODE,
    RunState,
    TestTarget,
    ValidationPlan,
    ValidationRun,
    authorize,
)
from app.engine.recommendation_evaluation_validation.validation.experiment_record import ExperimentRecorder
from app.engine.recommendation_evaluation_validation.validation.experiment_repository import ExperimentRepository
from app.engine.recommendation_evaluation_validation.yaml_proposal import build_yaml_proposal
from app.shared.schemas.deployment import DeploymentGraph
from tests.recommendation_evaluation_validation.test_independent_inputs import (  # noqa: F401  (fixtures and helpers)
    concentrated_db,
    declared,
    no_frozen_workflow,
)
from tests.recommendation_evaluation_validation.test_research_approval import ACK, evaluate_declared
from tests.support.member4_approvals import InMemoryApprovals, approved_for_test
from tests.support.member4_harness import bookinfo_scenario
from tests.support.member4_test_cluster import FakeTestCluster

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
TARGET = TestTarget("kind-test", "test")


class RealLooking(FakeTestCluster):
    """A test cluster that CLAIMS to be real, only so the measurement filter can be exercised. It touches nothing."""

    def describe(self):
        return {"evidenceClass": "REAL_CLUSTER", "target": self.target.name, "cluster": {}, "namespace": "shop"}


@pytest.fixture
def session(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'research.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(config, "head")
    with Session(get_engine(url)) as db:
        yield db


def run_and_record(session, plan, approval, cluster):
    repo = ExperimentRepository(session)
    run = ValidationRun(plan, cluster)
    recorder = ExperimentRecorder(repo, clock=lambda: NOW)
    experiment_id = recorder.start(plan, approval, cluster, run.thresholds)
    result = recorder.execute(run, experiment_id, authorize(plan, APPLY, "asesh"), authorize(plan, FAIL_NODE, "asesh"))
    return result, repo.get(experiment_id), repo


def declared_run(session, use_cluster):
    use_cluster(concentrated_db())
    live = evaluate_declared()
    service = ResearchApprovalService(InMemoryApprovals())
    selected = live.report.selected_recommendation_id
    service.request_research_approval(selected, "v1", live, acknowledged_by="asesh", acknowledgement=ACK,
                                      proposal_yaml=build_yaml_proposal(live, selected).yaml)
    approval = service.approve(selected, "v1", "", decided_by="asesh", evaluate=lambda _a: evaluate_declared())
    workflow = get_declared_workflow("checkout").graph
    plan = ValidationPlan.from_approval(approval, evaluate_declared(), workflow, TARGET, allow_declared_workflow=True)
    graph = DeploymentGraph.model_validate(live.details["currentDeployment"])
    return plan, approval, graph


def test_a_declared_plan_runs_through_the_validation_pipeline_and_the_record_says_it_rests_on_a_declaration(
        session, use_cluster, declared, no_frozen_workflow):
    plan, approval, graph = declared_run(session, use_cluster)

    result, record, _ = run_and_record(session, plan, approval, FakeTestCluster(graph))

    assert result.state is RunState.COMPLETED and record.status == "COMPLETED"
    provenance = record.provenance
    assert provenance["workflowBasis"] == "DECLARED" and provenance["conditionalOnDeclaration"] is True
    assert provenance["declaredWorkflow"]["declarationSha256"] == plan.declaration["declarationSha256"]
    assert provenance["declaredWorkflow"]["observed"] is False
    assert provenance["researchAcknowledgement"]["by"] == "asesh" and "disposable test cluster" in provenance["researchAcknowledgement"]["scope"]
    assert record.proposal["approval"]["decidedBy"] == "asesh"
    assert record.workflow["mandatoryServices"] == ["web", "db"]          # the declared workflow is stored with the experiment


def test_the_measurement_filter_keeps_declared_and_frozen_results_apart(session, use_cluster, declared, no_frozen_workflow):
    plan, approval, graph = declared_run(session, use_cluster)
    _, declared_record, repo = run_and_record(session, plan, approval, RealLooking(graph))

    scenario = bookinfo_scenario("concentrated")
    frozen_approval, frozen_live = approved_for_test(scenario)
    frozen_plan = ValidationPlan.from_approval(frozen_approval, frozen_live, scenario.workflow, TARGET)
    _, frozen_record, _ = run_and_record(session, frozen_plan, frozen_approval, RealLooking(scenario.deployment))

    assert frozen_record.provenance["workflowBasis"] == "FROZEN" and frozen_record.provenance["conditionalOnDeclaration"] is False
    assert {r.experiment_id for r in repo.list_measurements()} == {declared_record.experiment_id, frozen_record.experiment_id}
    assert [r.experiment_id for r in repo.list_measurements("DECLARED")] == [declared_record.experiment_id]
    assert [r.experiment_id for r in repo.list_measurements("FROZEN")] == [frozen_record.experiment_id]


def test_a_run_on_a_test_double_is_stored_but_still_never_listed_even_when_declared(session, use_cluster, declared, no_frozen_workflow):
    plan, approval, graph = declared_run(session, use_cluster)

    _, record, repo = run_and_record(session, plan, approval, FakeTestCluster(graph))

    assert record.evidence_class == "TEST_DOUBLE" and repo.list_measurements() == [] and repo.list_measurements("DECLARED") == []
