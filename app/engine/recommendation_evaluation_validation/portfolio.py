"""Several workflows sized as one: Member 4's adapter over Component 3's single-workflow predictor.

A recommendation protects workflows, and a cluster usually runs more than one. Component 3 sizes a failure for one workflow at a
time, so this module calls its public predictor once per workflow and weights each outcome by the workflow's criticality. It does
not touch Component 3, and the shared contract (``FailureImpactReport``) carries the result unchanged, so the generator, the
feasibility check, the simulator and the ranking work on it exactly as they do on one workflow.

* blast radius = 100 * (sum of criticality of the workflows that fail) / (sum of criticality of all of them). With one workflow it
  is that workflow's own 0 or 100; with equal criticality it is the share of workflows lost. The criticality is the one the workflow
  carries (Component 1's frozen graph, or the person's declaration). Nothing is defaulted: a portfolio whose criticality sums to zero
  has no weights and is refused.
* a workflow is lost when Component 3 says it is lost. This module never decides survival.
* root causes are those of every failing workflow, each offered once, so one candidate (spread ``reviews``) is considered once even
  when two workflows depend on it.

A portfolio evaluation carries its own evidence source, which no approval, apply or validation path accepts. What a person approves is
a change for one named workflow, chosen after the portfolio shows which change protects the most.
"""
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.shared.schemas.failure_impact import FailureImpactReport, NodeRisk
from app.shared.schemas.workflow import WorkflowGraph

PORTFOLIO_WORKFLOW_ID = "all-workflows"
PORTFOLIO_ANALYSIS_SOURCE = "portfolio-analysis"

NOT_APPROVABLE = (
    "This is an analysis across every workflow, so there is no single workflow to approve it for. Choose one workflow this change "
    "protects and review it there: approval is for one named workflow, on that workflow's own evidence."
)


@dataclass(frozen=True)
class WorkflowPortfolio:
    """The workflows evaluated together. It stands where a ``WorkflowGraph`` does in the recommendation pipeline."""

    workflows: tuple[WorkflowGraph, ...]

    def __post_init__(self) -> None:
        if not self.workflows:
            raise ValueError("A portfolio needs at least one workflow")
        ids = [workflow.workflow_id for workflow in self.workflows]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError("Duplicate workflow IDs: " + ", ".join(duplicates))
        if sum(workflow.criticality for workflow in self.workflows) <= 0:
            raise ValueError("The workflows' criticality sums to zero, so there is nothing to weigh the outcomes by")

    @property
    def workflow_id(self) -> str:
        return PORTFOLIO_WORKFLOW_ID

    @property
    def mandatory_services(self) -> list[str]:
        return sorted({name for workflow in self.workflows for name in workflow.mandatory_services})

    @property
    def optional_services(self) -> list[str]:
        mandatory = set(self.mandatory_services)
        return sorted({name for workflow in self.workflows for name in workflow.optional_services} - mandatory)

    @property
    def total_criticality(self) -> float:
        return sum(workflow.criticality for workflow in self.workflows)

    def model_copy(self, *, deep: bool = False) -> "WorkflowPortfolio":
        """Mirrors the pydantic call the single-workflow builders make, so a portfolio can be copied the same way."""
        return WorkflowPortfolio(tuple(workflow.model_copy(deep=deep) for workflow in self.workflows))

    def describe(self) -> list[dict[str, object]]:
        return [{"workflowId": workflow.workflow_id, "criticality": workflow.criticality} for workflow in self.workflows]


Predictor = Callable[[WorkflowGraph, object, str], FailureImpactReport]


def _share(portfolio: WorkflowPortfolio, losses: Sequence[float]) -> float:
    """The criticality-weighted share (percent) of workflows lost, given each workflow's own 0..100 blast radius."""
    weighted = sum(workflow.criticality * loss for workflow, loss in zip(portfolio.workflows, losses, strict=True))
    return round(weighted / portfolio.total_criticality, 2)


def predict_portfolio_impact(portfolio: WorkflowPortfolio, deployment, failed_node: str, predict: Predictor) -> FailureImpactReport:
    """Component 3's result for each workflow at one assumed node failure, combined by criticality.

    ``predict`` is Component 3's ``predict_failure_impact``; it is passed in so this module imports nothing from Component 3. Raises
    ValueError for an unknown node, as the single-workflow predictor does.
    """
    reports = [predict(workflow, deployment, failed_node) for workflow in portfolio.workflows]

    blast = _share(portfolio, [report.blast_radius for report in reports])
    per_node: dict[str, list[float]] = {}
    for report in reports:
        for risk in report.node_risk_ranking:
            per_node.setdefault(risk.node, []).append(risk.blast_radius)
    ranking = sorted((NodeRisk(node=node, blast_radius=_share(portfolio, losses)) for node, losses in per_node.items()),
                     key=lambda risk: (-risk.blast_radius, risk.node))

    causes, seen = [], set()
    for report in reports:
        for cause in report.root_causes:
            key = (cause.type, cause.service, cause.failure_domain)
            if key not in seen:
                seen.add(key)
                causes.append(cause)

    return FailureImpactReport(
        workflow_id=PORTFOLIO_WORKFLOW_ID,
        failed_node=failed_node,
        blast_radius=blast,
        survivability=round(100.0 - blast, 2),
        failed_services=sorted({service for report in reports for service in report.failed_services}),
        failed_workflows=[workflow_id for report in reports for workflow_id in report.failed_workflows],
        root_causes=causes,
        node_risk_ranking=ranking,
    )
