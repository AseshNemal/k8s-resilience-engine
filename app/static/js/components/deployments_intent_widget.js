/**
 * Phase 5 Declared Intent vs Live Compliance Widget:
 * Renders the side-by-side specification comparison table.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsIntentWidget = (function () {
    "use strict";

    // Every request carries the namespace chosen in the page header (deployments_namespace.js).
    const scoped = (url) => (window.DeploymentsNamespace ? window.DeploymentsNamespace.withNamespace(url) : url);

    // Same order as service._declared_target: say where the declared intent comes from.
    async function showManifestSource() {
        const note = document.querySelector("[data-role='manifest-source']");
        if (!note) return;
        try {
            const resp = await fetch("/api/deployments/manifests/source");
            if (!resp.ok) return;
            const src = await resp.json();
            note.classList.toggle("manifest-source--warning", !src.found);
            if (src.source === "cluster") {
                note.textContent = "Declared intent from the connected cluster (no manifest file found): the YAML last applied with " +
                    "kubectl (kubectl.kubernetes.io/last-applied-configuration), or the Deployment spec when none was kept.";
            } else if (src.found) {
                note.textContent = `Declared intent from ${src.path}.`;
            } else {
                note.textContent = `The named manifest ${src.path} does not exist, so there is no declared intent to compare.`;
            }
            const short = document.querySelector("[data-role='plan-source']");
            if (short) {
                short.textContent = src.source === "cluster"
                    ? "📄 The YAML here is the one last applied with kubectl, kept by the cluster; a kubectl scale does not change it, so hand changes show as drift."
                    : `📄 The YAML here is ${src.path}.`;
            }
        } catch (err) {
            console.warn("Could not load manifest source:", err);
        }
    }

    // activeServices are the connected cluster's services; the declared intent is compared with them.
    async function loadIntentCompliance(activeServices) {
        await showManifestSource();
        try {
            const resp = await fetch(scoped("/api/deployments/manifests"));
            if (!resp.ok) return;
            const intents = await resp.json();
            renderIntentTable(intents, activeServices);
        } catch (err) {
            console.warn("Could not load manifest intent:", err);
        }
        await loadPlacementCompliance();
    }

    // Placement-rule compliance comes from the backend definition (placement_metrics.compliance_rate).
    async function loadPlacementCompliance() {
        const rateEl = document.getElementById("compliance-rate-val");
        if (!rateEl) return;
        rateEl.textContent = "n/a";
        try {
            const resp = await fetch(scoped("/api/deployments/manifests/compliance"));
            if (!resp.ok) return;
            const data = await resp.json();
            rateEl.textContent = data.constraint_compliance_rate == null
                ? "n/a (no service declares a rule)"
                : `${(data.constraint_compliance_rate * 100).toFixed(1)}% of ${data.services_with_placement_rules}`;
        } catch (err) {
            console.warn("Could not load placement-rule compliance:", err);
        }
    }

    // Live ready replicas from camelCase, snake_case or the pod list; null when the service was not observed.
    function liveReadyReplicas(live) {
        if (live.readyReplicas !== undefined) return live.readyReplicas;
        if (live.ready_replicas !== undefined) return live.ready_replicas;
        if (Array.isArray(live.pods)) return live.pods.filter((p) => p.ready).length;
        return null;
    }

    function limitsText(item) {
        if (!item.resource_limits) return "None";
        return `${item.resource_limits.cpu || "N/A"} / ${item.resource_limits.memory || "N/A"}`;
    }

    function replicaStatusHtml(comparable, replicaMismatch, declaredRep, readyRep) {
        if (replicaMismatch) {
            return `<span class="status-pill status-pill--warning" title="Declared ${declaredRep} vs live ${readyRep} ready">⚠️ Replica drift</span>`;
        }
        if (comparable) return '<span class="status-pill status-pill--success">✓ Match</span>';
        return '<span class="status-pill">Not observed</span>';
    }

    // Keyed by namespace + name, like the backend, so a YAML in another namespace that reuses a
    // service name (e.g. deploy/test-workloads in resilience-test) is never compared with it.
    const key = (namespace, name) => `${namespace || "default"}/${name}`;

    // A declared Deployment is matched to the live Service that the backend resolved it to through owner
    // references (deploymentNames), not only by name: Bookinfo's Deployment reviews-v1 backs Service reviews.
    function buildLiveMap(liveServices) {
        const liveMap = {};
        (liveServices || []).forEach((s) => {
            liveMap[key(s.namespace, s.name)] = s;
            (s.deploymentNames ?? s.deployment_names ?? []).forEach((deployment) => {
                liveMap[key(s.namespace, deployment)] = s;
            });
        });
        return liveMap;
    }

    function renderIntentTable(intents, liveServices = []) {
        const tbody = document.getElementById("intent-table-body");
        if (!tbody) return;

        tbody.innerHTML = "";
        const liveMap = buildLiveMap(liveServices);

        let compliantCount = 0;
        let comparedCount = 0;

        intents.forEach((item) => {
            const svcName = item.service_name || "unknown";
            const declaredRep = item.expected_replicas ?? item.declared_replicas ?? null;
            const readyRep = liveReadyReplicas(liveMap[key(item.namespace, svcName)] || {});
            const limits = limitsText(item);
            const hasAffinity = item.has_node_affinity ? "✓ Required" : "None";
            const hasSpread = item.spread_constraints?.length > 0 ? "✓ Configured" : "None";

            // Only compared when both sides are known; otherwise shown as not observed.
            const comparable = declaredRep != null && readyRep != null;
            const replicaMismatch = comparable && readyRep !== declaredRep;
            if (comparable) {
                comparedCount++;
                if (!replicaMismatch) compliantCount++;
            }
            const statusHtml = replicaStatusHtml(comparable, replicaMismatch, declaredRep, readyRep);

            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${svcName}</strong></td>
                <td>${declaredRep ?? "n/a"}</td>
                <td><strong style="color: ${replicaMismatch ? 'var(--color-danger, #ef4444)' : 'inherit'};">${readyRep ?? "n/a"}</strong></td>
                <td>${statusHtml}</td>
                <td class="intent-col--extra"><code>${limits}</code></td>
                <td class="intent-col--extra">${hasAffinity}</td>
                <td class="intent-col--extra">${hasSpread}</td>
            `;
            tbody.appendChild(tr);
        });

        const matchEl = document.getElementById("replica-match-val");
        if (matchEl) {
            matchEl.textContent = comparedCount > 0 ? `${compliantCount} / ${comparedCount}` : "n/a";
        }
        renderPlanCards(intents, liveMap);
    }

    // ---------------------------------------------------------------------------------------------
    // The picture: per service, the copies the YAML asks for (booked seats) and the pods really
    // running (filled seats). Same matching as the table: declared vs live READY replicas.
    // ---------------------------------------------------------------------------------------------

    const shortNode = (node) => (node || "no node").replace(/^.*-(worker\d*|control-plane)$/, "$1");

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    // Declared copies against ready copies, in words: the scoreboard's verdict.
    function compareCounts(declared, ready) {
        if (declared == null || ready == null) return { tone: "muted", diff: null, text: "Not running in the cluster" };
        const diff = ready - declared;
        if (diff === 0) return { tone: "ok", diff, text: "✓ Same as the YAML" };
        if (diff < 0) return { tone: "danger", diff, text: `⚠️ ${-diff} fewer than the YAML` };
        return { tone: "warn", diff, text: `➕ ${diff} more than the YAML` };
    }

    function ruleChip(label, value, tone) {
        const chip = el("span", `plan-rule plan-rule--${tone}`);
        chip.append(el("strong", null, label), el("span", null, value));
        return chip;
    }

    function scoreTile(label, value, sub, className) {
        const tile = el("span", `score-tile ${className}`);
        tile.append(el("span", "score-tile__label", label), el("span", "score-tile__value", value), el("span", "score-tile__sub", sub));
        return tile;
    }

    // One scoreboard per service: what the YAML asks for, what the cluster really runs, the difference.
    function planCard(item, live) {
        const declared = item.expected_replicas ?? item.declared_replicas ?? null;
        const pods = live && Array.isArray(live.pods) ? live.pods : [];
        const ready = live ? liveReadyReplicas(live) : null;
        const verdict = compareCounts(declared, ready);
        const notReady = pods.filter((p) => !p.ready).length;

        const card = el("article", `plan-card plan-card--${verdict.tone}`);
        const head = el("button", "plan-card__head");
        head.type = "button";
        head.setAttribute("aria-expanded", "false");
        head.title = "Click for its pods per node and its declared rules";

        const board = el("span", "score");
        board.append(
            scoreTile("📄 YAML", declared == null ? "?" : String(declared), declared === 1 ? "copy asked for" : "copies asked for", "score-tile--yaml"),
            el("span", "score__arrow", "→"),
            scoreTile("🖥️ Cluster", ready == null ? "?" : String(ready), notReady ? `ready · ${notReady} not ready` : "running and ready", "score-tile--live"),
        );
        head.append(el("span", "plan-card__name", item.service_name || "unknown"), board, el("span", "plan-card__verdict", verdict.text));

        // Opened: the pods per node, then the declared placement rules.
        const rules = el("div", "plan-card__rules");
        const byNode = {};
        pods.forEach((p) => (byNode[shortNode(p.node)] = byNode[shortNode(p.node)] || []).push(p));
        Object.keys(byNode).sort().forEach((node) => {
            const ok = byNode[node].filter((p) => p.ready).length;
            rules.appendChild(ruleChip(`🖥️ ${node}`, `${ok} ready${byNode[node].length > ok ? `, ${byNode[node].length - ok} not ready` : ""}`, "ok"));
        });
        const spread = item.spread_constraints?.length > 0;
        const anti = Boolean(item.pod_anti_affinity_configured);
        const compliant = item.is_topology_compliant;
        rules.append(
            ruleChip("Spread rule", spread ? "declared" : "none", spread ? "ok" : "muted"),
            ruleChip("Anti-affinity", anti ? "declared" : "none", anti ? "ok" : "muted"),
            ruleChip("Node affinity", item.has_node_affinity ? "declared" : "none", item.has_node_affinity ? "ok" : "muted"),
            ruleChip("Rules satisfied", compliant == null ? "nothing to check" : compliant ? "yes" : "no", compliant == null ? "muted" : compliant ? "ok" : "danger"),
            ruleChip("Limits (info)", limitsText(item), "muted"),
        );
        rules.hidden = true;
        head.addEventListener("click", () => {
            rules.hidden = !rules.hidden;
            card.classList.toggle("is-open", !rules.hidden);
            head.setAttribute("aria-expanded", String(!rules.hidden));
        });

        card.append(head, rules);
        return { card, tone: verdict.tone, name: item.service_name };
    }

    function renderPlanCards(intents, liveMap) {
        const box = document.querySelector("[data-role='plan-cards']");
        if (!box) return;
        const cards = intents.map((item) => planCard(item, liveMap[key(item.namespace, item.service_name || "unknown")]));
        const order = { danger: 0, warn: 1, muted: 2, ok: 3 };
        cards.sort((a, b) => order[a.tone] - order[b.tone] || String(a.name).localeCompare(String(b.name)));
        box.replaceChildren(...cards.map((c) => c.card));

        const headline = document.querySelector("[data-role='plan-headline']");
        if (!headline) return;
        const differ = cards.filter((c) => c.tone === "danger" || c.tone === "warn");
        const matched = cards.filter((c) => c.tone === "ok").length;
        if (!cards.length) headline.textContent = "No declared intent to compare.";
        else if (!differ.length) headline.textContent = `✅ ${matched} of ${cards.length} services run exactly what the YAML asks for.`;
        else headline.textContent = `⚠️ ${differ.length} of ${cards.length} services differ from the YAML.`;
        headline.dataset.tone = differ.length ? "danger" : "ok";
    }

    // The table under Show details always shows every column; nothing to wire.
    function init() {}

    return {
        init: init,
        loadIntentCompliance: loadIntentCompliance,
        compareCounts: compareCounts,
    };
})();
