/**
 * Phase 5 Model Validation Panel Widget:
 * One ring for every check the model answered against kubectl ground truth, one bar per kind of check,
 * one line about real node failures; the 2×2 of outcomes, timing and sample are under Show details.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsAcademicWidget = (function () {
    "use strict";

    const SVG_NS = "http://www.w3.org/2000/svg";
    const RADIUS = 50;
    const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
    const MEASURES = ["service_pod", "pod_node", "owner", "exposure"];

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

    // Every check kubectl could confirm, summed over the measures that were recorded.
    function overall(counts) {
        let correct = 0;
        let compared = 0;
        MEASURES.forEach((m) => {
            if (counts[m]) {
                correct += counts[m].correct;
                compared += counts[m].compared;
            }
        });
        return { correct, compared };
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

    function drawOverall(total) {
        const box = document.querySelector("#academic-panel [data-role='acc-overall']");
        if (!box) return;
        const share = total.compared ? total.correct / total.compared : null;
        const parts = [circle("acc-ring__track")];
        if (share != null) {
            parts.push(circle("acc-ring__fill", {
                "stroke-dasharray": `${CIRCUMFERENCE * share} ${CIRCUMFERENCE}`,
                transform: "rotate(-90 60 60)",
            }));
        }
        box.querySelector("svg").replaceChildren(...parts);
        box.classList.toggle("is-unmeasured", share == null);
        box.querySelector("[data-role='acc-overall-value']").textContent = share == null ? "–" : `${Math.round(share * 1000) / 10}%`;
        box.querySelector("[data-role='acc-overall-count']").textContent =
            share == null ? "Not measured yet" : `${total.correct} of ${total.compared}`;
    }

    function drawBar(bar, count) {
        const share = count && count.compared ? count.correct / count.compared : null;
        const target = Number(bar.dataset.target);
        bar.classList.toggle("is-unmeasured", share == null);
        bar.classList.toggle("is-below-target", share != null && share < target);
        bar.querySelector(".acc-bar__fill").style.width = share == null ? "0%" : `${share * 100}%`;
        bar.querySelector(".acc-bar__value").textContent = share == null ? "Not measured" : `${count.correct}/${count.compared}`;
    }

    function render(evaluation) {
        const counts = evaluation.counts || {};
        drawOverall(overall(counts));
        document.querySelectorAll("#academic-panel .acc-bar").forEach((bar) => drawBar(bar, counts[bar.dataset.measure]));

        const confusion = evaluation.exposure_confusion || {};
        document.querySelectorAll("#academic-panel [data-cell]").forEach((cell) => {
            const n = confusion[cell.dataset.cell] ?? 0;
            cell.querySelector("b").textContent = String(n);
            cell.classList.toggle("is-zero", n === 0);
        });

        const failures = document.querySelector("#academic-panel [data-role='acc-failures']");
        if (failures) {
            failures.textContent = evaluation.failure_experiments
                ? `${failureRuns(evaluation)}: ${confusion.down_predicted_down ?? 0} outages caught · ` +
                  `${confusion.down_predicted_up ?? 0} missed · ${confusion.up_predicted_down ?? 0} false alarms`
                : "";
        }

        const latencyEl = document.getElementById("stat-latency-overhead");
        const latency = evaluation.certificate?.runtime_overhead_ms;
        if (latencyEl) latencyEl.textContent = latency == null ? "Not measured" : `${latency.toFixed(1)} ms`;

        const sampleEl = document.getElementById("academic-overall-status");
        if (sampleEl) {
            const recordings = (evaluation.recordings || []).length;
            sampleEl.textContent = recordings
                ? `Checked against kubectl in ${recordings} recording${recordings === 1 ? "" : "s"}: ` +
                  `${evaluation.pods_evaluated} pod observations, ${failureRuns(evaluation)}. A small sample, not a statistical guarantee.`
                : "Accuracy not measured against ground truth";
        }
    }

    function failureRuns(evaluation) {
        const n = evaluation.failure_experiments;
        return `${n} real node failure${n === 1 ? "" : "s"}`;
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
        overall: overall,
    };
})();
