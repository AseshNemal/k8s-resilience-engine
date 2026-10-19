"""
Academic Metric Presenter: Formats research accuracy metrics, latency thresholds,
and confidence intervals for executive presentation to viva evaluation panels.
Component: Component 2 - Deployment Topology Modeling
"""
from typing import Any, Dict


class AcademicMetricPresenter:
    """Formats mathematical and statistical modeling metrics into presentation-ready structures."""

    TARGET_MAPPING_ACCURACY = 0.98
    TARGET_CONCENTRATION_ACCURACY = 0.95
    TARGET_LATENCY_MAX_MS = 50.0

    @classmethod
    def format_accuracy_badge(cls, accuracy: float, target: float = TARGET_MAPPING_ACCURACY) -> Dict[str, Any]:
        """Formats an individual accuracy metric into a status badge descriptor."""
        is_passing = accuracy >= target
        pct_formatted = f"{accuracy * 100:.1f}%"
        status_label = "PASSED" if is_passing else "DEGRADED"
        css_modifier = "success" if is_passing else "warning"

        return {
            "value": accuracy,
            "display": pct_formatted,
            "target_display": f"≥ {target * 100:.0f}%",
            "is_passing": is_passing,
            "status_label": status_label,
            "css_class": f"badge--{css_modifier}",
        }

    @classmethod
    def format_latency_metric(cls, latency_ms: float) -> Dict[str, Any]:
        """Formats algorithmic execution latency with sub-50ms target validation."""
        is_passing = latency_ms <= cls.TARGET_LATENCY_MAX_MS
        css_modifier = "success" if is_passing else "warning"

        return {
            "value_ms": latency_ms,
            "display": f"{latency_ms:.2f} ms",
            "target_display": f"< {cls.TARGET_LATENCY_MAX_MS:.0f} ms",
            "is_passing": is_passing,
            "status_label": "EXCELLENT" if latency_ms < 10.0 else ("PASS" if is_passing else "HIGH"),
            "css_class": f"badge--{css_modifier}",
        }

    @classmethod
    def format_certificate_summary(cls, certificate: Any) -> Dict[str, Any]:
        """Assembles comprehensive executive summary for the Academic Validation Panel."""
        svc_pod = getattr(certificate, "service_pod_mapping_accuracy", 1.0)
        pod_node = getattr(certificate, "pod_node_mapping_accuracy", 1.0)
        owner_res = getattr(certificate, "owner_resolution_accuracy", 1.0)
        conc_acc = getattr(certificate, "concentration_detection_accuracy", 1.0)
        is_sound = getattr(certificate, "is_statistically_sound", True)

        mean_acc = (svc_pod + pod_node + owner_res + conc_acc) / 4.0

        return {
            "is_statistically_sound": is_sound,
            "mean_accuracy_display": f"{mean_acc * 100:.1f}%",
            "service_pod_mapping": cls.format_accuracy_badge(svc_pod, cls.TARGET_MAPPING_ACCURACY),
            "pod_node_mapping": cls.format_accuracy_badge(pod_node, cls.TARGET_MAPPING_ACCURACY),
            "owner_resolution": cls.format_accuracy_badge(owner_res, cls.TARGET_MAPPING_ACCURACY),
            "concentration_detection": cls.format_accuracy_badge(conc_acc, cls.TARGET_CONCENTRATION_ACCURACY),
            "overall_status": "VERIFIED RESEARCH CERTIFICATE" if is_sound else "PROVISIONAL",
        }
