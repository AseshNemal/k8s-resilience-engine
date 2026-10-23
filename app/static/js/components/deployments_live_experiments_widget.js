/**
 * Live Experiments Widget: shows the stored live placement-drift runs on KinD
 * (GET /api/deployments/benchmarks/live-experiments). Values are shown as stored; nothing is computed here
 * except counts and formatting.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsLiveExperimentsWidget = (function () {
    "use strict";

    const CONFIGS = ["A-no-rule", "B-spread"];

    const pct = (v) => (v == null ? "n/a" : `${(v * 100).toFixed(1)}%`);

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    function statWithCi(label, stat) {
        if (stat?.mean == null) return `${label}: n/a`;
        const ci = stat.ci95 ? ` [${pct(stat.ci95[0])}, ${pct(stat.ci95[1])}]` : "";
        return `${label}: ${pct(stat.mean)}${ci}`;
    }

    // One verdict per condition for the simple table. Exposed wins over stuck, stuck over safe; a run whose
    // target ended in any other status (e.g. UNAVAILABLE) is never shown as safe.
    function cellVerdict(cond) {
        const ok = cond.runs - cond.failed_runs;
        const counts = cond.target_status_t1_counts || {};
        if (ok === 0) return { variant: "danger", text: `Failed ${cond.failed_runs}/${cond.runs}` };
        if (counts.EXPOSED > 0) return { variant: "danger", text: `⚠️ Exposed ${counts.EXPOSED}/${ok}` };
        if (cond.unstable_runs > 0) return { variant: "warning", text: `⏳ Pods stuck ${cond.unstable_runs}/${ok}` };
        if ((counts.NOT_EXPOSED || 0) === ok) return { variant: "success", text: `✅ Safe ${ok}/${ok}` };
        const other = Object.entries(counts).filter(([s]) => s !== "NOT_EXPOSED").map(([s, n]) => `${s.replace("_", " ")} ${n}/${ok}`);
        return { variant: "muted", text: other.join(", ") };
    }

    // Everything behind the verdict, for the cell's tooltip.
    function cellNumbers(cond) {
        const ok = cond.runs - cond.failed_runs;
        const lines = Object.entries(cond.target_status_t1_counts || {}).map(([s, n]) => `Target ${s.replace("_", " ")}: ${n}/${ok} runs`);
        if (cond.unstable_runs > 0) lines.push(`Pods stuck: ${cond.unstable_runs}/${ok} runs`);
        if (cond.failed_runs > 0) lines.push(`Failed: ${cond.failed_runs}/${cond.runs} runs`);
        lines.push(
            statWithCi("D_rate", cond.d_rate),
            `Exposure rate: ${pct(cond.exposure_rate_t0?.mean)} → ${pct(cond.exposure_rate_t1?.mean)}`,
        );
        if (cond.compliance_rate_t1?.mean != null) lines.push(`Compliance: ${pct(cond.compliance_rate_t1.mean)}`);
        return lines.join("\n");
    }

    function renderCell(cond) {
        const td = el("td");
        if (!cond) {
            td.textContent = "not run";
            return td;
        }
        const verdict = cellVerdict(cond);
        td.appendChild(el("span", `status-badge status-badge--no-dot status-badge--${verdict.variant}`, verdict.text));
        td.title = cellNumbers(cond);
        return td;
    }

    function renderSummary(container, data) {
        container.innerHTML = "";
        const conds = data.conditions;
        const exposedConds = conds.filter((c) => c.target_became_exposed > 0).length;
        const stuckConds = conds.filter((c) => c.unstable_runs > 0).length;
        const repetitions = conds.length ? Math.max(...conds.map((c) => c.runs)) : 0;

        const items = [
            { label: "Live Runs", value: `${data.total_runs}`, sub: `${conds.length} conditions × ${repetitions} repetitions; ${data.failed_runs} failed` },
            { label: "Target Became Exposed", value: `${exposedConds} / ${conds.length}`, sub: `Conditions where ${data.target_service} went NOT_EXPOSED → EXPOSED` },
            { label: "Pods Stuck", value: `${stuckConds} / ${conds.length}`, sub: "Conditions with runs that did not settle within 90 s" },
        ];
        items.forEach((item) => {
            const box = el("div", "ensemble-metric-box");
            box.append(
                el("span", "ensemble-metric-box__label", item.label),
                el("span", "ensemble-metric-box__value", item.value),
                el("span", "ensemble-metric-box__sub", item.sub),
            );
            container.appendChild(box);
        });
    }

    const CONFIG_LABELS = { "A-no-rule": "no placement rule", "B-spread": "topology spread rule" };
    const possessive = (name) => (name.endsWith("s") ? `${name}'` : `${name}'s`);

    // Each sentence is derived from the stored counts; nothing here is typed in by hand.
    function buildFindings(data) {
        const conds = data.conditions;
        const target = data.target_service;
        const label = (c) => `${c.scenario_name} (${CONFIG_LABELS[c.config] || c.config})`;
        const okRuns = (c) => c.runs - c.failed_runs;
        const findings = [];

        conds.filter((c) => c.target_became_exposed > 0).forEach((c) => {
            const health = c.unstable_runs === 0
                ? "while every replica was Ready and the rollout reported complete"
                : `while pods were stuck Pending in ${c.unstable_runs}/${okRuns(c)} runs`;
            findings.push({
                key: c.unstable_runs === 0 ? "exposed-healthy" : "exposed-pending",
                severity: "danger",
                title: c.unstable_runs === 0 ? "Exposed while Kubernetes reported it healthy" : "Exposed, with pods stuck Pending",
                text: `${label(c)}: ${target} went from NOT_EXPOSED to EXPOSED in ${c.target_became_exposed}/${okRuns(c)} runs, ${health}.`,
            });
        });

        // The rule was satisfied in every run, yet the target ended exposed. When pods were left Pending,
        // that is how the rule was kept: the copies it could not place never ran.
        conds.filter((c) => c.target_became_exposed > 0 && c.compliance_rate_t1?.mean === 1).forEach((c) => {
            const rule = CONFIG_LABELS[c.config] || "placement rule";
            const exposedRuns = `${c.target_became_exposed}/${okRuns(c)} runs`;
            findings.push({
                key: "rule-obeyed-exposed",
                severity: "danger",
                scenario: c.scenario_id,
                title: `Following the ${rule} still left ${target} exposed`,
                text: c.unstable_runs > 0
                    ? `${c.scenario_name}, with the ${rule}: Kubernetes obeyed the rule by leaving ${possessive(target)} other copies Pending ` +
                      `(${c.unstable_runs}/${okRuns(c)} runs). The copies left running sat on a single node, so one node failure ` +
                      `would take ${target} down (${exposedRuns}).`
                    : `${c.scenario_name}, with the ${rule}: the rule was satisfied, yet every ready copy of ${target} sat on ` +
                      `one node, so one node failure would take it down (${exposedRuns}).`,
            });
        });

        const stuck = conds.filter((c) => c.unstable_runs > 0 && c.target_became_exposed === 0);
        const stuckList = stuck.map((c) => label(c) + " " + c.unstable_runs + "/" + okRuns(c)).join("; ");
        if (stuck.length) {
            findings.push({
                key: "rule-left-pending",
                severity: "warning",
                title: "The spread rule left pods Pending",
                text: "Pods stayed Pending (workload not fully ready within 90 s) in: " + stuckList + ".",
            });
        }

        const unchanged = conds.filter((c) => c.d_rate?.mean === 0 && c.target_became_exposed === 0);
        findings.push({
            key: "routine-unchanged",
            severity: "success",
            title: "Routine operations kept services spread",
            text: `In ${unchanged.length} of ${conds.length} conditions no service changed exposure status in any run (D_rate 0).`,
        });

        const repeatable = conds.filter((c) => c.d_rate?.sd === 0).length;
        findings.push({
            key: "repeatable",
            severity: "muted",
            title: "Repeatable in every run",
            text: `${repeatable} of ${conds.length} conditions gave the same D_rate in every repetition.`,
        });
        return findings;
    }

    const FINDING_ICONS = { danger: "⚠️", warning: "⏳", success: "✅", muted: "🔁" };

    // One card per finding: a short title and the generated sentence, coloured by severity.
    function renderFindings(container, data) {
        container.replaceChildren();
        buildFindings(data).forEach((f) => {
            const card = el("article", `finding-card finding-card--${f.severity}`);
            const title = el("h5", "finding-card__title");
            title.append(el("span", "finding-card__icon", FINDING_ICONS[f.severity] || ""), el("span", null, f.title));
            card.append(title, el("p", "finding-card__text", f.text));
            container.appendChild(card);
        });
    }

    // ---------------------------------------------------------------------------------------------------------
    // The player: one experiment at a time, the target's pods before (T0) and after (T1) the real action, as
    // stored by the run. Pick an experiment, the config (no rule / spread rule) and the run; Play steps through.
    // ---------------------------------------------------------------------------------------------------------

    const SCENARIO_ICONS = { "01": "🚀", "02": "⬆️", "03": "⬇️", "04": "🔄", "05": "🗑️", "06": "🚧", "07": "🧹", "08": "🔥" };
    const NO_NODE = new Set(["unscheduled", "Pending (no node)"]);
    const PLAY_MS = 3200;
    const player = { data: null, scenarios: [], scenario: null, config: "A-no-rule", run: 0, timer: null };
    const shortNodeName = (node) => node.replace(/^.*-(worker\d*|control-plane)$/, "$1");

    function scenarioList(data) {
        const byId = new Map();
        data.conditions.forEach((c) => {
            if (!byId.has(c.scenario_id)) byId.set(c.scenario_id, { id: c.scenario_id, name: c.scenario_name, configs: {} });
            byId.get(c.scenario_id).configs[c.config] = c;
        });
        return [...byId.values()].sort((a, b) => a.id.localeCompare(b.id));
    }

    // The target in one snapshot: its pods per node, and the ones no node took (Pending).
    function targetPlacement(snapshot, target) {
        const pods = snapshot?.[target] || [];
        const nodes = {};
        let pending = 0;
        pods.forEach((p) => {
            if (!p.node || NO_NODE.has(p.node)) pending++;
            else {
                nodes[p.node] = nodes[p.node] || [];
                nodes[p.node].push(p);
            }
        });
        return { nodes, pending, total: pods.length };
    }

    // What the run shows after the action, from its own stored status, as short sentences.
    function runVerdict(run, target) {
        const after = targetPlacement(run.t1, target);
        const readyNodes = Object.entries(after.nodes).filter(([, ps]) => ps.some((p) => p.ready)).map(([n]) => shortNodeName(n));
        const parts = [];
        if (run.target_status_t1 === "EXPOSED") {
            parts.push({ tone: "danger", text: `⚠️ ${target} exposed: every ready copy on ${readyNodes[0] || "one node"}` });
        } else if (run.target_status_t1 === "NOT_EXPOSED") {
            parts.push({ tone: "ok", text: `✅ ${target} still spread over ${readyNodes.length} nodes` });
        } else {
            parts.push({ tone: "muted", text: `${target}: ${String(run.target_status_t1).replace("_", " ").toLowerCase()}` });
        }
        if (after.pending) parts.push({ tone: "warn", text: `⏳ ${after.pending} ${after.pending === 1 ? "copy" : "copies"} stuck Pending, no node took them` });
        return parts;
    }

    function allNodes(run) {
        const names = new Set();
        [run.t0, run.t1].forEach((snap) => Object.values(snap || {}).forEach((pods) => pods.forEach((p) => {
            if (p.node && !NO_NODE.has(p.node)) names.add(p.node);
        })));
        return [...names].sort((a, b) => a.localeCompare(b));
    }

    // One colour per service, by sorted name, so a service looks the same in Before and After.
    const SERVICE_HUES = [212, 152, 32, 350, 190, 95, 15, 320];
    function serviceHues(snapshots, target) {
        const names = new Set();
        snapshots.forEach((snap) => Object.keys(snap || {}).forEach((svc) => { if (svc !== target) names.add(svc); }));
        const hues = {};
        [...names].sort((a, b) => a.localeCompare(b)).forEach((svc, i) => { hues[svc] = SERVICE_HUES[i % SERVICE_HUES.length]; });
        return hues;
    }

    // A pod: the disrupted service is a solid purple pill marked 🎯, the others light pills in their colour.
    function podPill(svc, pod, target, hues, where) {
        const isTarget = svc === target;
        let cls = isTarget ? "xp-pod" : "xp-pod xp-pod--other";
        if (!where) cls += " xp-pod--pending";
        else if (!pod.ready) cls += " xp-pod--notready";
        const pill = el("span", cls, isTarget ? `🎯 ${svc}` : svc);
        if (!isTarget) pill.style.setProperty("--svc-hue", hues[svc]);
        let place = ": no node took it (Pending)";
        if (where) place = pod.ready ? " on " + where : " on " + where + " (not ready)";
        pill.title = pod.name + place;
        return pill;
    }

    // One snapshot: a column per node with every service's pods (the target first), and a No node box for
    // the copies Kubernetes created but could not place.
    function snapshotView(title, snapshot, target, nodes, hues) {
        const box = el("div", "xp-snap");
        box.appendChild(el("span", "xp-snap__title", title));
        const cols = el("div", "xp-snap__nodes");
        // The target first, then the others by name.
        const others = Object.keys(snapshot || {}).filter((s) => s !== target).sort((a, b) => a.localeCompare(b));
        const services = snapshot?.[target] ? [target, ...others] : others;
        const podsOn = (node) => services.flatMap((svc) => (snapshot[svc] || [])
            .filter((p) => (node ? p.node === node : !p.node || NO_NODE.has(p.node)))
            .map((p) => ({ svc, p })));

        nodes.forEach((node) => {
            const col = el("div", "xp-node");
            col.appendChild(el("span", "xp-node__name", `🖥️ ${shortNodeName(node)}`));
            const pods = el("div", "xp-node__pods");
            podsOn(node).forEach(({ svc, p }) => pods.appendChild(podPill(svc, p, target, hues, node)));
            if (!pods.childElementCount) pods.appendChild(el("span", "xp-node__empty", "empty"));
            col.appendChild(pods);
            cols.appendChild(col);
        });
        const pending = podsOn(null);
        if (pending.length) {
            const col = el("div", "xp-node xp-node--pending");
            col.appendChild(el("span", "xp-node__name", "⏳ No node"));
            const pods = el("div", "xp-node__pods");
            pending.forEach(({ svc, p }) => pods.appendChild(podPill(svc, p, target, hues, null)));
            col.appendChild(pods);
            col.title = "Kubernetes created these copies but could not place them on any node (Pending)";
            cols.appendChild(col);
        }
        box.appendChild(cols);
        return box;
    }

    function currentRun() {
        const cond = player.scenario?.configs[player.config];
        const runs = cond?.run_placements?.length ? cond.run_placements : [cond?.example_run].filter(Boolean);
        return { cond, runs, run: runs[Math.min(player.run, runs.length - 1)] };
    }

    // ---- this run's measurements, as stored by the runner, with the mean over the condition's runs ----

    const pctText = (v) => (v == null ? "n/a" : `${Math.round(v * 100)}%`);

    function miniBar(value, className) {
        const bar = el("span", `xp-mini ${className || ""}`);
        const fill = el("span", "xp-mini__fill");
        fill.style.height = `${Math.max(value ?? 0, 0) * 100}%`;
        bar.appendChild(fill);
        return bar;
    }

    function beforeAfter(before, after, danger) {
        const box = el("span", "xp-ba");
        box.append(miniBar(before, ""), el("span", "xp-ba__arrow", "→"), miniBar(after, danger && after > before ? "xp-mini--bad" : ""));
        return box;
    }

    function metricTile(label, value, picture, foot, tip) {
        const tile = el("div", "xp-metric");
        tile.title = tip;
        tile.append(el("span", "xp-metric__label", label), el("span", "xp-metric__value", value), picture, el("span", "xp-metric__foot", foot));
        return tile;
    }

    function renderMetrics(box, run, cond, target) {
        const services = Object.keys(run.t1 || {}).sort((a, b) => a.localeCompare(b));
        const changed = new Set(run.changed_services || []);
        const dots = el("span", "xp-dots");
        services.forEach((svc) => {
            const d = el("span", changed.has(svc) ? "xp-dot-svc is-changed" : "xp-dot-svc");
            d.title = `${svc}: ${changed.has(svc) ? "exposure status changed" : "no change"}`;
            dots.appendChild(d);
        });
        const mean = (stat) => (stat?.mean == null ? "n/a" : pctText(stat.mean));
        const c0 = run.concentration_t0?.[target]?.ratio ?? null;
        const c1 = run.concentration_t1?.[target]?.ratio ?? null;
        const ruled = run.compliance_rate_t1 != null;

        box.replaceChildren(
            metricTile("D_rate", pctText(run.d_rate), dots,
                changed.size ? `changed: ${[...changed].join(", ")} · mean ${mean(cond?.d_rate)}` : `no service changed · mean ${mean(cond?.d_rate)}`,
                "Placement drift rate: the share of services whose exposure status changed between before (T0) and after (T1)."),
            metricTile("Exposure rate", `${pctText(run.exposure_rate_t0)} → ${pctText(run.exposure_rate_t1)}`,
                beforeAfter(run.exposure_rate_t0, run.exposure_rate_t1, true),
                `before → after · mean after ${mean(cond?.exposure_rate_t1)}`,
                "The share of services that one node failure would take down, before and after the action."),
            metricTile(`${target} concentration`, `${pctText(c0)} → ${pctText(c1)}`,
                beforeAfter(c0, c1, true),
                "share of its ready copies on the busiest node",
                `Replica concentration of ${target}: 100% means every ready copy sits on one node.`),
            metricTile("Rule compliance", ruled ? pctText(run.compliance_rate_t1) : "no rule", el("span", "xp-rule", ruled ? "📐" : "—"),
                ruled ? `services meeting their spread rule · mean ${mean(cond?.compliance_rate_t1)}` : "this config declares no placement rule",
                "Of the services that declare a spread rule, the share whose rule is satisfied after the action."),
        );
    }

    function paintPlayer() {
        const root = document.querySelector("#tab-live-experiments [data-role='xp']");
        if (!root || !player.data || !player.scenario) return;
        const target = player.data.target_service;

        root.querySelectorAll(".xp-item").forEach((b) => {
            const on = b.dataset.scenario === player.scenario.id;
            b.classList.toggle("is-active", on);
            b.setAttribute("aria-pressed", String(on));
        });
        root.querySelectorAll("[data-config]").forEach((b) => {
            const on = b.dataset.config === player.config;
            b.classList.toggle("is-active", on);
            b.setAttribute("aria-pressed", String(on));
        });

        const { cond, runs, run } = currentRun();
        const chosen = Math.min(player.run, runs.length - 1);
        root.querySelector("[data-role='xp-runs']").replaceChildren(...runs.map((r, i) => {
            const cls = `xp-run${i === chosen ? " is-active" : ""}${r.target_status_t1 === "EXPOSED" ? " xp-run--danger" : ""}`;
            const btn = el("button", cls, `Run ${r.repetition}`);
            btn.type = "button";
            btn.addEventListener("click", () => { stopPlayer(); player.run = i; paintPlayer(); });
            return btn;
        }));

        root.querySelector("[data-role='xp-title']").textContent =
            `${SCENARIO_ICONS[player.scenario.id.slice(0, 2)] || "🧪"} ${player.scenario.name} · ${CONFIG_LABELS[player.config] || player.config}`;
        const before = root.querySelector("[data-role='xp-before']");
        const after = root.querySelector("[data-role='xp-after']");
        const action = root.querySelector("[data-role='xp-action']");
        const verdict = root.querySelector("[data-role='xp-verdict']");
        if (!run) {
            before.replaceChildren();
            after.replaceChildren();
            action.replaceChildren(el("span", "xp-action__text", "No stored run"));
            verdict.replaceChildren();
            root.querySelector("[data-role='xp-metrics']")?.replaceChildren();
            return;
        }
        const nodes = allNodes(run);
        const hues = serviceHues([run.t0, run.t1], target);
        before.replaceChildren(snapshotView("Before", run.t0, target, nodes, hues));
        after.replaceChildren(snapshotView("After", run.t1, target, nodes, hues));
        action.replaceChildren(el("span", "xp-action__arrow", "➜"), el("code", "xp-action__text", run.action.action));
        verdict.replaceChildren(...runVerdict(run, target).map((p) => el("span", `xp-verdict xp-verdict--${p.tone}`, p.text)));
        const metrics = root.querySelector("[data-role='xp-metrics']");
        if (metrics) renderMetrics(metrics, run, cond, target);
    }

    function selectScenario(scenario) {
        player.scenario = scenario;
        player.run = 0;
        paintPlayer();
    }

    function stopPlayer() {
        if (player.timer) clearInterval(player.timer);
        player.timer = null;
        const btn = document.querySelector("#tab-live-experiments [data-role='xp-play']");
        if (btn) btn.textContent = "▶ Play all";
    }

    // Step through every experiment with the chosen config, then stop.
    function togglePlay() {
        if (player.timer) {
            stopPlayer();
            return;
        }
        let i = 0;
        selectScenario(player.scenarios[0]);
        document.querySelector("#tab-live-experiments [data-role='xp-play']").textContent = "■ Stop";
        player.timer = setInterval(() => {
            i++;
            if (i >= player.scenarios.length) {
                stopPlayer();
                return;
            }
            selectScenario(player.scenarios[i]);
        }, PLAY_MS);
    }

    function renderPlayer(data) {
        const root = document.querySelector("#tab-live-experiments [data-role='xp']");
        if (!root) return;
        stopPlayer();
        player.data = data;
        player.scenarios = scenarioList(data);
        root.querySelector("[data-role='xp-list']").replaceChildren(...player.scenarios.map((s) => {
            const btn = el("button", "xp-item");
            btn.type = "button";
            btn.dataset.scenario = s.id;
            btn.append(el("span", "xp-item__icon", SCENARIO_ICONS[s.id.slice(0, 2)] || "🧪"), el("span", "xp-item__name", s.name));
            const dots = el("span", "xp-item__dots");
            CONFIGS.forEach((cfg) => {
                const cond = s.configs[cfg];
                const v = cond ? cellVerdict(cond) : { variant: "muted", text: "not run" };
                const dot = el("span", `xp-dot xp-dot--${v.variant}`);
                dot.title = `${CONFIG_LABELS[cfg] || cfg}: ${v.text}`;
                dots.appendChild(dot);
            });
            btn.appendChild(dots);
            btn.addEventListener("click", () => { stopPlayer(); selectScenario(s); });
            return btn;
        }));
        selectScenario(player.scenarios.find((s) => s.id === player.scenario?.id) || player.scenarios[0]);
    }

    function initPlayer() {
        const root = document.querySelector("#tab-live-experiments [data-role='xp']");
        if (!root) return;
        root.querySelectorAll("[data-config]").forEach((b) => b.addEventListener("click", () => {
            stopPlayer();
            player.config = b.dataset.config;
            player.run = 0;
            paintPlayer();
        }));
        root.querySelector("[data-role='xp-play']").addEventListener("click", togglePlay);
    }

    // How much was run for the workload on show, counted from its stored results.
    function scaleFacts(data) {
        const conds = data.conditions;
        const scenarios = new Set(conds.map((c) => c.scenario_id)).size;
        const configs = new Set(conds.map((c) => c.config)).size;
        const reps = conds.length ? Math.max(...conds.map((c) => c.runs)) : 0;
        return [
            { value: data.total_runs, label: "real runs on a live cluster" },
            { value: scenarios, label: "disruptions" },
            { value: configs, label: "placement configs" },
            { value: reps, label: "repetitions each" },
            { value: data.target_service, label: "target service" },
        ];
    }

    function renderScale(container, data) {
        if (!container) return;
        container.replaceChildren(...scaleFacts(data).map((fact) => {
            const item = el("div", "exp-scale__item");
            item.append(el("dt", "exp-scale__label", fact.label), el("dd", "exp-scale__value", String(fact.value)));
            return item;
        }));
        container.hidden = false;
    }

    // One placement snapshot as node columns; pods without a node are shown as Pending.
    // Concentration = largest share of the target's ready replicas on one node (stored by the run).
    function concentrationText(conc) {
        if (conc?.ratio == null) return "concentration n/a (no ready replica)";
        const onBusiest = Math.round(conc.ratio * conc.ready_replicas);
        return `concentration ${Math.round(conc.ratio * 100)}% (${onBusiest} of ${conc.ready_replicas} ready on one node)`;
    }

    // Target service first, then the others alphabetically.
    const targetFirst = (target) => (a, b) => {
        if (a.svc === target) return -1;
        if (b.svc === target) return 1;
        return a.svc.localeCompare(b.svc);
    };

    function placementGrid(title, snapshot, target, statusText, conc, previousConc) {
        const wrap = el("div", "placement-snap");
        const head = el("div", "placement-snap__title");
        head.append(el("strong", null, title), el("span", null, ` · ${target}: ${statusText} · `));
        const concSpan = el("span", null, concentrationText(conc));
        const rose = previousConc?.ratio != null && conc?.ratio != null && conc.ratio > previousConc.ratio;
        if (rose) {
            concSpan.className = "placement-snap__conc-up";
            concSpan.textContent += " ⚠️ more concentrated";
            concSpan.title = "Not exposed yet, but losing the busiest node now removes more replicas than before.";
        }
        head.appendChild(concSpan);
        wrap.appendChild(head);

        const byNode = {};
        Object.entries(snapshot).forEach(([svc, pods]) => {
            pods.forEach((p) => {
                const key = p.node || "Pending (no node)";
                byNode[key] = byNode[key] || [];
                byNode[key].push({ svc, ready: p.ready });
            });
        });
        // Show "worker2" instead of "resilience-research-worker2"; the full name is in the tooltip.
        // Pods with no node (stored as "unscheduled" or null) are Pending: created but not placed anywhere.
        const PENDING_KEYS = new Set(["unscheduled", "Pending (no node)"]);
        const short = (node) => node.replace(/^.*-(worker\d*|control-plane)$/, "$1");
        const cols = el("div", "placement-snap__cols");
        Object.keys(byNode).sort((a, b) => a.localeCompare(b)).forEach((node) => {
            const pending = PENDING_KEYS.has(node);
            const col = el("div", pending ? "placement-snap__col placement-snap__col--pending" : "placement-snap__col");
            const label = el("div", "placement-snap__node", pending ? "⏳ Waiting: no node found (Pending)" : `🖥️ ${short(node)}`);
            label.title = pending ? "Kubernetes created these pods but could not place them on any node" : node;
            col.appendChild(label);
            byNode[node].sort(targetFirst(target)).forEach((p) => {
                let cls = "placement-pod";
                if (p.svc === target) cls += " placement-pod--target";
                if (!p.ready) cls += " placement-pod--notready";
                col.appendChild(el("span", cls, `${p.svc.replace("service", "")}${p.ready ? "" : " (not ready)"}`));
            });
            cols.appendChild(col);
        });
        wrap.appendChild(cols);
        return wrap;
    }

    // The action line and the before → after placement of one stored run.
    function runView(run, target) {
        const view = el("div", "live-exp-run");
        view.appendChild(el("p", "ui-note", `Action: ${run.action.action}${run.stable_after_disruption ? "" : " · did not settle within 90 s"}`));
        const pair = el("div", "placement-pair");
        const c0 = run.concentration_t0?.[target];
        const c1 = run.concentration_t1?.[target];
        pair.append(
            placementGrid("Before (T0)", run.t0, target, run.target_status_t0, c0, null),
            el("div", "placement-pair__arrow", "→"),
            placementGrid("After (T1)", run.t1, target, run.target_status_t1, c1, c0),
        );
        view.appendChild(pair);
        return view;
    }

    // One button per stored run; the chosen run's placement is shown below the buttons.
    function runPicker(runs, target, configLabel) {
        const picker = el("div", "run-picker");
        picker.setAttribute("role", "group");
        picker.setAttribute("aria-label", `Run of ${configLabel}`);
        const slot = el("div");
        const buttons = runs.map((run, i) => {
            const btn = el("button", "run-picker__btn", `Run ${run.repetition}`);
            btn.type = "button";
            btn.title = `${target} at T1: ${String(run.target_status_t1).replace("_", " ")}`;
            btn.classList.add(run.target_status_t1 === "EXPOSED" ? "run-picker__btn--danger" : "run-picker__btn--ok");
            btn.addEventListener("click", (event) => {
                event.stopPropagation();  // the click selects a run; it must not close the row
                show(i);
            });
            return btn;
        });
        function show(i) {
            buttons.forEach((b, j) => {
                b.classList.toggle("is-active", i === j);
                b.setAttribute("aria-pressed", String(i === j));
            });
            slot.replaceChildren(runView(runs[i], target));
        }
        picker.append(...buttons);
        show(0);
        return [picker, slot];
    }

    function detailRow(row, target) {
        const tr = el("tr", "live-exp-detail");
        const td = el("td");
        td.colSpan = 3;
        CONFIGS.forEach((cfg) => {
            const cond = row.configs[cfg];
            const runs = cond?.run_placements?.length ? cond.run_placements : [cond?.example_run].filter(Boolean);
            const box = el("div", "live-exp-detail__config");
            const label = CONFIG_LABELS[cfg] || cfg;
            box.appendChild(el("h5", null, `${label[0].toUpperCase()}${label.slice(1)} · ${runs.length} stored run${runs.length === 1 ? "" : "s"}`));
            if (!runs.length) {
                box.appendChild(el("p", "ui-note", "No stored run."));
            } else {
                box.append(...runPicker(runs, target, label));
            }
            td.appendChild(box);
        });
        tr.appendChild(td);
        return tr;
    }

    function renderTable(tbody, data) {
        tbody.innerHTML = "";
        const byScenario = new Map();
        data.conditions.forEach((c) => {
            if (!byScenario.has(c.scenario_id)) byScenario.set(c.scenario_id, { name: c.scenario_name, configs: {} });
            byScenario.get(c.scenario_id).configs[c.config] = c;
        });

        [...byScenario.keys()].sort((a, b) => a.localeCompare(b)).forEach((id) => {
            const row = byScenario.get(id);
            const tr = el("tr");
            const nameCell = el("td", null, row.name);
            nameCell.style.fontWeight = "600";
            tr.appendChild(nameCell);
            CONFIGS.forEach((cfg) => tr.appendChild(renderCell(row.configs[cfg])));
            tr.className = "live-exp-row";
            tr.dataset.scenario = id;
            tr.title = `${id}: click to show the pods before and after`;
            const detail = detailRow(row, data.target_service);
            detail.hidden = true;
            tr.addEventListener("click", () => {
                detail.hidden = !detail.hidden;
                tr.classList.toggle("is-open", !detail.hidden);
            });
            tbody.append(tr, detail);
        });
    }

    // Stored results per workload ("standin" = the controlled runs, "bookinfo" = the real-application runs).
    const resultsCache = {};

    async function fetchResults(workload) {
        if (!resultsCache[workload]) {
            const resp = await fetch(`/api/deployments/benchmarks/live-experiments?workload=${encodeURIComponent(workload)}`);
            resultsCache[workload] = resp.ok ? await resp.json() : { missing: true, status: resp.status };
        }
        return resultsCache[workload];
    }

    function markActiveWorkload(root, workload) {
        root.querySelectorAll("[data-role='live-exp-workload'] [data-workload]").forEach((btn) => {
            const active = btn.dataset.workload === workload;
            btn.classList.toggle("is-active", active);
            btn.setAttribute("aria-pressed", String(active));
        });
    }

    // The page opens on the real Bookinfo runs; the stand-in runs are one switch away under Show details.
    const DEFAULT_WORKLOAD = "bookinfo";

    async function load(workload = DEFAULT_WORKLOAD) {
        const root = document.getElementById("tab-live-experiments");
        if (!root) return;
        const note = root.querySelector("[data-role='live-exp-note']");
        const target = root.querySelector("[data-role='live-exp-target']");
        markActiveWorkload(root, workload);

        try {
            const data = await fetchResults(workload);
            if (data.missing) {
                note.textContent = data.status === 404
                    ? "No live experiment results stored for this workload yet. Run experiments/placement_drift/run_live_experiments.py."
                    : `Could not load live experiment results (HTTP ${data.status}).`;
                return;
            }
            note.textContent = `${data.evidence_note} Source: ${data.results_source}`;
            if (target) target.textContent = `Target: ${data.target_service}`;
            renderScale(root.querySelector("[data-role='live-exp-scale']"), data);
            renderPlayer(data);
            renderFindings(root.querySelector("[data-role='live-exp-findings']"), data);
            renderSummary(root.querySelector("[data-role='live-exp-summary']"), data);
            renderTable(root.querySelector("[data-role='live-exp-table-body']"), data);
        } catch (err) {
            console.warn("Failed to load live experiment results:", err);
            note.textContent = "Could not load live experiment results.";
        }
    }

    // ---------------------------------------------------------------------------------------------------------
    // Stand-in pods vs Bookinfo: the same condition, side by side. "Same" compares what the runs observed:
    // the target's status at T1, whether pods were left unsettled, and the mean D_rate.
    // ---------------------------------------------------------------------------------------------------------

    const conditionKey = (c) => `${c.scenario_id}|${c.config}`;

    function outcomeText(cond) {
        const ok = cond.runs - cond.failed_runs;
        const statuses = Object.entries(cond.target_status_t1_counts || {})
            .map(([status, count]) => `${status.replace("_", " ")} ${count}/${ok}`);
        if (cond.unstable_runs > 0) statuses.push(`pods stuck ${cond.unstable_runs}/${ok}`);
        return `${statuses.join(", ")} · D_rate ${pct(cond.d_rate?.mean)}`;
    }

    function sameOutcome(a, b) {
        return JSON.stringify(Object.keys(a.target_status_t1_counts || {}).sort((x, y) => x.localeCompare(y)))
                === JSON.stringify(Object.keys(b.target_status_t1_counts || {}).sort((x, y) => x.localeCompare(y)))
            && (a.unstable_runs > 0) === (b.unstable_runs > 0)
            && a.d_rate?.mean === b.d_rate?.mean;
    }

    function compareRow(scenarioName, standinCond, bookinfoCond) {
        const tr = el("tr");
        tr.appendChild(el("td", null, scenarioName));
        tr.appendChild(el("td", null, CONFIG_LABELS[standinCond.config] || standinCond.config));
        tr.appendChild(el("td", null, outcomeText(standinCond)));
        tr.appendChild(el("td", null, bookinfoCond ? outcomeText(bookinfoCond) : "not run"));
        const same = Boolean(bookinfoCond) && sameOutcome(standinCond, bookinfoCond);
        const verdict = el("td");
        verdict.appendChild(el("span", `status-badge status-badge--${same ? "success" : "warning"}`, same ? "✓ Same" : "Different"));
        tr.appendChild(verdict);
        return { tr, same };
    }

    async function renderComparison() {
        const root = document.getElementById("tab-live-experiments");
        const card = root?.querySelector("[data-role='live-exp-compare-card']");
        if (!card) return;
        const [standin, bookinfo] = await Promise.all([fetchResults("standin"), fetchResults("bookinfo")]);
        if (standin.missing || bookinfo.missing) return;

        const byKey = new Map(bookinfo.conditions.map((c) => [conditionKey(c), c]));
        const tbody = card.querySelector("[data-role='live-exp-compare-body']");
        tbody.replaceChildren();
        let sameCount = 0;
        [...standin.conditions]
            .sort((a, b) => conditionKey(a).localeCompare(conditionKey(b)))
            .forEach((c) => {
                const { tr, same } = compareRow(c.scenario_name, c, byKey.get(conditionKey(c)));
                if (same) sameCount++;
                tbody.appendChild(tr);
            });
        card.querySelector("[data-role='live-exp-compare-summary']").textContent =
            `${sameCount} of ${standin.conditions.length} conditions gave the same outcome with stand-in pods ` +
            `(${standin.total_runs} runs) and with the real Bookinfo app (${bookinfo.total_runs} runs).`;
        card.hidden = false;
        const headline = root.querySelector("[data-role='live-exp-headline']");
        if (headline) {
            headline.textContent = `Same result on the real Bookinfo app: ${sameCount} of ${standin.conditions.length} conditions`;
            headline.hidden = false;
        }
    }

    function initWorkloadSwitch() {
        const root = document.getElementById("tab-live-experiments");
        root?.querySelectorAll("[data-role='live-exp-workload'] [data-workload]").forEach((btn) => {
            btn.addEventListener("click", () => {
                load(btn.dataset.workload).catch((err) => console.warn("Failed to load live experiment results:", err));
            });
        });
    }

    // ---------------------------------------------------------------------------------------------------------
    // Simulation on the connected cluster
    //
    // Runs the eight disruption scenarios (GET /api/deployments/chaos/benchmarks?source=live) on the real
    // topology of one namespace of the connected cluster. It is a prediction by a fixed rule, not an
    // observation, and nothing in the cluster changes. The response is shown as live only when the server
    // says its topology was live; anything else is refused, never relabelled.
    // ---------------------------------------------------------------------------------------------------------

    const OUTCOME_VARIANT = { COMPLETED: "success", FAILED: "danger", ABORTED: "danger" };
    let simulationInFlight = false;
    let simulationRerun = false;

    const api = () => window.ResilienceApi || { fetchJson: (url) => fetch(url).then((r) => r.json()) };

    function simulationParts() {
        const root = document.getElementById("tab-live-experiments");
        if (!root) return null;
        return {
            select: root.querySelector("[data-role='live-sim-namespace']"),
            run: root.querySelector("[data-role='live-sim-run']"),
            status: root.querySelector("[data-role='live-sim-status']"),
            results: root.querySelector("[data-role='live-sim-results']"),
            body: root.querySelector("[data-role='live-sim-body']"),
        };
    }

    function setSimulationStatus(parts, message, isError) {
        parts.status.textContent = message;
        parts.status.style.color = isError ? "var(--color-danger)" : "var(--color-text-muted)";
    }

    // The namespace with the most services is the default: it is the one most likely to be the workload.
    function chooseNamespace(rows) {
        let best = null;
        rows.forEach((row) => {
            const count = (row.graph?.services || []).length;
            if (count > 0 && (!best || count > best.count)) best = { name: row.namespace, count };
        });
        return best ? best.name : null;
    }

    // A transition record is one pair of nodes and the number of pods that moved between them, so the pods that
    // moved are the sum of pod_count; the number of records would count three pods moving between the same two
    // nodes as one. A record without a usable count adds nothing.
    function podsMoved(transitions) {
        return (transitions || []).reduce((total, record) => {
            const count = Number(record?.pod_count);
            return total + (Number.isFinite(count) && count > 0 ? count : 0);
        }, 0);
    }

    function renderSimulationRows(parts, payload) {
        parts.body.replaceChildren();
        const results = payload.scenario_results || [];
        results.forEach((result) => {
            const row = el("tr");
            row.appendChild(el("td", null, String(result.scenario_name || result.scenario_id).replace(" (live topology)", "")));
            const outcome = el("td");
            outcome.appendChild(el("span", `status-badge status-badge--${OUTCOME_VARIANT[result.status] || "muted"}`, result.status));
            if (result.error_message) {
                const reason = el("div", null, result.error_message);
                reason.style.fontSize = "12px";
                outcome.appendChild(reason);
            }
            row.appendChild(outcome);
            row.appendChild(el("td", null, pct(result.d_rate)));
            const exposure = el("td");
            if (result.delta_e_rate == null) {
                exposure.textContent = "n/a";
            } else {
                const sign = result.delta_e_rate > 0 ? "+" : "";
                exposure.appendChild(el("span", `status-badge status-badge--${result.delta_e_rate > 0 ? "danger" : "muted"}`,
                    `${sign}${(result.delta_e_rate * 100).toFixed(1)}%`));
            }
            row.appendChild(exposure);
            row.appendChild(el("td", null, (result.drifted_services || []).join(", ") || "none"));
            row.appendChild(el("td", null, String(podsMoved(result.transitions))));
            parts.body.appendChild(row);
        });
        parts.results.hidden = results.length === 0;
    }

    async function runSimulation() {
        const parts = simulationParts();
        if (!parts?.select?.value) return;
        if (simulationInFlight) {
            // A change made while a run is under way is run straight after it, never alongside it.
            simulationRerun = true;
            return;
        }
        simulationInFlight = true;
        parts.run.disabled = true;
        parts.results.hidden = true;
        const namespace = parts.select.value;
        setSimulationStatus(parts, `Simulating on namespace ${namespace}…`, false);
        try {
            const payload = await api().fetchJson(
                `/api/deployments/chaos/benchmarks?source=live&namespace=${encodeURIComponent(namespace)}`
            );
            const metrics = payload.aggregate_metrics || {};
            if (metrics.topology_source !== "live") {
                setSimulationStatus(parts, "The server did not report a live topology, so these results are not shown as your cluster's.", true);
                return;
            }
            renderSimulationRows(parts, payload);
            const total = Math.round(metrics.total_scenarios || 0);
            const done = Math.round(metrics.completed_count || 0);
            setSimulationStatus(
                parts,
                `Namespace ${namespace}: ${done} of ${total} scenarios completed · mean D_rate ${pct(metrics.mean_d_rate)} · ` +
                `max ${pct(metrics.max_d_rate)}. Simulated on the cluster's real topology; not experimental evidence.`,
                false
            );
        } catch (error) {
            // The server's own message names the cause: no cluster connected, no workloads in this namespace, ...
            const hint = error?.code === "TOPOLOGY_HAS_NO_WORKLOADS" ? " Choose another namespace." : "";
            setSimulationStatus(parts, `Could not simulate: ${error.message || "request failed"}.${hint}`, true);
        } finally {
            simulationInFlight = false;
            parts.run.disabled = !parts.select.value;
            if (simulationRerun) {
                simulationRerun = false;
                void runSimulation();
            }
        }
    }

    async function loadSimulationNamespaces() {
        const parts = simulationParts();
        if (!parts?.select) return;
        try {
            const overview = await api().fetchJson("/api/deployments/discovery/all");
            const rows = overview.namespaces || [];
            parts.select.replaceChildren();
            rows.forEach((row) => {
                const option = document.createElement("option");
                option.value = row.namespace;
                option.textContent = `${row.namespace} · ${(row.graph?.services || []).length} services`;
                parts.select.appendChild(option);
            });
            const chosen = chooseNamespace(rows);
            if (!rows.length || !chosen) {
                parts.select.replaceChildren();
                const none = document.createElement("option");
                none.value = "";
                none.textContent = "No namespace with workloads";
                parts.select.appendChild(none);
                parts.select.disabled = true;
                parts.run.disabled = true;
                setSimulationStatus(parts, "The connected cluster has no namespace with workloads to simulate.", false);
                return;
            }
            parts.select.value = chosen;
            parts.select.disabled = false;
            parts.run.disabled = false;
            setSimulationStatus(parts, "", false);
            await runSimulation();
        } catch (error) {
            parts.select.replaceChildren();
            const failed = document.createElement("option");
            failed.value = "";
            failed.textContent = "Cluster not available";
            parts.select.appendChild(failed);
            parts.select.disabled = true;
            parts.run.disabled = true;
            setSimulationStatus(parts, `Could not read the cluster: ${error.message || "request failed"}.`, true);
        }
    }

    function initSimulation() {
        const parts = simulationParts();
        if (!parts?.select || !parts.run) return;
        parts.run.addEventListener("click", runSimulation);
        parts.select.addEventListener("change", runSimulation);
        const start = () => {
            loadSimulationNamespaces().catch((err) => console.warn("Failed to start the cluster simulation:", err));
        };
        // The card sits under Show details; reading every namespace of the cluster waits until it is opened,
        // so it does not slow the page's own first load.
        const details = parts.select.closest("details");
        if (!details || details.open) {
            start();
            return;
        }
        details.addEventListener("toggle", function once() {
            if (!details.open) return;
            details.removeEventListener("toggle", once);
            start();
        });
    }

    function init() {
        initWorkloadSwitch();
        initPlayer();
        load().catch((err) => console.warn("Failed to load live experiment results:", err));
        renderComparison().catch((err) => console.warn("Failed to compare the experiment results:", err));
        initSimulation();
    }

    return {
        init: init, load: load, buildFindings: buildFindings, sameOutcome: sameOutcome, cellVerdict: cellVerdict,
        scaleFacts: scaleFacts, targetPlacement: targetPlacement, runVerdict: runVerdict,
        runSimulation: runSimulation, loadSimulationNamespaces: loadSimulationNamespaces,
    };
})();
