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
            // The Bookinfo recordings only: the team's benchmark, three real worker-node failures.
            const resp = await fetch("/api/deployments/benchmarks/ground-truth?recordings=bookinfo-exp");
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
        const share = count?.compared ? count.correct / count.compared : null;
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

        renderConfusion(evaluation.exposure_confusion || {});
        renderProof(evaluation);
        renderSummaryLine(evaluation, counts);
        renderOtherChecks(counts);
        renderSample(evaluation);
    }

    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

    function renderConfusion(confusion) {
        document.querySelectorAll("#academic-panel [data-cell]").forEach((cell) => {
            const n = confusion[cell.dataset.cell] ?? 0;
            cell.querySelector("b").textContent = String(n);
            cell.classList.toggle("is-zero", n === 0);
        });
    }

    // One line: did the model predict the outages of the real node failures? (the cards below)
    function renderSummaryLine(evaluation, counts) {
        const line = document.querySelector("#academic-panel [data-role='acc-failures']");
        if (!line) return;
        const outages = counts.exposure;
        const allRight = Boolean(outages?.compared && outages.correct === outages.compared);
        let text = `⚠️ ${outages?.correct ?? 0} of ${outages?.compared ?? 0} predictions came true · ${failureRuns(evaluation)}`;
        if (!evaluation.failure_experiments) text = "No real node failure has been recorded yet.";
        else if (allRight) text = `✅ The model predicted every outage correctly · ${failureRuns(evaluation)} on Bookinfo`;
        line.textContent = text;
        line.dataset.tone = allRight ? "ok" : "warn";
    }

    // The other checks, which have no picture of their own: one line, the numbers under Show details.
    function renderOtherChecks(counts) {
        const other = document.querySelector("#academic-panel [data-role='acc-other']");
        if (!other) return;
        const parts = ["service_pod", "pod_node", "owner"].map((m) => counts[m]).filter(Boolean);
        const correct = parts.reduce((n, c) => n + c.correct, 0);
        const compared = parts.reduce((n, c) => n + c.compared, 0);
        other.textContent = compared
            ? `Also checked: which service, node and Deployment each pod belongs to · ${correct} of ${compared} correct`
            : "";
    }

    function renderSample(evaluation) {
        const sampleEl = document.getElementById("academic-overall-status");
        if (!sampleEl) return;
        const recordings = (evaluation.recordings || []).length;
        sampleEl.textContent = recordings
            ? `Checked against kubectl in ${plural(recordings, "recording")}: ${evaluation.pods_evaluated} pod observations, ` +
              `${failureRuns(evaluation)}. A small sample, not a statistical guarantee.`
            : "Accuracy not measured against ground truth";
    }

    // -----------------------------------------------------------------------------------------------
    // The proof: one card per real node failure, the services the model said would go down next to the
    // ones that really went down. A card opens to every service of that failure.
    // -----------------------------------------------------------------------------------------------

    const shortNode = (node) => String(node).replace(/^.*-(worker\d*|control-plane)$/, "$1");

    // The checks of each node failure together, in node order.
    function failureCases(checks) {
        const byNode = new Map();
        (checks || []).forEach((c) => {
            const node = (c.failed_nodes || []).join(", ") || "unknown";
            if (!byNode.has(node)) byNode.set(node, []);
            byNode.get(node).push(c);
        });
        return [...byNode.entries()]
            .sort((a, b) => a[0].localeCompare(b[0]))
            .map(([node, items]) => ({ node, checks: [...items].sort((a, b) => a.service.localeCompare(b.service)) }));
    }

    function make(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    // The services listed as chips, or "nothing" when none went down.
    function downList(label, services) {
        const line = make("div", "proof-line");
        line.appendChild(make("span", "proof-line__label", label));
        const chips = make("span", "proof-line__chips");
        if (services.length) services.forEach((s) => chips.appendChild(make("span", "proof-chip", `💥 ${s}`)));
        else chips.appendChild(make("span", "proof-none", "nothing"));
        line.appendChild(chips);
        return line;
    }

    function outcomePill(available) {
        return make("span", `proof-pill ${available ? "proof-pill--up" : "proof-pill--down"}`, available ? "✅ up" : "💥 down");
    }

    // Opened card: every service of that failure, predicted against observed.
    function serviceRows(checks) {
        const box = make("div", "proof-rows");
        const head = make("div", "proof-row proof-row--head");
        ["Service", "Model said", "Really", ""].forEach((t) => head.appendChild(make("span", null, t)));
        box.appendChild(head);
        checks.forEach((c) => {
            const match = c.predicted_available === c.observed_available;
            const row = make("div", `proof-row${match ? "" : " proof-row--miss"}`);
            row.append(make("strong", null, c.service), outcomePill(c.predicted_available), outcomePill(c.observed_available),
                make("span", "proof-match", match ? "✅" : "❌"));
            box.appendChild(row);
        });
        return box;
    }

    function proofCard(failure) {
        const predicted = failure.checks.filter((c) => !c.predicted_available).map((c) => c.service);
        const observed = failure.checks.filter((c) => !c.observed_available).map((c) => c.service);
        const allMatch = failure.checks.every((c) => c.predicted_available === c.observed_available);

        const card = make("article", `proof-card${allMatch ? "" : " proof-card--miss"}`);
        const rows = serviceRows(failure.checks);
        rows.hidden = true;
        const more = make("button", "proof-card__more", "Every service ▾");
        more.type = "button";
        more.setAttribute("aria-expanded", "false");
        more.addEventListener("click", () => {
            rows.hidden = !rows.hidden;
            card.classList.toggle("is-open", !rows.hidden);
            more.setAttribute("aria-expanded", String(!rows.hidden));
            more.textContent = rows.hidden ? "Every service ▾" : "Every service ▴";
        });
        card.append(
            make("h4", "proof-card__title", `💥 ${failure.node.split(", ").map(shortNode).join(" + ")} switched off`),
            downList("🧩 Model said will go down", predicted),
            downList("🖥️ Really went down", observed),
            make("span", "proof-card__verdict", allMatch ? "✅ Prediction came true" : "❌ Prediction was wrong"),
            more,
            rows,
        );
        return card;
    }

    function renderProof(evaluation) {
        const box = document.querySelector("#academic-panel [data-role='acc-proof-cards']");
        if (!box) return;
        box.replaceChildren(...failureCases(evaluation.exposure_checks).map(proofCard));
    }

    function failureRuns(evaluation) {
        return plural(evaluation.failure_experiments, "real node failure");
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
        failureCases: failureCases,
    };
})();
