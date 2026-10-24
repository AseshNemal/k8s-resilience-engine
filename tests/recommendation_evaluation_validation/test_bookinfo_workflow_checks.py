"""The Bookinfo example workflow checks are valid for the product's own check runner, and tell a healthy page from a degraded one.

The strings come from the Bookinfo page itself (k8s-bookinfo/src/productpage/templates/productpage.html). No cluster is read and no
request leaves this process: the page body is a stand-in handed to the runner's fetch hook.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.engine.recommendation_evaluation_validation.apply_test.measurements import measure_workflows
from app.engine.recommendation_evaluation_validation.declared_workflow import load_declared_workflows
from app.integrations.probe.http_fetch import Fetched

EXPERIMENTS = Path(__file__).resolve().parents[2] / "experiments" / "member4_real_validation"
CHECKS = EXPERIMENTS / "workflow-checks.bookinfo.example.json"
DECLARATION = EXPERIMENTS / "declared-workflow.bookinfo.example.json"

HEALTHY = "<h4>Book Details</h4> ISBN-10 Publisher <h4>Book Reviews</h4> reviews text"
NO_DETAILS = "<p>Error fetching product details</p> <h4>Book Reviews</h4>"
NO_REVIEWS = "<h4>Book Details</h4> ISBN-10 Publisher <p>Error fetching product reviews</p>"


def live():
    workflows = [{"workflowId": w["workflowId"], "mandatoryServices": w["mandatoryServices"]} for w in json.loads(DECLARATION.read_text())["workflows"]]
    services = [{"name": n, "pods": [{"ready": True}]} for n in ("productpage", "details", "reviews", "ratings")]
    return SimpleNamespace(details={"portfolioWorkflows": workflows, "currentDeployment": {"services": services}})


def run(body, monkeypatch):
    monkeypatch.setenv("RESILIENCE_WORKFLOW_PROBES_PATH", str(CHECKS))
    return {r["workflowId"]: r["httpStatus"] for r in measure_workflows(live(), fetch=lambda url: Fetched(200, body, 1.0, None))["rows"]}


def test_example_checks_cover_every_example_workflow():
    declared = {w.graph.workflow_id for w in load_declared_workflows(DECLARATION)}
    assert declared == set(json.loads(CHECKS.read_text())["workflowChecks"])


def test_a_healthy_page_passes_every_check(monkeypatch):
    assert run(HEALTHY, monkeypatch) == {"productpage-view": "PASSED", "book-rating-view": "PASSED", "book-details-lookup": "PASSED"}


def test_a_page_without_details_fails_the_workflows_that_need_them_and_not_the_reviews_one(monkeypatch):
    assert run(NO_DETAILS, monkeypatch) == {"productpage-view": "FAILED", "book-rating-view": "PASSED", "book-details-lookup": "FAILED"}


def test_a_page_without_reviews_fails_the_workflows_that_need_them(monkeypatch):
    assert run(NO_REVIEWS, monkeypatch) == {"productpage-view": "FAILED", "book-rating-view": "FAILED", "book-details-lookup": "PASSED"}


def test_the_declaration_file_may_carry_the_checks_too(tmp_path, monkeypatch):
    """The checks fall back to the declaration file, so one file can hold both; the declaration loader must tolerate the extra key."""
    merged = json.loads(DECLARATION.read_text())
    merged["workflowChecks"] = json.loads(CHECKS.read_text())["workflowChecks"]
    path = tmp_path / "declared-workflows.json"
    path.write_text(json.dumps(merged))
    monkeypatch.delenv("RESILIENCE_WORKFLOW_PROBES_PATH", raising=False)
    monkeypatch.setenv("RESILIENCE_DECLARED_WORKFLOW_PATH", str(path))

    assert len(load_declared_workflows(path)) == 3
    rows = measure_workflows(live(), fetch=lambda url: Fetched(200, HEALTHY, 1.0, None))["rows"]
    assert {r["httpStatus"] for r in rows} == {"PASSED"}
