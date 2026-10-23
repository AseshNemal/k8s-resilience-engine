"""Every worker node as its own failure: one row per failure, each with its own selected candidate, never one merged ranking."""

import pytest

from tests.recommendation_evaluation_validation.test_approval_api import (  # noqa: F401  (fixtures)
    client,
    override_get_db,
    sqlite_url,
)
from tests.recommendation_evaluation_validation.test_declared_api import nothing_frozen  # noqa: F401  (fixture)
from tests.recommendation_evaluation_validation.test_independent_inputs import (  # noqa: F401  (fixtures and helpers)
    concentrated_db,
    declared,
    freeze_checkout,
)

PARAMS = {"workflow_id": "checkout", "namespace": "shop"}
DECLARED, LIVE = "/api/recommendations/declared", "/api/recommendations/live"


def rows(overview):
    return {row["failedNode"]: row for row in overview["failures"]}


def test_every_worker_is_assessed_as_a_failure_worst_first_and_the_single_points_of_failure_are_named(client, use_cluster, declared, nothing_frozen):
    use_cluster(concentrated_db())

    response = client.get(f"{DECLARED}/failures", params=PARAMS)

    assert response.status_code == 200
    overview = response.json()
    by_node = rows(overview)
    assert len(by_node) >= 2 and all(set(row) >= {"failedNode", "blastRadiusBefore", "singlePointOfFailure", "candidates", "feasible", "ranked", "selected"}
                                     for row in by_node.values())
    blast = [row["blastRadiusBefore"] for row in overview["failures"]]
    assert blast == sorted(blast, reverse=True), "worst first"
    assert overview["singlePointsOfFailure"] == [row["failedNode"] for row in overview["failures"] if row["blastRadiusBefore"] > 0]
    assert overview["singlePointsOfFailure"], "the concentrated node is a single point of failure"
    assert "never merged" in overview["scope"] and overview["source"] == "declared-workflow"
    assert response.headers["X-Evidence-Source"] == "declared-workflow" and response.headers["X-Evidence-Approvable"] == "false"


def test_each_row_is_exactly_what_the_detail_for_that_failure_selects(client, use_cluster, declared, nothing_frozen):
    use_cluster(concentrated_db())
    overview = client.get(f"{DECLARED}/failures", params=PARAMS).json()

    for node, row in rows(overview).items():
        report = client.get(DECLARED, params={**PARAMS, "failed_node": node}).json()
        selected = next((c for c in report["recommendations"] if c["id"] == report["selectedRecommendationId"]), None)

        assert row["candidates"] == len(report["recommendations"])
        assert row["feasible"] == sum(1 for c in report["recommendations"] if c["feasible"])
        if selected is None:
            assert row["selected"] is None
        else:
            assert row["selected"]["id"] == selected["id"] and row["selected"]["score"] == pytest.approx(selected["score"])
            assert row["selected"]["type"] == selected["type"]


def test_a_node_that_hosts_nothing_the_workflow_needs_is_listed_but_is_not_a_single_point_of_failure(client, use_cluster, declared, nothing_frozen):
    use_cluster(concentrated_db())

    overview = client.get(f"{DECLARED}/failures", params=PARAMS).json()

    harmless = [row for row in overview["failures"] if not row["singlePointOfFailure"]]
    assert harmless and all(row["blastRadiusBefore"] == 0 and row["selected"] is None for row in harmless)


def test_a_frozen_workflow_gets_the_same_overview_labelled_live(client, monkeypatch, use_cluster):
    use_cluster(concentrated_db())
    freeze_checkout(monkeypatch)

    response = client.get(f"{LIVE}/failures", params=PARAMS)

    assert response.status_code == 200 and response.json()["source"] == "live-discovery"
    assert response.headers["X-Evidence-Source"] == "live-discovery"
    assert "X-Evidence-Approvable" not in response.headers


def test_without_a_workflow_or_with_an_unknown_one_the_overview_is_an_explicit_error(client, use_cluster, declared, nothing_frozen):
    use_cluster(concentrated_db())

    assert client.get(f"{DECLARED}/failures", params={"namespace": "shop"}).json()["detail"]["code"] == "RECOMMENDATION_EVIDENCE_UNAVAILABLE"
    unknown = client.get(f"{DECLARED}/failures", params={"workflow_id": "nope", "namespace": "shop"})
    assert unknown.status_code == 404 and unknown.json()["detail"]["code"] == "DECLARED_WORKFLOW_UNAVAILABLE"
    assert client.get(f"{LIVE}/failures", params={"namespace": "shop"}).json()["detail"]["code"] == "RECOMMENDATION_EVIDENCE_UNAVAILABLE"
