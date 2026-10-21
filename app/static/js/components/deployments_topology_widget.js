/**
 * Phase 5 Physical Topology Widget:
 * Renders interactive node cards, availability zones, and pod chips.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsTopologyWidget = (function () {
    "use strict";

    let lastData = null;
    let failedNode = null;

    // Same rule as placement_metrics.exposure_status: a service is down after node n fails when it
    // had ready replicas and every one of them was on n (SA(s,{n}) = 0).
    function simulateFailure(node) {
        const down = [];
        const degraded = [];
        let unaffected = 0;
        (lastData.services || []).forEach((svc) => {
            const readyPods = (svc.pods || []).filter((p) => p.ready);
            if (readyPods.length === 0) return;
            const onNode = readyPods.filter((p) => p.node === node).length;
            if (onNode === readyPods.length) down.push(svc.name);
            else if (onNode > 0) degraded.push(`${svc.name} (${readyPods.length - onNode}/${readyPods.length} left)`);
            else unaffected++;
        });
        return { down, degraded, unaffected };
    }

    function applyFailure(node) {
        failedNode = failedNode === node ? null : node;
        const grid = document.getElementById("deployment-zone-grid");
        const result = document.querySelector("[data-role='failure-sim-result']");
        grid.querySelectorAll(".node-chip-container").forEach((c) => c.classList.toggle("is-failed", c.dataset.node === failedNode));
        grid.querySelectorAll(".pod-pill").forEach((p) => p.classList.remove("is-down", "is-lost"));
        if (!failedNode) {
            if (result) {
                result.className = "failure-sim";
                result.textContent = "👆 Click a node below to see what happens if it fails.";
            }
            return;
        }
        const sim = simulateFailure(failedNode);
        grid.querySelectorAll(".pod-pill").forEach((p) => {
            if (sim.down.includes(p.dataset.service)) p.classList.add("is-down");
            else if (p.dataset.node === failedNode) p.classList.add("is-lost");
        });
        if (result) {
            result.className = `failure-sim ${sim.down.length ? "failure-sim--danger" : "failure-sim--ok"}`;
            result.innerHTML = "";
            const title = document.createElement("strong");
            title.textContent = `If ${failedNode} fails: `;
            result.appendChild(title);
            result.appendChild(document.createTextNode(sim.down.length
                ? `${sim.down.length} service(s) go down (0 ready replicas): ${sim.down.join(", ")}.`
                : "no service goes down."));
            if (sim.degraded.length) {
                result.appendChild(document.createElement("br"));
                result.appendChild(document.createTextNode(`Still running with fewer replicas: ${sim.degraded.join(", ")}.`));
            }
            result.appendChild(document.createElement("br"));
            result.appendChild(document.createTextNode(`${sim.unaffected} service(s) have no ready replica on this node. Click the node again to undo.`));
        }
    }

    function renderZoneGrid(data) {
        const grid = document.getElementById("deployment-zone-grid");
        if (!grid || !data?.nodes) return;
        lastData = data;
        failedNode = null;

        // Group nodes by zone. Without a zone label the failure domain is the node itself.
        const NO_ZONE = "No zone label (failure domain = node)";
        const zoneMap = {};
        data.nodes.forEach((node) => {
            const z = node.zone || NO_ZONE;
            if (!zoneMap[z]) zoneMap[z] = [];
            zoneMap[z].push(node);
        });

        // Map pods to nodes
        const nodePods = {};
        if (data.services) {
            data.services.forEach((svc) => {
                (svc.pods || []).forEach((pod) => {
                    const n = pod.node || "unassigned";
                    if (!nodePods[n]) nodePods[n] = [];
                    nodePods[n].push({
                        name: pod.name,
                        service: svc.name,
                        is_ready: pod.ready,
                        node: pod.node,
                    });
                });
            });
        }

        grid.innerHTML = "";
        const zoneNames = Object.keys(zoneMap).sort((a, b) => a.localeCompare(b));
        const zoneSelect = document.getElementById("filter-topology-zone");
        if (zoneSelect) {
            // Rebuilt on every render so switching sample <-> live does not keep stale zones.
            zoneSelect.length = 1;
            zoneSelect.value = "all";
            zoneNames.forEach((z) => {
                const opt = document.createElement("option");
                opt.value = z;
                opt.textContent = z;
                zoneSelect.appendChild(opt);
            });
        }

        const singleZone = zoneNames.length === 1;
        zoneNames.forEach((zoneName) => {
            const nodes = zoneMap[zoneName];
            const zoneCard = document.createElement("div");
            zoneCard.className = singleZone ? "zone-card zone-card--wide" : "zone-card";
            zoneCard.dataset.zone = zoneName;

            let nodesHtml = "";
            nodes.forEach((n) => {
                const pods = nodePods[n.name] || [];
                let podsHtml = "";
                if (pods.length === 0) {
                    podsHtml = '<span class="node-card__empty">No pods of the analysed services</span>';
                } else {
                    podsHtml = pods.map(podPill).join("");
                }

                // The API serialises camelCase (cpuAvailable); values are free capacity after pod requests.
                const cpuRaw = n.cpuAvailable ?? n.cpu_available;
                const memRaw = n.memoryAvailable ?? n.memory_available;
                const cpuText = cpuRaw == null ? "—" : Number(cpuRaw.toFixed(2)) + " vCPU";
                const memText = memRaw == null ? "—" : memRaw.toFixed(1) + " GiB";
                let nodeState = "";
                if (n.ready === false) nodeState += '<span class="status-badge status-badge--danger">NotReady</span>';
                if (n.schedulable === false) nodeState += '<span class="status-badge status-badge--warning">Cordoned</span>';

                nodesHtml += `
                    <div class="node-chip-container is-clickable" data-node="${n.name}" role="button" tabindex="0" title="Click to simulate the failure of ${n.name}">
                        <div class="node-chip__header">
                            <span class="node-name">🖥️ ${n.name} ${nodeState}</span>
                            <span class="node-capacity" title="Allocatable minus requests of scheduled pods">free: ${cpuText} · ${memText}</span>
                        </div>
                        <div class="pod-badge-list">
                            ${podsHtml}
                        </div>
                    </div>
                `;
            });

            zoneCard.innerHTML = `
                <div class="zone-card__header">
                    <span class="zone-card__title">📍 ${zoneName}</span>
                    <span class="status-badge">${nodes.length} Nodes</span>
                </div>
                <div class="zone-card__body">
                    ${nodesHtml}
                </div>
            `;
            grid.appendChild(zoneCard);
        });

        // Pods not on a listed worker (e.g. Pending with no node) would otherwise disappear from the map.
        const known = new Set(data.nodes.map((n) => n.name));
        const orphans = Object.keys(nodePods).filter((n) => !known.has(n)).flatMap((n) => nodePods[n].map((p) => ({ ...p, node: n })));
        if (orphans.length) {
            const card = document.createElement("div");
            card.className = "zone-card";
            card.dataset.zone = "all";
            card.innerHTML = `
                <div class="zone-card__header">
                    <span class="zone-card__title">⏳ Not on a worker node</span>
                    <span class="status-badge">${orphans.length} Pods</span>
                </div>
                <div class="zone-card__body"><div class="pod-badge-list">${orphans.map(podPill).join("")}</div></div>
            `;
            grid.appendChild(card);
        }

        grid.querySelectorAll(".node-chip-container[data-node]").forEach((c) => {
            c.addEventListener("click", () => applyFailure(c.dataset.node));
            c.addEventListener("keydown", (e) => {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    applyFailure(c.dataset.node);
                }
            });
        });
        applyFailure(null);
    }

    function podPill(p) {
        const notReady = p.is_ready === false;
        const style = notReady ? ' style="opacity: 0.55; border-style: dashed;"' : "";
        const nodeLine = p.node ? "&#10;Node: " + p.node : "";
        const title = `Pod: ${p.name}&#10;Service: ${p.service}&#10;${notReady ? "Not ready" : "Ready"}${nodeLine}`;
        return `<span class="pod-pill" data-service="${p.service}" data-node="${p.node || ""}"${style} title="${title}">
                    ${p.service}${notReady ? " (not ready)" : ""}
                </span>`;
    }

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

    function init() {
        const zoneSelect = document.getElementById("filter-topology-zone");
        if (zoneSelect) {
            zoneSelect.addEventListener("change", function () {
                const selected = this.value;
                document.querySelectorAll(".zone-card").forEach((card) => {
                    if (selected === "all" || card.dataset.zone === selected) {
                        card.style.display = "";
                    } else {
                        card.style.display = "none";
                    }
                });
            });
        }
    }

    return {
        init: init,
        renderZoneGrid: renderZoneGrid,
        renderConcentrationPanel: renderConcentrationPanel,
    };
})();
