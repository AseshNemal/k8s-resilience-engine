/* One editor for declared workflows and observed calls. No Kubernetes mutation requests. */
(function () {
    "use strict";
    const clone = value => JSON.parse(JSON.stringify(value));
    const TYPES = ["Mandatory", "Optional", "Supporting", "Unknown"];
    function editable(row = {}) {
        return { name: row.name || "", entryService: row.entryService || null, criticality: row.criticality ?? null,
            nodes: (row.nodes || []).map(n => ({ service: n.service, x: n.x, y: n.y })),
            edges: (row.edges || []).map(e => ({ source: e.source, target: e.target, dependency: TYPES.includes(e.dependency) ? e.dependency : "Unknown" })),
            recordingIds: [...(row.recordingIds || [])] };
    }
    function createModel(seed) {
        let state = editable(seed), clean = JSON.stringify(state), undo = [], redo = [];
        function change(action) {
            const before = clone(state);
            action(state);
            if (JSON.stringify(before) !== JSON.stringify(state)) {
                undo.push(before); undo = undo.slice(-80); redo = [];
            }
        }
        function add(s, service, x, y) {
            if (!s.nodes.some(n => n.service === service)) s.nodes.push({ service, x: Math.max(8, Math.min(1025, x)), y: Math.max(8, Math.min(670, y)) });
        }
        return {
            state: () => clone(state), dirty: () => JSON.stringify(state) !== clean,
            canUndo: () => undo.length > 0, canRedo: () => redo.length > 0,
            load(row) { state = editable(row); clean = JSON.stringify(state); undo = []; redo = []; },
            change,
            add(service, x = 40, y = 60) { change(s => add(s, service, x, y)); },
            move(service, x, y) { change(s => { const n = s.nodes.find(n => n.service === service); if (n) Object.assign(n, { x: Math.max(8, Math.min(1025, x)), y: Math.max(8, Math.min(670, y)) }); }); },
            connect(source, target) { change(s => {
                if (source && target && source !== target && [source, target].every(name => s.nodes.some(n => n.service === name)) && !s.edges.some(e => e.source === source && e.target === target)) s.edges.push({ source, target, dependency: "Unknown" });
            }); },
            remove(service) { change(s => { s.nodes = s.nodes.filter(n => n.service !== service); s.edges = s.edges.filter(e => e.source !== service && e.target !== service); if (s.entryService === service) s.entryService = null; }); },
            merge(candidate, recordingId, allowed) { change(s => {
                const names = new Set(allowed);
                for (const service of candidate.services || []) if (names.has(service)) add(s, service, 40 + (s.nodes.length % 4) * 235, 60 + Math.floor(s.nodes.length / 4) * 150);
                for (const edge of candidate.edges || []) if (names.has(edge.source) && names.has(edge.target) && !s.edges.some(e => e.source === edge.source && e.target === edge.target)) s.edges.push({ source: edge.source, target: edge.target, dependency: "Unknown" });
                if (!s.recordingIds.includes(recordingId)) s.recordingIds.push(recordingId);
                if (!s.entryService && names.has(candidate.entryService)) s.entryService = candidate.entryService;
                if (!s.name) s.name = candidate.name;
            }); },
            undo() { if (undo.length) { redo.push(clone(state)); state = undo.pop(); } },
            redo() { if (redo.length) { undo.push(clone(state)); state = redo.pop(); } },
        };
    }
    window.ResilienceWorkflowEditor = { createModel, editable, TYPES };
    const root = document.getElementById("workflow-editor");
    if (!root) return;
    const q = name => root.querySelector(`[data-we="${name}"]`);
    const BASE = "/api/workflows/management";
    const model = createModel();
    let facts = null, saved = [], sessions = [], documentRow = null, history = [], recording = null;
    let selected = null, connecting = null, busy = false, generation = 0, binding = null;
    const el = (tag, text, className) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (className) n.className = className; return n; };
    function button(text, action, className = "secondary-button") {
        const b = el("button", text, className); b.type = "button"; b.addEventListener("click", action); return b;
    }
    function say(message, error = false) { q("status").textContent = message; q("status").dataset.error = String(error); }
    function options(select, rows, value, empty = "Choose…") {
        select.replaceChildren();
        if (empty !== null) { const option = el("option", empty); option.value = ""; select.append(option); }
        for (const [key, label] of rows) { const option = el("option", label); option.value = key; select.append(option); }
        select.value = value || "";
    }
    async function request(path, method = "GET", body) {
        const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 90000);
        try {
            const response = await fetch(path.startsWith("/api/") ? path : BASE + path, {
                method, signal: abort.signal, headers: { "Content-Type": "application/json" },
                ...(body === undefined ? {} : { body: JSON.stringify(body) }),
            });
            const data = await response.json();
            if (!response.ok) {
                const detail = data.detail;
                throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map(e => e.msg).join("; ") : detail?.message || `Request failed (${response.status})`);
            }
            return data;
        } finally { clearTimeout(timer); }
    }
    function permissions() {
        const bound = facts && binding && binding === facts.connectionFingerprint && (!documentRow || documentRow.namespace === facts.namespace);
        root.querySelectorAll("input, select").forEach(control => { control.disabled = busy; });
        root.querySelectorAll(".we-services button, .we-node button, .we-inspector button, .we-candidate button").forEach(control => { control.disabled = busy || !facts; });
        for (const name of ["save", "review", "start", "connect"]) q(name).disabled = busy || !bound;
        q("stop").disabled = busy || !recording || !bound;
        q("undo").disabled = busy || !model.canUndo(); q("redo").disabled = busy || !model.canRedo();
        q("delete").disabled = busy || !documentRow;
        for (const name of ["new", "duplicate", "refresh", "namespace", "saved", "recording", "load-version", "export"]) q(name).disabled = busy;
    }
    async function run(action) {
        if (busy) return;
        busy = true; permissions();
        try { await action(); } catch (error) { say(error.name === "AbortError" ? "The request timed out. Reload the saved record before retrying; a server operation may have completed." : error.message, true); }
        finally { busy = false; permissions(); }
    }
    function mayLeave() { return !model.dirty() || window.confirm("This workflow has unsaved edits. Leave those edits and continue?"); }
    function render() {
        const state = model.state();
        q("name").value = state.name; q("criticality").value = state.criticality ?? "";
        const names = state.nodes.map(n => [n.service, n.service]);
        for (const key of ["entry", "from", "to"]) options(q(key), names, key === "entry" ? state.entryService : q(key).value);
        q("version").textContent = (documentRow ? `Version ${documentRow.version}${documentRow.reviewed ? " · reviewed declaration" : " · draft"}` : "Unsaved workflow") + (model.dirty() ? " · edited" : "");
        q("empty").hidden = state.nodes.length > 0;
        q("conflicts").replaceChildren();
        const conflicts = documentRow?.conflicts || [];
        q("conflicts").hidden = conflicts.length === 0;
        if (conflicts.length) {
            q("conflicts").append(el("strong", "Recorded calls differ from this declaration"), el("p", "Omitting an observed call does not prove it is absent from a workflow. Review these differences and save any correction as a new version."));
            for (const conflict of conflicts) q("conflicts").append(el("p", `${conflict.source} → ${conflict.target}: ${conflict.reason}`));
        }
        drawNodes(state); drawLines(state); inspect(state); permissions();
    }
    function drawNodes(state) {
        q("nodes").replaceChildren();
        for (const node of state.nodes) {
            const card = el("div", null, "we-node"); card.tabIndex = 0; card.dataset.service = node.service;
            card.setAttribute("role", "group"); card.setAttribute("aria-label", `${node.service}, use arrow keys to move`);
            card.dataset.selected = String(selected?.service === node.service);
            Object.assign(card.style, { left: `${node.x}px`, top: `${node.y}px` });
            card.append(el("strong", node.service), el("small", node.service === state.entryService ? "Entry service" : "Logical service"));
            const port = button("Connect", event => { event.stopPropagation(); connecting = node.service; say(`Choose the dependency called by ${node.service}.`); });
            port.addEventListener("pointerdown", event => {
                event.stopPropagation();
                const up = ended => { window.removeEventListener("pointerup", up); const target = document.elementFromPoint(ended.clientX, ended.clientY)?.closest(".we-node")?.dataset.service; if (target && target !== node.service) { model.connect(node.service, target); connecting = null; render(); } };
                window.addEventListener("pointerup", up, { once: true });
            });
            card.append(port);
            card.addEventListener("click", event => {
                if (busy) return;
                if (event.target.closest("button")) return;
                if (connecting && connecting !== node.service) { model.connect(connecting, node.service); connecting = null; }
                selected = { service: node.service }; render();
            });
            card.addEventListener("keydown", event => {
                if (busy || event.target !== card) return;
                const delta = { ArrowLeft: [-12, 0], ArrowRight: [12, 0], ArrowUp: [0, -12], ArrowDown: [0, 12] }[event.key];
                if (delta) { event.preventDefault(); model.move(node.service, node.x + delta[0], node.y + delta[1]); render(); q("nodes").querySelectorAll(".we-node").forEach(n => { if (n.dataset.service === node.service) n.focus(); }); }
                if (event.key === "Enter") { selected = { service: node.service }; inspect(model.state()); }
            });
            card.addEventListener("pointerdown", event => {
                if (event.target.closest("button") || event.button !== 0 || busy) return;
                const start = { x: event.clientX, y: event.clientY }; let moved = false;
                const move = current => { moved = true; card.style.left = `${Math.max(8, Math.min(1025, node.x + current.clientX - start.x))}px`; card.style.top = `${Math.max(8, Math.min(670, node.y + current.clientY - start.y))}px`; };
                const end = current => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", end); window.removeEventListener("pointercancel", cancel); if (moved) { model.move(node.service, node.x + current.clientX - start.x, node.y + current.clientY - start.y); selected = { service: node.service }; render(); } };
                const cancel = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", end); window.removeEventListener("pointercancel", cancel); render(); };
                window.addEventListener("pointermove", move); window.addEventListener("pointerup", end); window.addEventListener("pointercancel", cancel);
            });
            q("nodes").append(card);
        }
    }
    function drawLines(state) {
        const svg = q("lines"), ns = "http://www.w3.org/2000/svg";
        const shape = (tag, attrs) => { const n = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };
        svg.replaceChildren();
        const defs = shape("defs", {}), marker = shape("marker", { id: "we-arrow", markerWidth: "9", markerHeight: "9", refX: "8", refY: "4", orient: "auto", markerUnits: "userSpaceOnUse" });
        marker.append(shape("path", { d: "M0 0 L8 4 L0 8 Z", fill: "currentColor" })); defs.append(marker); svg.append(defs);
        for (const [index, edge] of state.edges.entries()) {
            const from = state.nodes.find(n => n.service === edge.source), to = state.nodes.find(n => n.service === edge.target);
            if (!from || !to) continue;
            const forward = to.x >= from.x;
            const x1 = from.x + (forward ? 162 : 0), x2 = to.x + (forward ? 0 : 162), y1 = from.y + 40, y2 = to.y + 40;
            const bend = Math.max(60, Math.abs(x2 - x1) / 2) * (forward ? 1 : -1);
            const d = `M${x1} ${y1} C${x1 + bend} ${y1},${x2 - bend} ${y2},${x2} ${y2}`;
            const path = shape("path", { d, class: "we-line", "data-dependency": edge.dependency, "data-selected": String(selected?.edge === index), "marker-end": "url(#we-arrow)" });
            const hit = shape("path", { d, class: "we-edge-hit" });
            hit.addEventListener("click", () => { selected = { edge: index }; render(); }); svg.append(path, hit);
        }
    }
    function inspect(state) {
        const panel = q("inspector"); panel.replaceChildren(); q("edge-list").replaceChildren();
        state.edges.forEach((edge, index) => q("edge-list").append(button(`${edge.source} → ${edge.target} · ${edge.dependency}`, () => { selected = { edge: index }; render(); }, "")));
        const edge = selected?.edge != null ? state.edges[selected.edge] : null;
        if (edge) {
            panel.append(el("strong", `${edge.source} → ${edge.target}`));
            const label = el("label", "Dependency classification"), select = el("select"); select.setAttribute("aria-label", "Dependency classification");
            options(select, TYPES.map(type => [type, type]), edge.dependency, null);
            select.addEventListener("change", () => { model.change(s => { s.edges[selected.edge].dependency = select.value; }); render(); }); label.append(select); panel.append(label);
            panel.append(el("p", "Labels are declared. A recorded call does not prove this dependency is necessary."));
            const evidence = documentRow?.edges?.find(e => e.source === edge.source && e.target === edge.target);
            if (evidence?.evidence?.length) { panel.append(el("p", "Observed call references"), el("pre", JSON.stringify(evidence.evidence, null, 2))); }
            panel.append(button("Remove connection", () => { model.change(s => s.edges.splice(selected.edge, 1)); selected = null; render(); }));
        } else if (selected?.service) {
            const service = facts?.services.find(s => s.name === selected.service);
            panel.append(el("strong", selected.service));
            if (service) {
                panel.append(el("p", `${service.readyReplicas} ready / ${service.declaredReplicas} declared replicas`), el("p", `Deployments: ${(service.deploymentNames || []).join(", ") || "Unresolved"}`));
                for (const pod of service.pods || []) panel.append(el("p", `${pod.name}: ${pod.node || "unplaced"}, ${pod.ready ? "ready" : "not ready"}`));
                for (const node of facts.nodes.filter(n => (service.pods || []).some(p => p.node === n.name))) panel.append(el("p", `${node.name}: zone ${node.zone ?? "unknown"}, CPU available ${node.cpuAvailable ?? "unknown"}, memory available ${node.memoryAvailable ?? "unknown"}`));
            } else panel.append(el("p", "Live infrastructure for this service is unavailable."));
            panel.append(button("Remove service", () => { model.remove(selected.service); selected = null; render(); }));
        } else panel.append(el("p", "Select a service or a connection."));
    }
    function serviceLibrary() {
        q("services").replaceChildren();
        for (const service of facts?.services || []) {
            const row = el("div", null, "we-service"); row.draggable = true;
            row.append(el("span", service.name), button("Add", () => { const count = model.state().nodes.length; model.add(service.name, 40 + (count % 4) * 235, 60 + Math.floor(count / 4) * 150); render(); }, ""));
            row.addEventListener("dragstart", event => event.dataTransfer.setData("text/plain", service.name)); q("services").append(row);
        }
        if (!facts?.services.length) q("services").append(el("p", "No live services available. Connect a cluster and select an application."));
    }
    function fresh() { documentRow = null; selected = null; connecting = null; history = []; binding = facts?.connectionFingerprint || null; model.load({}); q("saved").value = ""; q("provenance").textContent = "No saved graph selected."; options(q("history"), [], ""); render(); }
    function savedOptions() { options(q("saved"), saved.map(row => [row.id, `${row.name} · ${row.namespace} · v${row.version}`]), documentRow?.id, "New workflow"); }
    async function loadCatalog(namespace, token) {
        const next = await request(`/catalog?namespace=${encodeURIComponent(namespace)}`);
        if (token !== generation) return;
        facts = next;
        if (!binding) binding = next.connectionFingerprint;
        serviceLibrary();
        if (binding !== next.connectionFingerprint || (documentRow && documentRow.namespace !== namespace)) say("This workflow belongs to another connection or namespace. Create a new workflow here; this graph cannot be saved to this application.", true);
        else say(`${facts.services.length} live logical services. ${facts.status === "partial" ? "Topology is incomplete; review unresolved evidence before prediction." : "No cluster changes are made by the editor."}`);
        permissions();
    }
    async function loadSaved(row) {
        documentRow = row; binding = row.connectionFingerprint; selected = null; facts = null; serviceLibrary();
        if (recording?.namespace !== row.namespace) showRecording(null);
        model.load(row); q("actor").value = row.actor || ""; q("namespace").value = row.namespace;
        q("provenance").textContent = JSON.stringify({ basis: row.evidenceBasis, version: row.version, conflicts: row.conflicts, recordingIds: row.recordingIds, edges: row.edges, limits: row.limits }, null, 2);
        history = await request(`/workflows/${encodeURIComponent(row.id)}/history`);
        options(q("history"), history.map(v => [String(v.version), `Version ${v.version} · ${v.updatedAt} · ${v.deleted ? "deleted" : v.reviewed ? "reviewed declaration" : "draft"}`]), String(row.version), null);
        render(); savedOptions();
        await loadCatalog(row.namespace, generation);
    }
    async function loadLists() {
        const [docs, records] = await Promise.all([request("/workflows"), request("/recordings")]); saved = docs; sessions = records; savedOptions(); recordingOptions();
    }
    function recordingOptions() {
        const relevant = sessions.filter(row => row.namespace === facts?.namespace && row.connectionFingerprint === facts?.connectionFingerprint);
        options(q("recording"), relevant.map(row => [row.id, `${row.state} · ${new Date(row.startedMicros / 1000).toLocaleString()}`]), recording?.id);
    }
    function showRecording(row) {
        recording = row; q("recording").value = row?.id || ""; q("candidates").replaceChildren();
        q("stop").textContent = row?.state === "stopped" ? "Refresh recording evidence" : "Stop recording";
        q("record-status").textContent = row ? `${row.state === "recording" ? "Recording window open. Use your application, then Stop recording." : `Found ${row.candidates.length} candidate workflows. Correct the connections and dependency labels before review.`} ${row.correlation} ${row.coverage ? JSON.stringify(row.coverage) : ""}` : "Manual editing works without tracing.";
        for (const candidate of row?.candidates || []) {
            const box = el("div", null, "we-candidate"); box.append(el("strong", candidate.name), el("p", candidate.services.join(", ")));
            const merge = () => { model.merge(candidate, row.id, facts.services.map(s => s.name)); selected = null; render(); const unmatched = candidate.services.filter(name => !facts.services.some(s => s.name === name)); say(unmatched.length ? `Imported matching logical services. Unmatched trace services: ${unmatched.join(", ")}. No name mapping was guessed.` : "Observed connections merged. Existing dependency labels were preserved. Review every Unknown label."); };
            box.append(button("Merge into canvas", merge), button("Open as new workflow", () => { if (mayLeave()) { fresh(); merge(); } })); q("candidates").append(box);
        }
        permissions();
    }
    async function refresh() {
        const token = ++generation;
        facts = null; serviceLibrary(); permissions();
        const overview = await request("/api/deployments/discovery/all");
        if (token !== generation) return;
        const namespaces = overview.namespaces.filter(row => row.graph.services.length).map(row => [row.namespace, row.namespace]);
        const namespace = q("namespace").value || (namespaces.length === 1 ? namespaces[0][0] : "");
        options(q("namespace"), namespaces, namespace, "Choose an application…");
        if (namespace) await loadCatalog(namespace, token);
        else { facts = null; serviceLibrary(); say("Choose an application namespace to discover its services."); }
        await loadLists(); showRecording(recording);
    }
    async function save(reviewed) {
        if (!facts || binding !== facts.connectionFingerprint) throw new Error("Reload the correct cluster before saving.");
        const token = generation, state = model.state();
        const body = { ...state, actor: q("actor").value.trim(), namespace: facts.namespace, connectionFingerprint: binding, expectedVersion: documentRow?.version || 0, reviewed };
        const row = await request(documentRow ? `/workflows/${encodeURIComponent(documentRow.id)}` : "/workflows", documentRow ? "PUT" : "POST", body);
        if (token !== generation) return;
        await loadSaved(row); await loadLists();
        say(reviewed ? "Saved reviewed declaration. It is available to research predictions in Recommendations; it is not experimentally validated." : "Draft saved as a new immutable version.");
    }
    function updateDraftIndicator() { q("version").textContent = (documentRow ? `Version ${documentRow.version}` : "Unsaved workflow") + " · edited"; permissions(); }
    q("name").addEventListener("input", () => { model.change(s => s.name = q("name").value); updateDraftIndicator(); });
    q("entry").addEventListener("change", () => { model.change(s => s.entryService = q("entry").value || null); render(); });
    q("criticality").addEventListener("input", () => { model.change(s => s.criticality = q("criticality").value === "" ? null : Number(q("criticality").value)); updateDraftIndicator(); });
    for (const action of ["undo", "redo"]) q(action).addEventListener("click", () => { model[action](); selected = null; render(); });
    q("connect").addEventListener("click", () => { model.connect(q("from").value, q("to").value); render(); });
    q("new").addEventListener("click", () => { if (mayLeave()) fresh(); });
    q("duplicate").addEventListener("click", () => { const state = model.state(); fresh(); model.change(s => Object.assign(s, { ...state, name: state.name + " copy" })); render(); say("Duplicate created on the canvas. Save it to create a separate workflow history."); });
    q("namespace").addEventListener("change", () => run(async () => { if (!mayLeave()) { q("namespace").value = facts?.namespace || ""; return; } const namespace = q("namespace").value; facts = null; binding = null; fresh(); showRecording(null); if (namespace) { await loadCatalog(namespace, ++generation); binding = facts.connectionFingerprint; } serviceLibrary(); recordingOptions(); render(); }));
    q("saved").addEventListener("change", () => run(async () => { if (!mayLeave()) { savedOptions(); return; } const id = q("saved").value; if (!id) fresh(); else { ++generation; await loadSaved(await request(`/workflows/${encodeURIComponent(id)}`)); recordingOptions(); } }));
    q("refresh").addEventListener("click", () => run(refresh));
    q("save").addEventListener("click", () => run(() => save(false)));
    q("review").addEventListener("click", () => run(() => save(true)));
    q("delete").addEventListener("click", () => run(async () => {
        if (!documentRow || !window.confirm("Delete this workflow from active use? Previous graph versions and evidence will be preserved.")) return;
        await request(`/workflows/${encodeURIComponent(documentRow.id)}/delete`, "POST", { expectedVersion: documentRow.version, actor: q("actor").value.trim() }); fresh(); await loadLists(); say("Workflow deleted from active use. Its version history is preserved.");
    }));
    q("load-version").addEventListener("click", () => run(async () => {
        if (!documentRow || !mayLeave()) return;
        const row = await request(`/workflows/${encodeURIComponent(documentRow.id)}?version=${encodeURIComponent(q("history").value)}`);
        await loadSaved(row); say("Historical version opened. Duplicate it to preserve changes in a new workflow; saving over a newer version is refused.");
    }));
    q("export").addEventListener("click", () => run(async () => {
        if (!documentRow) throw new Error("Save a reviewed graph first.");
        const payload = await request(`/workflows/${encodeURIComponent(documentRow.id)}/graph?version=${documentRow.version}`);
        const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
        const anchor = el("a"); anchor.href = url; anchor.download = `workflow-${documentRow.id}-v${documentRow.version}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }));
    q("start").addEventListener("click", () => run(async () => {
        if (sessions.some(row => row.state === "recording" && row.namespace === facts.namespace && row.connectionFingerprint === binding)) throw new Error("A recording is already open here. Select it and Stop recording first.");
        const row = await request("/recordings", "POST", { namespace: facts.namespace, connectionFingerprint: binding, jaegerUrl: q("jaeger").value }); sessions.unshift(row); recording = row; recordingOptions(); showRecording(row); say("Recording window started. Use your application normally.");
    }));
    q("stop").addEventListener("click", () => run(async () => { q("record-status").textContent = "Reading the fixed recording window…"; const row = await request(`/recordings/${encodeURIComponent(recording.id)}/stop`, "POST"); sessions = sessions.map(old => old.id === row.id ? row : old); recordingOptions(); showRecording(row); }));
    q("recording").addEventListener("change", () => showRecording(sessions.find(row => row.id === q("recording").value) || null));
    q("canvas").addEventListener("dragover", event => event.preventDefault());
    q("canvas").addEventListener("drop", event => { event.preventDefault(); if (busy) return; const service = event.dataTransfer.getData("text/plain"); if (!facts?.services.some(s => s.name === service)) return; const rect = q("space").getBoundingClientRect(); model.add(service, event.clientX - rect.left, event.clientY - rect.top); render(); });
    window.addEventListener("beforeunload", event => { if (model.dirty()) { event.preventDefault(); event.returnValue = ""; } });
    window.addEventListener("resilience-cluster-changed", () => {
        // Routine renewal notifications preserve this graph. A changed fingerprint disables its save path.
        if (!busy && facts) run(async () => { const namespace = facts.namespace; facts = null; permissions(); await loadCatalog(namespace, ++generation); recordingOptions(); });
    });
    render(); run(refresh);
})();
