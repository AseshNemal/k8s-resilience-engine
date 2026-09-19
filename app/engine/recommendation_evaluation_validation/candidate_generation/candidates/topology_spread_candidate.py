from ..models import CandidateContext, CandidateSpec
from .base import CandidateBuilder


class TopologySpreadCandidateBuilder(CandidateBuilder):
    """Build Topology Spread candidates for replica-concentration causes."""

    def build(
        self,
        context: CandidateContext,
    ) -> list[CandidateSpec]:
        candidates: list[CandidateSpec] = []

        for cause in context.failure_impact.root_causes:
            if cause.type != "REPLICA_CONCENTRATION":
                continue

            reason = self._build_reason(
                service=cause.service,
                failure_domain=cause.failure_domain,
                concentration=cause.concentration,
            )

            candidates.append(
                CandidateSpec(
                    recommendation_type="TOPOLOGY_SPREAD",
                    service=cause.service,
                    reason=reason,
                )
            )

        return candidates

    @staticmethod
    def _build_reason(
        service: str,
        failure_domain: str,
        concentration: float | None,
    ) -> str:
        if concentration is not None:
            percentage = round(concentration * 100, 2)

            return (
                f"{service} has {percentage}% replica concentration "
                f"in failure domain {failure_domain}. "
                f"Topology Spread is a candidate for distributing "
                f"replicas across worker-node failure domains."
            )

        return (
            f"Replicas of {service} are concentrated "
            f"in failure domain {failure_domain}. "
            f"Topology Spread is a candidate for distributing "
            f"replicas across worker-node failure domains."
        )
