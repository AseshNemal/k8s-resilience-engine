/* One editor for declared workflows and observed calls. No Kubernetes mutation requests. */
(function () {
    "use strict";
    const clone = (value) => JSON.parse(JSON.stringify(value));
    const TYPES = ["Mandatory", "Optional", "Supporting", "Unknown"];
    function editable(row = {}) {
        return {
            name: row.name || "",
            entryService: row.entryService || null,
            criticality: row.criticality ?? null,
            nodes: (row.nodes || []).map((n) => ({
                service: n.service,
                x: n.x,
                y: n.y,
            })),
            edges: (row.edges || []).map((e) => ({
                source: e.source,
                target: e.target,
                dependency: TYPES.includes(e.dependency) ? e.dependency : "Unknown",
                reviewedEvidence: [...(e.reviewedEvidence || [])],
            })),
            recordingIds: [...(row.recordingIds || [])],
            evidenceRefs: clone(row.evidenceRefs || []),
        };
    }
    const evidenceKey = (ref) => `${ref.kind}:${ref.id}:${ref.version}:${ref.candidateId}`;
    const isProtected = (edge) =>
        ["validated", "experimentally_validated"].includes(edge.classificationBasis);
    const conflicting = (edge) =>
        (edge.declarations || []).filter(
            (d) =>
                d.dependency !== "Unknown" &&
                d.dependency !== edge.dependency &&
                !(edge.reviewedEvidence || []).includes(d.evidenceKey),
        );
    const uniqueEvidence = (items) => [
        ...new Map(items.map((item) => [JSON.stringify(item), item])).values(),
    ];
    function editorState(row = {}) {
        const state = editable(row);
        state.evidenceSources = clone(row.evidenceSources || []);
        state.edges.forEach((edge, i) => {
            const original = row.edges[i];
            for (const key of ["classificationBasis", "interactionBasis", "evidence", "declarations"]) {
                if (original[key] !== undefined) edge[key] = clone(original[key]);
            }
        });
        return state;
    }
    function createModel(seed) {
        let state = editorState(seed),
            clean = JSON.stringify(state),
            undo = [],
            redo = [];
        function change(action) {
            const before = clone(state);
            action(state);
            if (JSON.stringify(before) !== JSON.stringify(state)) {
                undo.push(before);
                undo = undo.slice(-80);
                redo = [];
            }
        }
        function add(s, service, x, y) {
            if (!s.nodes.some((n) => n.service === service))
                s.nodes.push({
                    service,
                    x: Math.max(8, Math.min(3780, x)),
                    y: Math.max(8, Math.min(3880, y)),
                });
        }
        return {
            state: () => clone(state),
            dirty: () => JSON.stringify(state) !== clean,
            canUndo: () => undo.length > 0,
            canRedo: () => redo.length > 0,
            load(row) {
                state = editorState(row);
                clean = JSON.stringify(state);
                undo = [];
                redo = [];
            },
            change,
            add(service, x = 40, y = 60) {
                change((s) => add(s, service, x, y));
            },
            move(service, x, y) {
                change((s) => {
                    const n = s.nodes.find((n) => n.service === service);
                    if (n)
                        Object.assign(n, {
                            x: Math.max(8, Math.min(3780, x)),
                            y: Math.max(8, Math.min(3880, y)),
                        });
                });
            },
            connect(source, target) {
                change((s) => {
                    if (
                        source &&
                        target &&
                        source !== target &&
                        [source, target].every((name) => s.nodes.some((n) => n.service === name)) &&
                        !s.edges.some((e) => e.source === source && e.target === target)
                    )
                        s.edges.push({ source, target, dependency: "Unknown" });
                });
            },
            remove(service) {
                change((s) => {
                    if (s.edges.some((e) => isProtected(e) && (e.source === service || e.target === service)))
                        return;
                    s.nodes = s.nodes.filter((n) => n.service !== service);
                    s.edges = s.edges.filter((e) => e.source !== service && e.target !== service);
                    if (s.entryService === service) s.entryService = null;
                });
            },
            merge(candidate, sourceId, allowed, ref = null, summary = null) {
                change((s) => {
                    const names = new Set(allowed),
                        key = ref ? evidenceKey(ref) : `recording:${sourceId}`;
                    for (const service of [
                        ...new Set([candidate.entryService, ...(candidate.services || [])].filter(Boolean)),
                    ]) {
                        if (names.has(service))
                            add(
                                s,
                                service,
                                40 + (s.nodes.length % 4) * 250,
                                60 + Math.floor(s.nodes.length / 4) * 150,
                            );
                    }
                    for (const incoming of candidate.edges || []) {
                        if (
                            !names.has(incoming.source) ||
                            !names.has(incoming.target) ||
                            incoming.source === incoming.target
                        )
                            continue;
                        let edge = s.edges.find(
                            (e) => e.source === incoming.source && e.target === incoming.target,
                        );
                        if (!edge) {
                            edge = {
                                source: incoming.source,
                                target: incoming.target,
                                dependency: "Unknown",
                                classificationBasis: "declared",
                                reviewedEvidence: [],
                            };
                            s.edges.push(edge);
                        }
                        edge.evidence = uniqueEvidence([
                            ...(edge.evidence || []),
                            ...(incoming.evidence || []).map((e) => ({
                                ...e,
                                evidenceKey: key,
                                sourceKind: ref?.kind || "recording",
                            })),
                        ]);
                        const declarations = [...(incoming.declarations || [])];
                        if (TYPES.includes(incoming.dependency) && incoming.dependency !== "Unknown")
                            declarations.push({ dependency: incoming.dependency });
                        edge.declarations = uniqueEvidence([
                            ...(edge.declarations || []),
                            ...declarations.map((d) => ({
                                ...d,
                                evidenceKey: key,
                                testName: candidate.name,
                                basis: "declared",
                            })),
                        ]);
                        if (edge.evidence.length) edge.interactionBasis = "observed";
                        // Existing classifications, including verified ones, are never overwritten.
                    }
                    if (ref && !s.evidenceRefs.some((r) => evidenceKey(r) === key))
                        s.evidenceRefs.push(clone(ref));
                    if ((!ref || ref.kind === "recording") && !s.recordingIds.includes(sourceId))
                        s.recordingIds.push(sourceId);
                    if (summary && !s.evidenceSources.some((r) => r.key === key))
                        s.evidenceSources.push({ ...clone(summary), key });
                    if (!s.entryService && names.has(candidate.entryService))
                        s.entryService = candidate.entryService;
                    if (!s.name) s.name = candidate.name;
                });
            },
            undo() {
                if (undo.length) {
                    redo.push(clone(state));
                    state = undo.pop();
                }
            },
            redo() {
                if (redo.length) {
                    undo.push(clone(state));
                    state = redo.pop();
                }
            },
        };
    }
    // Service labels affect existing incoming calls only; no relationship is invented.
    function classifyService(state, service, dependency) {
        if (TYPES.includes(dependency))
            state.edges
                .filter((e) => e.target === service)
                .forEach((e) => {
                    if (!isProtected(e)) {
                        e.dependency = dependency;
                        e.reviewedEvidence = [...new Set((e.declarations || []).map((d) => d.evidenceKey))];
                    }
                });
    }
    function reviewIssues(state) {
        const issues = [];
        if (!state.nodes.length) issues.push({ message: "Add at least one service." });
        if (!state.entryService)
            issues.push({
                message: "Choose the entry service.",
                service: state.nodes[0]?.service,
            });
        const reached = new Set(state.entryService ? [state.entryService] : []);
        let size;
        do {
            size = reached.size;
            state.edges.forEach((e) => {
                if (reached.has(e.source)) reached.add(e.target);
            });
        } while (size !== reached.size);
        for (const n of state.nodes)
            if (state.entryService && !reached.has(n.service))
                issues.push({
                    message: `${n.service} needs a connection from the entry path.`,
                    service: n.service,
                });
        state.edges.forEach((e, edge) => {
            if (conflicting(e).length)
                issues.push({ message: `Review conflicting labels for ${e.source} → ${e.target}.`, edge });
            if (e.dependency === "Unknown")
                issues.push({
                    message: `Choose a role for ${e.source} → ${e.target}.`,
                    edge,
                });
        });
        return issues;
    }
    window.ResilienceWorkflowEditor = {
        createModel,
        editable,
        TYPES,
        classifyService,
        reviewIssues,
        conflicting,
    };
    const root = document.getElementById("workflow-editor");
    if (!root) return;
    const q = (name) => root.querySelector(`[data-we="${name}"]`);
    const handoff = new URLSearchParams(window.location?.search || "");
    const fromRecommendations = handoff.get("purpose") === "recommendations";
    const reviewHelp = q("recommendation-review-help");
    if (reviewHelp) reviewHelp.hidden = !fromRecommendations;
    const recommendationsLink = q("recommendations-return");
    if (recommendationsLink && handoff.get("namespace")) {
        const base = recommendationsLink.getAttribute("href");
        recommendationsLink.setAttribute("href", `${base}?namespace=${encodeURIComponent(handoff.get("namespace"))}`);
    }
    const BASE = "/api/workflows/management",
        model = createModel();
    const labels = {
        Mandatory: "Required",
        Optional: "Optional",
        Supporting: "Supporting",
        Unknown: "Unknown",
    };
    const descriptions = {
        Mandatory: "Required by the calling service",
        Optional: "The caller can continue without it",
        Supporting: "Helps the workflow run",
        Unknown: "Needs review",
    };
    const symbols = {
        Mandatory: "━",
        Optional: "┄",
        Supporting: "┅",
        Unknown: "···",
    };
    let facts = null,
        saved = [],
        sessions = [],
        documentRow = null,
        recording = null,
        history = [], operationalReviews = [];
    let selected = null,
        connecting = null,
        binding = null,
        boundNamespace = null,
        busy = false,
        showAll = false,
        historical = false;
    let view = "manager",
        generation = 0,
        dialogMode = null,
        dialogRow = null,
        transientEvidence = [];
    let testDocuments = [],
        testDocument = null,
        testFile = null,
        testFileBinding = null;
    let viewport = { x: 35, y: 45, k: 1 },
        dragged = false;
    const el = (tag, text, cls) => {
        const n = document.createElement(tag);
        if (text != null) n.textContent = text;
        if (cls) n.className = cls;
        return n;
    };
    const button = (text, action, cls = "we-button") => {
        const b = el("button", text, cls);
        b.type = "button";
        b.addEventListener("click", action);
        return b;
    };
    function say(message = "", error = false) {
        q("status").textContent = message;
        q("status").hidden = !message;
        q("status").dataset.error = String(error);
        requestAnimationFrame(sizeWorkspace);
    }
    function options(select, rows, value, empty = "Choose…") {
        select.replaceChildren();
        if (empty !== null) {
            const n = el("option", empty);
            n.value = "";
            select.append(n);
        }
        for (const [key, label] of rows) {
            const n = el("option", label);
            n.value = key;
            select.append(n);
        }
        select.value = value || "";
    }
    async function request(path, method = "GET", body) {
        const abort = new AbortController(),
            timer = setTimeout(() => abort.abort(), 90000);
        try {
            const response = await fetch(
                path.startsWith("/api/") || path.startsWith("/_extension/") ? path : BASE + path,
                {
                    method,
                    signal: abort.signal,
                    headers: { "Content-Type": "application/json" },
                    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
                },
            );
            const data = await response.json();
            if (!response.ok) {
                const detail = data.detail;
                throw new Error(
                    typeof detail === "string"
                        ? detail
                        : Array.isArray(detail)
                          ? detail.map((e) => e.msg).join("; ")
                          : detail?.message || `Request failed (${response.status})`,
                );
            }
            return data;
        } finally {
            clearTimeout(timer);
        }
    }
    function isBound() {
        return !!facts && binding === facts.connectionFingerprint && boundNamespace === facts.namespace;
    }
    function permissions() {
        root.querySelectorAll("button,input,select,textarea").forEach((n) => {
            n.disabled = busy;
        });
        for (const name of ["new", "discover", "tests"]) q(name).disabled = busy || !facts;
        for (const name of ["save", "review"]) q(name).disabled = busy || !isBound() || historical;
        q("undo").disabled = busy || !model.canUndo();
        q("redo").disabled = busy || !model.canRedo();
        q("start").disabled = busy || !facts || !q("jaeger").value.trim();
        q("stop").disabled =
            busy ||
            !recording ||
            recording.connectionFingerprint !== facts?.connectionFingerprint ||
            recording.namespace !== facts?.namespace;
        q("refresh-recording").disabled = q("stop").disabled;
        q("history-open").disabled = busy || !documentRow;
        q("load-version").disabled = busy || !history.length;
        q("export").disabled =
            busy || !history.find((r) => String(r.version) === q("history").value)?.reviewed;
        q("namespace").disabled = busy || ["editor", "discovery", "tests"].includes(view);
        q("dialog-submit").disabled =
            busy || (dialogMode === "review" && reviewIssues(model.state()).length > 0);
        root.querySelectorAll(".we-service button[data-added]").forEach((n) => {
            n.disabled = true;
        });
        root.querySelectorAll(".we-role-option[data-locked],button[data-no-evidence]").forEach((n) => {
            n.disabled = true;
        });
        for (const key of ["add-manually", "add-recording", "add-tests"])
            q(key).disabled = busy || !isBound() || historical;
        q("test-submit").disabled = busy || !testFile || !isBound();
        q("test-refresh").disabled = busy || !testDocument || !isBound();
        q("layout").dataset.readonly = String(!isBound());
    }
    async function run(action) {
        if (busy) return;
        busy = true;
        permissions();
        try {
            await action();
        } catch (error) {
            const message =
                error.name === "AbortError"
                    ? "The request timed out. Refresh before retrying; the server may have completed the operation."
                    : error.message;
            if (q("dialog").open) {
                q("dialog-error").hidden = false;
                q("dialog-error").textContent = message;
            } else say(message, true);
        } finally {
            busy = false;
            permissions();
        }
    }
    function mayLeave() {
        return !model.dirty() || window.confirm("Leave this workflow’s unsaved changes?");
    }
    function show(next) {
        view = next;
        root.dataset.view = next;
        root.querySelectorAll("[data-we-panel]").forEach((n) => {
            n.hidden = n.dataset.wePanel !== next;
        });
        root.querySelectorAll(".we-menu[open]").forEach((n) => {
            n.open = false;
        });
        const research = document.getElementById("workflow-research");
        if (research) research.hidden = true;
        permissions();
        requestAnimationFrame(sizeWorkspace);
    }
    function research() {
        const panel = document.getElementById("workflow-research");
        if (panel) {
            panel.hidden = false;
            panel.open = true;
            panel.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    }
    function thumb() {
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("viewBox", "0 0 100 58");
        svg.setAttribute("aria-hidden", "true");
        svg.classList.add("we-workflow-thumb");
        for (const [tag, attrs] of [
            [
                "path",
                {
                    d: "M26 29H43M60 29H76M51 29V46H76",
                    fill: "none",
                    stroke: "currentColor",
                    "stroke-width": 1.5,
                },
            ],
            ...[
                [5, 18],
                [40, 18],
                [75, 18],
                [75, 40],
            ].map(([x, y]) => [
                "rect",
                {
                    x,
                    y,
                    width: 20,
                    height: 20,
                    rx: 4,
                    fill: "none",
                    stroke: "currentColor",
                    "stroke-width": 1.5,
                },
            ]),
        ]) {
            const n = document.createElementNS(svg.namespaceURI, tag);
            Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
            svg.append(n);
        }
        return svg;
    }
    function renderManager() {
        const list = q("workflow-list");
        list.replaceChildren();
        const rows = saved.filter(
            (r) =>
                showAll ||
                (r.namespace === q("namespace").value &&
                    (!facts || r.connectionFingerprint === facts.connectionFingerprint)),
        );
        q("workflow-count").textContent = rows.length;
        q("show-all").textContent = showAll
            ? "Show selected namespace only"
            : "Show workflows from all namespaces";
        q("other-workflows").hidden = showAll || rows.length === saved.length;
        q("other-workflows").textContent =
            `${saved.length - rows.length} workflow(s) belong to other namespaces or connections. Find them in More tools.`;
        if (!rows.length) {
            const blank = el("div", null, "we-blank");
            blank.append(
                thumb(),
                el("h3", "Your first workflow starts here"),
                el(
                    "p",
                    "Choose Create Manually to draw a workflow, or Discover Automatically to record your application.",
                ),
            );
            list.append(blank);
        }
        for (const row of rows) {
            const box = el("div", null, "we-workflow-row"),
                info = el("div", null, "we-workflow-info"),
                meta = el("div", null, "we-workflow-meta");
            const open = () => run(() => openDocument(row));
            info.append(button(row.name, open, "we-workflow-name"));
            const operational = operationalReviews.some((r) => r.kind === "managed" && r.workflowId === row.id && r.sourceVersion === String(row.version));
            const status = el("span", operational ? "Operational review recorded · operator-declared" : row.reviewed ? "Declaration · operational review required" : "Draft", "we-badge");
            status.dataset.kind = row.reviewed ? "reviewed" : "draft";
            meta.append(
                status,
                el(
                    "span",
                    row.evidenceRefs?.some((r) => r.kind === "test")
                        ? "⊕ Test evidence + edits"
                        : row.recordingIds?.length
                          ? "◉ Recording + edits"
                          : "◇ Manual",
                ),
                el("span", row.namespace),
                el("span", `${row.nodes.length} services · v${row.version}`),
            );
            info.append(meta);
            const menu = el("details", null, "we-menu"),
                summary = el("summary", "•••");
            summary.setAttribute("aria-label", `Actions for ${row.name}`);
            const actions = el("div");
            actions.append(
                button("Duplicate", () => {
                    menu.open = false;
                    duplicate(row);
                }),
                button("Rename", () => {
                    menu.open = false;
                    openDialog("rename", row);
                }),
                button("Delete", () => {
                    menu.open = false;
                    openDialog("delete", row);
                }),
            );
            menu.append(summary, actions);
            const openBtn = button("Open", open);
            openBtn.setAttribute("data-we-open", "true");
            box.append(thumb(), info, openBtn, menu);
            // The whole row opens the workflow. The actions menu keeps its own clicks.
            // At narrow Docker Desktop widths the separate Open button is hidden; the row still works.
            box.addEventListener("click", (event) => {
                if (event.target.closest(".we-menu, details, a, [data-we-open]")) return;
                open();
            });
            box.setAttribute("role", "button");
            box.tabIndex = 0;
            box.addEventListener("keydown", (event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                if (event.target !== box) return;
                event.preventDefault();
                open();
            });
            list.append(box);
        }
        permissions();
    }
    async function loadLists() {
        const [docs, records, reviews] = await Promise.all([request("/workflows"), request("/recordings"), request("/operational-reviews")]);
        operationalReviews = reviews;
        saved = docs;
        sessions = records;
        renderManager();
        recordingOptions();
    }
    function clusterLabel(text, available) {
        const dot = el("span", null, "we-dot");
        dot.setAttribute("aria-hidden", "true");
        q("cluster").replaceChildren(dot, document.createTextNode(text));
        q("cluster").dataset.state = available ? "connected" : "unavailable";
    }
    async function loadCatalog(namespace, token = generation) {
        facts = null;
        serviceLibrary();
        permissions();
        const next = await request(`/catalog?namespace=${encodeURIComponent(namespace)}`);
        if (token !== generation) return;
        facts = next;
        serviceLibrary();
        recordingOptions();
        renderManager();
        if (view === "editor" && !isBound())
            say(
                "This graph belongs to another cluster connection. Reconnect to its cluster before saving. Your edits are still on the canvas.",
                true,
            );
        else if (next.status === "partial")
            say(
                "Some live service placement is unresolved. You can design the workflow; review placement evidence before risk analysis.",
            );
    }
    async function refresh() {
        const token = ++generation;
        facts = null;
        serviceLibrary();
        permissions();
        clusterLabel("Checking connection…", false);
        const lists = loadLists().catch((error) => {
            say(error.message, true);
        });
        try {
            const overview = await request("/api/deployments/discovery/all");
            if (token !== generation) return;
            const namespaces = overview.namespaces
                .filter((r) => r.graph?.services?.length)
                .map((r) => [r.namespace, r.namespace]);
            const prior = q("namespace").value || handoff.get("namespace"),
                ns = namespaces.some((r) => r[0] === prior)
                    ? prior
                    : namespaces.length === 1
                      ? namespaces[0][0]
                      : "";
            options(q("namespace"), namespaces, ns, "Choose namespace…");
            clusterLabel("Connected cluster", true);
            if (window.ddClient)
                request("/_extension/cluster")
                    .then((data) => {
                        if (token === generation)
                            clusterLabel(data.approved?.context || data.server || "Connected cluster", true);
                    })
                    .catch(() => {});
            if (ns) await loadCatalog(ns, token);
            else say("Select your application namespace to load its services.");
        } catch (error) {
            facts = null;
            clusterLabel("Cluster unavailable", false);
            throw error;
        } finally {
            await lists;
            permissions();
        }
    }
    function fresh(seed = {}, evidence = []) {
        documentRow = null;
        historical = false;
        history = [];
        selected = null;
        connecting = null;
        binding = facts?.connectionFingerprint || null;
        boundNamespace = facts?.namespace || null;
        transientEvidence = evidence;
        model.load({});
        if (Object.keys(seed).length) model.change((s) => Object.assign(s, editorState(seed)));
        viewport = { x: 35, y: 45, k: 1 };
        q("layout").dataset.library = "true";
        show("editor");
        render();
        say();
    }
    function duplicate(row = model.state()) {
        if (!mayLeave()) return;
        const sourceBinding = row.connectionFingerprint || binding,
            ns = row.namespace || boundNamespace,
            evidence = row.id ? row.edges : [...(documentRow?.edges || []), ...transientEvidence];
        fresh({ ...row, name: `${row.name || "Workflow"} copy` }, evidence);
        binding = sourceBinding;
        boundNamespace = ns;
        render();
        fit();
        say("Copy opened. Save it to create a separate workflow and version history.");
    }
    async function openDocument(row) {
        if (!mayLeave()) return;
        documentRow = row;
        historical = false;
        binding = row.connectionFingerprint;
        boundNamespace = row.namespace;
        model.load(row);
        selected = null;
        connecting = null;
        transientEvidence = row.edges || [];
        if (![...q("namespace").options].some((n) => n.value === row.namespace)) {
            const n = el("option", row.namespace);
            n.value = row.namespace;
            q("namespace").append(n);
        }
        q("namespace").value = row.namespace;
        show("editor");
        render();
        say();
        await loadCatalog(row.namespace, ++generation);
        fit();
    }
    function updateVersion() {
        q("version").textContent =
            (documentRow
                ? `v${documentRow.version} · ${documentRow.reviewed ? "Reviewed declaration" : "Draft"}`
                : "Not saved yet") +
            (model.dirty() ? " · Unsaved changes" : "") +
            (historical ? " · Historical, duplicate to edit" : "");
    }
    function observed(edge) {
        return !!edge.evidence?.length || edge.interactionBasis === "observed";
    }
    function nodeRole(state, service) {
        if (state.entryService === service) return "Required · entry";
        const incoming = state.edges.filter((e) => e.target === service),
            types = new Set(incoming.map((e) => e.dependency));
        return !incoming.length
            ? "Not connected"
            : types.size > 1
              ? "Mixed roles"
              : `${labels[incoming[0].dependency]}${incoming[0].dependency === "Mandatory" ? " by caller" : ""}`;
    }
    function render() {
        const inspectorWasOpen = q("layout").dataset.inspector === "true";
        const state = model.state();
        q("name").value = state.name;
        updateVersion();
        q("empty").hidden = !!state.nodes.length;
        q("source-summary").textContent =
            `${state.evidenceSources.length} evidence source${state.evidenceSources.length === 1 ? "" : "s"}`;
        q("source-list").replaceChildren();
        if (!state.evidenceSources.length)
            q("source-list").append(
                el(
                    "p",
                    "Manually declared services and connections. Add recordings or test evidence at any time.",
                ),
            );
        for (const source of state.evidenceSources) {
            const row = el("div", null, "we-evidence-row");
            row.append(
                el("strong", `${source.kind === "test" ? "Test" : "Recording"}: ${source.name || source.id}`),
            );
            if (source.execution)
                row.append(
                    el("p", `${source.execution.runner} · ${source.execution.executionId}`),
                    el("p", source.execution.applicationUrl),
                    el(
                        "p",
                        `Runner: ${source.execution.reportedStatus}. Trace assertions: ${source.outcome?.status || "indeterminate"}.`,
                    ),
                );
            if (source.unmappedServices?.length)
                row.append(el("p", `Unmapped services: ${source.unmappedServices.join(", ")}`));
            if (source.correlation) row.append(el("p", source.correlation));
            q("source-list").append(row);
        }
        q("canvas-summary").textContent =
            `${state.nodes.length} services · ${state.edges.length} connections${state.edges.some((e) => e.dependency === "Unknown") ? " · Needs review" : ""}`;
        q("connect-prompt").hidden = !connecting;
        q("connect-prompt").textContent =
            `Connect ${connecting} → click the service it calls. Escape to cancel.`;
        drawNodes(state);
        drawLines(state);
        inspect(state);
        serviceLibrary();
        transform();
        revealSelection();
        if (!inspectorWasOpen && selected?.edge != null) fit();
        permissions();
    }
    function revealSelection() {
        if (!selected?.service) return;
        const card = [...q("nodes").children].find((n) => n.dataset.service === selected.service);
        const rect = card?.getBoundingClientRect(),
            canvas = q("canvas").getBoundingClientRect();
        if (!rect) return;
        const right = canvas.right - (root.clientWidth <= 850 ? 245 : 0) - 20;
        if (rect.right > right) viewport.x -= rect.right - right;
        else if (rect.left < canvas.left + 20) viewport.x += canvas.left + 20 - rect.left;
        if (rect.bottom > canvas.bottom - 20) viewport.y -= rect.bottom - canvas.bottom + 20;
        else if (rect.top < canvas.top + 20) viewport.y += canvas.top + 20 - rect.top;
        transform();
    }
    function serviceLibrary() {
        const panel = q("services");
        panel.replaceChildren();
        const state = model.state(),
            filter = q("search").value.toLowerCase();
        const available = (facts?.services || []).filter((s) => s.name.toLowerCase().includes(filter));
        for (const service of available) {
            const added = state.nodes.some((n) => n.service === service.name),
                row = el("div", null, "we-service"),
                name = el("span", service.name, "we-service-name");
            row.draggable = !added;
            name.append(el("small", added ? "On canvas" : "Kubernetes service"));
            const add = button(added ? "✓" : "＋", () => addService(service.name), "we-icon-button");
            add.setAttribute("aria-label", `Add ${service.name}`);
            if (added) add.dataset.added = "true";
            row.append(el("span", "⬡", "we-service-symbol"), name, add);
            row.addEventListener("dragstart", (e) => {
                if (!busy && isBound()) e.dataTransfer.setData("text/plain", service.name);
                else e.preventDefault();
            });
            panel.append(row);
        }
        if (!available.length)
            panel.append(
                el(
                    "p",
                    facts ? "No matching services." : "Live service inventory is unavailable.",
                    "we-muted",
                ),
            );
    }
    function world(x, y) {
        const r = q("canvas").getBoundingClientRect();
        return {
            x: (x - r.left - viewport.x) / viewport.k,
            y: (y - r.top - viewport.y) / viewport.k,
        };
    }
    function addService(service, point) {
        if (busy || !isBound() || model.state().nodes.some((n) => n.service === service)) return;
        const count = model.state().nodes.length,
            center = world(
                q("canvas").getBoundingClientRect().left + q("canvas").clientWidth / 2,
                q("canvas").getBoundingClientRect().top + q("canvas").clientHeight / 2,
            );
        const p = point || {
            x: count < 3 ? 35 + count * 250 : center.x - 98 + (count % 3) * 25,
            y: count < 3 ? 100 : center.y - 50 + (count % 3) * 30,
        };
        model.change((s) => {
            s.nodes.push({
                service,
                x: Math.max(8, Math.min(3780, p.x)),
                y: Math.max(8, Math.min(3880, p.y)),
            });
            if (!s.entryService && !s.nodes.slice(0, -1).length) s.entryService = service;
        });
        selected = null;
        q("layout").dataset.library = root.clientWidth > 700 ? "true" : "false";
        render();
        // Keep new nodes visible without zooming the entire graph down on every addition.
        const card = [...q("nodes").children].find((n) => n.dataset.service === service),
            r = card?.getBoundingClientRect(),
            canvas = q("canvas").getBoundingClientRect();
        if (
            r &&
            (r.right > canvas.right - 20 ||
                r.left < canvas.left ||
                r.bottom > canvas.bottom ||
                r.top < canvas.top)
        )
            fit();
    }
    function shape(tag, attrs) {
        const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
        for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
        return n;
    }
    function drawLines(state, preview) {
        const svg = q("lines");
        svg.replaceChildren();
        const defs = shape("defs", {}),
            marker = shape("marker", {
                id: "we-arrow",
                markerWidth: 9,
                markerHeight: 9,
                refX: 8,
                refY: 4,
                orient: "auto",
                markerUnits: "userSpaceOnUse",
            });
        marker.append(shape("path", { d: "M0 0 L8 4 L0 8 Z", fill: "#5e7392" }));
        defs.append(marker);
        svg.append(defs);
        for (const [index, edge] of state.edges.entries()) {
            const from = state.nodes.find((n) => n.service === edge.source),
                to = state.nodes.find((n) => n.service === edge.target);
            if (!from || !to) continue;
            const forward = to.x >= from.x;
            const x1 = from.x + (forward ? 196 : 0),
                y1 = from.y + 56,
                x2 = to.x + (forward ? 0 : 196),
                y2 = to.y + 56,
                bend = Math.max(45, Math.abs(x2 - x1) / 2) * (forward ? 1 : -1);
            const d = `M${x1} ${y1} C${x1 + bend} ${y1},${x2 - bend} ${y2},${x2} ${y2}`;
            const path = shape("path", {
                d,
                class: "we-line",
                "data-dependency": edge.dependency,
                "data-selected": String(selected?.edge === index),
                "data-conflict": String(conflicting(edge).length > 0),
                "marker-end": "url(#we-arrow)",
            });
            const hit = shape("path", {
                d,
                class: "we-edge-hit",
                tabindex: 0,
                role: "button",
                "aria-label": `${edge.source} to ${edge.target}, ${labels[edge.dependency]}, ${observed(edge) ? "observed call" : "declared"}`,
            });
            const choose = () => {
                if (busy) return;
                selected = { edge: index };
                connecting = null;
                render();
            };
            hit.addEventListener("click", choose);
            hit.addEventListener("keydown", (e) => {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    choose();
                }
            });
            const label = shape("text", {
                x: (x1 + x2) / 2,
                y: (y1 + y2) / 2 - 10,
                "text-anchor": "middle",
                class: "we-edge-label",
            });
            label.textContent = `${conflicting(edge).length ? "! " : ""}${observed(edge) ? "◉" : "◇"} ${labels[edge.dependency]}`;
            svg.append(path, hit, label);
        }
        if (preview && connecting) {
            const from = state.nodes.find((n) => n.service === connecting);
            if (from)
                svg.append(
                    shape("path", {
                        d: `M${from.x + 196} ${from.y + 56} L${preview.x} ${preview.y}`,
                        class: "we-line",
                        "data-dependency": "Unknown",
                    }),
                );
        }
    }
    function drawNodes(state) {
        q("nodes").replaceChildren();
        for (const node of state.nodes) {
            const card = el("div", null, "we-node");
            card.dataset.service = node.service;
            card.dataset.selected = String(selected?.service === node.service);
            card.dataset.entry = String(state.entryService === node.service);
            card.tabIndex = 0;
            card.setAttribute("role", "group");
            card.setAttribute(
                "aria-label",
                `${node.service}, ${nodeRole(state, node.service)}. Enter for properties, arrow keys to move.`,
            );
            Object.assign(card.style, { left: `${node.x}px`, top: `${node.y}px` });
            const top = el("div", null, "we-node-top"),
                isObserved = state.edges.some(
                    (e) => (e.source === node.service || e.target === node.service) && observed(e),
                ),
                source = el("span", isObserved ? "◉ Observed" : "◇ Declared", "we-node-source");
            source.dataset.observed = String(isObserved);
            top.append(source);
            if (state.entryService === node.service) top.append(el("span", "ENTRY", "we-node-entry"));
            card.append(
                top,
                el("strong", node.service),
                el("span", nodeRole(state, node.service), "we-node-role"),
            );
            const port = button(
                "→",
                (e) => {
                    e.stopPropagation();
                    if (dragged) {
                        dragged = false;
                        return;
                    }
                    connecting = connecting === node.service ? null : node.service;
                    render();
                },
                "we-port",
            );
            port.setAttribute("aria-label", `Connect from ${node.service}`);
            port.setAttribute("aria-pressed", String(connecting === node.service));
            port.addEventListener("pointerdown", (e) => {
                if (busy || !isBound() || e.button !== 0) return;
                e.stopPropagation();
                const start = { x: e.clientX, y: e.clientY };
                let moved = false;
                const move = (ev) => {
                    if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) > 4) {
                        moved = true;
                        connecting = node.service;
                        drawLines(model.state(), world(ev.clientX, ev.clientY));
                    }
                };
                const end = (ev) => {
                    window.removeEventListener("pointermove", move);
                    window.removeEventListener("pointerup", end);
                    window.removeEventListener("pointercancel", cancel);
                    if (moved) {
                        const target = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".we-node")
                            ?.dataset.service;
                        if (target && target !== node.service) {
                            model.connect(node.service, target);
                            selected = {
                                edge: model
                                    .state()
                                    .edges.findIndex((e) => e.source === node.service && e.target === target),
                            };
                            connecting = null;
                        }
                        dragged = true;
                        setTimeout(() => {
                            dragged = false;
                        }, 0);
                        render();
                    }
                };
                const cancel = () => {
                    window.removeEventListener("pointermove", move);
                    window.removeEventListener("pointerup", end);
                    connecting = null;
                    render();
                };
                window.addEventListener("pointermove", move);
                window.addEventListener("pointerup", end);
                window.addEventListener("pointercancel", cancel, { once: true });
            });
            card.append(port);
            card.addEventListener("click", (e) => {
                if (busy || e.target.closest("button") || dragged) return;
                if (connecting && connecting !== node.service) {
                    model.connect(connecting, node.service);
                    connecting = null;
                }
                selected = { service: node.service };
                render();
            });
            card.addEventListener("keydown", (e) => {
                if (busy || e.target !== card) return;
                if (e.key === "Enter") {
                    selected = { service: node.service };
                    render();
                }
                const d = {
                    ArrowLeft: [-12, 0],
                    ArrowRight: [12, 0],
                    ArrowUp: [0, -12],
                    ArrowDown: [0, 12],
                }[e.key];
                if (d) {
                    e.preventDefault();
                    model.move(node.service, node.x + d[0], node.y + d[1]);
                    render();
                    [...q("nodes").children].find((n) => n.dataset.service === node.service)?.focus();
                }
            });
            card.addEventListener("pointerdown", (e) => {
                if (e.target.closest("button") || e.button !== 0 || busy) return;
                const start = { x: e.clientX, y: e.clientY };
                let moved = false,
                    last = null;
                const move = (ev) => {
                    if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 4 && !moved) return;
                    moved = true;
                    last = {
                        x: Math.max(8, Math.min(3780, node.x + (ev.clientX - start.x) / viewport.k)),
                        y: Math.max(8, Math.min(3880, node.y + (ev.clientY - start.y) / viewport.k)),
                    };
                    Object.assign(card.style, {
                        left: `${last.x}px`,
                        top: `${last.y}px`,
                    });
                    const draft = model.state();
                    Object.assign(
                        draft.nodes.find((n) => n.service === node.service),
                        last,
                    );
                    drawLines(draft);
                };
                const end = () => {
                    window.removeEventListener("pointermove", move);
                    window.removeEventListener("pointerup", end);
                    window.removeEventListener("pointercancel", cancel);
                    if (moved) {
                        model.move(node.service, last.x, last.y);
                        selected = { service: node.service };
                        dragged = true;
                        setTimeout(() => {
                            dragged = false;
                        }, 0);
                        render();
                    }
                };
                const cancel = () => {
                    window.removeEventListener("pointermove", move);
                    window.removeEventListener("pointerup", end);
                    render();
                };
                window.addEventListener("pointermove", move);
                window.addEventListener("pointerup", end);
                window.addEventListener("pointercancel", cancel, { once: true });
            });
            q("nodes").append(card);
        }
    }
    function disclosure(title, content) {
        const d = el("details", null, "we-disclosure");
        d.append(el("summary", title), content);
        return d;
    }
    function inspect(state) {
        const panel = q("inspector");
        panel.replaceChildren();
        const edge = selected?.edge != null ? state.edges[selected.edge] : null,
            node = state.nodes.find((n) => n.service === selected?.service);
        q("layout").dataset.inspector = String(!!edge || !!node);
        if ((edge || node) && root.clientWidth <= 850) q("layout").dataset.library = "false";
        if (!edge && !node) return;
        q("inspector-heading").textContent = edge ? "Connection" : "Service";
        panel.append(
            el("strong", edge ? `${edge.source} → ${edge.target}` : node.service, "we-selection-name"),
        );
        if (node) {
            if (state.entryService === node.service)
                panel.append(
                    el("span", "→ Entry service", "we-badge"),
                    el("p", "Requests begin here. This service is required for the workflow."),
                );
            else
                panel.append(
                    button(
                        "Make entry service",
                        () => {
                            model.change((s) => {
                                s.entryService = node.service;
                            });
                            render();
                        },
                        "we-text-button we-entry-action",
                    ),
                );
        }
        const incoming = edge ? [edge] : state.edges.filter((e) => e.target === node.service);
        if (edge || (node.service !== state.entryService && incoming.length)) {
            panel.append(el("h4", edge ? "How does the caller use it?" : "Role in this workflow"));
            const roles = el("div", null, "we-role-options");
            for (const type of TYPES) {
                const b = button(
                    null,
                    () => {
                        model.change((s) => {
                            if (edge) {
                                if (!isProtected(edge)) {
                                    s.edges[selected.edge].dependency = type;
                                    s.edges[selected.edge].reviewedEvidence = [
                                        ...new Set((edge.declarations || []).map((d) => d.evidenceKey)),
                                    ];
                                }
                            } else classifyService(s, node.service, type);
                        });
                        render();
                    },
                    "we-role-option",
                );
                const text = el("span", labels[type]);
                text.append(el("small", descriptions[type]));
                b.append(el("span", symbols[type]), text);
                b.title = descriptions[type];
                b.setAttribute("aria-pressed", String(incoming.every((e) => e.dependency === type)));
                if (!isBound() || incoming.some(isProtected)) b.dataset.locked = "true";
                roles.append(b);
            }
            panel.append(roles);
            panel.append(
                el(
                    "p",
                    edge
                        ? "Your label is declared, not experimentally validated."
                        : "Sets the role on existing incoming calls.",
                ),
            );
            if (!edge)
                panel.append(
                    disclosure(
                        "How roles work",
                        el(
                            "p",
                            "Required means required by the caller. A required call inside an optional branch does not make the whole branch required. Labels are declarations, not experimental validation.",
                        ),
                    ),
                );
        } else if (node && !incoming.length && node.service !== state.entryService)
            panel.append(
                el(
                    "p",
                    "Connect this service from a caller to choose Required, Optional, Supporting or Unknown. No connection is assumed.",
                ),
            );
        if (node) {
            const connect = button("→ Connect to a service", () => {
                connecting = node.service;
                render();
            });
            panel.append(connect);
            const calls = state.edges
                .map((e, i) => ({ e, i }))
                .filter(({ e }) => e.source === node.service || e.target === node.service);
            if (calls.length) {
                const links = el("div");
                for (const { e, i } of calls)
                    links.append(
                        button(
                            `${e.source} → ${e.target} · ${labels[e.dependency]}`,
                            () => {
                                selected = { edge: i };
                                render();
                            },
                            "we-text-button",
                        ),
                    );
                panel.append(disclosure("Connections", links));
            }
            const service = facts?.services.find((s) => s.name === node.service),
                infra = el("div");
            if (service) {
                infra.append(
                    el(
                        "p",
                        `${service.readyReplicas ?? "Unknown"} ready / ${service.declaredReplicas ?? "unknown"} declared replicas`,
                    ),
                    el("p", `Deployments: ${(service.deploymentNames || []).join(", ") || "Unresolved"}`),
                );
                for (const pod of service.pods || [])
                    infra.append(
                        el(
                            "p",
                            `${pod.name}: ${pod.node || "unplaced"}, ${pod.ready === true ? "ready" : pod.ready === false ? "not ready" : "readiness unknown"}`,
                        ),
                    );
                for (const n of facts.nodes || [])
                    if ((service.pods || []).some((p) => p.node === n.name))
                        infra.append(
                            el(
                                "p",
                                `${n.name}: zone ${n.zone ?? "unknown"}, CPU available ${n.cpuAvailable ?? "unknown"}, memory available ${n.memoryAvailable ?? "unknown"}`,
                            ),
                        );
            } else infra.append(el("p", "Live placement is unavailable for this service."));
            panel.append(disclosure("Infrastructure details", infra));
        } else {
            panel.append(
                el(
                    "span",
                    observed(edge) ? "◉ Observed call · not validated" : "◇ Manually declared",
                    "we-badge",
                ),
            );
            const evidence = edge.evidence || [];
            for (const kind of new Set(
                evidence.map(
                    (e) =>
                        e.sourceKind || (e.recordingId ? "recording" : e.testEvidenceId ? "test" : "source"),
                ),
            ))
                panel.append(
                    el(
                        "p",
                        kind === "test"
                            ? "⊕ Application test evidence"
                            : kind === "recording"
                              ? "◎ Recorded activity"
                              : "Observed trace reference",
                    ),
                );
            if (evidence.length)
                panel.append(
                    disclosure("All trace references", el("pre", JSON.stringify(evidence, null, 2))),
                );
            if (isProtected(edge)) {
                panel.append(
                    el("p", "✓ Experimentally validated classification. Imports cannot overwrite it."),
                );
                if (conflicting(edge).length)
                    panel.append(
                        button("Keep validated classification", () => {
                            model.change(
                                (s) =>
                                    (s.edges[selected.edge].reviewedEvidence = [
                                        ...new Set((edge.declarations || []).map((d) => d.evidenceKey)),
                                    ]),
                            );
                            render();
                        }),
                    );
            }
            if (conflicting(edge).length) {
                const warning = el("div", null, "we-conflict");
                warning.append(el("strong", "Conflicting declarations"));
                for (const declaration of conflicting(edge))
                    warning.append(
                        el(
                            "p",
                            `${declaration.testName || "Test"}: ${labels[declaration.dependency]} (declared)`,
                        ),
                    );
                warning.append(
                    el("p", "Choose a role above to confirm your decision. Existing labels were kept."),
                );
                panel.append(warning);
            }
        }
        panel.append(
            button(
                edge ? "Remove connection" : "Remove service",
                () => {
                    if (edge) {
                        if (!isProtected(edge)) model.change((s) => s.edges.splice(selected.edge, 1));
                    } else model.remove(node.service);
                    selected = null;
                    render();
                },
                "we-text-button we-remove",
            ),
        );
    }
    function sizeWorkspace() {
        if (view !== "editor") return;
        const top = q("layout").getBoundingClientRect().top;
        q("layout").style.height = `${Math.max(350, Math.min(900, window.innerHeight - top - 40))}px`;
    }
    function transform() {
        q("space").style.transform = `translate(${viewport.x}px,${viewport.y}px) scale(${viewport.k})`;
        q("zoom").textContent = `${Math.round(viewport.k * 100)}%`;
        q("canvas").style.backgroundPosition = `${viewport.x}px ${viewport.y}px`;
        q("canvas").style.backgroundSize = `${20 * viewport.k}px ${20 * viewport.k}px`;
    }
    function zoom(factor, x = q("canvas").clientWidth / 2, y = q("canvas").clientHeight / 2) {
        const k = Math.max(0.25, Math.min(1.75, viewport.k * factor));
        viewport.x = x - ((x - viewport.x) * k) / viewport.k;
        viewport.y = y - ((y - viewport.y) * k) / viewport.k;
        viewport.k = k;
        transform();
    }
    function fit() {
        const nodes = model.state().nodes;
        if (!nodes.length) {
            viewport = { x: 35, y: 45, k: 1 };
            transform();
            return;
        }
        const left = Math.min(...nodes.map((n) => n.x)),
            top = Math.min(...nodes.map((n) => n.y)),
            width = Math.max(...nodes.map((n) => n.x + 215)) - left,
            height = Math.max(...nodes.map((n) => n.y + 120)) - top;
        const available =
            q("canvas").clientWidth -
            (q("layout").dataset.inspector === "true" && root.clientWidth <= 850 ? 245 : 0);
        const k = Math.max(
            0.25,
            Math.min(1, (available - 70) / width, (q("canvas").clientHeight - 90) / height),
        );
        viewport = {
            k,
            x: Math.max(20, (available - width * k) / 2) - left * k,
            y: Math.max(35, (q("canvas").clientHeight - height * k) / 2) - top * k,
        };
        transform();
    }
    function addCandidate(row, candidate, kind) {
        if (!isBound() || row.namespace !== boundNamespace || row.connectionFingerprint !== binding) {
            say("This evidence belongs to another application connection.", true);
            return;
        }
        if (!candidate.services.length || !candidate.entryService) {
            say(
                "Map the observed entry and participating services first. No service identity is guessed.",
                true,
            );
            return;
        }
        const ref = { kind, id: row.id, version: row.version, candidateId: candidate.candidateId };
        model.merge(
            candidate,
            row.id,
            facts.services.map((s) => s.name),
            ref,
            {
                ...ref,
                name: candidate.name,
                execution: candidate.execution,
                outcome: candidate.outcome,
                correlation: row.correlation,
                unmappedServices: candidate.unmappedServices || [],
                coverage: candidate.coverage,
            },
        );
        selected = null;
        show("editor");
        render();
        fit();
        const unmapped =
            candidate.unmappedServices ||
            candidate.services.filter((name) => !facts.services.some((s) => s.name === name));
        say(
            unmapped.length
                ? `Evidence added. Unmapped services were kept in the source details: ${unmapped.join(", ")}. No mapping was guessed.`
                : "Evidence added to this workflow. Existing positions and dependency labels were preserved; new calls need review.",
            !!unmapped.length,
        );
    }
    async function openTests() {
        if (!binding) fresh();
        if (testFileBinding !== `${boundNamespace}:${binding}`) {
            testFile = null;
            q("test-file").value = "";
            q("test-file-name").textContent = "No file selected";
            q("test-preview").replaceChildren();
        }
        if (
            testDocument &&
            (testDocument.namespace !== boundNamespace || testDocument.connectionFingerprint !== binding)
        )
            testDocument = null;
        show("tests");
        say();
        testDocuments = (await request("/test-evidence")).filter(
            (r) => r.namespace === boundNamespace && r.connectionFingerprint === binding,
        );
        options(
            q("test-history"),
            testDocuments.map((r) => [r.id, `${r.candidates.map((c) => c.name).join(", ")} · v${r.version}`]),
            testDocument?.id,
        );
        if (!q("test-jaeger").value) {
            if (q("jaeger").value) q("test-jaeger").value = q("jaeger").value;
            else {
                const status = await request("/api/workflows/jaeger/status");
                q("test-jaeger").value = status.jaegerUrl || "";
            }
        }
        showTests(testDocument);
    }
    function showTests(row) {
        testDocument = row;
        q("test-results").replaceChildren();
        q("test-refresh").hidden = !row?.jaegerUrl;
        q("test-details").textContent = row ? JSON.stringify(row, null, 2) : "No evidence imported yet.";
        if (q("test-empty")) q("test-empty").hidden = !!(row || testDocuments.length);
        if (!row) return;
        for (const candidate of row.candidates) {
            const box = el("div", null, "we-candidate"),
                info = el("div");
            info.append(
                el("strong", candidate.name),
                el("p", `${candidate.execution.runner} · ${candidate.execution.executionId}`),
                el(
                    "p",
                    `Runner reported ${candidate.execution.reportedStatus}; trace assertions: ${candidate.outcome.status}.`,
                ),
            );
            if (candidate.coverage.gaps.length)
                info.append(
                    el(
                        "p",
                        `${candidate.coverage.matched}/${candidate.coverage.requested} traces matched. Missing evidence is not a passing result.`,
                    ),
                );
            if (candidate.unmappedServices.length)
                info.append(
                    el(
                        "p",
                        `Unmapped: ${candidate.unmappedServices.join(", ")}. Add mappings below and re-import.`,
                    ),
                );
            const add = button(
                "Add to workflow",
                () => addCandidate(row, candidate, "test"),
                "we-button we-button--primary",
            );
            if (!candidate.services.length) {
                add.disabled = true;
                add.dataset.noEvidence = "true";
                info.append(
                    el(
                        "p",
                        "No trace-backed services found. Connect your test runner using the guide below.",
                    ),
                );
            }
            box.append(info, add);
            q("test-results").append(box);
        }
    }
    q("test-file").addEventListener("change", () =>
        run(async () => {
            testFile = null;
            showTests(null);
            q("test-history").value = "";
            q("test-preview").replaceChildren();
            const file = q("test-file").files[0];
            if (!file) return;
            q("test-file-name").textContent = file.name;
            if (file.size > 8 * 1024 * 1024) throw new Error("Choose an evidence export smaller than 8 MiB.");
            let data;
            try {
                data = JSON.parse(await file.text());
            } catch {
                throw new Error(
                    "Choose an evidence JSON export or HTTP probe definition. Test source files, JUnit XML and Playwright trace.zip need the runner integration below.",
                );
            }
            if (data.namespace && data.namespace !== boundNamespace)
                throw new Error(
                    `This file targets ${data.namespace}. Open a workflow in that namespace first.`,
                );
            const probe = !!data.executionDefinition;
            if (!probe && !Array.isArray(data.executions))
                throw new Error(
                    "This file has no execution metadata. Use the test-runner export helper; test names alone cannot identify distributed workflows.",
                );
            q("test-preview").append(
                el(
                    "strong",
                    probe
                        ? data.testName || "HTTP workflow probe"
                        : `${data.executions.length} test execution(s)`,
                ),
            );
            for (const e of probe ? [data] : data.executions)
                q("test-preview").append(
                    el(
                        "p",
                        `${e.testName || "Unnamed test"} → ${e.applicationUrl || "Target required"} (${e.entryService || "Entry service required"})`,
                    ),
                );
            if (data.serviceMap) q("test-map").value = JSON.stringify(data.serviceMap, null, 2);
            if (data.jaegerUrl) q("test-jaeger").value = data.jaegerUrl;
            testFile = data;
            testFileBinding = `${boundNamespace}:${binding}`;
            q("test-submit").textContent = probe ? "Run HTTP probe & find calls" : "Import test evidence";
            if (probe)
                q("test-preview").append(
                    el(
                        "p",
                        "Runs only this explicit HTTP sequence against its target. Review its requests and success conditions before running it.",
                    ),
                );
        }),
    );
    q("test-submit").addEventListener("click", () =>
        run(async () => {
            if (!testFile || !isBound()) return;
            let serviceMap;
            try {
                serviceMap = JSON.parse(q("test-map").value || "{}");
            } catch {
                throw new Error(
                    "Service mappings must be a JSON object of trace name to Kubernetes service name.",
                );
            }
            const probe = !!testFile.executionDefinition,
                data = { ...testFile, namespace: boundNamespace, connectionFingerprint: binding, serviceMap };
            data.jaegerUrl = q("test-jaeger").value.trim() || null;
            if (probe && !data.jaegerUrl) {
                q("test-jaeger").closest("details").open = true;
                throw new Error("Set the Jaeger query URL below before running a traced HTTP probe.");
            }
            const row = await request(probe ? "/test-evidence/run" : "/test-evidence/import", "POST", data);
            testDocuments.unshift(row);
            options(
                q("test-history"),
                testDocuments.map((r) => [
                    r.id,
                    `${r.candidates.map((c) => c.name).join(", ")} · v${r.version}`,
                ]),
                row.id,
            );
            showTests(row);
        }),
    );
    q("test-history").addEventListener("change", () => {
        showTests(testDocuments.find((r) => r.id === q("test-history").value) || null);
        permissions();
    });
    q("test-refresh").addEventListener("click", () =>
        run(async () => {
            const row = await request(
                `/test-evidence/${encodeURIComponent(testDocument.id)}/refresh`,
                "POST",
            );
            testDocuments = testDocuments.map((r) => (r.id === row.id ? row : r));
            options(
                q("test-history"),
                testDocuments.map((r) => [
                    r.id,
                    `${r.candidates.map((c) => c.name).join(", ")} · v${r.version}`,
                ]),
                row.id,
            );
            showTests(row);
            say("Trace evidence refreshed. The application test was not run again.");
        }),
    );
    function recordingOptions() {
        const relevant = sessions.filter(
            (r) =>
                r.namespace === facts?.namespace && r.connectionFingerprint === facts?.connectionFingerprint,
        );
        options(
            q("recording"),
            relevant.map((r) => [r.id, `${r.state} · ${new Date(r.startedMicros / 1000).toLocaleString()}`]),
            recording?.id,
        );
    }
    function showRecording(row) {
        recording = row;
        const active = row?.state === "recording",
            stopped = row?.state === "stopped",
            coverage = row?.coverage || {},
            unresolved = (row?.candidates || []).filter((candidate) => !candidate.services.length || !candidate.entryService);
        q("start").hidden = active;
        q("stop").hidden = !active;
        q("refresh-recording").hidden = !stopped;
        q("record-symbol").dataset.recording = String(active);
        q("recording").value = row?.id || "";
        q("record-map").value = JSON.stringify(row?.serviceMap || {}, null, 2);
        q("start").textContent = stopped ? "Record more activity" : "Start Recording";
        q("record-title").textContent = active
            ? "Recording your application"
            : stopped
              ? `${row.candidates.length} workflow candidate${row.candidates.length === 1 ? "" : "s"} found`
              : "Ready when you are";
        q("record-guidance").textContent = active
            ? "Switch to your application and complete one task. Come back here when you’re done."
            : stopped
              ? coverage.status === "no_traces"
                  ? "Jaeger answered, but no traces matched these services in this window. Stock Bookinfo without OpenTelemetry or a mesh sidecar does not export traces — recording will stay empty until the app is instrumented. Manual workflows still work; nothing is invented."
                  : coverage.status === "trace_service_unavailable"
                    ? "Jaeger could not be read. Check the query URL below; your manual workflow is unaffected."
                    : unresolved.length
                      ? "Some observed services need an explicit mapping below. Apply mappings and refresh this same window; no names are guessed."
                      : row.candidates.length
                        ? "Choose a candidate to review its services and calls on the canvas."
                        : "Traces arrived, but no request entry points could be identified. Check context propagation and instrumentation."
              : "Open an application that already sends OpenTelemetry traces to Jaeger, then start recording one task. Recording does not install instrumentation.";
        q("recording-coverage").textContent = stopped
            ? `${coverage.tracesFetched || 0} trace${coverage.tracesFetched === 1 ? "" : "s"} · ${(coverage.servicesQueried || []).length} trace services queried${coverage.unmappedServices?.length ? ` · unmapped: ${coverage.unmappedServices.join(", ")}` : ""}`
            : "";
        for (let i = 1; i <= 3; i++)
            q(`record-step-${i}`).dataset.active = String(i === (active ? 2 : stopped ? 3 : 1));
        q("record-details").textContent = row ? JSON.stringify(row, null, 2) : "No recording selected.";
        const list = q("candidates");
        list.replaceChildren();
        for (const candidate of row?.candidates || []) {
            const box = el("div", null, "we-candidate"),
                info = el("div");
            info.append(
                el("strong", candidate.name),
                el("p", candidate.services.length ? `Kubernetes services: ${candidate.services.join(", ")}` : "No Kubernetes services mapped yet"),
                el("p", `${candidate.traceIds?.length || 0} traces · entry: ${candidate.traceEntryService || candidate.entryService || "unknown"}`),
                el("span", "◉ Observed · roles need review", "we-badge"),
            );
            if (candidate.unmappedServices?.length)
                info.append(el("p", `Unmapped trace services: ${candidate.unmappedServices.join(", ")}`));
            const add = button(
                "Add to workflow",
                () => addCandidate(row, candidate, "recording"),
                "we-button we-button--primary",
            );
            add.disabled = !candidate.services.length || !candidate.entryService;
            box.append(info, add);
            list.append(box);
        }
        updateTimer();
        permissions();
    }
    function updateTimer() {
        q("record-time").textContent =
            recording?.state === "recording"
                ? `Recording · ${Math.max(0, Math.floor((Date.now() - recording.startedMicros / 1000) / 1000))}s`
                : recording
                  ? `Recorded ${new Date(recording.startedMicros / 1000).toLocaleString()}`
                  : "";
    }
    async function discovery() {
        if (!binding) fresh();
        show("discovery");
        say();
        recordingOptions();
        showRecording(
            sessions.find(
                (r) =>
                    r.namespace === facts?.namespace &&
                    r.connectionFingerprint === facts?.connectionFingerprint &&
                    r.state === "recording",
            ) || null,
        );
        if (!q("jaeger").value) {
            const status = await request("/api/workflows/jaeger/status");
            q("jaeger").value = status.jaegerUrl || "";
            if (!status.healthy) {
                q("record-settings").open = true;
                say(status.message || "Tracing is unavailable. Check the tracing query URL below.", true);
            }
        }
        permissions();
    }
    function openDialog(mode, row = null) {
        dialogMode = mode;
        dialogRow = row;
        const state = row || model.state(),
            review = mode === "review",
            deleting = mode === "delete";
        q("dialog-title").textContent = review
            ? "Review & Save"
            : mode === "draft"
              ? "Save draft"
              : deleting
                ? "Delete workflow?"
                : "Rename workflow";
        q("dialog-submit").textContent = review
            ? "Save workflow"
            : mode === "draft"
              ? "Save draft"
              : deleting
                ? "Delete workflow"
                : "Save name";
        q("dialog-submit").className = `we-button ${deleting ? "we-button--danger" : "we-button--primary"}`;
        q("dialog-name-wrap").hidden = deleting;
        q("dialog-name").required = !deleting;
        q("dialog-name").value = state.name || "";
        let configuredActor = "";
        try { configuredActor = window.localStorage?.getItem("resilience.audit.name") || ""; } catch (_) { /* Optional audit preference. */ }
        q("actor").value = configuredActor || q("actor").value || row?.actor || documentRow?.actor || "";
        q("operational-review-wrap").hidden = !review;
        q("operational-use").checked = false;
        q("operational-statement").value = "";
        q("criticality").value = state.criticality ?? "";
        q("criticality-wrap").hidden = mode === "rename" || deleting;
        q("criticality").required = review;
        q("review-settings").open = !q("actor").value || (review && state.criticality == null);
        q("review-settings").querySelector("summary").textContent =
            deleting || mode === "rename" ? "Revision author" : "Advanced review settings";
        q("review-summary").className = "we-review-summary";
        q("review-summary").textContent = deleting
            ? `“${state.name}” will leave the active list. Previous versions and evidence are preserved.`
            : `${state.nodes.length} services · ${state.edges.length} connections · ${state.entryService ? `Entry: ${state.entryService}` : "Entry not chosen"}`;
        q("review-issues").replaceChildren();
        if (review)
            for (const issue of reviewIssues(state)) {
                const item = el("div", null, "we-review-issue");
                item.append(
                    el("span", issue.message),
                    button(
                        "Fix",
                        () => {
                            q("dialog").close();
                            selected =
                                issue.edge != null
                                    ? { edge: issue.edge }
                                    : issue.service
                                      ? { service: issue.service }
                                      : null;
                            render();
                            if (!state.nodes.length) q("search").focus();
                        },
                        "we-text-button",
                    ),
                );
                q("review-issues").append(item);
            }
        q("review-note").hidden = deleting || mode === "rename";
        q("dialog-error").hidden = true;
        q("dialog").showModal();
        permissions();
    }
    async function submit() {
        if (!q("actor").value.trim() || (dialogMode === "review" && !q("criticality").value))
            q("review-settings").open = true;
        const mode = dialogMode,
            row = dialogRow,
            actor = q("actor").value.trim();
        if (!actor) throw new Error("Enter the name recorded with this revision.");
        if (mode === "delete") {
            await request(`/workflows/${encodeURIComponent(row.id)}/delete`, "POST", {
                expectedVersion: row.version,
                actor,
            });
            await loadLists();
            q("dialog").close();
            say("Workflow deleted. Its version history is preserved.");
            return;
        }
        if (mode === "rename") {
            await request(`/workflows/${encodeURIComponent(row.id)}`, "PUT", {
                ...editable(row),
                name: q("dialog-name").value.trim(),
                actor,
                namespace: row.namespace,
                connectionFingerprint: row.connectionFingerprint,
                expectedVersion: row.version,
                reviewed: row.reviewed,
            });
            await loadLists();
            q("dialog").close();
            return;
        }
        if (!isBound() || historical) throw new Error("Reconnect to this workflow’s cluster before saving.");
        if (mode === "review" && reviewIssues(model.state()).length)
            throw new Error("Resolve the highlighted connections first.");
        const state = editable(model.state());
        if (mode === "review" && q("operational-use").checked && q("operational-statement").value.trim().length < 20)
            throw new Error("Describe how this exact real workflow definition was verified (at least 20 characters).");
        state.name = q("dialog-name").value.trim();
        state.criticality = q("criticality").value === "" ? null : Number(q("criticality").value);
        const body = {
            ...state,
            actor,
            namespace: boundNamespace,
            connectionFingerprint: binding,
            expectedVersion: documentRow?.version || 0,
            reviewed: mode === "review",
        };
        const result = await request(
            documentRow ? `/workflows/${encodeURIComponent(documentRow.id)}` : "/workflows",
            documentRow ? "PUT" : "POST",
            body,
        );
        documentRow = result;
        model.load(result);
        if (mode === "review" && q("operational-use").checked) {
            const reviews = await request("/operational-reviews");
            const prior = reviews.find((r) => r.kind === "managed" && r.workflowId === result.id && r.namespace === boundNamespace);
            await request("/operational-reviews", "POST", { workflowId: result.id, kind: "managed",
                sourceVersion: String(result.version), namespace: boundNamespace, connectionFingerprint: binding,
                expectedVersion: prior?.version || 0, actor, statement: q("operational-statement").value.trim() });
        }
        try { window.localStorage?.setItem("resilience.audit.name", actor); } catch (_) { /* Optional audit preference. */ }
        transientEvidence = result.edges;
        historical = false;
        q("dialog").close();
        await loadLists();
        render();
        show("saved");
        q("saved-title").textContent = mode === "review" ? "Workflow saved" : "Draft saved";
        q("saved-message").textContent =
            mode === "review"
                ? q("operational-use").checked
                    ? `“${result.name}” is reviewed for operational predictions on this cluster and application. Dependencies remain operator-declared; they are not experimentally validated.`
                    : `“${result.name}” is saved as a reviewed declaration. Operational analysis requires an explicit review of the real application.`
                : `“${result.name}” is saved as version ${result.version}. Continue editing when you’re ready to review it.`;
        q("risk").hidden = mode !== "review";
        q("continue").className = `we-button ${mode === "review" ? "" : "we-button--primary"}`;
    }
    async function openHistory() {
        history = await request(`/workflows/${encodeURIComponent(documentRow.id)}/history`);
        options(
            q("history"),
            history.map((r) => [
                String(r.version),
                `v${r.version} · ${r.deleted ? "deleted" : r.reviewed ? "reviewed declaration" : "draft"} · ${new Date(r.updatedAt).toLocaleString()}`,
            ]),
            String(documentRow.version),
            null,
        );
        historyEvidence();
        q("history-dialog").showModal();
        permissions();
    }
    function historyEvidence() {
        const row = history.find((r) => String(r.version) === q("history").value) || documentRow;
        q("provenance").textContent = JSON.stringify(row, null, 2);
        q("conflicts").replaceChildren();
        if (row?.conflicts?.length) {
            q("conflicts").append(
                el("p", "Observed calls differ from this declaration. An omitted call is not proven absent."),
            );
            for (const c of row.conflicts)
                q("conflicts").append(el("p", `${c.source} → ${c.target}: ${c.reason}`));
        }
        permissions();
    }
    for (const key of ["dialog", "history-dialog"])
        q(key).addEventListener("cancel", (e) => {
            if (busy) e.preventDefault();
        });
    root.addEventListener("click", (event) => {
        root.querySelectorAll(".we-menu[open]").forEach((menu) => {
            if (!menu.contains(event.target) || event.target.closest("button")) menu.open = false;
        });
    });
    const on = (key, action) => q(key).addEventListener("click", action);
    on("new", () => {
        if (mayLeave()) fresh();
    });
    on("discover", () => {
        fresh();
        run(discovery);
    });
    on("tests", () => {
        fresh();
        run(openTests);
    });
    on("add-recording", () => run(discovery));
    on("add-tests", () => run(openTests));
    on("add-manually", () => {
        q("layout").dataset.library = "true";
        selected = null;
        render();
        q("search").focus();
    });
    for (const key of ["tests-back", "discovery-back"])
        on(key, () => {
            show("editor");
            render();
        });
    on("refresh", () => run(refresh));
    on("duplicate", () =>
        duplicate({
            ...model.state(),
            connectionFingerprint: binding,
            namespace: boundNamespace,
        }),
    );
    for (const key of ["editor-back", "saved-back"])
        on(key, () => {
            if (mayLeave()) {
                model.load({});
                binding = null;
                boundNamespace = null;
                selected = null;
                show("manager");
                renderManager();
                say();
            }
        });
    on("continue", () => {
        show("editor");
        render();
    });
    on("show-all", () => {
        showAll = !showAll;
        renderManager();
    });
    for (const key of ["research", "editor-research", "record-research", "legend-research"])
        on(key, research);
    q("namespace").addEventListener("change", () =>
        run(async () => {
            recording = null;
            say();
            if (q("namespace").value) await loadCatalog(q("namespace").value, ++generation);
            else {
                facts = null;
                renderManager();
            }
            recordingOptions();
        }),
    );
    q("name").addEventListener("input", () => {
        model.change((s) => {
            s.name = q("name").value;
        });
        updateVersion();
    });
    q("search").addEventListener("input", () => {
        serviceLibrary();
        permissions();
    });
    for (const key of ["undo", "redo"])
        on(key, () => {
            model[key]();
            selected = null;
            connecting = null;
            render();
        });
    on("inspector-close", () => {
        selected = null;
        render();
    });
    on("library-close", () => {
        q("layout").dataset.library = "false";
    });
    on("library-toggle", () => {
        q("layout").dataset.library = q("layout").dataset.library === "false" ? "true" : "false";
    });
    on("empty-add", () => {
        q("layout").dataset.library = "true";
        q("search").focus();
    });
    on("zoom-in", () => zoom(1.2));
    on("zoom-out", () => zoom(1 / 1.2));
    on("fit", fit);
    on("save", () => openDialog("draft"));
    on("review", () => openDialog("review"));
    q("dialog-form").addEventListener("submit", (e) => {
        e.preventDefault();
        if (!q("actor").value.trim() || (dialogMode === "review" && q("criticality").value === ""))
            q("review-settings").open = true;
        if (q("dialog-form").reportValidity()) run(submit);
    });
    for (const key of ["dialog-close", "dialog-cancel"]) on(key, () => q("dialog").close());
    on("history-open", () => run(openHistory));
    on("history-close", () => q("history-dialog").close());
    q("history").addEventListener("change", historyEvidence);
    on("load-version", () =>
        run(async () => {
            if (!mayLeave()) return;
            const row = history.find((r) => String(r.version) === q("history").value);
            if (!row) return;
            documentRow = row;
            historical = row.version !== Math.max(...history.map((r) => r.version)) || row.deleted;
            model.load(row);
            transientEvidence = row.edges;
            selected = null;
            q("history-dialog").close();
            render();
            fit();
            if (historical) say("Historical version opened. Duplicate it to save edits as a new workflow.");
        }),
    );
    on("export", () =>
        run(async () => {
            const version = q("history").value,
                payload = await request(
                    `/workflows/${encodeURIComponent(documentRow.id)}/graph?version=${encodeURIComponent(version)}`,
                );
            const url = URL.createObjectURL(
                    new Blob([JSON.stringify(payload, null, 2)], {
                        type: "application/json",
                    }),
                ),
                a = el("a");
            a.href = url;
            a.download = `workflow-${documentRow.id}-v${version}.json`;
            a.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        }),
    );
    function recordingServiceMap() {
        let value;
        try {
            value = JSON.parse(q("record-map").value || "{}");
        } catch {
            throw new Error("Service mappings must be a JSON object of trace name to Kubernetes service name.");
        }
        if (!value || Array.isArray(value) || typeof value !== "object" || Object.values(value).some((name) => typeof name !== "string"))
            throw new Error("Service mappings must be a JSON object of trace name to Kubernetes service name.");
        return value;
    }
    on("start", () =>
        run(async () => {
            if (!facts) throw new Error("Select a connected application first.");
            if (
                sessions.some(
                    (r) =>
                        r.state === "recording" &&
                        r.namespace === facts.namespace &&
                        r.connectionFingerprint === facts.connectionFingerprint,
                )
            )
                throw new Error(
                    "A recording is already open. Choose it in Recording settings and stop it first.",
                );
            const row = await request("/recordings", "POST", {
                namespace: facts.namespace,
                connectionFingerprint: facts.connectionFingerprint,
                jaegerUrl: q("jaeger").value.trim(),
                serviceMap: recordingServiceMap(),
            });
            sessions.unshift(row);
            recordingOptions();
            showRecording(row);
            say();
        }),
    );
    const stop = () =>
        run(async () => {
            if (
                !recording ||
                recording.namespace !== facts?.namespace ||
                recording.connectionFingerprint !== facts?.connectionFingerprint
            )
                throw new Error("Reconnect to the recording’s cluster and namespace first.");
            const serviceMap = recordingServiceMap();
            q("record-guidance").textContent = "Reading calls from the fixed recording window…";
            const row = await request(`/recordings/${encodeURIComponent(recording.id)}/stop`, "POST", { serviceMap });
            sessions = sessions.map((r) => (r.id === row.id ? row : r));
            recordingOptions();
            showRecording(row);
        });
    on("stop", stop);
    on("refresh-recording", stop);
    q("jaeger").addEventListener("input", permissions);
    q("recording").addEventListener("change", () =>
        showRecording(sessions.find((r) => r.id === q("recording").value) || null),
    );
    q("canvas").addEventListener("dragover", (e) => {
        if (isBound()) e.preventDefault();
    });
    q("canvas").addEventListener("drop", (e) => {
        e.preventDefault();
        const service = e.dataTransfer.getData("text/plain");
        if (facts?.services.some((s) => s.name === service)) addService(service, world(e.clientX, e.clientY));
    });
    q("canvas").addEventListener("pointerdown", (e) => {
        if (e.button !== 0 || e.target.closest(".we-node,.we-edge-hit,button") || busy) return;
        selected = null;
        connecting = null;
        let moved = false;
        const origin = { x: viewport.x, y: viewport.y };
        const pan = (ev) => {
            if (Math.hypot(ev.clientX - e.clientX, ev.clientY - e.clientY) > 3) moved = true;
            viewport.x = origin.x + ev.clientX - e.clientX;
            viewport.y = origin.y + ev.clientY - e.clientY;
            transform();
        };
        const end = () => {
            window.removeEventListener("pointermove", pan);
            window.removeEventListener("pointerup", end);
            window.removeEventListener("pointercancel", end);
            if (!moved) render();
        };
        window.addEventListener("pointermove", pan);
        window.addEventListener("pointerup", end);
        window.addEventListener("pointercancel", end, { once: true });
    });
    q("canvas").addEventListener(
        "wheel",
        (e) => {
            e.preventDefault();
            if (e.ctrlKey || e.metaKey) {
                const r = q("canvas").getBoundingClientRect();
                zoom(Math.exp(-e.deltaY * 0.004), e.clientX - r.left, e.clientY - r.top);
            } else {
                viewport.x -= e.deltaX;
                viewport.y -= e.deltaY;
                transform();
            }
        },
        { passive: false },
    );
    document.addEventListener("keydown", (e) => {
        if (
            view !== "editor" ||
            busy ||
            q("dialog").open ||
            q("history-dialog").open ||
            e.target.closest("input,textarea,select,[contenteditable]")
        )
            return;
        if (e.key === "Escape") {
            connecting = null;
            selected = null;
            render();
        }
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
            e.preventDefault();
            model[e.shiftKey ? "redo" : "undo"]();
            selected = null;
            render();
        }
        if ((e.key === "Delete" || e.key === "Backspace") && selected) {
            e.preventDefault();
            if (selected.service) model.remove(selected.service);
            else
                model.change((s) => {
                    if (!isProtected(s.edges[selected.edge])) s.edges.splice(selected.edge, 1);
                });
            selected = null;
            render();
        }
    });
    window.addEventListener("resize", sizeWorkspace);
    window.addEventListener("beforeunload", (e) => {
        if (model.dirty()) {
            e.preventDefault();
            e.returnValue = "";
        }
    });
    window.addEventListener("resilience-cluster-changed", () => {
        if (!busy) run(refresh);
    });
    const timer = setInterval(updateTimer, 1000);
    window.addEventListener("pagehide", () => clearInterval(timer), {
        once: true,
    });
    render();
    renderManager();
    run(refresh);
})();
