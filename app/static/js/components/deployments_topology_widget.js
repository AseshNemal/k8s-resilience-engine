/**
 * Phase 5 Physical Topology Widget:
 * Renders interactive node cards, availability zones, and pod chips.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsTopologyWidget = (function () {
    "use strict";

    function renderZoneGrid(data) {
        const grid = document.getElementById("deployment-zone-grid");
        if (!grid || !data?.nodes) return;

        // Group nodes by zone
        const zoneMap = {};
        data.nodes.forEach((node) => {
            const z = node.zone || "unassigned-zone";
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
                    });
                });
            });
        }

        grid.innerHTML = "";
        const zoneNames = Object.keys(zoneMap).sort((a, b) => a.localeCompare(b));
        const zoneSelect = document.getElementById("filter-topology-zone");
        if (zoneSelect && zoneSelect.options.length <= 1) {
            zoneNames.forEach((z) => {
                const opt = document.createElement("option");
                opt.value = z;
                opt.textContent = z;
                zoneSelect.appendChild(opt);
            });
        }

        zoneNames.forEach((zoneName) => {
            const nodes = zoneMap[zoneName];
            const zoneCard = document.createElement("div");
            zoneCard.className = "zone-card";
            zoneCard.dataset.zone = zoneName;

            let nodesHtml = "";
            nodes.forEach((n) => {
                const pods = nodePods[n.name] || [];
                let podsHtml = "";
                if (pods.length === 0) {
                    podsHtml = '<span class="node-card__empty">No active pods hosted</span>';
                } else {
                    podsHtml = pods
                        .map(
                            (p) =>
                                `<span class="pod-pill" title="Pod: ${p.name}&#10;Service: ${p.service}">
                                    ${p.service}
                                </span>`
                        )
                        .join("");
                }

                nodesHtml += `
                    <div class="node-chip-container">
                        <div class="node-chip__header">
                            <span class="node-name">🖥️ ${n.name}</span>
                            <span class="node-capacity">${n.cpu_available == null ? "—" : `${n.cpu_available} vCPU`} · ${n.memory_available == null ? "—" : `${n.memory_available} GiB`}</span>
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
            const isDanger = conc > 0.5;

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
