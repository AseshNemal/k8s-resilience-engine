/*
 * The "Prove it on a test cluster" step of the Recommendations page. Owned by: Member 4 (L.A. Nemal).
 *
 * A candidate change is only a prediction until a worker node has really been stopped under it. This panel lets the person choose a
 * cluster and a depth (Smoke, Quick, Full), say how to tell that the workflow works, read exactly what a run would do and what else on
 * the cluster it would take down, approve that once, and then watch the run and read its result. Nothing here applies a change to the
 * person's own cluster: the separate Approve step below does that, and this step only tells it what was proven.
 *
 * Everything shown comes from the server: the plan is computed there from the target cluster, the run is stored there, and this
 * script draws what it is given. It invents no progress, no percentage, no time estimate and no success. A run is found again after
 * a reload or a restart of the extension from the database. Recovery, restoration and success are never claimed here before the
 * server's recorded evidence says so, and while an abort is under way the page says "Abort requested — recovering worker" until it
 * has ended.
 *
 * Requests are sequenced: a response older than one already shown is dropped. A failed request keeps the last recorded state on
 * screen, marks it stale, and says the backend cannot be reached. All text goes through textContent.
 */
(function () {
    "use strict";

    const BASE = "/api/recommendations/validation-loop";
    const POLL_MS = 2000;
    const MAX_BACKOFF = 8;
    const UNKNOWN = "unknown";

    const TARGET_NAME = { DOCKER_DESKTOP: "Docker Desktop", DISPOSABLE: "Test cluster" };
    const TARGET_CAPTION = {
        DOCKER_DESKTOP: "Your everyday cluster. Its workers are stopped on purpose.",
        DISPOSABLE: "A separate cluster. Yours is not touched.",
    };
    const MODES = [
        { id: "SMOKE", name: "Smoke", detail: "1 change · 1 worker · once", preliminary: true },
        { id: "QUICK", name: "Quick", detail: "best change · every worker · once", preliminary: true },
        { id: "FULL", name: "Full", detail: "best 2 changes · every worker · twice · baseline", preliminary: false },
    ];
    const CELL = {
        PASS: { glyph: "✓", text: "Pass", tone: "pass" }, FAIL: { glyph: "✕", text: "Fail", tone: "fail" },
        INCONCLUSIVE: { glyph: "!", text: "Inconclusive", tone: "warn" }, RUNNING: { glyph: "●", text: "Running", tone: "run" },
        PENDING: { glyph: "○", text: "Pending", tone: "wait" }, SKIPPED: { glyph: "–", text: "Skipped", tone: "skip" },
    };
    const RUN_TONE = { RUNNING: "run", ABORTING: "warn", COMPLETED: "pass", INTERRUPTED: "warn", FAILED: "fail", NEEDS_MANUAL_RESTORE: "fail", PLANNED: "wait" };

    // ---- small helpers (also exported for tests) --------------------------------------------------------------------------

    function make(doc, tag, text, className, attributes) {
        const node = doc.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, value);
        return node;
    }

    function errorMessage(result, fallback) {
        const detail = result && result.payload && result.payload.detail;
        if (detail && typeof detail === "object") return detail.message || detail.code || fallback;
        return typeof detail === "string" && result.status < 500 ? detail : fallback;
    }

    function lines(value) {
        return String(value || "").split("\n").map((line) => line.trim()).filter(Boolean);
    }

    /** The request body for a plan, from the form and what the page already knows. */
    function planBody(form, context) {
        const probe = form.probe && form.probe.url.trim() ? {
            url: form.probe.url.trim(), expectedStatus: Number(form.probe.status) || 200,
            mustContain: lines(form.probe.contain), mustNotContain: lines(form.probe.notContain),
        } : null;
        return {
            targetKind: form.targetKind, mode: form.mode, workflowId: context.workflowId, workflowSource: context.workflowSource || "FROZEN",
            namespace: context.namespace, probe,
            measureBaseline: form.mode === "FULL" ? true : form.baseline ? true : null,
            smokeWorker: form.mode === "SMOKE" && form.smokeWorker ? form.smokeWorker : null,
        };
    }

    /** What the stepper is told: none (nothing run), running, passed, failed (no pass), attention (needs the person). */
    function proofFact(run) {
        if (!run) return "none";
        if (run.status === "RUNNING" || run.status === "ABORTING" || run.status === "PLANNED") return "running";
        if (run.status === "NEEDS_MANUAL_RESTORE" || (run.safety && run.safety.level === "alert")) return "attention";
        if (run.status === "COMPLETED") return run.result && run.result.winner ? "passed" : "failed";
        return "failed";
    }

    function canStart(plan, acknowledged, desktopAcknowledged) {
        if (!plan || !plan.canApprove) return false;
        const needed = plan.acknowledgementsNeeded || [];
        if (needed.some((name) => !acknowledged.has(name))) return false;
        return !(plan.target && plan.target.kind === "DOCKER_DESKTOP" && !desktopAcknowledged);
    }

    function formatSeconds(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return UNKNOWN;
        const total = Math.round(Number(value));
        return total < 90 ? `${total} s` : `${Math.round(total / 60)} min`;
    }

    function progressText(metrics) {
        if (!metrics) return "";
        const p = metrics.progress || { done: 0, total: 0 };
        return `${p.done} of ${p.total} experiments`;
    }

    function chip(doc, text, tone) {
        return make(doc, "span", text, `prf-chip prf-chip--${tone || "wait"}`);
    }

    function stateChip(doc, status) {
        const cell = CELL[status] || CELL.PENDING;
        const node = make(doc, "span", undefined, `prf-state prf-state--${cell.tone}`, { "data-status": status });
        node.append(make(doc, "span", cell.glyph, "prf-state__glyph", { "aria-hidden": "true" }), make(doc, "span", cell.text, "prf-state__text"));
        return node;
    }

    // ---- the panel -------------------------------------------------------------------------------------------------------

    function createProofPanel(options) {
        const { root, document: doc, fetchImpl, getContext } = options;
        const schedule = options.schedule || ((fn, ms) => setTimeout(fn, ms));
        const cancel = options.cancel || ((id) => clearTimeout(id));
        const now = options.now || (() => Date.now());
        const onChange = options.onChange || (() => {});
        const isResearch = options.isResearch || (() => false);
        const onContinue = options.onContinue || (() => {});

        const form = {
            targetKind: "DOCKER_DESKTOP", mode: "QUICK", baseline: false, smokeWorker: "",
            probe: { url: "", status: "200", contain: "", notContain: "" },
        };
        const state = {
            phase: "loading", targets: null, blockingRun: null, plan: null, run: null, probeTest: null, message: null, alert: false,
            acknowledged: new Set(), desktopAcknowledged: false, confirmAbort: false, busy: false,
            lastOkAt: null, failing: false, backoff: 1, assessment: null, researchIndex: 0,
        };
        let issued = 0;
        let shown = 0;
        let timer = null;
        let destroyed = false;

        // ---- requests ----

        async function request(method, path, body) {
            const response = await fetchImpl(`${BASE}${path}`, {
                method, headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
                body: body ? JSON.stringify(body) : undefined,
            });
            let payload = null;
            try { payload = await response.json(); } catch (_error) { payload = null; }
            return { ok: response.ok, status: response.status, payload };
        }

        function setMessage(text, alert) {
            state.message = text || null;
            state.alert = !!alert;
        }

        function context() {
            return getContext() || {};
        }

        function unwatch() {
            if (timer !== null) { cancel(timer); timer = null; }
        }

        function arm() {
            unwatch();
            if (destroyed) return;
            const run = state.run;
            if (run && (run.status === "RUNNING" || run.status === "ABORTING")) timer = schedule(() => { timer = null; poll(); }, POLL_MS * state.backoff);
        }

        function adopt(run, order) {
            if (destroyed || order < shown) return false;
            shown = order;
            state.run = run;
            state.lastOkAt = now();
            state.failing = false;
            state.backoff = 1;
            if (run) {
                if (state.phase === "starting" || state.phase === "loading" || state.phase === "run") state.phase = "run";
            }
            onChange(proofFact(run), run);
            return true;
        }

        async function poll() {
            if (destroyed) return;
            const order = ++issued;
            try {
                const result = await request("GET", `/current${isResearch() ? "?research=true" : ""}`);
                if (result.ok) {
                    adopt(result.payload && result.payload.run, order);
                } else {
                    state.failing = true;
                    state.backoff = Math.min(MAX_BACKOFF, state.backoff * 2);
                    setMessage(errorMessage(result, "The backend could not report the run. Showing the last recorded state."), true);
                }
            } catch (_error) {
                state.failing = true;
                state.backoff = Math.min(MAX_BACKOFF, state.backoff * 2);
                setMessage("The backend cannot be reached. Showing the last recorded state; it will be asked again.", true);
            }
            if (destroyed) return;
            draw();
            arm();
        }

        async function start() {
            state.phase = "loading";
            draw();
            const order = ++issued;
            try {
                const [current, targets] = await Promise.all([request("GET", `/current${isResearch() ? "?research=true" : ""}`), request("GET", "/targets")]);
                if (targets.ok) {
                    state.targets = targets.payload.targets;
                    state.blockingRun = targets.payload.blockingRun;
                    // Only a cluster that can be used is ever shown as chosen.
                    if (!state.targets.some((t) => t.kind === form.targetKind && t.available)) {
                        const usable = state.targets.find((t) => t.available);
                        form.targetKind = usable ? usable.kind : "";
                    }
                }
                const run = current.ok && current.payload ? current.payload.run : null;
                // The latest run is always shown when there is one: a run that finished while the extension was closed is still the answer
                // the person came back for. "Plan another run" leads to the settings.
                state.phase = run ? "run" : "setup";
                if (current.ok) adopt(run, order);
                else if (!targets.ok) setMessage(errorMessage(targets, "The backend could not be reached."), true);
            } catch (_error) {
                state.phase = "setup";
                setMessage("The backend cannot be reached, so test runs cannot be planned yet.", true);
            }
            draw();
            arm();
        }

        async function reviewPlan() {
            if (state.busy) return;
            const ctx = context();
            if (!ctx.namespace || !ctx.workflowId) { setMessage("Choose an application and a workflow in step 1 first.", true); draw(); return; }
            state.busy = true;
            setMessage("Reading the cluster and working out the plan…", false);
            draw();
            try {
                const result = await request("POST", "/plan", planBody(form, ctx));
                if (result.ok) {
                    state.plan = result.payload;
                    state.acknowledged = new Set();
                    state.desktopAcknowledged = false;
                    state.phase = "plan";
                    setMessage(null);
                } else {
                    setMessage(errorMessage(result, "The plan could not be worked out."), true);
                }
            } catch (_error) {
                setMessage("The backend cannot be reached, so the plan could not be worked out.", true);
            }
            state.busy = false;
            draw();
        }

        async function testProbe() {
            if (state.busy) return;
            const body = planBody(form, context());
            if (!body.probe) { setMessage("Enter the address of the page that shows the workflow works.", true); draw(); return; }
            state.busy = true;
            state.probeTest = { pending: true };
            draw();
            try {
                const result = await request("POST", "/probe-test", { targetKind: form.targetKind, probe: body.probe });
                state.probeTest = result.ok ? result.payload : { error: errorMessage(result, "The check could not be run.") };
            } catch (_error) {
                state.probeTest = { error: "The backend cannot be reached." };
            }
            state.busy = false;
            draw();
        }

        /** Can this computer hold a disposable test cluster? Reads Docker's memory through the proxy; creates nothing. */
        async function checkMachine() {
            if (state.busy) return;
            state.busy = true;
            state.assessment = { pending: true };
            draw();
            try {
                const result = await request("GET", "/disposable/assessment");
                state.assessment = result.ok ? result.payload : { error: errorMessage(result, "The check could not be run.") };
            } catch (_error) {
                state.assessment = { error: "The backend cannot be reached." };
            }
            state.busy = false;
            draw();
        }

        async function startRun() {
            if (state.busy || !canStart(state.plan, state.acknowledged, state.desktopAcknowledged)) return;
            state.busy = true;
            state.phase = "starting";
            setMessage("Starting the run…", false);
            draw();
            const order = ++issued;
            try {
                const result = await request("POST", "/runs", {
                    plan: planBody(form, context()), planDigest: state.plan.planDigest,
                    acknowledgedWorkloads: [...state.acknowledged], desktopAcknowledged: state.desktopAcknowledged,
                });
                if (result.ok) {
                    setMessage(null);
                    adopt(result.payload, order);
                    state.phase = "run";
                } else {
                    state.phase = "plan";
                    setMessage(errorMessage(result, "The run could not be started."), true);
                    if (result.payload && result.payload.detail && result.payload.detail.code === "PLAN_CHANGED") state.plan = null, state.phase = "setup";
                }
            } catch (_error) {
                state.phase = "plan";
                setMessage("The request did not return, so it is not known whether the run started. Reload this step to see.", true);
            }
            state.busy = false;
            draw();
            arm();
        }

        async function abortRun() {
            if (state.busy || !state.run) return;
            state.busy = true;
            state.confirmAbort = false;
            draw();
            try {
                const result = await request("POST", `/runs/${encodeURIComponent(state.run.runKey)}/abort`);
                if (result.ok) { adopt(result.payload.run, ++issued); setMessage(null); }
                else setMessage(errorMessage(result, "The run could not be asked to stop."), true);
            } catch (_error) {
                setMessage("The abort request did not return. Check the run's state before trying again.", true);
            }
            state.busy = false;
            draw();
            arm();
        }

        async function recoverRun() {
            if (state.busy || !state.run) return;
            state.busy = true;
            setMessage("Trying to restore the cluster…", false);
            draw();
            try {
                const result = await request("POST", `/runs/${encodeURIComponent(state.run.runKey)}/recover`);
                if (result.ok) { adopt(result.payload.run, ++issued); setMessage(null); }
                else setMessage(errorMessage(result, "Recovery did not complete."), true);
            } catch (_error) {
                setMessage("The recovery request did not return. Check the cluster before trying again.", true);
            }
            state.busy = false;
            draw();
        }

        function again() {
            state.phase = "setup";
            state.plan = null;
            state.confirmAbort = false;
            setMessage(null);
            unwatch();
            draw();
        }

        // ---- drawing ----

        function button(label, className, onClick, disabled, id) {
            const node = make(doc, "button", label, className, { type: "button" });
            if (id) node.id = id;
            node.disabled = !!disabled;
            node.addEventListener("click", onClick);
            return node;
        }

        function field(labelText, control, id, hint) {
            const wrap = make(doc, "div", undefined, "form-group prf-field");
            const label = make(doc, "label", labelText, undefined, { for: id });
            control.id = id;
            wrap.append(label, control);
            if (hint) wrap.append(make(doc, "p", hint, "prf-caption"));
            return wrap;
        }

        /** A segmented control: one radio group, each option a short label; an option that cannot be used says why in its tooltip. */
        function segmented(name, label, options, selected, onSelect) {
            const group = make(doc, "div", undefined, "prf-seg", { role: "radiogroup", "aria-label": label });
            for (const option of options) {
                const on = option.value === selected;
                const node = make(doc, "label", undefined, `prf-seg__option${on ? " prf-seg__option--on" : ""}${option.disabled ? " prf-seg__option--off" : ""}`);
                if (option.title) node.setAttribute("title", option.title);
                const input = make(doc, "input", undefined, "prf-seg__input", { type: "radio", name, value: option.value });
                input.value = option.value;
                input.checked = on;
                input.disabled = !!option.disabled;
                input.addEventListener("change", () => onSelect(option.value));
                node.append(input, make(doc, "span", option.label, "prf-seg__label"));
                if (option.note) node.append(make(doc, "span", option.note, "prf-seg__note"));
                group.append(node);
            }
            return group;
        }

        function drawHead(container, title, sub) {
            const head = make(doc, "div", undefined, "rec-step__head");
            head.append(make(doc, "h3", title, "rec-step__title", { id: "rec-step-4" }));
            if (sub) head.append(make(doc, "p", sub, "rec-step__sub"));
            container.append(head);
        }

        function drawMessage(container) {
            if (!state.message) return;
            const node = make(doc, "p", state.message, `approval-note prf-message${state.alert ? " prf-message--alert" : ""}`, { role: state.alert ? "alert" : "status" });
            container.append(node);
        }

        function drawSetup(container) {
            drawHead(container, "4. Prove it on a test cluster", "Stop a worker on purpose and check the workflow survives.");
            drawMessage(container);
            if (state.blockingRun) {
                container.append(make(doc, "p", `A run is already ${String(state.blockingRun.status).toLowerCase().replace(/_/g, " ")}. Finish or recover it first.`,
                    "approval-note prf-message prf-message--alert", { role: "alert" }));
            }
            const targets = state.targets || [];
            const where = make(doc, "div", undefined, "prf-line");
            where.append(make(doc, "span", "Where", "prf-line__label"));
            where.append(segmented("prf-target", "Where to test", targets.map((t) => ({
                value: t.kind, label: TARGET_NAME[t.kind] || t.label, disabled: !t.available, note: t.available ? null : "unavailable",
                title: t.available ? TARGET_CAPTION[t.kind] : (t.reason || "Not available"),
            })), form.targetKind, (value) => { form.targetKind = value; draw(); }));
            container.append(where);
            const chosen = targets.find((t) => t.kind === form.targetKind);
            if (!targets.length) container.append(make(doc, "p", "Clusters could not be listed yet.", "prf-caption"));
            else if (chosen && chosen.available) container.append(make(doc, "p", TARGET_CAPTION[chosen.kind] || "", "prf-caption"));
            else container.append(make(doc, "p", targets.map((t) => `${TARGET_NAME[t.kind] || t.label}: ${t.reason || "not available"}`).join(" · "), "prf-caption prf-caption--warn"));
            if (targets.some((t) => t.kind === "DISPOSABLE" && !t.available)) container.append(drawPrepare());

            const depth = make(doc, "div", undefined, "prf-line");
            depth.append(make(doc, "span", "Depth", "prf-line__label"));
            depth.append(segmented("prf-mode", "How thorough", MODES.map((m) => ({ value: m.id, label: m.name })), form.mode, (value) => { form.mode = value; draw(); }));
            container.append(depth);
            const mode = MODES.find((m) => m.id === form.mode) || MODES[1];
            container.append(make(doc, "p", `${mode.detail}${mode.preliminary ? " · preliminary evidence" : " · repeatable evidence"}`, "prf-caption"));

            const probe = make(doc, "details", undefined, "prf-opt");
            if (form.probe.url.trim()) probe.setAttribute("open", "");
            probe.append(make(doc, "summary", `Workflow check ${form.probe.url.trim() ? "· set" : "(optional)"}`));
            const url = make(doc, "input", undefined, "form-input", { type: "text", autocomplete: "off", placeholder: "http://127.0.0.1:18080/productpage" });
            url.value = form.probe.url;
            url.addEventListener("input", () => { form.probe.url = url.value; });
            const status = make(doc, "input", undefined, "form-input prf-narrow", { type: "number", min: "100", max: "599" });
            status.value = form.probe.status;
            status.addEventListener("input", () => { form.probe.status = status.value; });
            const contain = make(doc, "textarea", undefined, "form-input", { rows: "2" });
            contain.value = form.probe.contain;
            contain.addEventListener("input", () => { form.probe.contain = contain.value; });
            const notContain = make(doc, "textarea", undefined, "form-input", { rows: "2" });
            notContain.value = form.probe.notContain;
            notContain.addEventListener("input", () => { form.probe.notContain = notContain.value; });
            const grid = make(doc, "div", undefined, "prf-fields");
            grid.append(field("Page address", url, "prf-probe-url"), field("Status", status, "prf-probe-status"),
                field("Must contain", contain, "prf-probe-contain"), field("Must not contain", notContain, "prf-probe-not"));
            const probeRow = make(doc, "div", undefined, "prf-row");
            probeRow.append(button("Test it", "secondary-button", testProbe, state.busy || !form.probe.url.trim(), "btn-prf-probe"));
            if (state.probeTest) probeRow.append(drawProbeResult());
            probe.append(grid, probeRow, make(doc, "p", "Without it a change passes on Kubernetes readiness alone.", "prf-caption"));
            container.append(probe);

            const baseline = make(doc, "label", undefined, "prf-check");
            const box = make(doc, "input", undefined, undefined, { type: "checkbox" });
            box.checked = form.mode === "FULL" || form.baseline;
            box.disabled = form.mode === "FULL";
            box.addEventListener("change", () => { form.baseline = box.checked; });
            baseline.append(box, make(doc, "span", form.mode === "FULL" ? "Measured baseline (always on for Full)" : "Measure a baseline first"));
            container.append(baseline);

            const actions = make(doc, "div", undefined, "approval-actions");
            actions.append(button("Review the plan", "primary-button", reviewPlan, state.busy || !!state.blockingRun || !targets.some((t) => t.available), "btn-prf-plan"));
            container.append(actions);
        }

        function drawPrepare() {
            const details = make(doc, "details", undefined, "prf-opt");
            details.append(make(doc, "summary", "No test cluster? Check this computer"));
            const row = make(doc, "div", undefined, "prf-row");
            row.append(button("Check memory", "secondary-button", checkMachine, state.busy, "btn-prf-assess"));
            details.append(row);
            const a = state.assessment;
            if (a && a.pending) details.append(make(doc, "p", "Checking…", "prf-caption", { role: "status" }));
            else if (a && a.error) details.append(make(doc, "p", a.error, "approval-note prf-message prf-message--alert", { role: "alert" }));
            else if (a) {
                const box = make(doc, "div", undefined, `prf-assess prf-assess--${a.canCreate ? "ok" : "no"}`, { role: "status" });
                box.append(make(doc, "strong", a.canCreate ? "Enough memory for a test cluster." : `Not enough yet (${a.verdict}).`));
                const steps = make(doc, "ul", undefined, "prf-advice");
                (a.advice || []).forEach((line) => steps.append(make(doc, "li", line)));
                if ((a.advice || []).length) box.append(steps);
                if (a.canCreate) box.append(make(doc, "p", "Create it with kind (see the runbook). It is not created from this page.", "prf-caption"));
                details.append(box);
            }
            return details;
        }

        function drawProbeResult() {
            const test = state.probeTest;
            if (test.pending) return make(doc, "span", "Checking…", "prf-caption", { role: "status" });
            if (test.error) return chip(doc, test.error, "fail");
            const ok = test.survives === true;
            const node = make(doc, "span", undefined, "prf-probe-result", { role: "status" });
            node.append(chip(doc, ok ? `Works · ${test.passed}/${test.samples}` : `Fails · ${test.passed}/${test.samples}`, ok ? "pass" : "fail"));
            if (!ok && (test.reasons || []).length) node.append(make(doc, "span", ` ${test.reasons.join("; ")}`, "prf-caption"));
            return node;
        }

        function drawPlan(container) {
            const plan = state.plan;
            drawHead(container, "4. Prove it on a test cluster", "Approving covers exactly this and nothing else.");
            drawMessage(container);
            const mode = plan.mode;
            const summary = make(doc, "ul", undefined, "prf-summary");
            const item = (label, value) => { const li = make(doc, "li"); li.append(make(doc, "span", label, "prf-summary__label"), make(doc, "span", value, "prf-summary__value")); summary.append(li); };
            item("Cluster", plan.target.kind === "DOCKER_DESKTOP" ? "Docker Desktop (your cluster)" : "Test cluster");
            item("Depth", `${(MODES.find((m) => m.id === mode.mode) || {}).name || mode.mode}${mode.preliminary ? " · preliminary" : ""}`);
            item("Changes", plan.candidates.length ? plan.candidates.map((c) => `${c.strategy} on ${c.service}`).join(", ") : "none can be proven");
            item("Stops", `${mode.plannedFailures} at most · ${mode.workers.join(", ") || "no worker"}`);
            item("Check", plan.probe ? `${plan.probe.url} → ${plan.probe.expectedStatus}` : "readiness only");
            if (plan.estimate) item("Time", `about ${formatSeconds(plan.estimate.estimatedSeconds)} (from ${plan.estimate.basedOnExperiments} earlier experiments)`);
            container.append(summary);
            container.append(make(doc, "p", "Afterwards every worker is started and every change is put back.", "prf-caption"));

            const issues = make(doc, "ul", undefined, "prf-issues", { "aria-label": "What stops or affects this run" });
            for (const issue of plan.issues || []) {
                const li = make(doc, "li", undefined, `prf-issue prf-issue--${issue.blocking ? "block" : "note"}`);
                li.append(make(doc, "span", issue.blocking ? "Stops the run" : "Note", "prf-issue__tag"), make(doc, "span", issue.message));
                issues.append(li);
            }
            if (issues.children && issues.children.length) container.append(issues);
            if ((plan.notes || []).length) {
                const notes = make(doc, "details", undefined, "prf-opt");
                notes.append(make(doc, "summary", "Notes"));
                plan.notes.forEach((note) => notes.append(make(doc, "p", note, "prf-caption")));
                container.append(notes);
            }

            const needed = plan.acknowledgementsNeeded || [];
            if (needed.length) {
                const box = make(doc, "fieldset", undefined, "prf-group");
                box.append(make(doc, "legend", "These go down with a worker. Accept each.", "prf-group__legend"));
                for (const name of needed) {
                    const label = make(doc, "label", undefined, "prf-check");
                    const input = make(doc, "input", undefined, undefined, { type: "checkbox" });
                    input.checked = state.acknowledged.has(name);
                    input.addEventListener("change", () => { if (input.checked) state.acknowledged.add(name); else state.acknowledged.delete(name); draw(); });
                    label.append(input, make(doc, "span", name));
                    box.append(label);
                }
                container.append(box);
            }
            if (plan.target.kind === "DOCKER_DESKTOP") {
                const label = make(doc, "label", undefined, "prf-check prf-check--strong");
                const input = make(doc, "input", undefined, undefined, { type: "checkbox" });
                input.checked = state.desktopAcknowledged;
                input.addEventListener("change", () => { state.desktopAcknowledged = input.checked; draw(); });
                label.append(input, make(doc, "span", "Stop my Docker Desktop cluster's workers for this run."));
                container.append(label);
            }

            const actions = make(doc, "div", undefined, "approval-actions");
            actions.append(button("Change settings", "secondary-button", again, state.busy, "btn-prf-back"),
                button("Start the run", "primary-button", startRun, state.busy || !canStart(plan, state.acknowledged, state.desktopAcknowledged), "btn-prf-start"));
            container.append(actions);
        }

        // ---- the run page: strip, figures, results by worker, current operation with safety, proof summary, research data -----------------

        const HUMAN = (id) => String(id || "").toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
        const MODE_NAME = { SMOKE: "Smoke", QUICK: "Quick validation", FULL: "Full validation" };
        const LEGEND = ["PASS", "RUNNING", "PENDING", "INCONCLUSIVE", "SKIPPED"];

        function tile(glyph, label, value, extra) {
            const node = make(doc, "div", undefined, "prf-strip__cell");
            node.append(make(doc, "span", glyph, "prf-ico", { "aria-hidden": "true" }));
            const body = make(doc, "div", undefined, "prf-strip__body");
            body.append(make(doc, "span", label, "prf-strip__label"));
            if (extra) body.append(extra); else body.append(make(doc, "span", value, "prf-strip__value"));
            node.append(body);
            return node;
        }

        function drawStrip(container, run) {
            const strip = make(doc, "div", undefined, "prf-strip");
            const status = make(doc, "span", undefined, "prf-strip__status");
            status.append(chip(doc, run.statusLabel, run.stale ? "warn" : RUN_TONE[run.status] || "wait"));
            strip.append(tile("◎", "Target cluster", run.target.name), tile("▦", "Namespace", run.application.namespace),
                tile("⑂", "Workflow", run.application.workflowId),
                tile("⚡", "Mode", `${MODE_NAME[run.mode] || run.mode}${run.preliminary ? " · preliminary" : ""}`), tile("●", "Status", "", status));
            container.append(strip);
        }

        function meter(value, total, label) {
            const bar = make(doc, "div", undefined, "prf-meter", { role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(total), "aria-valuenow": String(value), "aria-label": label });
            bar.append(make(doc, "div", undefined, "prf-meter__fill", { style: `width:${total ? Math.round((value / total) * 100) : 0}%` }));
            return bar;
        }

        function figure(glyph, label, value, sub, bar) {
            const card = make(doc, "div", undefined, "prf-figure");
            card.append(make(doc, "span", glyph, "prf-ico", { "aria-hidden": "true" }));
            const body = make(doc, "div", undefined, "prf-figure__body");
            body.append(make(doc, "span", label, "prf-figure__label"), make(doc, "span", value, "prf-figure__value"));
            if (bar) body.append(bar);
            if (sub) body.append(make(doc, "span", sub, "prf-figure__sub"));
            card.append(body);
            return card;
        }

        function drawFigures(container, run) {
            const m = run.metrics;
            const grid = make(doc, "div", undefined, "prf-figures");
            grid.append(
                figure("⚗", "Completed experiments", `${m.progress.done} / ${m.progress.total}`, m.skipped ? `${m.skipped} skipped` : null, meter(m.progress.done, m.progress.total, progressText(m))),
                figure("▤", "Changes tested", `${m.candidatesTested} / ${m.candidatesPlanned}`, null, meter(m.candidatesTested, m.candidatesPlanned, "Changes tested")),
                figure("▥", "Workers covered", `${m.workersCovered} / ${m.workersPlanned}`, `of ${m.workersPlanned} cluster workers`, meter(m.workersCovered, m.workersPlanned, "Workers covered")),
                figure("▮", "Measured baseline", run.baselineMeasured ? "On" : "Off", run.baselineMeasured ? "Each worker stopped before the change" : "Derived from placement", null));
            container.append(grid);
            container.append(make(doc, "p", progressText(m), "prf-progress__text visually-hidden", { role: "status", "aria-live": "polite" }));
        }

        function cellDetail(cell) {
            if (cell.status === "PENDING") return "Not started";
            if (cell.status === "SKIPPED") return "Skipped";
            if (cell.checks) return `${cell.checks.passed}/${cell.checks.total} checks`;
            if (cell.status === "RUNNING") return "In progress";
            return "";
        }

        function drawMatrix(container, run) {
            const matrix = run.matrix;
            const panel = make(doc, "section", undefined, "prf-panel");
            const head = make(doc, "div", undefined, "prf-panel__head");
            const title = make(doc, "div");
            title.append(make(doc, "h4", "Results by worker", "prf-panel__title"), make(doc, "p", "Each change, on each worker that was stopped.", "prf-caption"));
            const legend = make(doc, "ul", undefined, "prf-legend", { "aria-label": "States" });
            LEGEND.forEach((id) => { const li = make(doc, "li", undefined, `prf-legend__item prf-legend__item--${CELL[id].tone}`); li.append(make(doc, "span", undefined, "prf-legend__dot", { "aria-hidden": "true" }), make(doc, "span", CELL[id].text)); legend.append(li); });
            head.append(title, legend);
            const scroll = make(doc, "div", undefined, "prf-matrix-scroll");
            const table = make(doc, "table", undefined, "prf-matrix", { "aria-label": "Result of each change on each worker" });
            const thead = make(doc, "thead");
            const headRow = make(doc, "tr");
            headRow.append(make(doc, "th", "Change", undefined, { scope: "col" }));
            matrix.workers.forEach((w) => headRow.append(make(doc, "th", w, undefined, { scope: "col" })));
            thead.append(headRow);
            table.append(thead);
            const body = make(doc, "tbody");
            matrix.rows.forEach((row) => {
                const tr = make(doc, "tr");
                const first = make(doc, "th", undefined, "prf-matrix__row", { scope: "row" });
                first.append(make(doc, "span", HUMAN(row.strategy || row.candidateId), "prf-matrix__name"), make(doc, "span", row.service || "", "prf-caption"));
                tr.append(first);
                row.cells.forEach((cell) => {
                    const td = make(doc, "td", undefined, "prf-matrix__cell");
                    td.append(stateChip(doc, cell.status), make(doc, "span", cellDetail(cell), "prf-caption"));
                    if (matrix.repeatsPerCell > 1) {
                        const reps = make(doc, "span", undefined, "prf-reps", { "aria-label": `Repeats: ${cell.repeats.map((r) => `${r.repeat} ${(CELL[r.status] || CELL.PENDING).text}`).join(", ")}` });
                        cell.repeats.forEach((r) => reps.append(make(doc, "span", (CELL[r.status] || CELL.PENDING).glyph, `prf-rep prf-rep--${(CELL[r.status] || CELL.PENDING).tone}`, { "aria-hidden": "true" })));
                        td.append(reps);
                    }
                    tr.append(td);
                });
                body.append(tr);
            });
            table.append(body);
            scroll.append(table);
            panel.append(head, scroll);
            if ((run.baseline || []).length) panel.append(make(doc, "p", `Baseline: ${run.baseline.map((b) => `${b.worker} ${(CELL[b.status] || CELL.PENDING).text.toLowerCase()}`).join(", ")}.`, "prf-caption"));
            container.append(panel);
        }

        function duration(seconds) {
            if (seconds === null || seconds === undefined) return null;
            const total = Math.round(seconds);
            return total < 90 ? `${total}s` : `${Math.floor(total / 60)}m ${total % 60}s`;
        }

        function tracker(run) {
            const list = make(doc, "ol", undefined, "prf-track", { "aria-label": "Stages of each experiment" });
            const at = run.stages.findIndex((s) => s.active);
            run.stages.forEach((s, index) => {
                const done = at >= 0 && index < at;
                const li = make(doc, "li", undefined, `prf-track__step${done ? " prf-track__step--done" : ""}${s.active ? " prf-track__step--on" : ""}`);
                if (s.active) li.setAttribute("aria-current", "step");
                li.append(make(doc, "span", done ? "✓" : String(index + 1), "prf-track__dot", { "aria-hidden": "true" }), make(doc, "span", s.label, "prf-track__label"));
                list.append(li);
            });
            return list;
        }

        function drawOperation(container, run) {
            const card = make(doc, "section", undefined, "prf-op");
            const live = run.status === "RUNNING" || run.status === "ABORTING";
            const head = make(doc, "div", undefined, "prf-op__head");
            const title = make(doc, "div");
            title.append(make(doc, "h4", "Current operation", "prf-op__title"));
            head.append(title);
            const timing = [];
            if (run.timing && run.timing.startedAgoS !== null && run.timing.startedAgoS !== undefined) timing.push(live ? `Started ${duration(run.timing.startedAgoS)} ago` : `Ran ${duration(run.timing.elapsedS)}`);
            if (live && run.timing && run.timing.elapsedS !== null) timing.push(`Elapsed ${duration(run.timing.elapsedS)}`);
            if (live && run.estimate) timing.push(`about ${formatSeconds(run.estimate.estimatedSeconds)} left`);
            if (timing.length) head.append(make(doc, "span", timing.join(" · "), "prf-caption"));
            card.append(head);
            if (!run.current) {
                card.append(make(doc, "p", live ? "Waiting for the next experiment." : "No experiment is running.", "prf-caption", { role: "status" }));
                return card;
            }
            const cur = run.current;
            const facts = make(doc, "div", undefined, "prf-op__facts", { role: "status", "aria-live": "polite" });
            const fact = (label, value) => { const f = make(doc, "div", undefined, "prf-op__fact"); f.append(make(doc, "span", label, "prf-strip__label"), make(doc, "span", value, "prf-strip__value")); facts.append(f); };
            fact("Change", cur.candidateId ? HUMAN((run.matrix.rows.find((r) => r.candidateId === cur.candidateId) || {}).strategy || cur.candidateId) : "Preparing");
            if (cur.worker) fact("Worker", cur.worker);
            if (cur.repeat) fact("Repeat", `${cur.repeat} of ${run.matrix.repeatsPerCell}`);
            card.append(facts, tracker(run));
            const now_ = make(doc, "p", undefined, "prf-op__now");
            now_.append(make(doc, "strong", "Now: "), make(doc, "span", cur.detail || (run.stages.find((s) => s.active) || {}).label || ""));
            card.append(now_);
            return card;
        }

        function stateRow(label, text, tone) {
            const row = make(doc, "li", undefined, "prf-check-row");
            row.append(make(doc, "span", label, "prf-check-row__label"), chip(doc, text, tone));
            return row;
        }

        function drawSafety(run) {
            const safety = make(doc, "section", undefined, `prf-safety prf-safety--${run.safety.level}`, { role: run.safety.level === "alert" ? "alert" : "status" });
            safety.append(make(doc, "h4", "Safety & recovery", "prf-op__title"));
            const live = run.status === "RUNNING" || run.status === "ABORTING";
            const s = run.safety;
            const rows = make(doc, "ul", undefined, "prf-checks");
            rows.append(stateRow("Critical workloads check", { passed: "Passed", blocked: "Blocked", pending: "Waiting" }[s.criticalCheck] || "Waiting", { passed: "pass", blocked: "fail", pending: "wait" }[s.criticalCheck] || "wait"));
            const recovery = s.level === "alert" ? ["Needs attention", "fail"] : s.workersRestored === "confirmed" ? [live ? "Verified so far" : "Verified", "pass"]
                : s.workersRestored === "none-stopped" ? ["Nothing stopped", "wait"] : live ? ["Monitoring", "run"] : ["Not confirmed", "warn"];
            rows.append(stateRow("Recovery status", recovery[0], recovery[1]));
            if (s.deploymentsRestored !== "none-changed") rows.append(stateRow("Deployments", s.deploymentsRestored === "confirmed" ? "Put back" : live ? "Changed" : "Not confirmed", s.deploymentsRestored === "confirmed" ? "pass" : "warn"));
            rows.append(stateRow("Prediction vs observed", { matches: "Matches", mismatch: "Mismatch", pending: "Waiting" }[s.predictionCheck] || "Waiting", { matches: "pass", mismatch: "warn", pending: "wait" }[s.predictionCheck] || "wait"));
            safety.append(rows);
            // The standing reassurance is not repeated while all is well; a warning or an alert is always said.
            if (s.level !== "info") safety.append(make(doc, "p", s.message, "prf-safety__message"));
            (s.actions || []).forEach((a) => safety.append(make(doc, "code", a, "prf-safety__action")));
            if (s.workersRestored !== "none-stopped" && !live) safety.append(make(doc, "p", `Workers: ${s.workersRestored === "confirmed" ? "restored (verified)" : "not yet confirmed"}`, "prf-safety__detail"));
            if (live) safety.append(make(doc, "p", "Finished tests, workers: " + (s.workersRestored === "confirmed" ? "restored (verified)" : "not yet confirmed"), "prf-safety__detail"));
            return safety;
        }

        function drawControls(run) {
            const actions = make(doc, "div", undefined, "approval-actions prf-controls");
            if (run.status === "RUNNING" || run.status === "ABORTING") {
                if (state.confirmAbort) {
                    actions.append(make(doc, "p", "Stop the run? The current worker is restarted and changes are reverted first.", "approval-note"),
                        button("Keep running", "secondary-button", () => { state.confirmAbort = false; draw(); }, state.busy, "btn-prf-keep"),
                        button("Abort run", "danger-button", abortRun, state.busy, "btn-prf-abort-yes"));
                } else {
                    actions.append(button(run.abort.requested ? run.abort.label : "Abort run", "prf-abort", () => { state.confirmAbort = true; draw(); }, state.busy || !run.abort.available, "btn-prf-abort"));
                }
            }
            if (run.status === "NEEDS_MANUAL_RESTORE") actions.append(button("Try recovery again", "primary-button", recoverRun, state.busy, "btn-prf-recover"));
            if (run.canStartAnother) actions.append(button("Plan another run", "secondary-button", again, state.busy, "btn-prf-again"));
            return actions;
        }

        function drawRun(container) {
            const run = state.run;
            drawHead(container, "4. Prove it on a test cluster", null);
            if (!run) { container.append(make(doc, "p", "No run recorded yet.", "prf-caption")); return; }
            drawStrip(container, run);
            if (state.failing || run.stale) {
                const since = state.lastOkAt ? `Last update ${Math.max(0, Math.round((now() - state.lastOkAt) / 1000))} s ago.` : "";
                container.append(make(doc, "p", `${run.stale ? "The run has not reported for a while; it may have stopped." : "The backend cannot be reached."} ${since} What is shown may be out of date.`,
                    "approval-note prf-message prf-message--alert", { role: "alert" }));
            }
            drawMessage(container);
            drawFigures(container, run);
            drawMatrix(container, run);
            const grid = make(doc, "div", undefined, "prf-opgrid");
            const operation = drawOperation(container, run);
            const safety = drawSafety(run);
            safety.append(drawControls(run));
            grid.append(operation, safety);
            container.append(grid);
            if (run.research) drawResearch(container, run);
            if (run.result) drawResult(container, run);
        }

        function drawResult(container, run) {
            const result = run.result;
            const checks = run.matrix.rows.flatMap((r) => r.cells).reduce((sum, c) => (c.checks ? { passed: sum.passed + c.checks.passed, total: sum.total + c.checks.total } : sum), { passed: 0, total: 0 });
            const mismatches = result.modelMismatches || [];
            const proof = make(doc, "section", undefined, `prf-panel prf-result prf-result--${result.winner ? "pass" : "none"}`, { role: "status" });
            const head = make(doc, "div", undefined, "prf-panel__head");
            head.append(make(doc, "h4", "Proof summary", "prf-panel__title"));
            if (run.preliminary) head.append(chip(doc, "Preliminary evidence", "warn"));
            proof.append(head);
            const tiles = make(doc, "div", undefined, "prf-figures");
            const winnerRow = result.winner ? (run.matrix.rows.find((r) => r.candidateId === result.winner) || {}) : null;
            tiles.append(
                figure(result.winner ? "✓" : "✕", "Observed outcome", result.winner ? "Resilient" : "Not proven",
                    result.winner ? `${HUMAN(winnerRow.strategy || result.winner)} on ${winnerRow.service || "the service"} held on every tested worker` : (run.status === "COMPLETED" ? "No change held on every worker" : "The run ended before a change was fully proven")),
                figure(mismatches.length ? "!" : "≈", "Matches prediction", mismatches.length ? "Mismatch" : (run.safety.predictionCheck === "pending" ? "—" : "Match"),
                    mismatches.length ? mismatches.map((m) => `${m.worker}: model said ${m.predictedSurvives ? "survives" : "fails"}, readiness ${m.readinessHolds ? "held" : "did not"}`).join("; ") : "Model and readiness agree"),
                figure("▤", "Evidence", run.evidenceLevel === "L2_PROBE" ? "Probe + readiness" : "Readiness only", checks.total ? `${checks.passed} of ${checks.total} checks passed` : "No workflow check was set"),
                figure("▮", "Improvement", result.improvement ? `${result.improvement.observedBefore}% → ${result.improvement.worstObservedBlastRadiusAfter}%` : "—", result.improvement ? result.improvement.basisNote : "Not measured"));
            proof.append(tiles);
            (result.probeFindings || []).forEach((m) => proof.append(make(doc, "p", `Check and readiness disagreed on ${m.worker}: a finding about the check, not a prediction error.`, "prf-caption")));
            const list = make(doc, "ul", undefined, "prf-outcomes");
            (result.candidates || []).forEach((c) => {
                const li = make(doc, "li", undefined, "prf-outcome");
                li.append(chip(doc, c.status, c.status === "PASSED" ? "pass" : c.status === "FAILED" ? "fail" : "warn"), make(doc, "span", ` ${c.candidateId}${c.reason ? ` — ${c.reason}` : ""}`));
                list.append(li);
            });
            proof.append(list);
            container.append(proof);

            const bar = make(doc, "div", undefined, "prf-finish");
            bar.append(make(doc, "span", "◈", "prf-ico", { "aria-hidden": "true" }),
                make(doc, "div", undefined, "prf-finish__text"));
            bar.children[1].append(make(doc, "strong", result.winner ? "A change is proven" : "No change is proven"),
                make(doc, "span", result.winner ? "Nothing has been applied to your cluster. Approving and applying is the next step." : "There is nothing to approve from this run.", "prf-caption"));
            const go = button("Continue to approval", "primary-button", () => onContinue(result.winner), !result.winner, "btn-prf-continue");
            go.setAttribute("title", "Nothing has been applied to your cluster. Approving and applying is the next step.");
            const slot = make(doc, "div", undefined, "approval-actions");
            slot.append(go);
            bar.append(slot);
            container.append(bar);
        }

        // ---- research data: observed values only; no predicted curves exist ------------------------------------------------------------

        const SVG = "http://www.w3.org/2000/svg";

        function svgNode(tag, attributes) {
            const node = doc.createElementNS ? doc.createElementNS(SVG, tag) : doc.createElement(tag);
            for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, String(value));
            return node;
        }

        /** A line chart of observed points. ``marks`` are vertical lines (the stop, the restart) at the same x scale. Nothing is smoothed or predicted. */
        function lineChart(title, unit, points, marks, yMax) {
            const wrap = make(doc, "figure", undefined, "prf-chart");
            wrap.append(make(doc, "figcaption", title, "prf-chart__title"));
            if (!points.length) { wrap.append(make(doc, "p", "No samples were taken.", "prf-caption")); return wrap; }
            const W = 320, H = 130, L = 34, B = 20, T = 8, R = 8;
            const xs = points.map((p) => p.x).concat(marks.map((m) => m.x));
            const x0 = Math.min(...xs), x1 = Math.max(...xs, x0 + 1);
            const top = yMax || Math.max(...points.map((p) => p.y), 1);
            const px = (x) => L + ((x - x0) / (x1 - x0)) * (W - L - R);
            const py = (y) => T + (1 - Math.min(y, top) / top) * (H - T - B);
            const svg = svgNode("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${title}, observed`, class: "prf-chart__svg" });
            [0, 0.5, 1].forEach((f) => {
                const y = T + (1 - f) * (H - T - B);
                svg.append(svgNode("line", { x1: L, x2: W - R, y1: y, y2: y, class: "prf-chart__grid" }));
                const label = svgNode("text", { x: L - 4, y: y + 3, "text-anchor": "end", class: "prf-chart__tick" });
                label.textContent = `${Math.round(top * f)}${unit}`;
                svg.append(label);
            });
            marks.forEach((m) => {
                svg.append(svgNode("line", { x1: px(m.x), x2: px(m.x), y1: T, y2: H - B, class: "prf-chart__mark" }));
                const late = px(m.x) > W * 0.65;       // a label near the right edge is written leftwards so it is not cut off
                const label = svgNode("text", { x: late ? px(m.x) - 3 : px(m.x) + 3, y: T + 9, "text-anchor": late ? "end" : "start", class: "prf-chart__marklabel" });
                label.textContent = m.label;
                svg.append(label);
            });
            svg.append(svgNode("polyline", { points: points.map((p) => `${px(p.x).toFixed(1)},${py(p.y).toFixed(1)}`).join(" "), class: "prf-chart__line", fill: "none" }));
            points.forEach((p) => svg.append(svgNode("circle", { cx: px(p.x).toFixed(1), cy: py(p.y).toFixed(1), r: 2.5, class: p.bad ? "prf-chart__dot prf-chart__dot--bad" : "prf-chart__dot" })));
            wrap.append(svg, make(doc, "p", "Observed · seconds from the moment the worker was stopped", "prf-caption"));
            return wrap;
        }

        function markTimes(probe) {
            const at = {};
            (probe.marks || []).forEach((m) => { at[m.name] = m.atS; });
            return at;
        }

        function drawResearch(container, run) {
            const withProbe = (run.research.experiments || []).filter((e) => e.outcome && e.outcome.probe);
            const panel = make(doc, "section", undefined, "prf-panel prf-research rec-research-only");
            const head = make(doc, "div", undefined, "prf-panel__head");
            const title = make(doc, "div");
            title.append(make(doc, "h4", "Research data", "prf-panel__title"), make(doc, "p", "Observed during the run. The model predicts survival, not curves, so none are drawn.", "prf-caption"));
            head.append(title);
            panel.append(head);
            if (!withProbe.length) {
                panel.append(make(doc, "p", "No workflow-check samples were taken in this run, so there is nothing to chart.", "prf-caption"));
            } else {
                const index = Math.min(state.researchIndex, withProbe.length - 1);
                const picker = make(doc, "select", undefined, "form-input prf-narrow-select", { "aria-label": "Experiment to show" });
                withProbe.forEach((e, i) => { const o = make(doc, "option", `${HUMAN(e.candidateId || "baseline")} · ${e.worker} · repeat ${e.repeat} (${e.status.toLowerCase()})`, undefined, { value: String(i) }); if (i === index) o.setAttribute("selected", ""); picker.append(o); });
                picker.value = String(index);
                picker.addEventListener("change", () => { state.researchIndex = Number(picker.value) || 0; draw(); });
                head.append(picker);
                const e = withProbe[index];
                const probe = e.outcome.probe;
                const raw = probe.samples || [];
                const stopped = markTimes(probe)["stop-issued"] ?? (raw[0] ? raw[0].at_s : 0);
                const samples = raw.map((s) => ({ x: s.at_s - stopped, y: s.ok ? 100 : 0, latency: s.latency_ms, bad: !s.ok, s }));
                const marks = [{ x: 0, label: "Worker stopped" }];
                const m = markTimes(probe);
                if (m["node-ready"] !== undefined) marks.push({ x: m["node-ready"] - stopped, label: "Worker back" });
                const charts = make(doc, "div", undefined, "prf-charts");
                charts.append(lineChart("Workflow check passing", "%", samples.map((p) => ({ x: p.x, y: p.y, bad: p.bad })), marks, 100),
                    lineChart("Response time", " ms", samples.filter((p) => p.latency !== null && p.latency !== undefined).map((p) => ({ x: p.x, y: p.latency, bad: p.bad })), marks));
                panel.append(charts);
                const grid = make(doc, "div", undefined, "prf-research__grid");
                // Probe samples
                const sampleBox = make(doc, "div", undefined, "prf-subpanel");
                sampleBox.append(make(doc, "h5", "Probe samples", "prf-subpanel__title"));
                const table = make(doc, "table", undefined, "data-table");
                const thead = make(doc, "thead"); const hr = make(doc, "tr");
                ["#", "Time", "Result", "HTTP", "Latency", "Note"].forEach((h) => hr.append(make(doc, "th", h, undefined, { scope: "col" })));
                thead.append(hr); table.append(thead);
                const body = make(doc, "tbody");
                raw.slice(0, 8).forEach((s, i) => {
                    const tr = make(doc, "tr");
                    const cell = (v) => tr.append(make(doc, "td", v));
                    cell(i + 1); cell(`${(s.at_s - stopped).toFixed(0)}s`);
                    const result = make(doc, "td"); result.append(stateChip(doc, s.ok ? "PASS" : "FAIL")); tr.append(result);
                    cell(s.status === null || s.status === undefined ? "—" : s.status); cell(s.latency_ms === null || s.latency_ms === undefined ? "—" : `${Math.round(s.latency_ms)} ms`); cell(s.ok ? "" : s.reason || s.error || "");
                    body.append(tr);
                });
                table.append(body);
                const scroll = make(doc, "div", undefined, "table-scroll"); scroll.append(table);
                sampleBox.append(scroll);
                if (raw.length > 8) sampleBox.append(make(doc, "p", `${raw.length - 8} more samples are in the stored record.`, "prf-caption"));
                grid.append(sampleBox);
                // Readiness
                const readiness = e.outcome.readiness || { services: {} };
                const readBox = make(doc, "div", undefined, "prf-subpanel");
                readBox.append(make(doc, "h5", "Readiness", "prf-subpanel__title"));
                const list = make(doc, "ul", undefined, "prf-checks");
                Object.entries(readiness.services || {}).forEach(([name, v]) => list.append(stateRow(`${name}: ${v.readyElsewhere ?? "?"} ready elsewhere, ${v.readyOnWorker ?? "?"} on the worker`, v.ok === true ? "Holds" : v.ok === false ? "Lost" : "Unknown", v.ok === true ? "pass" : v.ok === false ? "fail" : "wait")));
                readBox.append(list);
                grid.append(readBox);
                // Recovery timings, from the probe's own marks
                const timeBox = make(doc, "div", undefined, "prf-subpanel");
                timeBox.append(make(doc, "h5", "Recovery timings", "prf-subpanel__title"));
                const times = make(doc, "ul", undefined, "prf-checks");
                const seconds = (a, b) => (m[a] !== undefined && m[b] !== undefined ? `${(m[b] - m[a]).toFixed(0)} s` : "—");
                [["Seen as down after", seconds("stop-issued", "pods-unready")], ["Stopped for", seconds("stop-issued", "restore-issued")], ["Back to Ready after", seconds("restore-issued", "node-ready")],
                    ["Experiment took", e.durationS === null || e.durationS === undefined ? "—" : `${Math.round(e.durationS)} s`]].forEach(([k, v]) => {
                    const li = make(doc, "li", undefined, "prf-check-row"); li.append(make(doc, "span", k, "prf-check-row__label"), make(doc, "strong", v)); times.append(li);
                });
                timeBox.append(times);
                grid.append(timeBox);
                panel.append(grid);
            }
            const events = make(doc, "details", undefined, "prf-opt");
            events.append(make(doc, "summary", "Run events"));
            const list = make(doc, "ol", undefined, "op-events");
            (run.research.events || []).forEach((ev) => list.append(make(doc, "li", `${ev.event}${ev.detail ? ` — ${ev.detail}` : ""}`)));
            events.append(list);
            panel.append(events);
            container.append(panel);
        }

        function draw() {
            if (destroyed) return;
            const body = make(doc, "div", undefined, "prf-body");
            if (state.phase === "loading") {
                drawHead(body, "4. Prove it on a test cluster", null);
                body.append(make(doc, "p", "Looking for test clusters and any earlier run…", "prf-caption", { role: "status" }));
            } else if (state.phase === "plan" && state.plan) drawPlan(body);
            else if ((state.phase === "run" || state.phase === "starting") && (state.run || state.phase === "starting")) {
                if (state.phase === "starting" && !state.run) { drawHead(body, "4. Prove it on a test cluster", null); drawMessage(body); }
                else drawRun(body);
            } else drawSetup(body);
            root.replaceChildren(body);
            root.setAttribute("aria-busy", state.busy ? "true" : "false");
        }

        function destroy() {
            destroyed = true;
            unwatch();
        }

        return {
            start, reviewPlan, startRun, abortRun, recoverRun, testProbe, checkMachine, poll, destroy, draw, again,
            form, state: () => state, current: () => state.run,
            reset() { shown = 0; state.run = null; state.plan = null; state.phase = "loading"; unwatch(); return start(); },
        };
    }

    window.ResilienceRecommendationProof = { createProofPanel, planBody, proofFact, canStart, lines, formatSeconds, progressText, CELL, MODES };
})();
