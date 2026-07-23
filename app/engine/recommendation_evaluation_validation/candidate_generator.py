from dataclasses import dataclass

from app.shared.schemas.failure_impact import FailureImpactReport


@dataclass
class Candidate:
    recommendation_type: str
    service: str
    reason: str


def generate_candidates(
    failure_impact: FailureImpactReport,
) -> list[Candidate]:
    candidates: list[Candidate] = []

    for cause in failure_impact.root_causes:
        if cause.type == "REPLICA_CONCENTRATION":
            candidates.append(
                Candidate(
                    recommendation_type="TOPOLOGY_SPREAD",
                    service=cause.service,
                    reason=(
                        f"Replicas of {cause.service} are concentrated "
                        f"in {cause.failure_domain}."
                    ),
                )
            )

    return candidates