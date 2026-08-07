"""Tests for dependency_classifier.py — Member 1: Workflow Dependency Modeling.

Verifies:
  - Correct MANDATORY classification for services present in all traces.
  - Correct OPTIONAL classification for services absent from some traces.
  - Correct SUPPORTING classification for services not in the workflow def.
  - Both checkout and refund workflows produce the expected labels.
  - DependencyType enum values match the expected strings.
  - Configurable mandatory_threshold changes classification outcomes.
  - Edge cases: empty interactions, no expected services.

Expected classification for mock data:
  checkout:
    MANDATORY: cartservice, checkoutservice, frontend, paymentservice
    OPTIONAL:  emailservice  (absent from trace-002)
    SUPPORTING: (none in mock data)

  refund:
    MANDATORY: frontend, orderservice, paymentservice
    OPTIONAL:  notificationservice  (absent from trace-002)
    SUPPORTING: (none in mock data)
"""

from pathlib import Path

import pytest

from app.engine.workflow_dependency_modeling.dependency_classifier import (
    DependencyClassification,
    DependencyType,
    ServiceClassification,
    classify_dependencies,
)
from app.engine.workflow_dependency_modeling.interaction_extractor import (
    TraceInteraction,
    ServiceEdge,
    extract_interactions,
)
from app.engine.workflow_dependency_modeling.trace_reader import (
    load_traces_from_file,
    parse_spans,
)

import json

TRACES_DIR = Path("sample_data/traces")
DEFINITIONS_DIR = Path("sample_data/workflow_definitions")


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------


def load_workflow_def(name: str) -> dict:
    with open(DEFINITIONS_DIR / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


def make_interactions(workflow: str) -> list[TraceInteraction]:
    raw = load_traces_from_file(TRACES_DIR / f"{workflow}_traces.json")
    spans = parse_spans(raw)
    return extract_interactions(spans)


@pytest.fixture(scope="module")
def checkout_classification() -> DependencyClassification:
    interactions = make_interactions("checkout")
    definition = load_workflow_def("checkout")
    return classify_dependencies(interactions, definition)


@pytest.fixture(scope="module")
def refund_classification() -> DependencyClassification:
    interactions = make_interactions("refund")
    definition = load_workflow_def("refund")
    return classify_dependencies(interactions, definition)


# ---------------------------------------------------------------------------
# DependencyType enum
# ---------------------------------------------------------------------------


class TestDependencyTypeEnum:
    def test_mandatory_value(self) -> None:
        assert DependencyType.MANDATORY == "MANDATORY"

    def test_optional_value(self) -> None:
        assert DependencyType.OPTIONAL == "OPTIONAL"

    def test_supporting_value(self) -> None:
        assert DependencyType.SUPPORTING == "SUPPORTING"


# ---------------------------------------------------------------------------
# classify_dependencies — checkout
# ---------------------------------------------------------------------------


class TestClassifyCheckout:
    def test_workflow_id(
        self, checkout_classification: DependencyClassification
    ) -> None:
        assert checkout_classification.workflow_id == "checkout"

    def test_mandatory_services(
        self, checkout_classification: DependencyClassification
    ) -> None:
        assert sorted(checkout_classification.mandatory_services) == [
            "cartservice",
            "checkoutservice",
            "frontend",
            "paymentservice",
        ]

    def test_optional_services(
        self, checkout_classification: DependencyClassification
    ) -> None:
        assert checkout_classification.optional_services == ["emailservice"]

    def test_no_supporting_services(
        self, checkout_classification: DependencyClassification
    ) -> None:
        assert checkout_classification.supporting_services == []

    def test_returns_dependency_classification(
        self, checkout_classification: DependencyClassification
    ) -> None:
        assert isinstance(checkout_classification, DependencyClassification)

    def test_service_classifications_present(
        self, checkout_classification: DependencyClassification
    ) -> None:
        assert len(checkout_classification.service_classifications) > 0

    def test_all_service_classifications_are_typed(
        self, checkout_classification: DependencyClassification
    ) -> None:
        for sc in checkout_classification.service_classifications:
            assert isinstance(sc, ServiceClassification)
            assert isinstance(sc.dependency_type, DependencyType)

    def test_mandatory_services_have_frequency_one(
        self, checkout_classification: DependencyClassification
    ) -> None:
        by_name = {
            sc.service_name: sc
            for sc in checkout_classification.service_classifications
        }
        for svc in checkout_classification.mandatory_services:
            assert by_name[svc].trace_frequency == pytest.approx(1.0)

    def test_email_service_frequency_is_two_thirds(
        self, checkout_classification: DependencyClassification
    ) -> None:
        by_name = {
            sc.service_name: sc
            for sc in checkout_classification.service_classifications
        }
        assert by_name["emailservice"].trace_frequency == pytest.approx(
            2 / 3, abs=0.01
        )

    def test_email_service_is_in_workflow_definition(
        self, checkout_classification: DependencyClassification
    ) -> None:
        by_name = {
            sc.service_name: sc
            for sc in checkout_classification.service_classifications
        }
        assert by_name["emailservice"].in_workflow_definition is True

    def test_mandatory_services_are_in_workflow_definition(
        self, checkout_classification: DependencyClassification
    ) -> None:
        by_name = {
            sc.service_name: sc
            for sc in checkout_classification.service_classifications
        }
        for svc in checkout_classification.mandatory_services:
            assert by_name[svc].in_workflow_definition is True


# ---------------------------------------------------------------------------
# classify_dependencies — refund
# ---------------------------------------------------------------------------


class TestClassifyRefund:
    def test_workflow_id(
        self, refund_classification: DependencyClassification
    ) -> None:
        assert refund_classification.workflow_id == "refund"

    def test_mandatory_services(
        self, refund_classification: DependencyClassification
    ) -> None:
        assert sorted(refund_classification.mandatory_services) == [
            "frontend",
            "orderservice",
            "paymentservice",
        ]

    def test_optional_services(
        self, refund_classification: DependencyClassification
    ) -> None:
        assert refund_classification.optional_services == ["notificationservice"]

    def test_no_supporting_services(
        self, refund_classification: DependencyClassification
    ) -> None:
        assert refund_classification.supporting_services == []

    def test_notification_frequency_is_two_thirds(
        self, refund_classification: DependencyClassification
    ) -> None:
        by_name = {
            sc.service_name: sc
            for sc in refund_classification.service_classifications
        }
        assert by_name["notificationservice"].trace_frequency == pytest.approx(
            2 / 3, abs=0.01
        )


# ---------------------------------------------------------------------------
# SUPPORTING classification
# ---------------------------------------------------------------------------


class TestSupportingClassification:
    def test_unknown_service_is_supporting(self) -> None:
        """A service observed in traces but absent from expectedServices
        must be classified as SUPPORTING, regardless of frequency."""
        interactions = [
            TraceInteraction(
                trace_id="t1",
                edges=[
                    ServiceEdge(source="frontend", target="cartservice"),
                    ServiceEdge(source="cartservice", target="metricsservice"),
                ],
            ),
            TraceInteraction(
                trace_id="t2",
                edges=[
                    ServiceEdge(source="frontend", target="cartservice"),
                    ServiceEdge(source="cartservice", target="metricsservice"),
                ],
            ),
        ]
        definition = {
            "workflowId": "test-workflow",
            "expectedServices": ["frontend", "cartservice"],
        }
        result = classify_dependencies(interactions, definition)

        assert "metricsservice" in result.supporting_services
        assert "metricsservice" not in result.mandatory_services
        assert "metricsservice" not in result.optional_services

    def test_supporting_service_has_correct_type(self) -> None:
        interactions = [
            TraceInteraction(
                trace_id="t1",
                edges=[ServiceEdge(source="frontend", target="sidecar")],
            )
        ]
        definition = {"workflowId": "wf", "expectedServices": ["frontend"]}
        result = classify_dependencies(interactions, definition)
        by_name = {sc.service_name: sc for sc in result.service_classifications}
        assert by_name["sidecar"].dependency_type == DependencyType.SUPPORTING
        assert by_name["sidecar"].in_workflow_definition is False


# ---------------------------------------------------------------------------
# Configurable mandatory_threshold
# ---------------------------------------------------------------------------


class TestMandatoryThreshold:
    def test_lower_threshold_promotes_optional_to_mandatory(self) -> None:
        """With threshold=0.6, emailservice (freq≈0.667) becomes MANDATORY."""
        interactions = make_interactions("checkout")
        definition = load_workflow_def("checkout")
        result = classify_dependencies(
            interactions, definition, mandatory_threshold=0.6
        )
        assert "emailservice" in result.mandatory_services
        assert "emailservice" not in result.optional_services

    def test_default_threshold_keeps_email_optional(self) -> None:
        interactions = make_interactions("checkout")
        definition = load_workflow_def("checkout")
        result = classify_dependencies(interactions, definition)
        assert "emailservice" in result.optional_services


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_interactions_returns_empty_classification(self) -> None:
        definition = {
            "workflowId": "empty",
            "expectedServices": ["frontend"],
        }
        result = classify_dependencies([], definition)
        assert result.mandatory_services == []
        assert result.optional_services == []
        assert result.supporting_services == []
        assert result.service_classifications == []

    def test_missing_workflow_id_defaults_to_unknown(self) -> None:
        interactions = [
            TraceInteraction(
                trace_id="t1",
                edges=[ServiceEdge(source="a", target="b")],
            )
        ]
        result = classify_dependencies(interactions, {})
        assert result.workflow_id == "unknown"

    def test_no_expected_services_all_become_supporting(self) -> None:
        interactions = [
            TraceInteraction(
                trace_id="t1",
                edges=[ServiceEdge(source="frontend", target="api")],
            )
        ]
        definition = {"workflowId": "wf", "expectedServices": []}
        result = classify_dependencies(interactions, definition)
        assert result.mandatory_services == []
        assert result.optional_services == []
        assert "frontend" in result.supporting_services
        assert "api" in result.supporting_services
