/*
 * The failure monitor: what the application actually did while a worker node was stopped. Owned by: Member 4 (L.A. Nemal).
 *
 * Everything drawn here was MEASURED: ready replicas read from the cluster, real HTTP requests to the application, CPU and memory read
 * from the nodes, and the cluster's own warning events. Nothing is predicted or smoothed. A value that could not be read is drawn as a
 * gap (grey), never as healthy or as zero, and a figure that was not measured says "not measured" rather than showing a placeholder.
 * The model's prediction appears only in its own table, set beside what was measured.
 *
 * The script draws what it is given: ``monitor`` is the server's ``run.monitor`` (a live timeline while a failure is under way, a
 * summary for every finished one, before/after rows). The raw samples of a finished failure are fetched by the caller (``timelineFor``)
 * and passed back in. All text goes through textContent.
 */
(function () {
    "use strict";

    const UNKNOWN = "not measured";
    const SVG = "http://www.w3.org/2000/svg";
    const MARK_LABEL = {
        stop: "Stopped", "node-not-ready": "Seen down", restore: "Started again", "node-ready": "Ready", settled: "Back to normal",
    };
    const VERDICT = {
        better: { glyph: "▲", text: "Better", tone: "pass" }, worse: { glyph: "▼", text: "Worse", tone: "fail" },
        same: { glyph: "＝", text: "Same", tone: "wait" }, unknown: { glyph: "?", text: "Needs both runs", tone: "wait" },
    };

    // ---- pure helpers (exported for tests) --------------------------------------------------------------------------------

    function seconds(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return UNKNOWN;
        const v = Number(value);
        return v >= 90 ? `${Math.round(v / 60)} min` : `${v < 10 ? v.toFixed(1).replace(/\.0$/, "") : Math.round(v)} s`;
    }

    function percent(ratio) {
        return ratio === null || ratio === undefined ? UNKNOWN : `${Math.round(ratio * 100)}%`;
    }

    function episodeKey(e) {
        return e ? `${e.kind}|${e.candidateId || ""}|${e.worker}|${e.repeat}` : "";
    }

    function episodeLabel(e, repeats, options) {
        // A Fail a Node test applies no change, so "before the change" would be wrong there: name the workers that were stopped.
        if (options && options.lab) return `Stopped workers: ${String(e.worker).split("+").join(", ")}${repeats > 1 ? ` · run ${e.repeat}` : ""}`;
        const base = e.kind === "watch" ? "The failure you caused" : e.kind === "baseline" ? "Before the change" : "After the change";
        return `${base} · ${e.worker}${repeats > 1 ? ` · run ${e.repeat}` : ""}`;
    }

    /** The choices of the picker: the failure in progress first, then each finished one in the order it ran. */
    function choices(monitor, options) {
        const out = [];
        if (monitor && monitor.live) out.push({ key: "live", label: monitor.live.worker ? `Now · ${options && options.lab ? "stopped workers: " + String(monitor.live.worker).split("+").join(", ") : monitor.live.worker}` : "Now", live: true });
        const episodes = (monitor && monitor.episodes) || [];
        const repeats = Math.max(1, ...episodes.map((e) => e.repeat || 1));
        episodes.forEach((e) => out.push({ key: episodeKey(e), label: episodeLabel(e, repeats, options), episode: e }));
        return out;
    }

    /** Time-ordered intervals for one service: [t0, t1, ratio] where ratio is ready/baseline, or null when it was not read. */
    function laneCells(samples, service, baseline, intervalS) {
        const cells = [];
        samples.forEach((s, i) => {
            const next = i + 1 < samples.length ? samples[i + 1].t : s.t + intervalS;
            const ready = s.ready ? s.ready[service] : null;
            cells.push({ t0: s.t, t1: Math.min(next, s.t + Math.max(intervalS * 3, 3)), ratio: ready === null || ready === undefined || !baseline ? null : Math.min(ready / baseline, 1), ready });
        });
        return cells;
    }

    /** Split a series with gaps into runs of consecutive readings. A missing reading ends a run; it is never filled in. */
    function segments(samples, read) {
        const runs = [];
        let run = [];
        samples.forEach((s) => {
            const v = read(s);
            if (v === null || v === undefined || Number.isNaN(Number(v))) { if (run.length) runs.push(run); run = []; } else run.push({ t: s.t, v: Number(v) });
        });
        if (run.length) runs.push(run);
        return runs;
    }

    function laneTone(ratio) {
        if (ratio === null) return "unk";
        if (ratio >= 1) return "ok";
        return ratio <= 0 ? "down" : "part";
    }

    // ---- DOM ------------------------------------------------------------------------------------------------------------

    function make(doc, tag, text, className, attributes) {
        const node = doc.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, value);
        return node;
    }

    function svg(doc, tag, attributes) {
        const node = doc.createElementNS ? doc.createElementNS(SVG, tag) : doc.createElement(tag);
        for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, String(value));
        return node;
    }

    function titled(doc, node, text) {
        const title = svg(doc, "title");
        title.textContent = text;
        node.append(title);
        return node;
    }

    const W = 640, LEFT = 96, RIGHT = 12;

    function xScale(timeline) {
        const samples = timeline.samples;
        const stop = (timeline.marks.find((m) => m.kind === "stop") || {}).t;
        const x0 = samples[0].t, x1 = Math.max(samples[samples.length - 1].t + (timeline.intervalS || 1), x0 + 1);
        const px = (t) => LEFT + ((t - x0) / (x1 - x0)) * (W - LEFT - RIGHT);
        return { px, x0, x1, stop, rel: (t) => (stop === undefined ? t - x0 : t - stop) };
    }

    function drawMarks(doc, g, scale, timeline, top, bottom) {
        const seen = new Set();
        let row = 0;
        timeline.marks.forEach((m) => {
            const label = MARK_LABEL[m.kind];
            if (!label || (seen.has(m.kind) && m.kind !== "node-ready")) return;
            seen.add(m.kind);
            const x = scale.px(m.t);
            g.append(svg(doc, "line", { x1: x, x2: x, y1: top, y2: bottom, class: `fm-mark fm-mark--${m.kind}` }));
            const late = x > W * 0.7;
            const text = svg(doc, "text", { x: late ? x - 3 : x + 3, y: top + 9 + (row % 3) * 10, "text-anchor": late ? "end" : "start", class: "fm-marklabel" });
            text.textContent = label;
            g.append(text);
            row += 1;
        });
    }

    function axis(doc, g, scale, y) {
        const step = (scale.x1 - scale.x0) > 120 ? 30 : (scale.x1 - scale.x0) > 40 ? 10 : 5;
        const first = Math.ceil(scale.rel(scale.x0) / step) * step;
        for (let r = first; scale.stop + r <= scale.x1 || (scale.stop === undefined && r + scale.x0 <= scale.x1); r += step) {
            const t = (scale.stop === undefined ? scale.x0 : scale.stop) + r;
            const label = svg(doc, "text", { x: scale.px(t), y, "text-anchor": "middle", class: "fm-tick" });
            label.textContent = r === 0 && scale.stop !== undefined ? "0 s" : `${r > 0 ? "+" : ""}${r} s`;
            g.append(label);
        }
    }

    function availabilityChart(doc, timeline) {
        const figure = make(doc, "figure", undefined, "fm-chart");
        figure.append(make(doc, "figcaption", "Is each service up?", "fm-chart__title"));
        const services = Object.entries(timeline.baseline || {}).filter(([, n]) => n > 0).sort((a, b) => a[0].localeCompare(b[0]));
        if (!services.length) { figure.append(make(doc, "p", "No service had a ready replica at the start, so there is nothing to compare against.", "fm-note")); return figure; }
        const lane = 16, gap = 4, top = 34, height = top + services.length * (lane + gap) + 16;
        const chart = svg(doc, "svg", { viewBox: `0 0 ${W} ${height}`, role: "img", class: "fm-svg", "aria-label": "Ready replicas of each service over time, measured" });
        const scale = xScale(timeline);
        const g = svg(doc, "g");
        // Between the stop and the moment Kubernetes noticed, its readings still say Ready: those cells are drawn as "stopped, not yet seen", never as up.
        const stopAt = (timeline.marks.find((m) => m.kind === "stop") || {}).t;
        const seenAt = (timeline.marks.find((m) => m.kind === "node-not-ready") || {}).t;
        const restoreAt = (timeline.marks.find((m) => m.kind === "restore") || {}).t;
        const staleEnd = seenAt !== undefined ? seenAt : restoreAt !== undefined ? restoreAt : Infinity;
        let hasStale = false;
        services.forEach(([service, baseline], index) => {
            const y = top + index * (lane + gap);
            const label = svg(doc, "text", { x: LEFT - 6, y: y + lane - 4, "text-anchor": "end", class: "fm-lane__label" });
            label.textContent = service;
            g.append(label);
            laneCells(timeline.samples, service, baseline, timeline.intervalS || 1).forEach((c) => {
                const x = scale.px(c.t0), w = Math.max(scale.px(c.t1) - x, 1);
                let tone = laneTone(c.ratio), detail = c.ready === null || c.ready === undefined ? "not read" : `${c.ready} of ${baseline} ready`;
                if (tone === "ok" && stopAt !== undefined && c.t0 >= stopAt && c.t0 < staleEnd) { tone = "stale"; hasStale = true; detail = `${detail} as Kubernetes still reported it; the workers were already stopped`; }
                g.append(titled(doc, svg(doc, "rect", { x: x.toFixed(1), y, width: w.toFixed(1), height: lane, class: `fm-cell fm-cell--${tone}` }), `${service}: ${detail} at ${seconds(scale.rel(c.t0))}`));
            });
        });
        drawMarks(doc, g, scale, timeline, 2, top + services.length * (lane + gap));
        axis(doc, g, scale, height - 3);
        chart.append(g);
        figure.append(chart, legend(doc, [["ok", "all replicas ready"], ["part", "some lost"], ["down", "none ready"], ["unk", "not read"]].concat(hasStale ? [["stale", "stopped, Kubernetes had not noticed yet"]] : [])));
        return figure;
    }

    function legend(doc, items) {
        const list = make(doc, "ul", undefined, "fm-legend");
        items.forEach(([tone, text]) => {
            const li = make(doc, "li");
            li.append(make(doc, "span", undefined, `fm-swatch fm-swatch--${tone}`, { "aria-hidden": "true" }), make(doc, "span", text));
            list.append(li);
        });
        return list;
    }

    function requestsChart(doc, timeline) {
        const figure = make(doc, "figure", undefined, "fm-chart");
        figure.append(make(doc, "figcaption", "Did the application answer?", "fm-chart__title"));
        const taken = timeline.samples.filter((s) => s.req);
        if (!taken.length) {
            figure.append(make(doc, "p", "No requests were sent: no page address was given. Add one before you start to measure what a user would see.", "fm-note"));
            return figure;
        }
        const top = 34, strip = 14, plot = 70, height = top + strip + 10 + plot + 22;
        const chart = svg(doc, "svg", { viewBox: `0 0 ${W} ${height}`, role: "img", class: "fm-svg", "aria-label": "Real requests to the application: answered or failed, and how long each took" });
        const scale = xScale(timeline);
        const g = svg(doc, "g");
        const label = svg(doc, "text", { x: LEFT - 6, y: top + strip - 3, "text-anchor": "end", class: "fm-lane__label" });
        label.textContent = "Requests";
        g.append(label);
        const wide = Math.max((W - LEFT - RIGHT) / Math.max(timeline.samples.length, 1) - 1, 2);
        taken.forEach((s) => {
            const why = s.req.ok ? `answered in ${s.req.ms === null || s.req.ms === undefined ? "?" : s.req.ms} ms` : `failed: ${s.req.why || s.req.status || "no answer"}`;
            g.append(titled(doc, svg(doc, "rect", { x: scale.px(s.t).toFixed(1), y: top, width: wide.toFixed(1), height: strip, class: `fm-cell fm-cell--${s.req.ok ? "ok" : "down"}` }), `${seconds(scale.rel(s.t))}: ${why}`));
        });
        const lat = taken.filter((s) => s.req.ok && s.req.ms !== null && s.req.ms !== undefined);
        const maxMs = Math.max(...lat.map((s) => s.req.ms), 50);
        const y0 = top + strip + 10;
        const py = (ms) => y0 + plot - (Math.min(ms, maxMs) / maxMs) * plot;
        [0, 0.5, 1].forEach((f) => {
            g.append(svg(doc, "line", { x1: LEFT, x2: W - RIGHT, y1: y0 + plot - f * plot, y2: y0 + plot - f * plot, class: "fm-grid" }));
            const t = svg(doc, "text", { x: LEFT - 6, y: y0 + plot - f * plot + 3, "text-anchor": "end", class: "fm-tick" });
            t.textContent = `${Math.round(maxMs * f)} ms`;
            g.append(t);
        });
        segments(lat, (s) => s.req.ms).forEach((run) => {
            if (run.length > 1) g.append(svg(doc, "polyline", { points: run.map((p) => `${scale.px(p.t).toFixed(1)},${py(p.v).toFixed(1)}`).join(" "), class: "fm-line", fill: "none" }));
            else g.append(svg(doc, "circle", { cx: scale.px(run[0].t).toFixed(1), cy: py(run[0].v).toFixed(1), r: 2, class: "fm-dot" }));
        });
        drawMarks(doc, g, scale, timeline, 2, y0 + plot);
        axis(doc, g, scale, height - 3);
        chart.append(g);
        figure.append(chart, legend(doc, [["ok", "answered"], ["down", "failed or no answer"]]));
        return figure;
    }

    function resourceChart(doc, timeline, key, title, unit) {
        const figure = make(doc, "figure", undefined, "fm-chart fm-chart--small");
        figure.append(make(doc, "figcaption", title, "fm-chart__title"));
        const nodes = [...new Set(timeline.samples.flatMap((s) => Object.keys(s[key] || {})))].sort();
        const values = timeline.samples.flatMap((s) => Object.values(s[key] || {})).filter((v) => v !== null && v !== undefined);
        if (!nodes.length || !values.length) {
            figure.append(make(doc, "p", "The cluster did not report this (no metrics API and no readable kubelet).", "fm-note"));
            return figure;
        }
        const plot = 80, top = 34, height = top + plot + 22, maxV = Math.max(...values, 1);
        const chart = svg(doc, "svg", { viewBox: `0 0 ${W} ${height}`, role: "img", class: "fm-svg", "aria-label": `${title} of each node over time, measured` });
        const scale = xScale(timeline);
        const g = svg(doc, "g");
        const py = (v) => top + plot - (v / maxV) * plot;
        [0, 0.5, 1].forEach((f) => {
            g.append(svg(doc, "line", { x1: LEFT, x2: W - RIGHT, y1: top + plot - f * plot, y2: top + plot - f * plot, class: "fm-grid" }));
            const t = svg(doc, "text", { x: LEFT - 6, y: top + plot - f * plot + 3, "text-anchor": "end", class: "fm-tick" });
            t.textContent = `${Math.round(maxV * f)} ${unit}`;
            g.append(t);
        });
        nodes.forEach((node, index) => {
            segments(timeline.samples, (s) => (s[key] || {})[node]).forEach((run) => {
                const tone = `fm-node-${index % 6}`;
                if (run.length > 1) g.append(svg(doc, "polyline", { points: run.map((p) => `${scale.px(p.t).toFixed(1)},${py(p.v).toFixed(1)}`).join(" "), class: `fm-line ${tone}`, fill: "none" }));
                else g.append(svg(doc, "circle", { cx: scale.px(run[0].t).toFixed(1), cy: py(run[0].v).toFixed(1), r: 2, class: `fm-dot ${tone}` }));
            });
        });
        drawMarks(doc, g, scale, timeline, 2, top + plot);
        axis(doc, g, scale, height - 3);
        chart.append(g);
        const list = make(doc, "ul", undefined, "fm-legend");
        nodes.forEach((node, index) => {
            const li = make(doc, "li");
            li.append(make(doc, "span", undefined, `fm-swatch fm-swatch--node fm-node-${index % 6}`, { "aria-hidden": "true" }), make(doc, "span", node));
            list.append(li);
        });
        figure.append(chart, list, make(doc, "p", "A gap in a line is a node that could not answer, for example while it was stopped.", "fm-note"));
        return figure;
    }

    function tile(doc, label, value, sub, tone) {
        const node = make(doc, "div", undefined, `fm-tile fm-tile--${tone || "wait"}`);
        node.append(make(doc, "span", label, "fm-tile__label"), make(doc, "strong", value, "fm-tile__value"));
        if (sub) node.append(make(doc, "span", sub, "fm-tile__sub"));
        return node;
    }

    function tiles(doc, summary, running) {
        const row = make(doc, "div", undefined, "fm-tiles");
        if (!summary) { row.append(make(doc, "p", "Waiting for the first measurements…", "fm-note", { role: "status" })); return row; }
        const req = summary.requests;
        const down = summary.servicesDown || [];
        row.append(
            tile(doc, "Requests answered while the worker was down", req ? percent(req.windowSuccessRatio) : UNKNOWN,
                req ? `${req.windowFailed} of ${req.windowTotal} failed` : "no workflow check was set",
                req && req.windowSuccessRatio !== null ? (req.windowSuccessRatio >= 1 ? "pass" : req.windowSuccessRatio >= 0.5 ? "warn" : "fail") : "wait"),
            tile(doc, "Longest stretch without an answer", req ? seconds(req.longestOutageS) : UNKNOWN, req && req.firstFailureS !== null ? `first failure ${seconds(req.firstFailureS)} after the stop` : "",
                req ? (req.longestOutageS > 0 ? "fail" : "pass") : "wait"),
            // While a test runs, Kubernetes has not yet noticed a stopped node (about a minute): "0, none" would read as a good result, so say it is not known yet.
            running && !down.length && (summary.nodeNotReadyAfterS === null || summary.nodeNotReadyAfterS === undefined)
                ? tile(doc, "Services that lost every ready replica", "not yet", "Kubernetes has not noticed the stop yet (about a minute)", "wait")
                : tile(doc, "Services that lost every ready replica", String(down.length), down.length ? down.join(", ") : (summary.servicesDegraded.length ? `${summary.servicesDegraded.join(", ")} lost some` : "none"),
                    down.length ? "fail" : summary.servicesDegraded.length ? "warn" : "pass"),
            tile(doc, "Back to normal", summary.recoveryAfterRestoreS === null || summary.recoveryAfterRestoreS === undefined ? UNKNOWN : seconds(summary.recoveryAfterRestoreS),
                summary.recoveryAfterRestoreS === null || summary.recoveryAfterRestoreS === undefined ? "after the worker was started again" : "after the worker was started again",
                summary.recoveryAfterRestoreS === null || summary.recoveryAfterRestoreS === undefined ? "wait" : "pass"));
        return row;
    }

    function predictionTable(doc, check) {
        const box = make(doc, "section", undefined, "fm-panel");
        box.append(make(doc, "h5", "What the model predicted, and what was measured", "fm-panel__title"));
        if (!check) { box.append(make(doc, "p", "No prediction was recorded for this failure.", "fm-note")); return box; }
        const list = make(doc, "ul", undefined, "fm-rows");
        const names = (a) => (a && a.length ? a.join(", ") : "none");
        [["Model: services that lose every replica", names(check.predictedDown)], ["Measured: services that lost every replica", names(check.observedDown)]].forEach(([k, v]) => {
            const li = make(doc, "li", undefined, "fm-row");
            li.append(make(doc, "span", k, "fm-row__label"), make(doc, "strong", v));
            list.append(li);
        });
        const verdict = make(doc, "li", undefined, "fm-row");
        verdict.append(make(doc, "span", "Result", "fm-row__label"),
            make(doc, "span", check.matches ? "Matches" : "Different", `fm-chip fm-chip--${check.matches ? "pass" : "warn"}`));
        list.append(verdict);
        box.append(list);
        if (check.predictedButStayedUp && check.predictedButStayedUp.length) box.append(make(doc, "p", `Predicted lost, but stayed up: ${check.predictedButStayedUp.join(", ")}.`, "fm-note"));
        if (check.wentDownButNotPredicted && check.wentDownButNotPredicted.length) box.append(make(doc, "p", `Lost, but not predicted: ${check.wentDownButNotPredicted.join(", ")}.`, "fm-note"));
        if (check.otherServicesDown && check.otherServicesDown.length) box.append(make(doc, "p", `Also down, outside this workflow: ${check.otherServicesDown.join(", ")}.`, "fm-note"));
        box.append(make(doc, "p", check.note, "fm-note"));
        return box;
    }

    function formatCell(row, value) {
        if (value === null || value === undefined) return UNKNOWN;
        return row.unit === "ratio" ? percent(value) : row.unit === "seconds" ? seconds(value) : String(value);
    }

    function comparisonTable(doc, comparison) {
        const box = make(doc, "section", undefined, "fm-panel");
        box.append(make(doc, "h5", "Before and after the change", "fm-panel__title"));
        if (!comparison) { box.append(make(doc, "p", "Nothing to compare yet.", "fm-note")); return box; }
        if (!comparison.measuredBefore) {
            box.append(make(doc, "p", "The behaviour before the change was not measured, so no before-and-after is claimed. Choose “Measure the current behaviour first” (or Full) when you plan the run, and the same worker is stopped once without the change.", "fm-note"));
            return box;
        }
        const table = make(doc, "table", undefined, "data-table fm-compare");
        const head = make(doc, "tr");
        ["Measured", "Before", "After", ""].forEach((h) => head.append(make(doc, "th", h, undefined, { scope: "col" })));
        const thead = make(doc, "thead");
        thead.append(head);
        table.append(thead);
        const body = make(doc, "tbody");
        comparison.rows.forEach((row) => {
            const verdict = VERDICT[row.verdict] || VERDICT.unknown;
            const tr = make(doc, "tr");
            tr.append(make(doc, "td", row.label), make(doc, "td", formatCell(row, row.before)), make(doc, "td", formatCell(row, row.after)));
            const cell = make(doc, "td");
            const chip = make(doc, "span", undefined, `fm-chip fm-chip--${verdict.tone}`);
            chip.append(make(doc, "span", verdict.glyph, "fm-chip__glyph", { "aria-hidden": "true" }), make(doc, "span", verdict.text));
            cell.append(chip);
            tr.append(cell);
            body.append(tr);
        });
        table.append(body);
        const scroll = make(doc, "div", undefined, "table-scroll");
        scroll.append(table);
        box.append(scroll);
        box.append(make(doc, "p", comparison.improved === true ? "Measured behaviour improved with nothing getting worse. One run is one observation, not an accuracy figure."
            : comparison.improved === false ? "Something measured got worse after the change." : "No measured difference.", "fm-note"));
        return box;
    }

    function eventsPanel(doc, timeline) {
        const box = make(doc, "section", undefined, "fm-panel");
        box.append(make(doc, "h5", "What the cluster reported", "fm-panel__title"));
        const events = timeline.events || [];
        if (!events.length) box.append(make(doc, "p", "No warning events were recorded.", "fm-note"));
        else {
            const list = make(doc, "ul", undefined, "fm-events");
            events.slice(0, 10).forEach((e) => {
                const li = make(doc, "li", undefined, "fm-event");
                li.append(make(doc, "span", e.t === undefined ? "" : `+${Math.round(e.t)} s`, "fm-event__t"), make(doc, "strong", e.reason || "Warning"), make(doc, "span", ` ${e.object || ""}`, "fm-event__obj"));
                if (e.message) li.append(make(doc, "span", e.message, "fm-event__msg"));
                list.append(li);
            });
            box.append(list);
        }
        const logs = timeline.logs || [];
        if (logs.length) {
            const details = make(doc, "details", undefined, "fm-logs");
            details.append(make(doc, "summary", `Last log lines of ${logs.length} unhealthy pod${logs.length === 1 ? "" : "s"}`));
            logs.forEach((entry) => {
                details.append(make(doc, "h6", entry.pod, "fm-logs__pod"), make(doc, "pre", entry.lines.join("\n"), "fm-logs__text"));
            });
            box.append(details);
        } else box.append(make(doc, "p", "No crashing pod had a readable log. A pod on a stopped node cannot be asked for one.", "fm-note"));
        return box;
    }

    function limits(doc, timeline) {
        const gaps = Object.entries(timeline.reads || {}).filter(([, c]) => c.failed).map(([name, c]) => `${name}: ${c.failed} unreadable`);
        const lines = [
            `Measured every ${timeline.intervalS || 1} s${timeline.truncated ? " (older samples were thinned)" : ""}; times are accurate to about that.`,
            "Ready replicas are what Kubernetes reports. A pod on a stopped node can stay Ready for a while after the node is gone; the requests show what a user would have seen.",
        ];
        if (gaps.length) lines.push(`Could not be read at some moments — ${gaps.join("; ")}. Those moments are gaps, not zeros.`);
        const details = make(doc, "details", undefined, "fm-limits");
        details.append(make(doc, "summary", "How to read this"));
        lines.forEach((l) => details.append(make(doc, "p", l, "fm-note")));
        return details;
    }

    // ---- the whole section --------------------------------------------------------------------------------------------------

    /**
     * Draw the monitor into ``container``. ``options``: ``selected`` (a choice key), ``onSelect(key)``, ``timelineFor(episode)``
     * (the cached raw timeline of a finished failure, or null while it is being fetched), ``running`` (the run is still going).
     */
    function render(doc, container, monitor, options) {
        const opts = options || {};
        const picks = choices(monitor, { lab: !!opts.lab });
        const section = make(doc, "section", undefined, "fm-section", { "data-role": "failure-monitor", "aria-label": "Measured behaviour during the failure" });
        const head = make(doc, "div", undefined, "fm-head");
        const title = make(doc, "div");
        title.append(make(doc, "h4", "What happened to your application", "fm-title"),
            make(doc, "p", "Measured on the cluster while a worker was stopped. Nothing on this part is a prediction.", "fm-sub"));
        head.append(title, make(doc, "span", "Measured", "fm-chip fm-chip--measured"));
        section.append(head);
        if (!picks.length) {
            section.append(make(doc, "p", opts.running ? "The first failure has not started yet. Measurements appear here as soon as a worker is stopped." : "No failure was measured in this run.", "fm-note", { role: "status" }));
            container.append(section);
            return section;
        }
        const chosen = picks.find((p) => p.key === opts.selected) || picks[0];
        if (picks.length > 1) {
            const bar = make(doc, "div", undefined, "fm-picker", { role: "tablist", "aria-label": "Which failure to show" });
            picks.forEach((p) => {
                const tab = make(doc, "button", p.label, `fm-pick${p.key === chosen.key ? " fm-pick--on" : ""}`, { type: "button", role: "tab", "aria-selected": String(p.key === chosen.key), "data-fm-pick": p.key });
                tab.addEventListener("click", () => (opts.onSelect ? opts.onSelect(p.key) : null));
                bar.append(tab);
            });
            section.append(bar);
        } else section.append(make(doc, "p", chosen.label, "fm-which"));

        const summary = chosen.live ? monitor.live.summary : chosen.episode.summary;
        const timeline = chosen.live ? monitor.live.timeline : (opts.timelineFor ? opts.timelineFor(chosen.episode) : null);
        if (chosen.live) {
            const stage = String(monitor.live.stage || "").toLowerCase().replace(/_/g, " ");
            section.append(make(doc, "p", `Live${stage ? ` — ${monitor.live.kind === "watch" ? "status" : "stage"}: ${stage}` : ""}. Updated every few seconds.`, "fm-live", { role: "status" }));
        }
        section.append(tiles(doc, summary, !!opts.running));
        if (!timeline || !timeline.samples || !timeline.samples.length) {
            section.append(make(doc, "p", chosen.live ? "Waiting for the first measurements…" : "Loading the measured samples…", "fm-note", { role: "status" }));
        } else {
            const charts = make(doc, "div", undefined, "fm-charts");
            charts.append(availabilityChart(doc, timeline), requestsChart(doc, timeline));
            charts.append(resourceChart(doc, timeline, "cpu", "CPU used per node", "m"), resourceChart(doc, timeline, "mem", "Memory used per node", "MiB"));
            section.append(charts);
        }
        const panels = make(doc, "div", undefined, "fm-panels");
        if (!chosen.live) {
            panels.append(predictionTable(doc, chosen.episode.predictionCheck));
            const comparison = (monitor.comparisons || []).find((c) => c.worker === chosen.episode.worker && c.repeat === chosen.episode.repeat
                && (chosen.episode.kind === "candidate" ? c.candidateId === chosen.episode.candidateId : true));
            if (chosen.episode.kind === "candidate" || comparison) panels.append(comparisonTable(doc, comparison || null));
        }
        if (timeline && timeline.samples && timeline.samples.length) panels.append(eventsPanel(doc, timeline));
        section.append(panels);
        if (timeline && timeline.samples) section.append(limits(doc, timeline));
        container.append(section);
        return section;
    }

    window.ResilienceFailureMonitor = { render, choices, episodeKey, episodeLabel, laneCells, laneTone, segments, seconds, percent, VERDICT };
})();
