"""The probe entry point: a Service whose only job is to let the workflow check reach the application from this computer.

On the Docker Desktop cluster nothing is published on the host (every application Service is ClusterIP), so a workflow check
has no address to call. The entry point is ONE extra Service, created by a person from the shipped manifest, that selects the
entry service's pods and is published on loopback by Docker Desktop's load-balancer support. This module only READS: it
recognises that Service and reports its address. It creates and changes nothing.

Because that Service selects the same pods as the application's own Service, topology discovery must not mistake it for part
of the application (it would appear as a second service over the same pods). The label below marks it, and
``without_probe_entries`` is what discovery and the ownership resolver use to leave it out.
"""

from collections.abc import Iterable
from typing import Any

PROBE_ENTRY_LABEL = "resilience-engine.sliit/probe-entry"
MANIFEST_PATH = "experiments/member4_real_validation/manifests/probe-entry-docker-desktop.yaml"


def _labels(service: dict) -> dict:
    return ((service.get("metadata") or {}).get("labels")) or {}


def is_probe_entry(service: dict) -> bool:
    return _labels(service).get(PROBE_ENTRY_LABEL) == "true"


def without_probe_entries(services: Iterable[dict]) -> list[dict]:
    """The application's own Services: the probe entry point is not part of the application's topology."""
    return [service for service in services if not is_probe_entry(service)]


def _address(service: dict) -> str | None:
    ingress = (((service.get("status") or {}).get("loadBalancer")) or {}).get("ingress") or []
    for item in ingress:
        value = item.get("ip") or item.get("hostname")
        if value:
            return str(value)
    return None


def describe_entry_points(services: Iterable[dict], is_port_allowed) -> list[dict[str, Any]]:
    """Each probe entry Service: its published port, the address the cluster gave it (None until it has one), and the loopback
    URL a probe would call. The URL assumes the load balancer is published on this computer's localhost; ``Test it`` is what
    proves it, so it is never reported as reachable here."""
    entries = []
    for service in services:
        if not is_probe_entry(service):
            continue
        spec = service.get("spec") or {}
        for port in spec.get("ports") or []:
            number = port.get("port")
            if not isinstance(number, int):
                continue
            entries.append({
                "service": (service.get("metadata") or {}).get("name"),
                "type": spec.get("type"),
                "port": number,
                "address": _address(service),
                "url": f"http://127.0.0.1:{number}/",
                "portAllowed": bool(is_port_allowed(number)),
            })
    return sorted(entries, key=lambda item: (str(item["service"]), item["port"]))
