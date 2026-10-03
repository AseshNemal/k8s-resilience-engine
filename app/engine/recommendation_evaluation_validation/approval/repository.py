from typing import Optional
from datetime import timezone
from sqlalchemy.orm import Session
from app.shared.persistence.models import RecommendationApprovalRecord
from .models import ApprovalModel
from app.shared.schemas.approval import ApprovalStatus

class ApprovalRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_recommendation_id(self, recommendation_id: str) -> Optional[ApprovalModel]:
        record = (
            self.session.query(RecommendationApprovalRecord)
            .filter(RecommendationApprovalRecord.recommendation_id == recommendation_id)
            .order_by(RecommendationApprovalRecord.requested_at.desc())
            .first()
        )
        if not record:
            return None
        return self._to_domain(record)
        
    def get_by_id(self, approval_id: int) -> Optional[ApprovalModel]:
        record = (
            self.session.query(RecommendationApprovalRecord)
            .filter(RecommendationApprovalRecord.id == approval_id)
            .first()
        )
        if not record:
            return None
        return self._to_domain(record)

    def save(self, approval: ApprovalModel) -> ApprovalModel:
        record = RecommendationApprovalRecord(
            recommendation_id=approval.recommendation_id,
            proposal_version=approval.proposal_version,
            status=approval.status.value,
            recommendation_snapshot=approval.recommendation_snapshot,
            yaml_snapshot=approval.yaml_snapshot,
            evaluation_snapshot=approval.evaluation_snapshot,
            requested_at=approval.requested_at,
            requested_by=approval.requested_by,
            decided_at=approval.decided_at,
            decided_by=approval.decided_by,
            decision_note=approval.decision_note,
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return self._to_domain(record)

    def update(self, approval: ApprovalModel) -> ApprovalModel:
        record = (
            self.session.query(RecommendationApprovalRecord)
            .filter(RecommendationApprovalRecord.id == approval.id)
            .first()
        )
        if record:
            record.status = approval.status.value
            record.decided_at = approval.decided_at
            record.decided_by = approval.decided_by
            record.decision_note = approval.decision_note
            self.session.commit()
            self.session.refresh(record)
            return self._to_domain(record)
        raise ValueError(f"Approval with ID {approval.id} not found")

    def _to_domain(self, record: RecommendationApprovalRecord) -> ApprovalModel:
        requested_at = record.requested_at
        if requested_at and requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=timezone.utc)
            
        decided_at = record.decided_at
        if decided_at and decided_at.tzinfo is None:
            decided_at = decided_at.replace(tzinfo=timezone.utc)
            
        return ApprovalModel(
            id=record.id,
            recommendation_id=record.recommendation_id,
            proposal_version=record.proposal_version,
            status=ApprovalStatus(record.status),
            requested_at=requested_at,
            requested_by=record.requested_by,
            recommendation_snapshot=record.recommendation_snapshot,
            yaml_snapshot=record.yaml_snapshot,
            evaluation_snapshot=record.evaluation_snapshot,
            decided_at=decided_at,
            decided_by=record.decided_by,
            decision_note=record.decision_note,
        )
