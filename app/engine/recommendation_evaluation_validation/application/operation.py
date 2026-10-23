"""What an applied (or reset) change is doing right now, said from persisted facts only.

The page shows one process: Review, Approved, Applying, Rollout, Analysis, Results. Which of those the change is in, the
steps beneath it, the headline and the one next action are decided here, from the stored change, its watched rollout and
its analysis, and sent to the page as data. The browser draws it; it works none of it out, so a refresh, a second tab or a
restarted backend show the same thing.

Nothing here is a progress percentage. A step is ``done`` only when the record has the evidence for it (its event, its
observation, its timestamp), ``active`` when it is the one the record says is happening, ``attention`` when the record says it
failed or is blocked, and ``pending`` otherwise. A step that cannot be told apart from its neighbour is not split to look
busier: reading the live topology and recalculating the impact are two steps with their own timestamps once the analysis row
has them, and one wait before that.
"""

from typing import Any

from app.engine.recommendation_evaluation_validation.application import rollout as rollout_watch
from app.engine.recommendation_evaluation_validation.application.analysis import (
    COMPLETED as ANALYSIS_COMPLETED,
    FAILED as ANALYSIS_FAILED,
    PENDING as ANALYSIS_PENDING,
    UNAVAILABLE as ANALYSIS_UNAVAILABLE,
    ChangeAnalysis,
)
from app.engine.recommendation_evaluation_validation.application.models import (
    APPLIED,
    APPLYING,
    FAILED,
    RESET,
    RESET_FAILED,
    AppliedChange,
)

STAGES = ("review", "approved", "applying", "rollout", "analysis", "results")

POLL_BUSY_MS = 3000        # how soon the page may ask again while something is in progress; nothing is polled once settled

APPLY_STEPS = (("checking-approval", "Checking approval"), ("saving-snapshot", "Saving recovery snapshot"),
               ("submitting-changes", "Submitting changes"), ("waiting-for-rollout", "Waiting for rollout"),
               ("reading-topology", "Reading live topology"), ("recalculating-impact", "Recalculating impact"),
               ("preparing-results", "Preparing results"))
RESET_STEPS = (("submitting-changes", "Restoring the stored state"), ("waiting-for-rollout", "Waiting for restored rollout"),
               ("reading-topology", "Reading live topology"), ("recalculating-impact", "Recalculating impact"),
               ("preparing-results", "Preparing results"))


def direction_of(change: AppliedChange) -> str:
    return "reset" if change.status in (RESET, RESET_FAILED) else "apply"


def _event_at(change: AppliedChange, name: str) -> str | None:
    return next((e.get("at") for e in change.events if e.get("event") == name), None)


def _points(value: float | None) -> str:
    return "unknown" if value is None else f"{value:g}"


def _percent(value: float | None) -> str:
    return "unknown" if value is None else f"{value:g}%"


def steps(change: AppliedChange, rollout: dict[str, Any] | None, analysis: ChangeAnalysis | None) -> list[dict[str, Any]]:
    """The steps of the change's direction, each with the state its evidence supports."""
    reset = direction_of(change) == "reset"
    state_of = (rollout or {}).get("state")
    in_flight = change.status == APPLYING
    submitted = change.status in (APPLIED, RESET)
    submit_done_at = _event_at(change, "reset" if reset else "applied")
    observed = analysis.observed if analysis and analysis.status == ANALYSIS_COMPLETED else None
    rolled = state_of == rollout_watch.COMPLETED
    waiting_analysis = rolled and analysis is not None and analysis.status == ANALYSIS_PENDING
    analysis_stuck = rolled and analysis is not None and analysis.status in (ANALYSIS_FAILED, ANALYSIS_UNAVAILABLE)

    def row(step_id: str, label: str, state: str, at: str | None = None) -> dict[str, Any]:
        return {"id": step_id, "label": label, "state": state, "at": at}

    out = []
    for step_id, label in (RESET_STEPS if reset else APPLY_STEPS):
        if step_id == "checking-approval":
            at = _event_at(change, "approval-verified")
            out.append(row(step_id, label, "done" if at else "pending", at))
        elif step_id == "saving-snapshot":
            at = _event_at(change, "snapshot-saved")
            out.append(row(step_id, label, "done" if at else "pending", at))
        elif step_id == "submitting-changes":
            if change.status == FAILED:
                out.append(row(step_id, label, "attention"))
            elif change.status == RESET_FAILED:
                out.append(row(step_id, label, "attention"))
            elif submitted:
                out.append(row(step_id, label, "done", submit_done_at))
            else:
                out.append(row(step_id, label, "active" if in_flight else "pending"))
        elif step_id == "waiting-for-rollout":
            if not submitted:
                out.append(row(step_id, label, "pending"))
            elif rolled:
                out.append(row(step_id, label, "done", (rollout or {}).get("completedAt")))
            elif state_of in (rollout_watch.FAILED, rollout_watch.TIMED_OUT) or (rollout or {}).get("blocked"):
                out.append(row(step_id, label, "attention"))
            else:
                out.append(row(step_id, label, "active"))
        elif step_id == "reading-topology":
            if observed:
                out.append(row(step_id, label, "done", observed.get("topologyCapturedAt")))
            elif waiting_analysis:
                out.append(row(step_id, label, "active"))
            elif analysis_stuck:
                out.append(row(step_id, label, "attention"))
            else:
                out.append(row(step_id, label, "pending"))
        elif step_id == "recalculating-impact":
            if observed:
                out.append(row(step_id, label, "done", observed.get("recalculatedAt")))
            elif analysis_stuck:
                out.append(row(step_id, label, "attention"))
            else:
                out.append(row(step_id, label, "pending"))
        else:       # preparing-results
            at = analysis.completed_at.isoformat() if observed and analysis and analysis.completed_at else None
            out.append(row(step_id, label, "done" if observed else "pending", at))
    return out


def stage_of(change: AppliedChange | None, rollout: dict[str, Any] | None, analysis: ChangeAnalysis | None,
             approved: bool) -> str:
    if change is None:
        return "approved" if approved else "review"
    if change.status in (APPLYING, FAILED, RESET_FAILED):
        return "applying"
    if (rollout or {}).get("state") != rollout_watch.COMPLETED:
        return "rollout"
    if analysis is None or analysis.status in (ANALYSIS_COMPLETED, ANALYSIS_UNAVAILABLE):
        return "results"
    return "analysis"


def _comparison_line(change: AppliedChange, analysis: ChangeAnalysis) -> tuple[str, str]:
    comparison = analysis.comparison or {}
    before, after = comparison.get("beforeBlastRadius"), comparison.get("afterBlastRadius")
    delta, direction = comparison.get("reductionPoints"), comparison.get("direction")
    reset = analysis.direction == "reset"
    if delta is None or direction is None:
        return "neutral", ("Rollout complete. The blast radius could not be compared because one of the values is unknown." )
    basis = f"blast radius {_percent(before)} before, {_percent(after)} recalculated from the live topology"
    if reset:
        if direction == "unchanged":
            return "ok", f"Reset complete: {basis}, the same as before the change."
        return "attention", (f"Reset complete, but the recalculated risk differs from before the change: {basis} "
                             f"({_points(abs(delta))} percentage points {'lower' if delta > 0 else 'higher'}).")
    if direction == "improved":
        return "ok", f"Risk improved by {_points(delta)} percentage points: {basis}."
    if direction == "worsened":
        return "attention", f"Risk worsened by {_points(abs(delta))} percentage points: {basis}."
    return "neutral", f"Risk unchanged: {basis}."


def headline(change: AppliedChange | None, rollout: dict[str, Any] | None, analysis: ChangeAnalysis | None,
             approved: bool, connection: dict[str, str] | None, interrupted: bool = False) -> tuple[str, str, str]:
    """(tone, headline, next action). Tone is busy, ok, attention or neutral; the page also writes it as text.

    ``connection`` is why the cluster could not be read just now (None when it could); the stored state is shown either way.
    ``interrupted`` says an APPLYING change has no apply running for it any more (the backend stopped part-way).
    """
    if change is None:
        return ("neutral", "Approved. Nothing has been applied." if approved else "Not approved yet.",
                "apply" if approved else "approve")
    reset = direction_of(change) == "reset"
    if change.status == APPLYING and interrupted:
        return "attention", ("An earlier apply was interrupted: the backend stopped while it was applying. Some Deployments may have "
                             "been changed. The state to go back to is stored; Reset restores it."), "reset"
    if change.status == APPLYING:
        return "busy", "Applying change…", "wait"
    if change.status == FAILED:
        return "attention", "The change could not be applied. Everything it touched was put back.", "none"
    if change.status == RESET_FAILED:
        return "attention", "Reset did not complete. The stored state is kept; try the reset again.", "reset"
    suffix = (f" (the cluster could not be read just now: {connection['message']} This is the last recorded state.)" if connection else "")
    state = (rollout or {}).get("state")
    verb = "Reset" if reset else "Change"
    if state in (None, rollout_watch.UNKNOWN):
        return "attention", f"{verb} submitted. Its rollout status is unknown{suffix}.", "recheck"
    if state == rollout_watch.ROLLING_OUT:
        if (rollout or {}).get("blocked"):
            return "attention", f"{verb} is rolling out but blocked: {rollout['blockedReason']}{suffix}", "wait"
        return "busy", (f"{'Resetting' if reset else 'Rolling out'}… waiting for the Deployments to settle on the new "
                        f"generation{suffix}"), "wait"
    if state == rollout_watch.TIMED_OUT:
        return "attention", f"The rollout did not finish in time. It may still complete{suffix}.", "recheck"
    if state == rollout_watch.FAILED:
        reasons = "; ".join(d["reason"] for d in (rollout or {}).get("deployments", []) if d.get("state") == "failed" and d.get("reason"))
        return "attention", f"The rollout failed or is blocked: {reasons or 'see the rollout details'}{suffix}", "recheck"
    # the rollout completed
    restoration = (rollout or {}).get("restoration")
    if reset and restoration and restoration.get("verified") is False:
        # Not "reset again": the stored fields were put back and the change is already RESET. What differs now was changed by
        # someone or something else since, and is for a person to look at.
        return "attention", ("Reset rollout finished, but the Deployments do not match the stored state: "
                             + "; ".join(restoration.get("differences") or [])), "none"
    if analysis is None:
        return "neutral", f"{verb} rollout complete. No failure-impact analysis was recorded for it.", "none"
    if analysis.status == ANALYSIS_PENDING:
        return "busy", "Rollout complete. Reading the live topology and recalculating the failure impact…", "wait"
    if analysis.status == ANALYSIS_FAILED:
        message = (analysis.error or {}).get("message") or "the recalculation did not run"
        return "attention", f"Rollout complete. The failure-impact recalculation failed: {message}", "retry-analysis"
    if analysis.status == ANALYSIS_UNAVAILABLE:
        message = (analysis.error or {}).get("message") or "the comparison basis changed"
        return "neutral", f"Rollout complete. No comparison is made: {message}", "reset" if not reset else "none"
    tone, line = _comparison_line(change, analysis)
    return tone, line, "none" if reset else "reset"


def build(change: AppliedChange | None, rollout: dict[str, Any] | None, analysis: ChangeAnalysis | None, *,
          approved: bool, connection: dict[str, str] | None = None, interrupted: bool = False) -> dict[str, Any]:
    """The whole operation view for the page, from persisted facts."""
    tone, line, next_action = headline(change, rollout, analysis, approved, connection, interrupted)
    busy = tone == "busy" or next_action == "wait"     # a blocked rollout is still being waited on, and still polled
    return {
        "direction": None if change is None else direction_of(change),
        "stage": stage_of(change, rollout, analysis, approved),
        "stages": list(STAGES),
        "tone": tone, "headline": line, "nextAction": next_action,
        "steps": [] if change is None else steps(change, rollout, analysis),
        "inProgress": busy, "pollMs": POLL_BUSY_MS if busy else None,
        "connectionLost": connection is not None, "connection": connection, "interrupted": interrupted,
    }
