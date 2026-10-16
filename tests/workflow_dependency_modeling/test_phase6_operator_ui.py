"""Static contract tests for the presentation-ready Phase 6 operator flow."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = PROJECT_ROOT / "app" / "templates" / "components" / "workflows.html"
SCRIPT = PROJECT_ROOT / "app" / "static" / "js" / "components" / "workflows.js"
STYLES = PROJECT_ROOT / "app" / "static" / "css" / "pages" / "workflows.css"


def test_guided_flow_explains_preconditions_and_research_stages() -> None:
    markup = TEMPLATE.read_text(encoding="utf-8")

    assert 'class="operator-preflight"' in markup
    assert "Workflow is callable" in markup
    assert "Telemetry is complete" in markup
    assert "Success is measurable" in markup
    assert "Freeze &amp; baseline" in markup
    assert "Online Boutique · controlled experiment" not in markup


def test_guided_flow_gates_collection_and_exports_research_artifacts() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "loadManifestGate(campaign, manifestGate, runButton, runStatus)" in script
    assert "Freeze experiment record" in script
    assert "runButton.disabled = true" in script
    assert "manifest.integrityVerified" in script
    assert "Download experiment record JSON" in script
    assert "Download validated graph JSON" in script
    assert 'guidedText("h4", "", campaign.workflowName)' in script
    assert 'name="traceSourceType"' in TEMPLATE.read_text(encoding="utf-8")
    assert 'name="traceEndpoint"' in TEMPLATE.read_text(encoding="utf-8")
    assert 'name="traceAuthentication"' in TEMPLATE.read_text(encoding="utf-8")
    assert 'name="traceSecretEnv"' in TEMPLATE.read_text(encoding="utf-8")
    assert "traceSource:" in script
    assert 'values.get("traceSourceType")' in script


def test_guided_flow_has_keyboard_mobile_and_reduced_motion_rules() -> None:
    styles = STYLES.read_text(encoding="utf-8")

    assert ":focus-visible" in styles
    assert "min-height: 44px" in styles
    assert "@media (max-width: 700px)" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles


def test_recorded_graph_is_shown_before_dependency_validation() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    assert 'guidedResult.append(heading, metrics, warning, renderObservedGraph(session), evidence, contract)' in script
    assert 'renderObservedGraph(session)' in script
    assert 'campaign.analysisSessionId' in script
    assert 'They do not establish that a service is required for success.' in script
    assert '.guided-observed-graph__edges' in styles
