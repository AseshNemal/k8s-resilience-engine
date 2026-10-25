/**
 * Phase 5 Resilience Drift & Signals Widget:
 * Renders categories counters, SPOF lists, and worker node risk rankings.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsDriftWidget = (function () {
    "use strict";

    // Every request carries the namespace chosen in the page header (deployments_namespace.js).
    const scoped = (url) => (window.DeploymentsNamespace ? window.DeploymentsNamespace.withNamespace(url) : url);

    // The page reads the connected cluster only, so drift always compares declared intent with that cluster.
    async function loadDriftSignals() {
        try {
            // The three reads are independent, so they go out together rather than one after another.
            const [catResp, spofResp, nodeResp] = await Promise.all([
                fetch(scoped("/api/deployments/drift/categories")),
                fetch(scoped("/api/deployments/drift/spofs")),
                fetch(scoped("/api/deployments/drift/nodes/risk")),
            ]);
            if (!catResp.ok) {
                const body = await catResp.json().catch(() => ({}));
                const container = document.getElementById("spof-workloads-list");
                const reason = body.detail?.message || body.detail || "HTTP " + catResp.status;
                if (container) {
                    container.textContent = `Drift analysis unavailable: ${reason}`;
                }
                return;
            }
            renderCategoryCounters((await catResp.json()).summary || {});
            if (spofResp.ok) renderSpofList(await spofResp.json());
            if (nodeResp.ok) renderNodeRisks(await nodeResp.json());
        } catch (err) {
            console.warn("Could not load drift signals:", err);
        }
    }

    function renderCategoryCounters(summary) {
        const scaleEl = document.getElementById("count-scale-drift");
        const affEl = document.getElementById("count-affinity-drift");
        const resEl = document.getElementById("count-resource-drift");
        const contEl = document.getElementById("count-contention-drift");
        const spreadEl = document.getElementById("count-spread-drift");

        if (scaleEl) scaleEl.textContent = summary.scale_drift ?? 0;
        if (affEl) affEl.textContent = summary.node_affinity_drift ?? 0;
        if (resEl) resEl.textContent = summary.resource_limit_drift ?? 0;
        if (contEl) contEl.textContent = summary.resource_contention ?? 0;
        if (spreadEl) spreadEl.textContent = summary.topology_spread_violations ?? 0;
    }

    function renderSpofList(spofs) {
        const container = document.getElementById("spof-workloads-list");
        const bannerCount = document.getElementById("spof-banner-count");
        const totalBadge = document.getElementById("spof-total-badge");

        if (bannerCount) bannerCount.textContent = spofs.length;
        if (totalBadge) totalBadge.textContent = `${spofs.length} exposed`;
        // Red only when something is exposed; 0 exposed is the good outcome.
        document.getElementById("spof-banner-badge")?.classList.toggle("spof-alert-badge--clear", spofs.length === 0);
        if (!container) return;

        container.innerHTML = "";
        if (spofs.length === 0) return;  // the spread map above already shows every service as safe

        spofs.forEach((s) => {
            const item = document.createElement("div");
            item.className = "spof-item";
            item.innerHTML = `
                <div>
                    <strong>${s.service_name}</strong>
                    <div style="font-size: 11px; color: var(--color-text-muted); margin-top: 2px;">
                        ${s.reason || "All replicas co-located on single failure domain."}
                    </div>
                </div>
                <div style="display: flex; align-items: center; gap: 10px;">
                    <span class="status-badge status-badge--danger">${s.ready_replicas ?? 0} ready on ${(s.hosting_nodes || []).join(", ") || "n/a"}</span>
                    <button type="button" class="spof-drilldown-btn" data-spof-svc="${s.service_name}">
                        <span>Drill Down</span>
                        <span class="btn-arrow">→</span>
                    </button>
                </div>
            `;
            const drillBtn = item.querySelector("button");
            drillBtn.addEventListener("click", () => {
                if (window.DeploymentsModalManager) {
                    window.DeploymentsModalManager.openViolationModal(s.service_name);
                }
            });
            container.appendChild(item);
        });
    }

    // -----------------------------------------------------------------------------------------------
    // Spread map: services × worker nodes, one dot per pod. A row whose ready dots all sit in one
    // column is exposed. The status is the graph's own (exposedFailureDomains), not recomputed here.
    // -----------------------------------------------------------------------------------------------

    const PENDING = "__pending__";
    const shortNode = (node) => node.replace(/^.*-(worker\d*|control-plane)$/, "$1");

    function spreadMatrix(deployment) {
        const services = deployment.services || [];
        const nodes = (deployment.nodes || []).map((n) => n.name);
        const rows = services.map((svc) => {
            // Ready and not-ready copies of this service on each node (Pending pods get their own column).
            const cells = {};
            (svc.pods || []).forEach((pod) => {
                const key = pod.node || PENDING;
                const cell = (cells[key] = cells[key] || { ready: 0, notReady: 0 });
                if (pod.ready) cell.ready++;
                else cell.notReady++;
            });
            const ready = Object.values(cells).reduce((n, c) => n + c.ready, 0);
            const exposedOn = svc.exposedFailureDomains ?? svc.exposed_failure_domains ?? [];
            let status = "safe";
            if (ready === 0) status = "unavailable";
            else if (exposedOn.length) status = "exposed";
            // The graph's own replica concentration: largest share of the ready copies on one node.
            const concentration = svc.replicaConcentration ?? svc.replica_concentration ?? null;
            return { name: svc.name, cells, ready, status, exposedOn, concentration };
        });
        // Pods on a node the graph does not list (or on none) still get a column, so no pod disappears.
        const extra = new Set();
        rows.forEach((r) => Object.keys(r.cells).forEach((k) => { if (!nodes.includes(k)) extra.add(k); }));
        const columns = [...nodes, ...[...extra].sort((a, b) => (a === PENDING) - (b === PENDING) || a.localeCompare(b))];
        // Riskiest services first.
        const order = { exposed: 0, unavailable: 1, safe: 2 };
        rows.sort((a, b) => order[a.status] - order[b.status] || a.name.localeCompare(b.name));
        return { columns, rows };
    }

    // Ready copies a service keeps when one node fails. With no node chosen, the worst case: the node
    // holding most of its ready copies fails (the same rule as the exposure in the graph).
    function copiesLeft(row, failedNode) {
        const readyOn = (node) => (row.cells[node] || { ready: 0 }).ready;
        let lost = 0;
        let node = failedNode;
        if (failedNode) {
            lost = readyOn(failedNode);
        } else {
            Object.keys(row.cells).filter((k) => k !== PENDING).forEach((k) => {
                if (readyOn(k) > lost) {
                    lost = readyOn(k);
                    node = k;
                }
            });
        }
        const left = row.ready - lost;
        let outcome = "unaffected";
        if (row.ready === 0) outcome = "unavailable";
        else if (left === 0) outcome = "down";
        else if (lost > 0) outcome = failedNode ? "fewer" : "survives";  // worst case survived: safe
        return { left, lost, node, outcome };
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    // -----------------------------------------------------------------------------------------------
    // What-if board: crash a worker node (a picture only, the cluster is not touched) and see which
    // services keep running. Nodes are drawn with their pods; each service has a card with its copies.
    // -----------------------------------------------------------------------------------------------

    const STEP_MS = 2600;
    const board = { deployment: null, rows: [], columns: [], failed: null, focus: null, timer: null };
    const hueOf = (name) => window.DeploymentsTopologyWidget?.hueOf?.(name) ?? 215;
    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

    function podChip(service, ready) {
        const chip = el("span", ready ? "wi-pod" : "wi-pod wi-pod--notready", service);
        chip.dataset.service = service;
        chip.style.setProperty("--svc-hue", hueOf(service));
        chip.title = `${service}: ${ready ? "ready copy" : "not ready"}`;
        return chip;
    }

    function nodeBox(column) {
        const pending = column === PENDING;
        const box = el(pending ? "div" : "button", pending ? "wi-node wi-node--pending" : "wi-node");
        box.dataset.node = column;
        if (!pending) {
            box.type = "button";
            box.title = `${column}: click to crash it`;
            box.addEventListener("click", () => { stopPlay(); crash(board.failed === column ? null : column); });
        }
        const head = el("span", "wi-node__head");
        head.append(el("span", "wi-node__icon", pending ? "⏳" : "🖥️"), el("strong", null, pending ? "Pending" : shortNode(column)));
        const stamp = el("span", "wi-node__stamp", "✕ DOWN");
        const pods = el("span", "wi-node__pods");
        board.rows.forEach((row) => {
            const cell = row.cells[column];
            if (!cell) return;
            for (let i = 0; i < cell.ready; i++) pods.appendChild(podChip(row.name, true));
            for (let i = 0; i < cell.notReady; i++) pods.appendChild(podChip(row.name, false));
        });
        if (!pods.childElementCount) pods.appendChild(el("span", "wi-node__empty", "no pods"));
        box.append(head, pods, stamp);
        return box;
    }

    function serviceCard(row) {
        const card = el("button", "wi-svc");
        card.type = "button";
        card.dataset.service = row.name;
        card.style.setProperty("--svc-hue", hueOf(row.name));
        card.title = `${row.name}: click to see its copies in the picture`;
        card.append(
            el("span", "wi-svc__name", row.name),
            el("span", "wi-svc__copies"),
            el("span", "wi-svc__state"),
        );
        card.addEventListener("click", () => {
            board.focus = board.focus === row.name ? null : row.name;
            paint();
        });
        return card;
    }

    // What one card says, with no node crashed (where its copies are) or after a crash (what is left).
    function cardState(row) {
        const nodesUsed = Object.keys(row.cells).filter((k) => k !== PENDING && row.cells[k].ready > 0);
        if (!board.failed) {
            if (row.status === "unavailable") return { tone: "muted", icon: "⛔", text: "No ready copy" };
            if (row.status === "exposed") return { tone: "danger", icon: "⚠️", text: `Every copy on ${shortNode(nodesUsed[0] || "one node")}` };
            return { tone: "ok", icon: "✅", text: `Copies on ${plural(nodesUsed.length, "node")}` };
        }
        const r = copiesLeft(row, board.failed);
        if (r.outcome === "down") return { tone: "danger", icon: "💥", text: "Goes down" };
        if (r.outcome === "fewer") return { tone: "warn", icon: "⚠️", text: `Keeps running · ${r.left} of ${row.ready} left` };
        if (r.outcome === "unavailable") return { tone: "muted", icon: "⛔", text: "No ready copy" };
        return { tone: "ok", icon: "✅", text: "Not affected" };
    }

    function paint() {
        const root = document.querySelector("[data-role='whatif']");
        if (!root) return;
        root.classList.toggle("has-failure", Boolean(board.failed));
        root.classList.toggle("has-focus", Boolean(board.focus));
        root.querySelectorAll(".wi-node").forEach((n) => n.classList.toggle("is-crashed", n.dataset.node === board.failed));
        root.querySelectorAll(".wi-node").forEach((n) => {
            n.querySelectorAll(".wi-pod").forEach((p) => p.classList.toggle("is-lost", n.dataset.node === board.failed));
        });
        root.querySelectorAll("[data-service]").forEach((n) => n.classList.toggle("is-focus", n.dataset.service === board.focus));

        board.rows.forEach((row) => {
            const card = root.querySelector(`.wi-svc[data-service="${CSS.escape(row.name)}"]`);
            if (!card) return;
            const state = cardState(row);
            card.dataset.tone = state.tone;
            card.querySelector(".wi-svc__state").textContent = `${state.icon} ${state.text}`;
            // One small pod per ready copy: grey and crossed when it was on the crashed node.
            const lost = board.failed ? (row.cells[board.failed] || { ready: 0 }).ready : 0;
            card.querySelector(".wi-svc__copies").replaceChildren(
                ...Array.from({ length: row.ready }, (_, i) => el("span", i < row.ready - lost ? "wi-copy" : "wi-copy wi-copy--lost")),
                el("span", "wi-svc__count", `${row.ready} ${row.ready === 1 ? "copy" : "copies"}`),
            );
        });

        const headline = root.querySelector("[data-role='whatif-headline']");
        const exposed = board.rows.filter((r) => r.status === "exposed");
        if (!board.failed) {
            headline.textContent = exposed.length
                ? `⚠️ ${exposed.length} of ${board.rows.length} services have every copy on one node. Click a node to crash it.`
                : `✅ Every service has copies on more than one node. Click a node to crash it.`;
        } else {
            const outcomes = board.rows.map((r) => copiesLeft(r, board.failed).outcome);
            const down = outcomes.filter((o) => o === "down").length;
            const running = outcomes.filter((o) => o === "fewer" || o === "unaffected").length;
            headline.textContent = `💥 ${shortNode(board.failed)} crashed: ${down} service${down === 1 ? "" : "s"} down, ${running} still running.`;
        }
        root.querySelector("[data-role='whatif-reset']").hidden = !board.failed;

        // The two measures below the board follow its state: the chosen service, the crashed node.
        document.querySelectorAll("[data-role='conc-rows'] .risk-row").forEach((r) => r.classList.toggle("is-selected", r.dataset.service === board.focus));
        document.querySelectorAll("[data-role='expo-rows'] .risk-row").forEach((r) => r.classList.toggle("is-selected", r.dataset.node === board.failed));
    }

    // -----------------------------------------------------------------------------------------------
    // Below the board: replica concentration per service and failure-domain exposure per node, with
    // the graph's own values. A row is a button that drives the board above.
    // -----------------------------------------------------------------------------------------------

    function showOnBoard() {
        document.querySelector("[data-role='whatif']")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    // Busiest node of a service: the node holding most of its ready copies.
    function busiestNode(row) {
        let best = null;
        Object.entries(row.cells).forEach(([node, c]) => {
            if (node !== PENDING && c.ready > 0 && (!best || c.ready > best.ready)) best = { node, ready: c.ready };
        });
        return best;
    }

    function concentrationRow(row, workers) {
        const pct = row.concentration == null ? null : Math.round(row.concentration * 100);
        const even = workers ? Math.round(100 / workers) : null;
        let tone = "ok";
        if (pct === 100) tone = "danger";
        else if (pct != null && even != null && pct > even) tone = "warn";
        const busiest = busiestNode(row);

        const btn = el("button", `risk-row risk-row--${tone}`);
        btn.type = "button";
        btn.dataset.service = row.name;
        btn.title = `Click to show ${row.name}'s copies on the board`;
        const meter = el("span", "conc-meter");
        const fill = el("span", "conc-meter__fill");
        fill.style.width = `${pct ?? 0}%`;
        meter.appendChild(fill);
        if (even != null) {
            const mark = el("span", "conc-meter__even");
            mark.style.left = `${even}%`;
            mark.title = `Evenly spread over ${workers} nodes = ${even}%`;
            meter.appendChild(mark);
        }
        // When several nodes tie for the most copies, no node is singled out.
        const tied = busiest ? Object.entries(row.cells).filter(([n, c]) => n !== PENDING && c.ready === busiest.ready).length : 0;
        let detail = "no ready copy";
        if (busiest && tied > 1) detail = `${busiest.ready} ${busiest.ready === 1 ? "copy" : "copies"} on each of ${tied} nodes`;
        else if (busiest) detail = `${busiest.ready} of ${row.ready} ${row.ready === 1 ? "copy" : "copies"} on ${shortNode(busiest.node)}`;
        btn.append(
            el("span", "risk-row__name", row.name),
            meter,
            el("span", "risk-row__value", pct == null ? "n/a" : `${pct}%`),
            el("span", "risk-row__detail", detail),
        );
        // Showing where a service's copies are is one view and crashing a node another: choosing a
        // service brings any crashed node back, so the board never shows both at once.
        btn.addEventListener("click", () => {
            stopPlay();
            board.failed = null;
            board.focus = board.focus === row.name ? null : row.name;
            paint();
            showOnBoard();
        });
        return { btn, pct };
    }

    function exposureRow(node) {
        const takesDown = board.rows.filter((r) => r.exposedOn.includes(node)).map((r) => r.name);
        const btn = el("button", `risk-row risk-row--${takesDown.length ? "danger" : "ok"}`);
        btn.type = "button";
        btn.dataset.node = node;
        btn.title = `Click to crash ${shortNode(node)} on the board`;
        const chips = el("span", "expo-chips");
        if (takesDown.length) takesDown.forEach((name) => chips.appendChild(el("span", "expo-chip", name)));
        else chips.appendChild(el("span", "expo-none", "takes nothing down"));
        btn.append(
            el("span", "risk-row__name", `🖥️ ${shortNode(node)}`),
            chips,
            el("span", "risk-row__value", takesDown.length ? `💥 ${takesDown.length}` : "✅ 0"),
        );
        btn.addEventListener("click", () => {
            stopPlay();
            board.focus = null;  // crashing a node clears any highlighted service
            crash(board.failed === node ? null : node);
            showOnBoard();
        });
        return btn;
    }

    function renderRiskPanels() {
        const concBox = document.querySelector("[data-role='conc-rows']");
        const expoBox = document.querySelector("[data-role='expo-rows']");
        if (!concBox || !expoBox) return;
        const workers = board.columns.filter((c) => c !== PENDING);

        const conc = board.rows.map((row) => ({ row, ...concentrationRow(row, workers.length) }))
            .sort((a, b) => (b.pct ?? -1) - (a.pct ?? -1) || a.row.name.localeCompare(b.row.name));
        concBox.replaceChildren(...conc.map((c) => c.btn));

        expoBox.replaceChildren(...workers.map(exposureRow));
        const evaluated = board.rows.filter((r) => r.ready > 0);
        const exposed = evaluated.filter((r) => r.exposedOn.length);
        const summary = document.querySelector("[data-role='expo-summary']");
        if (summary) {
            summary.textContent = evaluated.length
                ? `${exposed.length} of ${evaluated.length} services depend on a single node (exposure rate ${Math.round(exposed.length / evaluated.length * 100)}%).`
                : "No service has a ready copy.";
            summary.dataset.tone = exposed.length ? "danger" : "ok";
        }
    }

    function crash(column) {
        board.failed = column;
        paint();
    }

    function stopPlay() {
        if (board.timer) clearInterval(board.timer);
        board.timer = null;
        const play = document.querySelector("[data-role='whatif-play']");
        if (play) play.textContent = "▶ Crash each node in turn";
    }

    // Crash every worker node in turn, then bring them all back.
    function play() {
        if (board.timer) {
            stopPlay();
            crash(null);
            return;
        }
        const nodes = board.columns.filter((c) => c !== PENDING);
        if (!nodes.length) return;
        let step = 0;
        crash(nodes[0]);
        document.querySelector("[data-role='whatif-play']").textContent = "■ Stop";
        board.timer = setInterval(() => {
            step++;
            if (step >= nodes.length) {
                stopPlay();
                crash(null);
                return;
            }
            crash(nodes[step]);
        }, STEP_MS);
    }

    function renderWhatIf(deployment) {
        const root = document.querySelector("[data-role='whatif']");
        if (!root) return;
        stopPlay();
        const { columns, rows } = spreadMatrix(deployment);
        Object.assign(board, { deployment, rows, columns, failed: null, focus: null });
        root.querySelector("[data-role='whatif-nodes']").replaceChildren(...columns.map(nodeBox));
        root.querySelector("[data-role='whatif-services']").replaceChildren(...rows.map(serviceCard));
        renderRiskPanels();
        paint();
    }

    function initWhatIf() {
        const root = document.querySelector("[data-role='whatif']");
        if (!root) return;
        root.querySelector("[data-role='whatif-play']")?.addEventListener("click", play);
        root.querySelector("[data-role='whatif-reset']")?.addEventListener("click", () => { stopPlay(); crash(null); });
    }

    const zonePrefix = (zone) => (zone ? "Zone: " + zone + " · " : "");

    function renderNodeRisks(nodeRisks) {
        const container = document.getElementById("node-risk-rankings-list");
        if (!container) return;

        container.innerHTML = "";
        nodeRisks.forEach((n) => {
            const item = document.createElement("div");
            const riskClass = n.risk_level === "CRITICAL"
                ? "node-risk-item--critical"
                : "node-risk-item--balanced";

            item.className = `node-risk-item ${riskClass}`;
            item.innerHTML = `
                <div>
                    <strong>🖥️ ${n.node_name}</strong>
                    <div style="font-size: 11px; color: var(--color-text-muted); margin-top: 2px;">
                        ${zonePrefix(n.zone)}${n.pod_count || 0} pods · only node hosting ${n.exposed_service_count || 0} service(s)
                    </div>
                </div>
                <span class="status-badge ${n.risk_level === 'CRITICAL' ? 'status-badge--danger' : 'status-badge--success'}">
                    ${n.exposed_service_count > 0 ? (n.exposed_services || []).join(", ") : "No exposed services"}
                </span>
            `;
            container.appendChild(item);
        });
    }

    // The data is loaded by deployments.js; init only wires the what-if buttons.
    function init() {
        initWhatIf();
    }

    return {
        init: init,
        loadDriftSignals: loadDriftSignals,
        renderWhatIf: renderWhatIf,
        spreadMatrix: spreadMatrix,
        copiesLeft: copiesLeft,
    };
})();
