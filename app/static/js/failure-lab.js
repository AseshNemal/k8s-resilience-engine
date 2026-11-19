/*
 * Failure lab: stop the workers you choose, together, and watch what your application does. Owned by: Member 4 (L.A. Nemal).
 *
 * The person picks where (their own cluster, or a separate test cluster), which workers (any number; a control plane is never offered),
 * and how long to hold them down. The server previews what that does to every service and what else on the cluster would go down, and the
 * person approves THAT plan once: no name is asked, because it is their own device. Essential cluster services that would go down are
 * accepted by name, never silently. While it runs the failure monitor shows the measured behaviour, and "Restore now" ends the hold and
 * starts every worker again. After it, a readable report can be opened or downloaded.
 *
 * It draws what the server returns (``/api/recommendations/validation-loop/*``) and decides nothing about safety: the server's checks,
 * its approval bounds and its recovery do. Nothing starts until the person presses the stop button. All text goes through textContent.
 */
(function (global) {
    "use strict";

    const BASE = "/api/recommendations/validation-loop";
    const POLL_MS = 2000;
    const HOLDS = [[30, "30 seconds"], [60, "1 minute"], [120, "2 minutes"], [300, "5 minutes"]];
    const LIMITS = { min: 10, max: 1800 };       // seconds; the server enforces the same bounds (``lab_hold_min_s`` / ``lab_hold_max_s``)
    const TARGETS = { DOCKER_DESKTOP: "My cluster (Docker Desktop)", DISPOSABLE: "A separate test cluster" };
    const OUTCOME = { down: "goes offline", degraded: "loses some replicas", ok: "unaffected", none: "has no ready replica today" };

    // ---- pure (exported for tests) ----------------------------------------------------------------------------------------------

    /** The workers a person may choose: whatever the cluster lists, minus control planes. The server checks again. */
    function chooseable(nodes) {
        return (nodes || []).filter((name) => !/control-plane|controlplane|master/i.test(name)).sort();
    }

    function canPreview(form) {
        return form.nodes.size > 0 && Number(form.hold) >= LIMITS.min && Number(form.hold) <= LIMITS.max;
    }

    /** A typed time as whole seconds: {seconds} when it is allowed, {problem} (in words) when it is not. Nothing is guessed. */
    function parseHold(amount, unit) {
        const typed = String(amount === undefined || amount === null ? "" : amount).trim();
        if (!typed) return { seconds: null, problem: "Type how long to keep the workers stopped." };
        const n = Number(typed);
        if (!Number.isFinite(n) || n <= 0) return { seconds: null, problem: "Use a number greater than zero." };
        const seconds = Math.round(unit === "minutes" ? n * 60 : n);
        if (seconds < LIMITS.min) return { seconds: null, problem: `Too short. Use at least ${LIMITS.min} seconds.` };
        if (seconds > LIMITS.max) return { seconds: null, problem: `Too long. Use at most ${LIMITS.max / 60} minutes.` };
        return { seconds, problem: null };
    }

    /** "45 seconds", "1 minute", "2 minutes 30 seconds". */
    function humanSeconds(total) {
        const t = Math.round(Number(total));
        const m = Math.floor(t / 60), s = t % 60;
        const part = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
        return m && s ? `${part(m, "minute")} ${part(s, "second")}` : m ? part(m, "minute") : part(s, "second");
    }

    function planBody(form, namespace) {
        const url = (form.url || "").trim();
        return {
            targetKind: form.target, mode: "LAB", workflowId: "lab", workflowSource: "FROZEN", namespace, nodes: [...form.nodes].sort(), holdSeconds: Number(form.hold),
            probe: url ? { url, expectedStatus: 200, mustContain: [], mustNotContain: [] } : null,
        };
    }

    function canStart(plan, acknowledged, desktopAck) {
        if (!plan || !plan.canApprove) return false;
        if ((plan.acknowledgementsNeeded || []).some((name) => !acknowledged.has(name))) return false;
        return !(plan.target && plan.target.kind === "DOCKER_DESKTOP" && !desktopAck);
    }

    function elapsedText(run, now) {
        const t = run && run.timing && run.timing.elapsedS;
        return t === null || t === undefined ? "" : `${Math.round(t)} s so far`;
    }

    // ---- DOM ------------------------------------------------------------------------------------------------------------------------

    function make(doc, tag, text, className, attributes) {
        const node = doc.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        for (const [k, v] of Object.entries(attributes || {})) node.setAttribute(k, v);
        return node;
    }

    /** ``visual``: the simple page ("Fail a node"): workers drawn as servers with their pods, services as chips, and the application read here. */
    function createPanel({ root, document: doc = global.document, fetchImpl = (...a) => global.fetch(...a), namespace = () => null, visual = false,
                          timers = { set: (fn, ms) => global.setTimeout(fn, ms), clear: (id) => global.clearTimeout(id) } }) {
        const form = { target: "DOCKER_DESKTOP", nodes: new Set(), hold: 60, url: "", custom: false, customAmount: "", customUnit: "seconds" };
        const state = { phase: "loading", targets: null, plan: null, run: null, message: null, busy: false, ack: new Set(), desktopAck: false,
            timelines: {}, asked: new Set(), pick: null, placement: null, ns: null, namespaces: [] };
        const ns = () => namespace() || state.ns;
        const V = () => global.ResilienceFailVisual;
        const hues = () => (global.ResiliencePlacement && state.placement ? global.ResiliencePlacement.hues(state.placement.map((row) => row.service)) : {});
        let timer = null, destroyed = false;

        async function call(path, init) {
            const response = await fetchImpl(`${BASE}${path}`, { headers: { Accept: "application/json", ...(init && init.body ? { "Content-Type": "application/json" } : {}) }, ...init });
            let body = null;
            try { body = await response.json(); } catch (_e) { body = null; }
            if (!response.ok) {
                const d = body && body.detail;
                throw new Error((d && d.message) || (typeof d === "string" ? d : `The backend answered HTTP ${response.status}.`));
            }
            return body;
        }

        const unwatch = () => { if (timer !== null) { timers.clear(timer); timer = null; } };
        const watch = () => { unwatch(); if (!destroyed && state.run && ["RUNNING", "ABORTING"].includes(state.run.status)) timer = timers.set(poll, POLL_MS); };

        async function poll() {
            timer = null;
            try {
                const body = await call("/current");
                const run = body.run;
                if (run && run.mode === "LAB") { state.run = run; if (state.phase !== "setup" && state.phase !== "plan") state.phase = "run"; }
            } catch (error) { state.message = `Could not read the test just now: ${error.message}`; }
            draw(); watch();
        }

        // ---- actions ----

        async function load() {
            if (visual) await readApplication(namespace());
            try {
                state.targets = (await call("/targets")).targets;
                const current = (await call("/current")).run;
                if (current && current.mode === "LAB") { state.run = current; state.phase = "run"; } else state.phase = "setup";
            } catch (error) { state.phase = "setup"; state.message = error.message; }
            draw(); watch();
        }

        /** The application as it runs now (the same read the Failure impact page makes): where every ready replica is. Read only. */
        async function readApplication(chosen) {
            try {
                const response = await fetchImpl(`/api/failure-impacts/overview${chosen ? `?namespace=${encodeURIComponent(chosen)}` : ""}`, { headers: { Accept: "application/json" } });
                if (!response.ok) throw new Error(String(response.status));
                const body = await response.json();
                state.placement = body.services || null; state.ns = body.namespace || chosen || null; state.namespaces = body.namespaces || [];
            } catch (_error) {
                state.placement = null;      // the pods are simply not drawn; the test itself does not need them
            }
        }

        async function preview() {
            if (state.busy || !canPreview(form)) return;
            state.busy = true; state.message = null; draw();
            try { state.plan = await call("/plan", { method: "POST", body: JSON.stringify(planBody(form, ns())) }); state.ack = new Set(); state.desktopAck = false; state.phase = "plan"; }
            catch (error) { state.message = error.message; }
            state.busy = false; draw();
        }

        async function begin() {
            if (state.busy || !canStart(state.plan, state.ack, state.desktopAck)) return;
            state.busy = true; state.message = null; draw();
            try {
                const body = await call("/runs", { method: "POST", body: JSON.stringify({ plan: planBody(form, ns()), planDigest: state.plan.planDigest,
                    acknowledgedWorkloads: [...state.ack], desktopAcknowledged: state.desktopAck }) });
                state.run = body.run || body; state.phase = "run";
            } catch (error) { state.message = error.message; }
            state.busy = false; draw(); watch();
        }

        async function restoreNow() {
            if (state.busy || !state.run) return;
            state.busy = true; draw();
            try { state.run = (await call(`/runs/${encodeURIComponent(state.run.runKey)}/abort`, { method: "POST" })).run; } catch (error) { state.message = error.message; }
            state.busy = false; draw(); watch();
        }

        async function recover() {
            if (state.busy || !state.run) return;
            state.busy = true; draw();
            try { state.run = (await call(`/runs/${encodeURIComponent(state.run.runKey)}/recover`, { method: "POST" })).run; state.message = null; } catch (error) { state.message = error.message; }
            state.busy = false; draw();
        }

        function another() { unwatch(); state.run = null; state.plan = null; state.phase = "setup"; state.message = null; state.timelines = {}; state.asked = new Set(); draw(); }

        // ---- drawing ----

        function msg(box) { if (state.message) box.append(make(doc, "p", state.message, "fl-message", { role: "alert" })); }
        function button(label, className, onClick, disabled, role) {
            const b = make(doc, "button", label, className, { type: "button", ...(role ? { "data-role": role } : {}) });
            b.disabled = !!disabled; b.addEventListener("click", onClick); return b;
        }

        /** The time choices. ``changed`` runs after a typed time changes, so the caller can update its button without a redraw (which would lose focus). */
        function holdButtons(box, changed) {
            const group = make(doc, "div", undefined, "fv-holds", { role: "group", "aria-label": "How long to keep them stopped" });
            HOLDS.forEach(([seconds, text]) => {
                const on = !form.custom && Number(form.hold) === seconds;
                const b = make(doc, "button", text, `fv-hold${on ? " fv-hold--on" : ""}`, { type: "button", "aria-pressed": String(on) });
                b.addEventListener("click", () => { form.custom = false; form.hold = seconds; draw(); });
                group.append(b);
            });
            const custom = make(doc, "button", "Custom", `fv-hold${form.custom ? " fv-hold--on" : ""}`, { type: "button", "aria-pressed": String(form.custom), "data-role": "fl-custom" });
            custom.addEventListener("click", () => {
                form.custom = true;
                if (!form.customAmount) { form.customAmount = String(form.hold); form.customUnit = "seconds"; }
                const typed = parseHold(form.customAmount, form.customUnit); form.hold = typed.seconds === null ? NaN : typed.seconds; draw();
            });
            group.append(custom);
            box.append(group);
            if (!form.custom) return;
            const row = make(doc, "div", undefined, "fv-custom");
            const amount = make(doc, "input", undefined, "form-input fv-custom__amount", { type: "number", min: "1", step: "any", inputmode: "decimal", "aria-label": "How long", "aria-describedby": "fv-custom-hint", "data-role": "fl-custom-amount" });
            amount.value = form.customAmount;
            const unit = make(doc, "select", undefined, "form-input", { "aria-label": "Unit", "data-role": "fl-custom-unit" });
            [["seconds", "seconds"], ["minutes", "minutes"]].forEach(([v, t]) => { const o = make(doc, "option", t, undefined, { value: v }); if (form.customUnit === v) o.setAttribute("selected", ""); unit.append(o); });
            unit.value = form.customUnit;
            const problem = make(doc, "span", undefined, "fl-hint fv-custom__problem", { id: "fv-custom-hint", role: "status" });
            const apply = () => {
                form.customAmount = amount.value; form.customUnit = unit.value;
                const typed = parseHold(amount.value, unit.value);
                form.hold = typed.seconds === null ? NaN : typed.seconds;
                problem.textContent = typed.problem || `${humanSeconds(typed.seconds)}. Allowed: ${LIMITS.min} seconds to ${LIMITS.max / 60} minutes.`;
                problem.className = `fl-hint fv-custom__problem${typed.problem ? " fv-custom__problem--bad" : ""}`;
                if (changed) changed();
            };
            amount.addEventListener("input", apply); unit.addEventListener("change", apply);
            row.append(make(doc, "span", "Keep them stopped for"), amount, unit, problem);
            box.append(row);
            apply();
        }

        function allNodes() {
            const target = (state.targets || []).find((t) => t.kind === form.target);
            const names = (target && target.identity && target.identity.nodes) || [];
            const open = new Set(chooseable(names));
            return [...names].sort().map((name) => ({ name, locked: !open.has(name) }));
        }

        function drawVisualSetup(box) {
            box.append(make(doc, "h4", "1 · Pick the workers to stop", "fl-sub"));
            box.append(make(doc, "p", "Each square is a running copy of one of your services. Tap a worker to choose it.", "fl-hint"));
            const available = (state.targets || []).filter((t) => t.available);
            if (!available.length) return drawNoCluster(box);
            const colours = hues();
            box.append(V().nodeTiles(doc, { nodes: allNodes(), placement: state.placement, hues: colours, picked: form.nodes, running: false,
                onToggle: (name) => { if (form.nodes.has(name)) form.nodes.delete(name); else form.nodes.add(name); draw(); } }));
            if (Object.keys(colours).length) box.append(V().legend(doc, colours));
            if (state.namespaces.length > 1) {
                const field = make(doc, "label", undefined, "fl-field");
                field.append(make(doc, "span", "Application"));
                const select = make(doc, "select", undefined, "form-input");
                state.namespaces.forEach((n) => { const o = make(doc, "option", n, undefined, { value: n }); if (n === state.ns) o.setAttribute("selected", ""); select.append(o); });
                select.value = state.ns || "";
                select.addEventListener("change", async () => { await readApplication(select.value); form.nodes = new Set(); draw(); });
                field.append(select); box.append(field);
            }
            box.append(make(doc, "h4", "2 · How long?", "fl-sub"));
            let previewButton = null;
            holdButtons(box, () => { if (previewButton) previewButton.disabled = state.busy || !canPreview(form); });
            const more = make(doc, "details", undefined, "fv-more");
            more.append(make(doc, "summary", "More options"));
            if (available.length > 1) {
                const where = make(doc, "label", undefined, "fl-field");
                where.append(make(doc, "span", "Where to run it"));
                const select = make(doc, "select", undefined, "form-input");
                available.forEach((t) => { const o = make(doc, "option", TARGETS[t.kind] || t.label, undefined, { value: t.kind }); if (t.kind === form.target) o.setAttribute("selected", ""); select.append(o); });
                select.addEventListener("change", () => { form.target = select.value; form.nodes = new Set(); draw(); });
                where.append(select); more.append(where);
            }
            const url = make(doc, "label", undefined, "fl-field");
            url.append(make(doc, "span", "Page address to measure real requests (optional)"));
            const input = make(doc, "input", undefined, "form-input", { type: "url", placeholder: "http://127.0.0.1:8080/productpage" });
            input.value = form.url; input.addEventListener("input", () => { form.url = input.value; });
            url.append(input, make(doc, "span", "Sends a real request every couple of seconds so you see what a user would see. Only addresses on this computer are allowed.", "fl-hint"));
            more.append(url);
            box.append(more);
            msg(box);
            previewButton = button(state.busy ? "Checking…" : "3 · See what would happen", "primary-button", preview, state.busy || !canPreview(form), "fl-preview");
            box.append(previewButton);
        }

        /** Nothing can be tested: say why, in the server's own words for each cluster, and offer to look again. */
        function drawNoCluster(box) {
            const panel = make(doc, "div", undefined, "fl-alert fv-nocluster", { role: "alert" });
            panel.append(make(doc, "strong", "No cluster can be tested right now."));
            const list = make(doc, "ul", undefined, "fv-nocluster__list");
            (state.targets || []).forEach((t) => list.append(make(doc, "li", `${TARGETS[t.kind] || t.label}: ${t.reason || "not available"}`)));
            if (!(state.targets || []).length) list.append(make(doc, "li", state.message ? `The clusters could not be read: ${state.message}` : "No cluster was listed."));
            panel.append(list);
            const row = make(doc, "div", undefined, "fl-row");
            row.append(button(state.busy ? "Checking…" : "Check again", "secondary-button", async () => { state.busy = true; state.phase = "loading"; draw(); await load(); state.busy = false; draw(); }, state.busy, "fl-recheck"));
            const link = Array.from(doc.querySelectorAll ? doc.querySelectorAll(".sidebar a[href]") : []).find((a) => /connect/.test(a.getAttribute("href") || ""));
            if (link) row.append(make(doc, "a", "Connect a cluster", "secondary-button fl-a", { href: link.getAttribute("href") }));
            panel.append(row);
            box.append(panel);
        }

        function drawVisualPlan(box) {
            const plan = state.plan;
            box.append(make(doc, "h4", `If ${plan.lab.nodes.join(", ")} stop${plan.lab.nodes.length === 1 ? "s" : ""} for ${humanSeconds(plan.lab.holdS)}`, "fl-sub"));
            box.append(V().nodeTiles(doc, { nodes: allNodes(), placement: state.placement, hues: hues(), picked: new Set(plan.lab.nodes), running: false, disabled: true }));
            if (plan.lab.allWorkersStopped) box.append(make(doc, "p", "Every worker would be stopped. Nothing is left to run your application until they start again.", "fl-alert", { role: "alert" }));
            box.append(V().serviceChips(doc, plan.lab.impact.services.map((x) => ({ service: x.service, state: x.state,
                text: x.state === "degraded" ? `keeps ${x.left} of ${x.ready}` : OUTCOME[x.state] }))));
            box.append(make(doc, "p", "This is a placement rule: a service goes offline when all of its ready copies run on the chosen workers.", "fl-hint"));
            const needed = plan.acknowledgementsNeeded || [];
            if (needed.length) {
                const ack = make(doc, "fieldset", undefined, "fl-group");
                ack.append(make(doc, "legend", "Other things on these workers would also go down. Tick each one to continue."));
                needed.forEach((name) => {
                    const label = make(doc, "label", undefined, "fl-node");
                    const input = make(doc, "input", undefined, undefined, { type: "checkbox" }); input.checked = state.ack.has(name);
                    input.addEventListener("change", () => { if (input.checked) state.ack.add(name); else state.ack.delete(name); draw(); });
                    label.append(input, make(doc, "span", name)); ack.append(label);
                });
                box.append(ack);
            }
            plan.issues.filter((i) => i.blocking).forEach((i) => box.append(make(doc, "p", i.message, "fl-alert", { role: "alert" })));
            if (plan.target && plan.target.kind === "DOCKER_DESKTOP") {
                const label = make(doc, "label", undefined, "fl-node fl-node--strong");
                const input = make(doc, "input", undefined, undefined, { type: "checkbox" }); input.checked = state.desktopAck;
                input.addEventListener("change", () => { state.desktopAck = input.checked; draw(); });
                label.append(input, make(doc, "span", "I understand these workers will really be stopped on my everyday cluster."));
                box.append(label);
            }
            msg(box);
            const row = make(doc, "div", undefined, "fl-row");
            row.append(button("Back", "secondary-button", () => { state.phase = "setup"; draw(); }),
                button(state.busy ? "Starting…" : "Stop these workers now", "danger-button", begin, state.busy || !canStart(plan, state.ack, state.desktopAck), "fl-start"));
            box.append(row);
        }

        function drawSetup(box) {
            if (visual) return drawVisualSetup(box);
            box.append(make(doc, "p", "Choose where to run it and which workers to stop. Nothing happens until you approve a preview.", "fl-lead"));
            const where = make(doc, "fieldset", undefined, "fl-group");
            where.append(make(doc, "legend", "Where"));
            (state.targets || []).forEach((t) => {
                const label = make(doc, "label", undefined, `fl-choice${t.available ? "" : " fl-choice--off"}`);
                const input = make(doc, "input", undefined, undefined, { type: "radio", name: "fl-target", value: t.kind });
                input.checked = form.target === t.kind; input.disabled = !t.available;
                input.addEventListener("change", () => { form.target = t.kind; form.nodes = new Set(); draw(); });
                const words = make(doc, "span", undefined, "fl-choice__text");
                words.append(make(doc, "strong", TARGETS[t.kind] || t.label), make(doc, "span", t.available ? t.warning : (t.reason || "Not available"), "fl-hint"));
                label.append(input, words);
                where.append(label);
            });
            box.append(where);
            const target = (state.targets || []).find((t) => t.kind === form.target);
            const workers = chooseable(target && target.identity && target.identity.nodes);
            const nodes = make(doc, "fieldset", undefined, "fl-group");
            nodes.append(make(doc, "legend", `Workers to stop (${form.nodes.size} of ${workers.length} chosen)`));
            if (!workers.length) nodes.append(make(doc, "p", "No worker was listed for this cluster.", "fl-hint"));
            workers.forEach((name) => {
                const label = make(doc, "label", undefined, "fl-node");
                const input = make(doc, "input", undefined, undefined, { type: "checkbox", value: name });
                input.checked = form.nodes.has(name);
                input.addEventListener("change", () => { if (input.checked) form.nodes.add(name); else form.nodes.delete(name); draw(); });
                label.append(input, make(doc, "span", name));
                nodes.append(label);
            });
            if (workers.length) {
                const row = make(doc, "div", undefined, "fl-row");
                row.append(button("Choose all", "fl-link", () => { workers.forEach((w) => form.nodes.add(w)); draw(); }), button("Choose none", "fl-link", () => { form.nodes = new Set(); draw(); }));
                nodes.append(row);
            }
            box.append(nodes);
            const hold = make(doc, "label", undefined, "fl-field");
            hold.append(make(doc, "span", "Hold them down for"));
            const select = make(doc, "select", undefined, "form-input");
            HOLDS.forEach(([s, t]) => { const o = make(doc, "option", t, undefined, { value: String(s) }); if (Number(form.hold) === s) o.setAttribute("selected", ""); select.append(o); });
            select.value = String(form.hold);
            select.addEventListener("change", () => { form.hold = Number(select.value); });
            hold.append(select);
            box.append(hold);
            const url = make(doc, "label", undefined, "fl-field");
            url.append(make(doc, "span", "Page address to measure requests (optional)"));
            const input = make(doc, "input", undefined, "form-input", { type: "url", placeholder: "http://127.0.0.1:8080/productpage" });
            input.value = form.url; input.addEventListener("input", () => { form.url = input.value; });
            url.append(input, make(doc, "span", "Real requests are sent every couple of seconds while the workers are down, so you can see what a user would see.", "fl-hint"));
            box.append(url);
            msg(box);
            box.append(button(state.busy ? "Checking…" : "Preview what this would do", "primary-button", preview, state.busy || !canPreview(form), "fl-preview"));
        }

        function drawPlan(box) {
            if (visual) return drawVisualPlan(box);
            const plan = state.plan;
            box.append(make(doc, "h4", `Preview: stop ${plan.lab.nodes.join(", ")} for ${plan.lab.holdS} s`, "fl-sub"));
            if (plan.lab.allWorkersStopped) box.append(make(doc, "p", "Every worker would be stopped. Nothing is left to run your application until they are started again.", "fl-alert", { role: "alert" }));
            const table = make(doc, "table", undefined, "data-table");
            const head = make(doc, "tr"); ["Service", "Ready replicas", "On the chosen workers", "If they stop"].forEach((h) => head.append(make(doc, "th", h, undefined, { scope: "col" })));
            const thead = make(doc, "thead"); thead.append(head); table.append(thead);
            const body = make(doc, "tbody");
            plan.lab.impact.services.forEach((s) => {
                const tr = make(doc, "tr", undefined, `fl-row--${s.state}`);
                tr.append(make(doc, "td", s.service), make(doc, "td", s.ready), make(doc, "td", s.lost), make(doc, "td", s.state === "degraded" ? `keeps ${s.left} of ${s.ready}` : OUTCOME[s.state]));
                body.append(tr);
            });
            table.append(body);
            const scroll = make(doc, "div", undefined, "table-scroll"); scroll.append(table); box.append(scroll);
            box.append(make(doc, "p", "This is the placement rule: a service goes offline when every ready replica ran on the chosen workers. It is a statement about placement, not a prediction about your workflow.", "fl-hint"));
            const needed = plan.acknowledgementsNeeded || [];
            if (needed.length) {
                const ack = make(doc, "fieldset", undefined, "fl-group");
                ack.append(make(doc, "legend", "Other workloads on these workers that would go down — accept each to continue"));
                needed.forEach((name) => {
                    const label = make(doc, "label", undefined, "fl-node");
                    const input = make(doc, "input", undefined, undefined, { type: "checkbox" }); input.checked = state.ack.has(name);
                    input.addEventListener("change", () => { if (input.checked) state.ack.add(name); else state.ack.delete(name); draw(); });
                    label.append(input, make(doc, "span", name)); ack.append(label);
                });
                box.append(ack);
            }
            plan.issues.filter((i) => i.blocking).forEach((i) => box.append(make(doc, "p", `${i.message}`, "fl-alert", { role: "alert" })));
            plan.issues.filter((i) => !i.blocking).forEach((i) => box.append(make(doc, "p", i.message, "fl-hint")));
            if (plan.target && plan.target.kind === "DOCKER_DESKTOP") {
                const label = make(doc, "label", undefined, "fl-node fl-node--strong");
                const input = make(doc, "input", undefined, undefined, { type: "checkbox" }); input.checked = state.desktopAck;
                input.addEventListener("change", () => { state.desktopAck = input.checked; draw(); });
                label.append(input, make(doc, "span", "I understand this is my everyday cluster and these workers will really be stopped."));
                box.append(label);
            }
            (plan.notes || []).forEach((n) => box.append(make(doc, "p", n, "fl-hint")));
            msg(box);
            const row = make(doc, "div", undefined, "fl-row");
            row.append(button("Back", "secondary-button", () => { state.phase = "setup"; draw(); }),
                button(state.busy ? "Starting…" : "Stop these workers now", "danger-button", begin, state.busy || !canStart(plan, state.ack, state.desktopAck), "fl-start"));
            box.append(row);
        }

        function wantTimeline(run, episode) {
            const key = ResilienceFailureMonitor().episodeKey(episode);
            if (state.timelines[key] || state.asked.has(key)) return;
            state.asked.add(key);
            call(`/runs/${encodeURIComponent(run.runKey)}/timeline?kind=${episode.kind}&worker=${encodeURIComponent(episode.worker)}&repeat=${episode.repeat}`)
                .then((body) => { state.timelines[key] = body.timeline; draw(); }).catch(() => { state.asked.delete(key); });
        }
        const ResilienceFailureMonitor = () => global.ResilienceFailureMonitor;

        function drawRun(box) {
            const run = state.run, lab = run.lab || {};
            const going = ["RUNNING", "ABORTING"].includes(run.status);
            const head = make(doc, "div", undefined, `fl-status fl-status--${going ? "run" : run.status === "COMPLETED" ? "ok" : "warn"}`, { role: "status" });
            head.append(make(doc, "strong", going ? (run.status === "ABORTING" ? "Restoring the workers…" : `Workers are being stopped and held: ${(lab.nodes || []).join(", ")}`) : (run.statusLabel || run.status)));
            if (going) head.append(make(doc, "span", `${(lab.stage || "").toLowerCase().replace(/_/g, " ")} ${lab.detail ? "· " + lab.detail : ""} · ${elapsedText(run)}`, "fl-hint"));
            box.append(head);
            if (run.stopReason) box.append(make(doc, "p", run.stopReason, "fl-hint"));
            if (run.safety && run.safety.level === "alert") {
                const alert = make(doc, "div", undefined, "fl-alert", { role: "alert" });
                alert.append(make(doc, "strong", run.safety.message));
                (run.safety.actions || []).forEach((a) => alert.append(make(doc, "code", a, "fl-code")));
                box.append(alert);
            }
            if (visual && going) {
                const now = V().liveNodes(run);
                box.append(V().nodeTiles(doc, { nodes: allNodes(), placement: state.placement, hues: hues(), running: true, stoppedOf: (name) => (name in now ? (now[name] === null ? null : now[name] === false) : null) }));
                const services = V().liveServices(run);
                if (services.length) box.append(V().serviceChips(doc, services.map((x) => ({ service: x.service, state: x.state,
                    text: x.state === "unknown" ? "unknown" : `${x.ready} of ${x.before} ready` }))));
            }
            const monitor = run.monitor, M = global.ResilienceFailureMonitor;
            if (M && monitor) {
                const picks = M.choices(monitor); const chosen = picks.find((p) => p.key === state.pick) || picks[0];
                if (chosen && chosen.episode) wantTimeline(run, chosen.episode);
                const holder = make(doc, "div");
                M.render(doc, holder, monitor, { selected: chosen && chosen.key, running: going, onSelect: (key) => { state.pick = key; draw(); }, timelineFor: (ep) => state.timelines[M.episodeKey(ep)] || null });
                box.append(holder);
            }
            msg(box);
            const row = make(doc, "div", undefined, "fl-row");
            if (going) row.append(button(state.busy || run.status === "ABORTING" ? "Restoring…" : "Restore now", "danger-button", restoreNow, state.busy || run.status === "ABORTING", "fl-restore"));
            else {
                if (run.status === "NEEDS_MANUAL_RESTORE") row.append(button("Try recovery again", "secondary-button", recover, state.busy));
                const base = `${BASE}/runs/${encodeURIComponent(run.runKey)}/report`;
                row.append(make(doc, "a", "Open report", "primary-button fl-a", { href: base, target: "_blank", rel: "noopener", "data-role": "fl-report" }),
                    make(doc, "a", "Download report", "secondary-button fl-a", { href: `${base}?download=true`, "data-role": "fl-download" }),
                    button("Run another test", "secondary-button", another, state.busy, "fl-another"));
            }
            box.append(row);
        }

        function draw() {
            if (destroyed) return;
            const box = make(doc, "div", undefined, "fl-body");
            if (state.phase === "loading") box.append(make(doc, "p", "Looking for clusters and any earlier test…", "fl-hint", { role: "status" }));
            else if (state.phase === "plan" && state.plan) drawPlan(box);
            else if (state.phase === "run" && state.run) drawRun(box);
            else drawSetup(box);
            root.replaceChildren(box);
        }

        draw();
        return { load, preview, begin, restoreNow, recover, another, form, state: () => state, poll, destroy: () => { destroyed = true; unwatch(); } };
    }

    global.ResilienceFailureLab = { createPanel, chooseable, canPreview, canStart, planBody, parseHold, humanSeconds, LIMITS };

    if (global.document && typeof global.document.querySelector === "function") {
        const mount = () => {
            const root = global.document.querySelector('[data-role="lab-panel"]');
            if (!root || root.getAttribute("data-mounted")) return;
            root.setAttribute("data-mounted", "true");
            const select = global.document.querySelector('[data-role="namespace-select"]');
            createPanel({ root, visual: root.getAttribute("data-visual") === "true", namespace: () => (select && select.value) || null }).load();
        };
        if (global.document.readyState === "loading") global.document.addEventListener("DOMContentLoaded", mount); else mount();
    }
})(typeof window !== "undefined" ? window : globalThis);
