"""Read-only audit of stored approval records.

Classifies each record as ``verified`` (carries the server-written live-evidence marker) or
``unverified`` (anything else). It never assumes a record is real because its id looks real, and it
never writes to or deletes from the database.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.shared.persistence.models import RecommendationApprovalRecord

from .models import LIVE_EVIDENCE_SOURCE

# Recommendation ids that appear in sample_data/recommendation.json. A match is reported as extra
# information; the absence of a match proves nothing.
KNOWN_FIXTURE_RECOMMENDATION_IDS = frozenset({"rec-001"})


def audit_approvals(session: Session) -> list[dict[str, Any]]:
    rows = []
    for record in session.query(RecommendationApprovalRecord).order_by(RecommendationApprovalRecord.id):
        provenance = (record.evaluation_snapshot or {}).get("provenance")
        verified = isinstance(provenance, dict) and provenance.get("source") == LIVE_EVIDENCE_SOURCE
        rows.append({
            "id": record.id,
            "recommendationId": record.recommendation_id,
            "status": record.status,
            "requestedAt": record.requested_at.isoformat() if record.requested_at else None,
            "provenance": "verified" if verified else "unverified",
            "knownFixtureId": record.recommendation_id in KNOWN_FIXTURE_RECOMMENDATION_IDS,
        })
    return rows
