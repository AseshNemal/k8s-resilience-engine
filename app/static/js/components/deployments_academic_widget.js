/**
 * Phase 5 Academic Validation Panel Widget:
 * Fetches ground-truth accuracy metrics and updates viva defense indicators.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsAcademicWidget = (function () {
    "use strict";

    let cachedCertificate = null;

    async function loadAccuracyCertificate() {
        try {
            const resp = await fetch("/api/deployments/benchmarks/ground-truth");
            if (!resp.ok) return;
            const evaluation = await resp.json();
            cachedCertificate = evaluation;
            renderCertificate(evaluation.certificate, evaluation);
        } catch (err) {
            console.warn("Could not load academic accuracy certificate:", err);
        }
    }

    function renderCertificate(cert, evaluation) {
        const svcPodEl = document.getElementById("stat-svc-pod-acc");
        const podNodeEl = document.getElementById("stat-pod-node-acc");
        const ownerEl = document.getElementById("stat-owner-acc");
        const concEl = document.getElementById("stat-conc-acc");

        const showAccuracy = (element, value) => {
            if (!element) return;
            element.textContent = value == null ? "Not measured" : `${(value * 100).toFixed(1)}%`;
            element.classList.toggle("is-unmeasured", value == null);
            const fill = element.closest(".academic-stat")?.querySelector(".academic-stat__fill");
            if (fill) {
                fill.style.width = value == null ? "0%" : `${Math.max(0, Math.min(1, value)) * 100}%`;
                fill.classList.toggle("academic-stat__fill--success", value != null);
            }
        };
        showAccuracy(svcPodEl, cert.service_pod_mapping_accuracy);
        showAccuracy(podNodeEl, cert.pod_node_mapping_accuracy);
        showAccuracy(ownerEl, cert.owner_resolution_accuracy);
        showAccuracy(concEl, cert.concentration_detection_accuracy);
        const latencyEl = document.getElementById("stat-latency-overhead");
        if (latencyEl) {
            const latency = cert.runtime_overhead_ms;
            latencyEl.textContent = latency == null ? "Not measured" : `${latency.toFixed(2)} ms`;
            const fill = latencyEl.closest(".academic-stat")?.querySelector(".academic-stat__fill");
            if (fill) fill.style.width = latency == null ? "0%" : `${Math.min(100, latency / 50 * 100)}%`;
        }

        const overallStatusEl = document.getElementById("academic-overall-status");
        if (overallStatusEl) {
            overallStatusEl.textContent = evaluation
                ? `Measured on ${evaluation.ground_truth_source}: ${evaluation.pods_evaluated} pods, ` +
                  `${evaluation.services_evaluated} service(s), ${evaluation.failure_experiments} node-failure run(s) — too small to generalise`
                : "Accuracy not measured against ground truth";
            overallStatusEl.className = "status-pill status-pill--warning";
        }
    }

    function init() {
        const copyBtn = document.getElementById("btn-copy-accuracy-json");
        if (copyBtn) {
            copyBtn.addEventListener("click", function () {
                if (!cachedCertificate) return;
                const jsonText = JSON.stringify(cachedCertificate, null, 2);
                navigator.clipboard.writeText(jsonText).then(() => {
                    const origText = copyBtn.textContent;
                    copyBtn.textContent = "✓ Copied!";
                    setTimeout(() => {
                        copyBtn.textContent = origText;
                    }, 2000);
                }).catch((err) => console.warn("Clipboard copy failed:", err));
            });
        }

        loadAccuracyCertificate().catch((err) => console.warn("Could not load accuracy:", err));
    }

    return {
        init: init,
        loadAccuracyCertificate: loadAccuracyCertificate,
    };
})();
