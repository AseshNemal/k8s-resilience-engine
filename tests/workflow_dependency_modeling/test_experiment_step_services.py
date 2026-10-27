"""Every step of a workflow contributes candidate services, read from that step's own recorded traces."""

from types import SimpleNamespace

import pytest

from app.engine.workflow_dependency_modeling import experiment_campaign_service as campaigns


def definition(*steps, measured="buy", variables=None):
    return SimpleNamespace(steps=[SimpleNamespace(name=name, method=method, path=path) for name, method, path in steps],
                           measured_step=measured, initial_variables=variables or {})


ANALYSIS = SimpleNamespace(entry_service="web", lookback="24h")


def test_preparation_steps_add_the_services_their_requests_use(monkeypatch):
    asked = []

    def reader(analysis, operation):
        asked.append(operation)
        return {"web", "ads", "catalog"} if operation.startswith("GET /item/") else {"web", "cart"}

    monkeypatch.setattr(campaigns, "step_services_reader", reader)
    found = campaigns.step_services(ANALYSIS, definition(
        ("view", "get", "/item/{{item}}"), ("add", "POST", "/cart"), ("buy", "POST", "/checkout"),
        variables={"item": "A1"}))
    assert asked == ["GET /item/A1", "POST /cart"]          # the measured step is not queried again
    assert found == {"web", "ads", "catalog", "cart"}


def test_a_step_without_recorded_traces_refuses_the_experiment(monkeypatch):
    def reader(analysis, operation):
        raise ValueError("No matching traces in this window.")

    monkeypatch.setattr(campaigns, "step_services_reader", reader)
    probe = definition(("view", "GET", "/item/A1"), ("buy", "POST", "/checkout"))
    with pytest.raises(ValueError, match="'view'.*no recorded traces in the 24h window"):
        campaigns.step_services(ANALYSIS, probe)


def test_a_step_whose_path_needs_a_runtime_value_is_refused():
    probe = definition(("open", "GET", "/order/{{order_id}}"), ("buy", "POST", "/checkout"))
    with pytest.raises(ValueError, match="captured at run time"):
        campaigns.step_services(ANALYSIS, probe)


def test_a_single_step_workflow_needs_no_extra_queries(monkeypatch):
    monkeypatch.setattr(campaigns, "step_services_reader", lambda analysis, operation: pytest.fail("no query expected"))
    assert campaigns.step_services(ANALYSIS, definition(("buy", "POST", "/checkout"))) == set()
