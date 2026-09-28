"""Unit tests for JaegerClient."""

from unittest.mock import MagicMock, patch
import pytest

from app.integrations.jaeger.client import JaegerClient


def test_jaeger_client_init():
    client = JaegerClient("http://jaeger.local:16686/", timeout_seconds=10.0)
    assert client.base_url == "http://jaeger.local:16686"
    assert client.timeout == 10.0


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
    mock_client.get.assert_called_once_with(
        "http://localhost:16686/api/traces",
        params={
            "service": "frontend",
            "operation": "checkout",
            "limit": 50,
            "lookback": "2h",
        },
    )
