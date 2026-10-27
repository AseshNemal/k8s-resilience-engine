"""Unit tests for JaegerClient."""

from unittest.mock import MagicMock, patch
import pytest

from app.integrations.jaeger.client import JaegerClient


def test_jaeger_client_init():
    client = JaegerClient(
        "http://jaeger.local:16686/",
        timeout_seconds=10.0,
        headers={"Authorization": "Bearer test-token"},
    )
    assert client.base_url == "http://jaeger.local:16686"
    assert client.timeout == 10.0
    assert client.headers == {"Authorization": "Bearer test-token"}


@patch("httpx.Client")
def test_jaeger_client_applies_authentication_to_every_request(mock_client_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": []}
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_client

    client = JaegerClient(
        "https://traces.example.test",
        headers={"Authorization": "Bearer secret"},
    )
    client.get_services()

    mock_client_cls.assert_called_once_with(
        timeout=5.0,
        headers={"Authorization": "Bearer secret"},
    )


@patch("httpx.Client")
def test_jaeger_client_is_healthy_true(mock_client_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_client

    client = JaegerClient("http://localhost:16686")
    assert client.is_healthy() is True
    mock_client.get.assert_called_once_with("http://localhost:16686/api/services")


@patch("httpx.Client")
def test_jaeger_client_is_healthy_false_on_exception(mock_client_cls):
    mock_client = MagicMock()
    mock_client.get.side_effect = Exception("Connection refused")
    mock_client_cls.return_value.__enter__.return_value = mock_client

    client = JaegerClient("http://localhost:16686")
    assert client.is_healthy() is False


@patch("httpx.Client")
def test_jaeger_client_get_services_success(mock_client_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": ["frontend", "cartservice", "paymentservice"]}
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_client

    client = JaegerClient("http://localhost:16686")
    services = client.get_services()
    assert services == ["frontend", "cartservice", "paymentservice"]


@patch("httpx.Client")
def test_jaeger_client_get_services_failure_raises(mock_client_cls):
    mock_client = MagicMock()
    mock_client.get.side_effect = Exception("Timeout")
    mock_client_cls.return_value.__enter__.return_value = mock_client

    client = JaegerClient("http://localhost:16686")
    with pytest.raises(ConnectionError, match="Could not connect to Jaeger"):
        client.get_services()


@patch("httpx.Client")
def test_jaeger_client_get_operations_success(mock_client_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": ["/cart/checkout", "/product/{id}"]}
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_client

    client = JaegerClient("http://localhost:16686")
    operations = client.get_operations("frontend")
    assert operations == ["/cart/checkout", "/product/{id}"]
    mock_client.get.assert_called_once_with(
        "http://localhost:16686/api/operations",
        params={"service": "frontend"},
    )


@patch("httpx.Client")
def test_jaeger_client_fetch_traces_success(mock_client_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": [{"traceID": "t1", "spans": []}]}
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_client

    client = JaegerClient("http://localhost:16686")
    traces = client.fetch_traces("frontend", operation="checkout", limit=50, lookback="2h")
    assert len(traces) == 1
    assert traces[0]["traceID"] == "t1"
    mock_client.get.assert_called_once()
    assert mock_client.get.call_args.args == ("http://localhost:16686/api/traces",)
    params = dict(mock_client.get.call_args.kwargs["params"])
    start, end = params.pop("start"), params.pop("end")
    assert params == {"service": "frontend", "operation": "checkout", "limit": 50, "lookback": "2h"}
    # Jaeger's API ignores "lookback"; the window has to be sent as start/end microseconds.
    assert end - start == 2 * 3_600_000_000


def test_jaeger_client_rejects_an_unparseable_lookback():
    client = JaegerClient("http://localhost:16686")
    with pytest.raises(ValueError):
        client.fetch_traces("frontend", lookback="an hour")


@patch("httpx.Client")
def test_jaeger_client_fetches_exact_trace_by_id(mock_client_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    trace_id = "0123456789abcdef0123456789abcdef"
    mock_response.json.return_value = {"data": [{"traceID": trace_id, "spans": []}]}
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_client

    traces = JaegerClient("http://localhost:16686").fetch_trace(trace_id.upper())

    assert traces == [{"traceID": trace_id, "spans": []}]
    mock_client.get.assert_called_once_with(
        f"http://localhost:16686/api/traces/{trace_id}"
    )


@patch("httpx.Client")
def test_jaeger_client_treats_missing_exact_trace_as_pending(mock_client_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_client

    trace_id = "0123456789abcdef0123456789abcdef"
    assert JaegerClient("http://localhost:16686").fetch_trace(trace_id) == []


def test_jaeger_client_rejects_unsafe_trace_id() -> None:
    client = JaegerClient("http://localhost:16686")
    with pytest.raises(ValueError, match="hexadecimal"):
        client.fetch_trace("../../api/services")


def test_get_dependencies_returns_caller_callee_pairs_for_the_window():
    from unittest.mock import MagicMock, patch

    response = MagicMock()
    response.json.return_value = {"data": [
        {"parent": "web", "child": "orders", "callCount": 9},
        {"parent": "orders", "child": "billing", "callCount": 4},
        {"parent": "", "child": "bad"},
        "not-an-edge",
    ]}
    with patch("httpx.Client") as client_class:
        get = client_class.return_value.__enter__.return_value.get
        get.return_value = response
        edges = JaegerClient("http://localhost:16686").get_dependencies(3_600_000)
    assert edges == [("web", "orders"), ("orders", "billing")]
    called_url = get.call_args.args[0]
    params = get.call_args.kwargs["params"]
    assert called_url == "http://localhost:16686/api/dependencies"
    assert params["lookback"] == 3_600_000
    assert params["endTs"] > 0


def test_get_dependencies_failure_is_a_connection_error():
    from unittest.mock import patch

    with patch("httpx.Client") as client_class:
        client_class.return_value.__enter__.return_value.get.side_effect = RuntimeError("boom")
        try:
            JaegerClient("http://localhost:16686").get_dependencies(1000)
        except ConnectionError as err:
            assert "dependencies" in str(err)
        else:
            raise AssertionError("expected ConnectionError")
