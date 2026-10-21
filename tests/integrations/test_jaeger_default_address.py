"""Where the backend looks for Jaeger when nobody names an address, and why it is not hard-coded."""

import os
import subprocess
import sys
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.integrations.jaeger.client import DEFAULT_JAEGER_URL, JaegerClient
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
client = TestClient(app)


def default_in_a_fresh_process(**env) -> str:
    code = "from app.integrations.jaeger.client import DEFAULT_JAEGER_URL as u; print(u)"
    base = {k: v for k, v in os.environ.items() if k != "WORKFLOW_JAEGER_URL"}
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**base, **env},
                          capture_output=True, text=True, check=True)
    return done.stdout.strip()


def test_without_a_setting_the_default_is_the_local_machine():
    assert default_in_a_fresh_process() == "http://localhost:16686"


def test_the_servers_own_setting_is_the_default():
    assert default_in_a_fresh_process(WORKFLOW_JAEGER_URL="http://host.docker.internal:16686") == \
        "http://host.docker.internal:16686"


def test_a_blank_setting_is_treated_as_not_set():
    assert default_in_a_fresh_process(WORKFLOW_JAEGER_URL="   ") == "http://localhost:16686"


def test_an_empty_address_means_the_default_not_an_empty_url():
    assert JaegerClient("").base_url == DEFAULT_JAEGER_URL
    assert JaegerClient("http://jaeger.local:16686/").base_url == "http://jaeger.local:16686"


def test_the_status_check_says_which_address_it_used(monkeypatch):
    monkeypatch.setattr(JaegerClient, "is_healthy", lambda self: False)

    given = client.get("/api/workflows/jaeger/status", params={"jaeger_url": "http://jaeger:16686"}).json()
    default = client.get("/api/workflows/jaeger/status").json()
    blank = client.get("/api/workflows/jaeger/status", params={"jaeger_url": ""}).json()

    assert given["jaegerUrl"] == "http://jaeger:16686"
    assert default["jaegerUrl"] == DEFAULT_JAEGER_URL == blank["jaegerUrl"]


def test_the_extension_points_jaeger_at_the_host_and_allows_that_host():
    environment = yaml.safe_load((ROOT / "extension" / "compose.yaml").read_text())["services"]["resilience-engine"]["environment"]

    url = environment["WORKFLOW_JAEGER_URL"]
    allowed = environment["WORKFLOW_JAEGER_ALLOWED_HOSTS"].split(",")
    host = url.split("//", 1)[1].split(":", 1)[0]
    # "localhost" inside the extension's container is the container itself, never the machine Jaeger runs on.
    assert host == "host.docker.internal"
    # The workflow component refuses any trace server that is not on its safelist.
    assert host in allowed
