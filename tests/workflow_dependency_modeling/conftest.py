"""Component 1 unit tests never reach a trace backend.

Creating an experiment reads the services of each preparation step from recorded traces. Tests that are not about
that rule get an empty answer; ``test_experiment_step_services.py`` replaces the reader to test the rule itself.
"""

import pytest

from app.engine.workflow_dependency_modeling import campaign_execution_service, experiment_campaign_service


@pytest.fixture(autouse=True)
def no_trace_backend_for_step_services(monkeypatch):
    monkeypatch.setattr(experiment_campaign_service, "step_services_reader", lambda analysis, operation: set())


@pytest.fixture(autouse=True)
def no_recorded_outage_traces(monkeypatch):
    """Inspections set aside traces of recorded outage runs; unit tests start with none recorded."""
    monkeypatch.setattr(campaign_execution_service, "controlled_outage_trace_ids", lambda repository=None: frozenset())
