from app.integrations.tracing import resolve_connection


def test_the_extension_container_convention_gives_the_standard_otlp_ports():
    conn = resolve_connection({"WORKFLOW_JAEGER_URL": "http://host.docker.internal:16686"})
    assert conn.query_url == "http://host.docker.internal:16686"
    assert conn.otlp_http == "http://host.docker.internal:4318"
    assert conn.otlp_grpc == "host.docker.internal:4317"
    assert conn.otlp_source == "same-host-standard-ports"


def test_an_ipv6_host_is_bracketed():
    conn = resolve_connection({"WORKFLOW_JAEGER_URL": "http://[::1]:16686"})
    assert conn.otlp_http == "http://[::1]:4318"


def test_another_jaeger_has_unknown_receivers_not_guessed_ones():
    conn = resolve_connection({"WORKFLOW_JAEGER_URL": "https://traces.example.test"})
    assert (conn.otlp_http, conn.otlp_grpc, conn.otlp_source) == (None, None, "unknown")
    other_port = resolve_connection({"WORKFLOW_JAEGER_URL": "http://jaeger.internal:8080"})
    assert other_port.otlp_source == "unknown"


def test_receivers_the_operator_names_are_used_as_given():
    conn = resolve_connection({
        "WORKFLOW_JAEGER_URL": "http://jaeger.internal:16686/",
        "TRACING_OTLP_HTTP_ENDPOINT": "http://collector.internal:4318/",
    })
    assert conn.query_url == "http://jaeger.internal:16686"          # a trailing slash is dropped
    assert conn.otlp_http == "http://collector.internal:4318"
    assert conn.otlp_grpc is None and conn.otlp_source == "configured"


def test_the_contract_shape():
    mapping = resolve_connection({"WORKFLOW_JAEGER_URL": "http://localhost:16686"}).to_mapping()
    assert set(mapping) == {"query", "otlp"} and set(mapping["otlp"]) == {"http", "grpc", "source"}


def test_an_empty_setting_falls_back_to_the_backends_default():
    from app.integrations.jaeger.client import DEFAULT_JAEGER_URL

    assert resolve_connection({"WORKFLOW_JAEGER_URL": "  "}).query_url == DEFAULT_JAEGER_URL
