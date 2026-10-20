"""Rendered input contracts. Interactive lifecycle checks run in workflow_browser.test.cjs."""
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient
from app.main import app


class Fields(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.fields = {}
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"input", "select", "textarea"} and "name" in attrs:
            self.fields[attrs["name"]] = attrs


def page():
    response = TestClient(app).get("/components/workflows")
    assert response.status_code == 200
    return response.text


def test_connection_does_not_assume_a_local_application_or_environment():
    fields = Fields(page()).fields
    for name in ("traceEndpoint", "environmentId"):
        assert "required" in fields[name]
        assert not fields[name].get("value")
    assert "required" in fields["evidenceTier"]


def test_secret_environment_reference_starts_disabled():
    fields = Fields(page()).fields
    assert "disabled" in fields["traceSecretEnv"]
    assert not fields["traceSecretEnv"].get("value")


def test_upload_accepts_a_file_and_requires_an_explicit_success_check():
    fields = Fields(page()).fields
    assert fields["traces"]["type"] == "file"
    for name in ("workflowName", "entryService", "operationName", "outcome", "statuses", "criticality"):
        assert "required" in fields[name]
        assert not fields[name].get("value")


def test_raw_definition_is_optional_in_the_standard_upload_flow():
    fields = Fields(page()).fields
    assert "disabled" in fields["workflowDefinition"]
    assert "required" not in fields["workflowDefinition"]


def test_results_are_empty_until_the_user_selects_or_analyzes_a_workflow():
    markup = page()
    assert 'aria-label="Selected workflow" hidden' in markup
    assert 'aria-label="Trace analysis results" hidden' in markup
    assert 'data-role="saved-workflows"' in markup
    assert 'role="status" aria-live="polite"' in markup


def test_research_and_demonstration_shortcuts_are_absent():
    markup = page()
    for text in ("PP1", "Research Environment", "Research audit", "Seed Synthetic", "precision", "reproducibility"):
        assert text not in markup
