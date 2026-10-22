/**
 * Phase 5 Model Validation Panel Widget:
 * Shows the model's accuracy against kubectl ground truth as rings (share confirmed, with counts) and a
 * 2×2 picture of the exposure predictions around real node failures, always with the sample size.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsAcademicWidget = (function () {
    "use strict";

    const SVG_NS = "http://www.w3.org/2000/svg";
    const RADIUS = 50;
    const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

    let cachedCertificate = null;

    async function loadAccuracyCertificate() {
        try {
            const resp = await fetch("/api/deployments/benchmarks/ground-truth");
            if (!resp.ok) return;
            const evaluation = await resp.json();
            cachedCertificate = evaluation;
            render(evaluation);
        } catch (err) {
            console.warn("Could not load the model accuracy:", err);
        }
    }

    function circle(className, extra = {}) {
        const c = document.createElementNS(SVG_NS, "circle");
        c.setAttribute("class", className);
        c.setAttribute("cx", "60");
        c.setAttribute("cy", "60");
        c.setAttribute("r", String(RADIUS));
        c.setAttribute("fill", "none");
        Object.entries(extra).forEach(([k, v]) => c.setAttribute(k, v));
        return c;
    }

    // A ring filled to the measured share, with a tick at the self-chosen target.
    function drawRing(figure, count) {
        const svg = figure.querySelector("svg");
        const value = figure.querySelector(".acc-ring__value");
        const countEl = figure.querySelector(".acc-ring__count");
        const target = Number(figure.dataset.target);
        const share = count && count.compared ? count.correct / count.compared : null;

        const parts = [circle("acc-ring__track")];
        if (share != null) {
            parts.push(circle("acc-ring__fill", {
                "stroke-dasharray": `${CIRCUMFERENCE * share} ${CIRCUMFERENCE}`,
                transform: "rotate(-90 60 60)",
            }));
        }
        const angle = (target * 360 - 90) * (Math.PI / 180);
        const tick = document.createElementNS(SVG_NS, "line");
        tick.setAttribute("class", "acc-ring__tick");
        tick.setAttribute("x1", String(60 + (RADIUS - 9) * Math.cos(angle)));
        tick.setAttribute("y1", String(60 + (RADIUS - 9) * Math.sin(angle)));
        tick.setAttribute("x2", String(60 + (RADIUS + 9) * Math.cos(angle)));
        tick.setAttribute("y2", String(60 + (RADIUS + 9) * Math.sin(angle)));
        parts.push(tick);
        svg.replaceChildren(...parts);

        figure.classList.toggle("is-unmeasured", share == null);
        figure.classList.toggle("is-below-target", share != null && share < target);
        value.textContent = share == null ? "Not measured" : `${(share * 100).toFixed(1)}%`;
        countEl.textContent = share == null ? "no ground truth recorded" : `${count.correct} of ${count.compared} confirmed`;
    }

    function render(evaluation) {
        const counts = evaluation.counts || {};
        document.querySelectorAll("#academic-panel .acc-ring").forEach((figure) => drawRing(figure, counts[figure.dataset.measure]));

        const confusion = evaluation.exposure_confusion || {};
        document.querySelectorAll("#academic-panel [data-cell]").forEach((cell) => {
            const n = confusion[cell.dataset.cell] ?? 0;
            cell.querySelector("b").textContent = String(n);
            cell.classList.toggle("is-zero", n === 0);
        });

        const latencyEl = document.getElementById("stat-latency-overhead");
        const latency = evaluation.certificate?.runtime_overhead_ms;
        if (latencyEl) latencyEl.textContent = latency == null ? "Not measured" : `${latency.toFixed(1)} ms`;

        const sampleEl = document.getElementById("academic-overall-status");
        if (sampleEl) {
            const recordings = (evaluation.recordings || []).length;
            sampleEl.textContent = recordings
                ? `Checked against kubectl in ${recordings} recording${recordings === 1 ? "" : "s"}: ` +
                  `${evaluation.pods_evaluated} pod observations, ${evaluation.failure_experiments} real node ` +
                  `failure${evaluation.failure_experiments === 1 ? "" : "s"}. A small sample, not a statistical guarantee.`
                : "Accuracy not measured against ground truth";
        }
    }

    function init() {
        const copyBtn = document.getElementById("btn-copy-accuracy-json");
        if (copyBtn) {
            copyBtn.addEventListener("click", function () {
                if (!cachedCertificate) return;
                navigator.clipboard.writeText(JSON.stringify(cachedCertificate, null, 2)).then(() => {
                    const origText = copyBtn.textContent;
                    copyBtn.textContent = "✓ Copied!";
                    setTimeout(() => { copyBtn.textContent = origText; }, 2000);
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
