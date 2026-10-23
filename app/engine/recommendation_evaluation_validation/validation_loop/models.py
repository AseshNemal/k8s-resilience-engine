"""Types of the fault-injection validation loop: modes, settings, the plan a mode resolves to, and experiment outcomes.

The loop proves a recommended placement change on a test cluster before the developer applies it to their own: it applies a
candidate once, stops each authorized worker in turn, checks the workflow, restores, and reverts. Everything here is plain data.
Defaults are experimental and configurable (``LoopSettings``); none was tuned on real data.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.engine.recommendation_evaluation_validation.validation.models import ValidationRefusedError


class Mode(str, Enum):
    SMOKE = "SMOKE"        # 1 candidate x 1 worker x 1 repeat: does the machinery work at all?
    QUICK = "QUICK"        # top 1 x every eligible worker x 1 repeat: preliminary evidence
    FULL = "FULL"          # top 2 x every eligible worker x 2 repeats (configurable), measured baseline: repeatable evidence


class ExperimentStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    SKIPPED = "SKIPPED"


class CandidateStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"              # every planned experiment passed: the only status a winner can have
    FAILED = "FAILED"              # at least one experiment conclusively failed
    INCONCLUSIVE = "INCONCLUSIVE"  # evidence is incomplete, so it is never selected
    SKIPPED = "SKIPPED"


class RunStatus(str, Enum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    ABORTING = "ABORTING"                  # abort requested; recovering the worker and reverting
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"            # stopped early (abort, block, budget); cluster restored and reverted
    FAILED = "FAILED"
    NEEDS_MANUAL_RESTORE = "NEEDS_MANUAL_RESTORE"


TERMINAL_RUN_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.INTERRUPTED, RunStatus.FAILED, RunStatus.NEEDS_MANUAL_RESTORE})

BASELINE, CANDIDATE = "baseline", "candidate"      # the two kinds of experiment

# Evidence levels: what the pass decision rested on. L1 is the Kubernetes readiness alone; L2 adds an HTTP probe that judged the page.
L1_READINESS, L2_PROBE = "L1_READINESS", "L2_PROBE"


@dataclass(frozen=True)
class LoopSettings:
    """Bounds and defaults. Configurable; the defaults are experimental."""

    max_candidates: int = 3
    max_repeats: int = 3
    full_candidates: int = 2
    full_repeats: int = 2
    # Namespaces whose workloads must not be taken down with a worker (Docker Desktop target); extend through configuration.
    critical_namespaces: tuple[str, ...] = ("kube-system", "local-path-storage")


@dataclass(frozen=True)
class ModePlan:
    """What a mode resolves to for one run: how many candidates, which workers, how many repeats, and whether to baseline."""

    mode: Mode
    candidates: int
    workers: tuple[str, ...]
    repeats: int
    measure_baseline: bool

    @property
    def baseline_failures(self) -> int:
        return len(self.workers) if self.measure_baseline else 0

    def planned_failures(self, candidates: int | None = None) -> int:
        """Node failures the plan may cause at most (candidates x workers x repeats, plus the baseline ones)."""
        return (self.candidates if candidates is None else candidates) * len(self.workers) * self.repeats + self.baseline_failures

    @property
    def preliminary(self) -> bool:
        """Quick and Smoke are preliminary evidence; only Full is the repeatable one."""
        return self.mode is not Mode.FULL

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode.value, "candidates": self.candidates, "workers": list(self.workers), "repeats": self.repeats,
                "measureBaseline": self.measure_baseline, "plannedFailures": self.planned_failures(),
                "preliminary": self.preliminary}


def resolve_mode(mode: Mode, eligible_workers: list[str], *, riskiest_worker: str | None = None,
                 smoke_worker: str | None = None, candidates: int | None = None, repeats: int | None = None,
                 measure_baseline: bool | None = None, settings: LoopSettings | None = None) -> ModePlan:
    """The plan a mode stands for. Full always measures a baseline; Smoke and Quick may, when asked.

    ``candidates`` and ``repeats`` are honoured for Full only (and capped); Smoke and Quick are fixed shapes.
    """
    settings = settings or LoopSettings()
    workers = tuple(sorted(dict.fromkeys(eligible_workers)))
    if not workers:
        raise ValidationRefusedError("NO_ELIGIBLE_WORKER", "There is no worker node that can be taken down for the test.")
    if mode is Mode.SMOKE:
        chosen = smoke_worker or riskiest_worker or workers[0]
        if chosen not in workers:
            raise ValidationRefusedError("WORKER_NOT_ELIGIBLE", f"{chosen!r} is not an eligible worker.")
        return ModePlan(mode, 1, (chosen,), 1, bool(measure_baseline))
    if mode is Mode.QUICK:
        return ModePlan(mode, 1, workers, 1, bool(measure_baseline))
    wanted_candidates = settings.full_candidates if candidates is None else candidates
    wanted_repeats = settings.full_repeats if repeats is None else repeats
    if not 1 <= wanted_candidates <= settings.max_candidates or not 1 <= wanted_repeats <= settings.max_repeats:
        raise ValidationRefusedError("RUN_TOO_LARGE", f"Full validation takes 1-{settings.max_candidates} candidates and "
                                     f"1-{settings.max_repeats} repeats.")
    if measure_baseline is False:
        raise ValidationRefusedError("BASELINE_REQUIRED", "Full Research Validation needs a measured baseline.")
    return ModePlan(mode, wanted_candidates, workers, wanted_repeats, True)


@dataclass(frozen=True)
class ExperimentKey:
    kind: str                 # BASELINE or CANDIDATE
    candidate_id: str | None  # None for a baseline
    worker: str
    repeat: int               # 1-based


@dataclass
class ExperimentOutcome:
    """What one node failure showed. Readiness-derived and probe-derived evidence are kept apart on purpose."""

    key: ExperimentKey
    status: ExperimentStatus
    reasons: list[str] = field(default_factory=list)
    evidence_level: str | None = None
    readiness: dict[str, Any] = field(default_factory=dict)          # what Kubernetes showed (pods Ready per mandatory service)
    probe: dict[str, Any] | None = None                              # the HTTP probe's own judgement, separate from readiness
    predicted: dict[str, Any] = field(default_factory=dict)
    comparison: dict[str, Any] = field(default_factory=dict)
    prediction_error: float | None = None
    model_agrees: bool | None = None          # predicted vs readiness-derived observation (comparable evidence only)
    probe_disagrees_with_readiness: bool | None = None   # a probe finding alone is NOT a prediction error
    recovery: dict[str, Any] = field(default_factory=dict)
    duration_s: float | None = None
    experiment_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.key.kind, "candidateId": self.key.candidate_id, "worker": self.key.worker, "repeat": self.key.repeat,
                "status": self.status.value, "reasons": list(self.reasons), "evidenceLevel": self.evidence_level,
                "readiness": self.readiness, "probe": self.probe, "predicted": self.predicted, "comparison": self.comparison,
                "predictionError": self.prediction_error, "modelAgrees": self.model_agrees,
                "probeDisagreesWithReadiness": self.probe_disagrees_with_readiness, "recovery": self.recovery,
                "durationS": self.duration_s, "experimentId": self.experiment_id}


@dataclass
class CandidateOutcome:
    candidate_id: str
    status: CandidateStatus
    experiments: list[ExperimentOutcome] = field(default_factory=list)
    reason: str | None = None
    reverted: bool | None = None          # None: nothing applied; True/False: the revert was verified / was not

    def to_dict(self) -> dict[str, Any]:
        return {"candidateId": self.candidate_id, "status": self.status.value, "reason": self.reason,
                "reverted": self.reverted, "experiments": [e.to_dict() for e in self.experiments]}
