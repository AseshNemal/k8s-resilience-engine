/*
 * Experiments and Accuracy of the CONNECTED cluster. Owned by: Member 4 (L.A. Nemal), shown on the Deployment Topology page.
 *
 * Every row drawn here is a node failure that really happened on this cluster and was recorded by this app: a worker stopped by an
 * approved test run, or one the developer stopped while it was watched. They come from GET /api/recommendations/cluster-experiments,
 * which already keeps only the connected cluster's own records. Nothing is simulated, bundled or carried over from another cluster. With
 * no cluster connected, or none recorded yet, the tab says so and says how to record one. Accuracy is a count of matches ("2 of 3"),
 * never a percentage of nothing. All text goes through textContent.
 */
(function () {
    "use strict";

    const ENDPOINT = "/api/recommendations/cluster-experiments";
    const SOURCE = { "validation-run": "Approved test run", "watched-failure": "You stopped it, the app watched" };

    // ---- pure (exported for tests) -----------------------------------------------------------------------------------------------

    function names(list) {
        return list && list.length ? list.join(", ") : "nothing";
    }

    function when(iso) {
        const date = iso ? new Date(iso) : null;
        return date && !Number.isNaN(date.getTime()) ? date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "time not recorded";
    }

    function matchWord(match) {
        return match === true ? { text: "Matched", tone: "pass" } : match === false ? { text: "Different", tone: "warn" } : { text: "Not compared", tone: "wait" };
    }

    /** The headline of the Accuracy tab, from the server's counts. Unknown stays unknown. */
    function headline(accuracy) {
        if (!accuracy || !accuracy.experimentsCompared) return { value: "—", text: "No failure with both a prediction and a measurement has been recorded on this cluster yet." };
        return { value: `${accuracy.matched} of ${accuracy.experimentsCompared}`, text: `node failures were predicted correctly on this cluster${accuracy.experimentsCompared < 5 ? " — too few to call an accuracy rate" : ""}.` };
    }

    // ---- DOM ---------------------------------------------------------------------------------------------------------------------

    function make(doc, tag, text, className, attributes) {
        const node = doc.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        for (const [k, v] of Object.entries(attributes || {})) node.setAttribute(k, v);
        return node;
    }

    function pageHref(doc, key, fallback) {
        const link = Array.from(doc.querySelectorAll ? doc.querySelectorAll(".sidebar a[href]") : []).find((a) => (a.getAttribute("href") || "").includes(key));
        return link ? link.getAttribute("href") : fallback;
    }

    function empty(doc, body, container) {
        const box = make(doc, "section", undefined, "ce-empty", { role: "status" });
        const text = make(doc, "div");
        text.append(make(doc, "h4", "No node failure has been recorded on this cluster yet", "ce-empty__title"),
            make(doc, "p", "This tab fills in when a worker is stopped on the connected cluster and this app records what happened. Two ways to record one:", "ce-empty__body"));
        const list = make(doc, "ul", undefined, "ce-empty__ways");
        const a = make(doc, "li");
        a.append(make(doc, "strong", "Prove it on a test cluster"), make(doc, "span", " — an approved run stops each worker in turn and measures the application. "),
            make(doc, "a", "Open Recommendations", "ce-link", { href: pageHref(doc, "recommendations", "/components/recommendations") }));
        const b = make(doc, "li");
        b.append(make(doc, "strong", "Watch a real failure"), make(doc, "span", " — you stop a worker yourself; the app only watches. "),
            make(doc, "a", "Open Failure impact", "ce-link", { href: pageHref(doc, "failure-impacts", pageHref(doc, "failure_impacts", "/components/failure-impacts")) }));
        list.append(a, b);
        text.append(list);
        if (body.elsewhere && body.elsewhere.count) text.append(make(doc, "p", `${body.elsewhere.count} experiment${body.elsewhere.count === 1 ? " is" : "s are"} saved from other clusters. They are not shown here, because they say nothing about this one.`, "ce-note"));
        if (body.unattributed && body.unattributed.count) text.append(make(doc, "p", `${body.unattributed.count} saved record${body.unattributed.count === 1 ? "" : "s"} did not store which cluster ${body.unattributed.count === 1 ? "it was" : "they were"} from, so ${body.unattributed.count === 1 ? "it is" : "they are"} not counted.`, "ce-note"));
        box.append(text);
        container.replaceChildren(box);
    }

    function chips(doc, label, list, tone) {
        const row = make(doc, "div", undefined, "ce-row");
        row.append(make(doc, "span", label, "ce-row__label"));
        const holder = make(doc, "span", undefined, "ce-chips");
        if (list === null || list === undefined) holder.append(make(doc, "span", "not recorded", "ce-chip ce-chip--wait"));
        else if (!list.length) holder.append(make(doc, "span", "nothing", "ce-chip ce-chip--ok"));
        else list.forEach((n) => holder.append(make(doc, "span", n, `ce-chip ce-chip--${tone}`)));
        row.append(holder);
        return row;
    }

    function card(doc, e) {
        const li = make(doc, "li", undefined, "ce-card", { "data-source": e.source });
        const head = make(doc, "div", undefined, "ce-card__head");
        const m = matchWord(e.match);
        head.append(make(doc, "strong", `${e.failedNode} was stopped`, "ce-card__title"), make(doc, "span", m.text, `ce-pill ce-pill--${m.tone}`));
        li.append(head, make(doc, "p", `${when(e.at)} · ${SOURCE[e.source] || e.source}${e.withChange === true ? " · after the change" : e.withChange === false ? " · before any change" : ""}`, "ce-card__meta"));
        li.append(chips(doc, "Model predicted down", e.predictedDown, "predicted"), chips(doc, "Measured down", e.observedDown, "down"));
        const facts = [];
        if (e.workflowHeld !== null && e.workflowHeld !== undefined) facts.push(e.workflowHeld ? "every workflow service kept a ready replica elsewhere" : "a workflow service had no ready replica elsewhere");
        if (e.checkPassed !== null && e.checkPassed !== undefined) facts.push(e.checkPassed ? "the workflow check passed" : "the workflow check failed");
        facts.push(e.recoveryVerified ? "the worker was back Ready afterwards" : "recovery was not confirmed");
        if (e.durationS) facts.push(`${Math.round(e.durationS)} s`);
        li.append(make(doc, "p", facts.join(" · "), "ce-card__facts"));
        if (e.reasons && e.reasons.length) li.append(make(doc, "p", e.reasons[0], "ce-note"));
        return li;
    }

    function drawExperiments(doc, body, container) {
        if (!body.experiments.length) return empty(doc, body, container);
        const wrap = make(doc, "div", undefined, "ce-wrap");
        wrap.append(make(doc, "p", `${body.experiments.length} node failure${body.experiments.length === 1 ? "" : "s"} recorded on ${body.cluster.context || "the connected cluster"}. Measured, not simulated.`, "ce-lead"));
        const list = make(doc, "ul", undefined, "ce-cards");
        body.experiments.forEach((e) => list.append(card(doc, e)));
        wrap.append(list);
        if (body.elsewhere && body.elsewhere.count) wrap.append(make(doc, "p", `${body.elsewhere.count} more experiment${body.elsewhere.count === 1 ? " is" : "s are"} saved from other clusters and left out.`, "ce-note"));
        container.replaceChildren(wrap);
    }

    function drawAccuracy(doc, body, container) {
        const a = body.accuracy;
        const wrap = make(doc, "div", undefined, "ce-wrap");
        const h = headline(a);
        const big = make(doc, "div", undefined, "ce-big");
        big.append(make(doc, "strong", h.value, "ce-big__value"), make(doc, "span", h.text, "ce-big__text"));
        wrap.append(big);
        if (a && a.byNode.length) {
            const table = make(doc, "table", undefined, "data-table ce-table");
            const head = make(doc, "tr");
            ["Worker stopped", "Predictions that matched", ""].forEach((t) => head.append(make(doc, "th", t, undefined, { scope: "col" })));
            const thead = make(doc, "thead"); thead.append(head); table.append(thead);
            const tbody = make(doc, "tbody");
            a.byNode.forEach((row) => {
                const tr = make(doc, "tr");
                tr.append(make(doc, "td", row.node), make(doc, "td", `${row.matched} of ${row.compared}`));
                const cell = make(doc, "td");
                const state = matchWord(row.matched === row.compared);
                cell.append(make(doc, "span", state.text, `ce-pill ce-pill--${state.tone}`)); tr.append(cell); tbody.append(tr);
            });
            table.append(tbody);
            const scroll = make(doc, "div", undefined, "table-scroll"); scroll.append(table); wrap.append(scroll);
        }
        if (a && a.differences.length) {
            const list = make(doc, "ul", undefined, "ce-cards");
            a.differences.forEach((e) => list.append(card(doc, e)));
            wrap.append(make(doc, "h4", "Where the model and the measurement differed", "ce-sub"), list);
        }
        if (a) wrap.append(make(doc, "p", a.note, "ce-note"));
        if (!a || !a.experimentsCompared) {
            const hint = make(doc, "p", "Record a failure first (Experiments tab) and this fills in.", "ce-note");
            wrap.append(hint);
        }
        container.replaceChildren(wrap);
    }

    function drawOffline(doc, body, containers) {
        containers.forEach((container) => {
            const cluster = window.ResilienceCluster;
            if (cluster) cluster.renderPanel(container, { state: body.cluster.state || "backend-unreachable" }, {
                document: doc, onRetry: () => cluster.refresh(true).then(() => load(doc)),
                whatStillWorks: body.elsewhere && body.elsewhere.count
                    ? `${body.elsewhere.count} experiment${body.elsewhere.count === 1 ? " is" : "s are"} saved from earlier sessions. They are not shown as this cluster's results.` : null });
            else container.replaceChildren(make(doc, "p", "No cluster is connected, so there are no experiments to show.", "ce-note"));
        });
    }

    async function load(doc, fetchImpl) {
        const experiments = doc.querySelector('[data-role="ce-experiments"]');
        const accuracy = doc.querySelector('[data-role="ce-accuracy"]');
        if (!experiments && !accuracy) return null;
        let body = null;
        try {
            const response = await (fetchImpl || fetch)(ENDPOINT, { headers: { Accept: "application/json" } });
            if (!response.ok) throw new Error(String(response.status));
            body = await response.json();
        } catch (_error) {
            [experiments, accuracy].filter(Boolean).forEach((c) => c.replaceChildren(make(doc, "p", "The experiments could not be read. Try again in a moment.", "ce-note", { role: "alert" })));
            return null;
        }
        if (!body.connected) drawOffline(doc, body, [experiments, accuracy].filter(Boolean));
        else {
            if (experiments) drawExperiments(doc, body, experiments);
            if (accuracy) drawAccuracy(doc, body, accuracy);
        }
        return body;
    }

    if (typeof document !== "undefined" && document.addEventListener) {
        const start = () => {
            load(document);
            if (window.ResilienceCluster) window.ResilienceCluster.onChange(() => load(document));
        };
        if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
    }

    window.ResilienceClusterExperiments = { load, headline, matchWord, drawExperiments, drawAccuracy, drawOffline };
})();
