/**
 * Phase 5 Physical Topology Widget:
 * Draws the Service → Pod → Node chain as a picture: services on the left, worker nodes on the right,
 * one wire from each service to each of its pods. Hover a service to trace it; click a node to fail it.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsTopologyWidget = (function () {
    "use strict";

    const SVG_NS = "http://www.w3.org/2000/svg";
    const PENDING = "__pending__";

    let lastData = null;
    let failedNode = null;
    let resizeObserver = null;

    // One fixed colour per service (by sorted name), so a service, its pods and its wires share a colour.
    const SERVICE_HUES = [212, 152, 32, 280, 350, 190, 95, 255, 15, 320];
    let serviceHue = {};
    const shortNode = (node) => node.replace(/^.*-(worker\d*|control-plane)$/, "$1");

    function assignServiceColours(services) {
        serviceHue = {};
        [...new Set(services.map((s) => s.name))].sort((a, b) => a.localeCompare(b)).forEach((name, i) => {
            serviceHue[name] = SERVICE_HUES[i % SERVICE_HUES.length];
        });
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    const readyPods = (svc) => (svc.pods || []).filter((p) => p.ready);

    // Same rule as placement_metrics.exposure_status: a service is down after node n fails when it
    // had ready replicas and every one of them was on n (SA(s,{n}) = 0).
    function simulateFailure(services, node) {
        const down = [];
        const degraded = [];
        let unaffected = 0;
        (services || []).forEach((svc) => {
            const ready = readyPods(svc);
            if (ready.length === 0) return;
            const onNode = ready.filter((p) => p.node === node).length;
            if (onNode === ready.length) down.push(svc.name);
            else if (onNode > 0) degraded.push({ name: svc.name, left: ready.length - onNode, of: ready.length });
            else unaffected++;
        });
        return { down, degraded, unaffected };
    }

    // ---------------------------------------------------------------------------------------------
    // The picture
    // ---------------------------------------------------------------------------------------------

    function serviceRow(svc) {
        const ready = readyPods(svc).length;
        const total = (svc.pods || []).length;
        const row = el("div", "topo-svc");
        row.dataset.service = svc.name;
        row.style.setProperty("--svc-hue", serviceHue[svc.name]);
        row.tabIndex = 0;
        row.title = `${svc.name}: ${ready} of ${total} pods ready. Hover to trace its pods.`;
        row.append(
            el("span", "topo-svc__swatch"),
            el("span", "topo-svc__name", svc.name),
            el("span", "topo-svc__count", `${ready}/${total}`),
            el("span", "topo-svc__state"),
        );
        return row;
    }

    function podDot(pod, svcName, nodeName) {
        const dot = el("span", pod.ready ? "topo-pod" : "topo-pod topo-pod--notready");
        dot.dataset.service = svcName;
        dot.dataset.node = nodeName;
        dot.style.setProperty("--svc-hue", serviceHue[svcName]);
        const lines = [`Pod: ${pod.name}`, `Service: ${svcName}`, pod.ready ? "Ready" : "Not ready"];
        if (pod.node) lines.push(`Node: ${pod.node}`);
        dot.title = lines.join("\n");
        dot.textContent = svcName;
        return dot;
    }

    function nodeCard(node, pods) {
        const pending = node.name === PENDING;
        const card = el("div", pending ? "topo-node topo-node--pending" : "topo-node node-chip-container");
        card.dataset.node = node.name;
        card.dataset.zone = node.zone || "No zone label (failure domain = node)";
        if (!pending) {
            card.tabIndex = 0;
            card.setAttribute("role", "button");
            card.title = `${node.name}: click to simulate its failure`;
        }
        const head = el("div", "topo-node__head");
        head.append(el("span", "topo-node__icon", pending ? "⏳" : "🖥️"), el("strong", "topo-node__name", pending ? "Pending (no node)" : shortNode(node.name)));
        if (node.ready === false) head.appendChild(el("span", "status-badge status-badge--danger", "NotReady"));
        if (node.schedulable === false) head.appendChild(el("span", "status-badge status-badge--warning", "Cordoned"));
        head.appendChild(el("span", "topo-node__failed", "✕ FAILED"));
        card.appendChild(head);

        if (!pending) {
            const cpu = node.cpuAvailable ?? node.cpu_available;
            const mem = node.memoryAvailable ?? node.memory_available;
            const meta = el("div", "topo-node__meta",
                `${node.zone ? node.zone + " · " : ""}free ${cpu == null ? "—" : Number(cpu.toFixed(2)) + " vCPU"} · ${mem == null ? "—" : mem.toFixed(1) + " GiB"}`);
            meta.title = "Allocatable minus requests of scheduled pods";
            card.appendChild(meta);
        }
        const tray = el("div", "topo-node__pods");
        if (!pods.length) tray.appendChild(el("span", "topo-node__empty", "No pods here"));
        pods.forEach((p) => tray.appendChild(podDot(p.pod, p.service, node.name)));
        card.appendChild(tray);
        return card;
    }

    function renderZoneGrid(data) {
        const grid = document.getElementById("deployment-zone-grid");
        if (!grid || !data?.nodes) return;
        lastData = data;
        failedNode = null;
        const services = [...(data.services || [])].sort((a, b) => a.name.localeCompare(b.name));
        assignServiceColours(services);

        // Pods per node; a pod on no listed node (e.g. Pending) goes to a Pending box so it never disappears.
        const known = new Set(data.nodes.map((n) => n.name));
        const byNode = {};
        services.forEach((svc) => (svc.pods || []).forEach((pod) => {
            const key = pod.node && known.has(pod.node) ? pod.node : PENDING;
            (byNode[key] = byNode[key] || []).push({ pod, service: svc.name });
        }));
        const nodes = [...data.nodes].sort((a, b) => a.name.localeCompare(b.name));
        if (byNode[PENDING]) nodes.push({ name: PENDING });

        const left = el("div", "topo__services");
        left.appendChild(el("div", "topo__label", "Services"));
        services.forEach((svc) => left.appendChild(serviceRow(svc)));
        const right = el("div", "topo__nodes");
        right.appendChild(el("div", "topo__label", "Worker nodes · click one to fail it"));
        nodes.forEach((n) => right.appendChild(nodeCard(n, byNode[n.name] || [])));
        const wires = document.createElementNS(SVG_NS, "svg");
        wires.setAttribute("class", "topo__wires");
        wires.setAttribute("aria-hidden", "true");

        grid.className = "topo";
        grid.replaceChildren(wires, left, right);
        fillZoneFilter(nodes);
        bindInteractions(grid);
        applyFailure(null);
        drawWires();
        // Redraw when the picture changes size, including when its tab is first shown (size 0 → real).
        resizeObserver?.disconnect();
        if (window.ResizeObserver) {
            resizeObserver = new ResizeObserver(() => drawWires());
            resizeObserver.observe(grid);
        }
    }

    // One curved wire from a service's row to each of its pods.
    function drawWires() {
        const grid = document.getElementById("deployment-zone-grid");
        const svg = grid?.querySelector(".topo__wires");
        if (!svg) return;
        const box = grid.getBoundingClientRect();
        svg.setAttribute("viewBox", `0 0 ${Math.max(box.width, 1)} ${Math.max(box.height, 1)}`);
        const paths = [];
        grid.querySelectorAll(".topo-pod").forEach((pod) => {
            if (!pod.offsetParent) return;  // filtered out by zone
            const row = grid.querySelector(`.topo-svc[data-service="${CSS.escape(pod.dataset.service)}"]`);
            if (!row) return;
            const a = row.getBoundingClientRect();
            const b = pod.getBoundingClientRect();
            const x1 = a.right - box.left;
            const y1 = a.top + a.height / 2 - box.top;
            const x2 = b.left + 6 - box.left;  // just inside the pod's left edge
            const y2 = b.top + b.height / 2 - box.top;
            const bend = Math.max(40, (x2 - x1) * 0.5);
            const path = document.createElementNS(SVG_NS, "path");
            path.setAttribute("d", `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`);
            path.setAttribute("class", "topo-wire");
            path.setAttribute("fill", "none");
            path.dataset.service = pod.dataset.service;
            path.dataset.node = pod.dataset.node;
            path.style.setProperty("--svc-hue", serviceHue[pod.dataset.service]);
            if (pod.classList.contains("topo-pod--notready")) path.classList.add("topo-wire--notready");
            paths.push(path);
        });
        svg.replaceChildren(...paths);
        paintFailure();
        paintFocus();
    }

    // ---------------------------------------------------------------------------------------------
    // Interactions: hover a service to trace it, click a node to fail it
    // ---------------------------------------------------------------------------------------------

    let focusedService = null;

    function setFocus(service) {
        focusedService = service;
        paintFocus();
    }

    function paintFocus() {
        const grid = document.getElementById("deployment-zone-grid");
        if (!grid) return;
        grid.classList.toggle("has-focus", Boolean(focusedService));
        grid.querySelectorAll("[data-service]").forEach((n) => {
            n.classList.toggle("is-focus", n.dataset.service === focusedService);
        });
    }

    function bindInteractions(grid) {
        grid.querySelectorAll(".topo-svc").forEach((row) => {
            row.addEventListener("mouseenter", () => setFocus(row.dataset.service));
            row.addEventListener("focus", () => setFocus(row.dataset.service));
            row.addEventListener("mouseleave", () => setFocus(null));
            row.addEventListener("blur", () => setFocus(null));
        });
        grid.querySelectorAll(".topo-pod").forEach((pod) => {
            pod.addEventListener("mouseenter", () => setFocus(pod.dataset.service));
            pod.addEventListener("mouseleave", () => setFocus(null));
        });
        grid.querySelectorAll(".node-chip-container[data-node]").forEach((card) => {
            card.addEventListener("click", () => applyFailure(card.dataset.node));
            card.addEventListener("keydown", (e) => {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    applyFailure(card.dataset.node);
                }
            });
        });
    }

    function applyFailure(node) {
        failedNode = node == null || failedNode === node ? null : node;
        paintFailure();
        describeFailure();
    }

    function paintFailure() {
        const grid = document.getElementById("deployment-zone-grid");
        if (!grid || !lastData) return;
        const sim = failedNode ? simulateFailure(lastData.services, failedNode) : { down: [], degraded: [] };
        grid.classList.toggle("has-failure", Boolean(failedNode));
        grid.querySelectorAll(".topo-node").forEach((c) => c.classList.toggle("is-failed", c.dataset.node === failedNode));
        grid.querySelectorAll(".topo-pod, .topo-wire").forEach((n) => n.classList.toggle("is-lost", n.dataset.node === failedNode));
        grid.querySelectorAll(".topo-svc").forEach((row) => {
            const name = row.dataset.service;
            const degraded = sim.degraded.find((d) => d.name === name);
            const isDown = sim.down.includes(name);
            row.classList.toggle("is-down", isDown);
            row.classList.toggle("is-degraded", Boolean(degraded));
            let state = "";
            if (isDown) state = "DOWN";
            else if (degraded) state = `${degraded.left}/${degraded.of} left`;
            row.querySelector(".topo-svc__state").textContent = state;
        });
    }

    function describeFailure() {
        const result = document.querySelector("[data-role='failure-sim-result']");
        if (!result) return;
        if (!failedNode) {
            result.className = "failure-sim";
            result.textContent = "👆 Hover a service to trace its pods. Click a worker node to see what happens if it fails.";
            return;
        }
        const sim = simulateFailure(lastData.services, failedNode);
        result.className = `failure-sim ${sim.down.length ? "failure-sim--danger" : "failure-sim--ok"}`;
        const title = el("strong", null, `If ${shortNode(failedNode)} fails: `);
        const text = sim.down.length
            ? `${sim.down.length} service(s) go down (0 ready replicas): ${sim.down.join(", ")}.`
            : "no service goes down.";
        const fewer = sim.degraded.map((d) => d.name + " (" + d.left + "/" + d.of + ")").join(", ");
        const extra = sim.degraded.length ? ` Still running with fewer replicas: ${fewer}.` : "";
        result.replaceChildren(title, document.createTextNode(text + extra + " Click the node again to undo."));
    }

    // ---------------------------------------------------------------------------------------------
    // Zone filter
    // ---------------------------------------------------------------------------------------------

    function fillZoneFilter(nodes) {
        const zoneSelect = document.getElementById("filter-topology-zone");
        if (!zoneSelect) return;
        const zones = [...new Set(nodes.filter((n) => n.name !== PENDING).map((n) => n.zone || "No zone label (failure domain = node)"))].sort((a, b) => a.localeCompare(b));
        zoneSelect.length = 1;
        zoneSelect.value = "all";
        zones.forEach((z) => zoneSelect.appendChild(new Option(z, z)));
        zoneSelect.closest(".tab-pane__controls")?.toggleAttribute("hidden", zones.length < 2);
    }

    function init() {
        const zoneSelect = document.getElementById("filter-topology-zone");
        if (zoneSelect) {
            zoneSelect.addEventListener("change", function () {
                const selected = this.value;
                document.querySelectorAll("#deployment-zone-grid .topo-node").forEach((card) => {
                    card.hidden = !(selected === "all" || card.dataset.zone === selected || card.dataset.node === PENDING);
                });
                drawWires();
            });
        }
        window.addEventListener("resize", () => drawWires());
    }

    // ---------------------------------------------------------------------------------------------
    // Replica concentration (Show details)
    // ---------------------------------------------------------------------------------------------

    function renderConcentrationPanel(services) {
        const panel = document.getElementById("deployment-concentration-panel");
        if (!panel || !services) return;

        panel.innerHTML = "";
        const sorted = [...services].sort((a, b) => {
            const concA = a.replicaConcentration ?? a.replica_concentration ?? 0.0;
            const concB = b.replicaConcentration ?? b.replica_concentration ?? 0.0;
            return concB - concA;
        });

        sorted.forEach((svc) => {
            const conc = svc.replicaConcentration ?? svc.replica_concentration ?? 0.0;
            const readyRep = svc.readyReplicas ?? svc.ready_replicas ?? (svc.pods ? svc.pods.filter((p) => p.ready).length : 0);
            const declRep = svc.declaredReplicas ?? svc.declared_replicas ?? readyRep;
            const pct = Math.round(conc * 100);
            // Concentrated (TAF p.7) = all ready replicas on one node; the ratio alone is not the rule.
            const isDanger = Boolean(svc.concentrated) || (svc.exposedFailureDomains ?? svc.exposed_failure_domains ?? []).length > 0;

            const item = document.createElement("div");
            item.className = "concentration-row";
            item.innerHTML = `
                <div class="concentration-row__header">
                    <strong class="concentration-row__name">${svc.name}</strong>
                    <span class="concentration-row__stat" style="color: ${isDanger ? 'var(--c2-accent-danger)' : 'var(--c2-accent-success)'}; font-weight: 700;">
                        ${pct}% (${readyRep}/${declRep} replicas)
                    </span>
                </div>
                <div class="concentration-progress-bar">
                    <div class="concentration-progress-fill" style="width: ${pct}%; background: ${isDanger ? 'var(--c2-accent-danger)' : 'var(--c2-accent-primary)'};"></div>
                </div>
            `;
            panel.appendChild(item);
        });
    }

    return {
        init: init,
        renderZoneGrid: renderZoneGrid,
        renderConcentrationPanel: renderConcentrationPanel,
        simulateFailure: simulateFailure,
        // The colour a service was given on the topology picture, so the other tabs use the same one.
        hueOf: (name) => serviceHue[name],
    };
})();
