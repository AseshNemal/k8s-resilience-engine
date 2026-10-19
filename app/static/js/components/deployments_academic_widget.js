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
            const resp = await fetch("/api/deployments/benchmarks/accuracy");
            if (!resp.ok) return;
            const cert = await resp.json();
            cachedCertificate = cert;
            renderCertificate(cert);
        } catch (err) {
            console.warn("Could not load academic accuracy certificate:", err);
        }
    }

    function renderCertificate(cert) {
        const svcPodEl = document.getElementById("stat-svc-pod-acc");
        const podNodeEl = document.getElementById("stat-pod-node-acc");
        const ownerEl = document.getElementById("stat-owner-acc");
        const concEl = document.getElementById("stat-conc-acc");

        if (svcPodEl && cert.service_pod_mapping_accuracy != null) {
            svcPodEl.textContent = `${(cert.service_pod_mapping_accuracy * 100).toFixed(1)}%`;
        }
        if (podNodeEl && cert.pod_node_mapping_accuracy != null) {
            podNodeEl.textContent = `${(cert.pod_node_mapping_accuracy * 100).toFixed(1)}%`;
        }
        if (ownerEl && cert.owner_resolution_accuracy != null) {
            ownerEl.textContent = `${(cert.owner_resolution_accuracy * 100).toFixed(1)}%`;
        }
        if (concEl && cert.concentration_detection_accuracy != null) {
            concEl.textContent = `${(cert.concentration_detection_accuracy * 100).toFixed(1)}%`;
        }

        const overallStatusEl = document.getElementById("academic-overall-status");
        if (overallStatusEl && cert.is_statistically_sound) {
            overallStatusEl.textContent = "✓ Verified Research Standard (≥ 98.0%)";
            overallStatusEl.className = "status-pill status-pill--success";
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
                });
            });
        }

        loadAccuracyCertificate();
    }

    return {
        init: init,
        loadAccuracyCertificate: loadAccuracyCertificate,
    };
})();
