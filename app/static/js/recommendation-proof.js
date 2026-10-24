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

    const TARGET_HELP = {
        DOCKER_DESKTOP: "Your Docker Desktop cluster. Its running application is used as it is. Worker nodes of it are stopped on purpose.",
        DISPOSABLE: "A separate cluster made for tests. Your own cluster is not touched.",
    };
    const MODES = [
        { id: "SMOKE", name: "Smoke test", detail: "One change, one worker, once. Checks that testing works at all.", tag: "Preliminary" },
        { id: "QUICK", name: "Quick validation", detail: "The best change, every worker once.", tag: "Preliminary · default" },
        { id: "FULL", name: "Full research validation", detail: "The best two changes, every worker, twice, with a measured baseline.", tag: "Repeatable" },
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
            lastOkAt: null, failing: false, backoff: 1,
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
            if (hint) wrap.append(make(doc, "p", hint, "prf-hint"));
            return wrap;
        }

        function choice(name, value, labelText, detail, tag, checked, onSelect, disabled) {
            const wrap = make(doc, "label", undefined, `prf-choice${checked ? " prf-choice--on" : ""}${disabled ? " prf-choice--off" : ""}`);
            const input = make(doc, "input", undefined, "prf-choice__input", { type: "radio", name, value });
            input.value = value;
            input.checked = !!checked;
            input.disabled = !!disabled;
            input.addEventListener("change", () => onSelect(value));
            const body = make(doc, "span", undefined, "prf-choice__body");
            const head = make(doc, "span", undefined, "prf-choice__head");
            head.append(make(doc, "span", labelText, "prf-choice__name"));
            if (tag) head.append(chip(doc, tag, "wait"));
            body.append(head, make(doc, "span", detail, "prf-choice__detail"));
            wrap.append(input, body);
            return wrap;
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
            drawHead(container, "4. Prove it on a test cluster",
                "Optional. Stops a worker node on purpose and checks the workflow keeps working. Everything is put back afterwards.");
            drawMessage(container);
            if (state.blockingRun) {
                container.append(make(doc, "p", `A run is already ${String(state.blockingRun.status).toLowerCase().replace(/_/g, " ")}. It has to finish, or be recovered, before another starts.`, "approval-note prf-message prf-message--alert", { role: "alert" }));
            }
            const targets = make(doc, "fieldset", undefined, "prf-group");
            targets.append(make(doc, "legend", "Where to test", "prf-group__legend"));
            for (const target of state.targets || []) {
                const reason = target.available ? null : target.reason || "Not available";
                const node = choice("prf-target", target.kind, target.label, `${TARGET_HELP[target.kind] || ""}${reason ? ` Not available: ${reason}` : ""}`,
                    target.available ? null : "Unavailable", form.targetKind === target.kind, (value) => { form.targetKind = value; draw(); }, !target.available);
                targets.append(node);
            }
            if (!(state.targets || []).length) targets.append(make(doc, "p", "Clusters could not be listed yet.", "prf-hint"));
            container.append(targets);

            const modes = make(doc, "fieldset", undefined, "prf-group");
            modes.append(make(doc, "legend", "How thorough", "prf-group__legend"));
            for (const mode of MODES) modes.append(choice("prf-mode", mode.id, mode.name, mode.detail, mode.tag, form.mode === mode.id, (value) => { form.mode = value; draw(); }, false));
            container.append(modes);

            const probe = make(doc, "fieldset", undefined, "prf-group prf-probe");
            probe.append(make(doc, "legend", "How we know the workflow works", "prf-group__legend"));
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
            probe.append(
                field("Page address (this computer)", url, "prf-probe-url", "Without one, a change passes on Kubernetes readiness alone, which is weaker evidence."),
                field("Expected status", status, "prf-probe-status"),
                field("Page must contain (one per line)", contain, "prf-probe-contain"),
                field("Page must not contain (one per line)", notContain, "prf-probe-not"));
            const probeRow = make(doc, "div", undefined, "prf-row");
            probeRow.append(button("Test the check now", "secondary-button", testProbe, state.busy || !form.probe.url.trim(), "btn-prf-probe"));
            if (state.probeTest) probeRow.append(drawProbeResult());
            probe.append(probeRow);
            container.append(probe);

            const baseline = make(doc, "label", undefined, "prf-check");
            const box = make(doc, "input", undefined, undefined, { type: "checkbox" });
            box.checked = form.mode === "FULL" || form.baseline;
            box.disabled = form.mode === "FULL";
            box.addEventListener("change", () => { form.baseline = box.checked; });
            baseline.append(box, make(doc, "span", form.mode === "FULL" ? "A measured baseline is part of every full validation." : "Also stop each worker once before the change, to measure the improvement."));
            container.append(baseline);

            const actions = make(doc, "div", undefined, "approval-actions");
            actions.append(button("Review the plan", "primary-button", reviewPlan, state.busy || !!state.blockingRun || !(state.targets || []).some((t) => t.available), "btn-prf-plan"));
            container.append(actions);
        }

        function drawProbeResult() {
            const test = state.probeTest;
            if (test.pending) return make(doc, "span", "Checking…", "prf-hint", { role: "status" });
            if (test.error) return chip(doc, test.error, "fail");
            const ok = test.survives === true;
            const node = make(doc, "span", undefined, "prf-probe-result", { role: "status" });
            node.append(chip(doc, ok ? `Works: ${test.passed} of ${test.samples} requests passed` : `Does not pass: ${test.passed} of ${test.samples} requests passed`, ok ? "pass" : "fail"));
            if (!ok && (test.reasons || []).length) node.append(make(doc, "span", ` ${test.reasons.join("; ")}`, "prf-hint"));
            return node;
        }

        function drawPlan(container) {
            const plan = state.plan;
            drawHead(container, "4. Prove it on a test cluster", "Review exactly what this run would do. Approving covers this and nothing else.");
            drawMessage(container);
            const mode = plan.mode;
            const summary = make(doc, "ul", undefined, "prf-summary");
            const item = (label, value) => { const li = make(doc, "li"); li.append(make(doc, "span", label, "prf-summary__label"), make(doc, "span", value, "prf-summary__value")); summary.append(li); };
            item("Cluster", `${plan.target.name} (${plan.target.kind === "DOCKER_DESKTOP" ? "your Docker Desktop cluster" : "disposable test cluster"})`);
            item("Application", `${plan.namespace} · ${plan.workflowId}`);
            item("Depth", `${(MODES.find((m) => m.id === mode.mode) || {}).name || mode.mode}${mode.preliminary ? " · preliminary evidence" : ""}`);
            item("Changes to test", plan.candidates.length ? plan.candidates.map((c) => `${c.strategy} on ${c.service}`).join("; ") : "none can be proven");
            item("Workers to stop", `${mode.workers.join(", ") || "none"} — ${mode.plannedFailures} node stops at most`);
            item("Workflow check", plan.probe ? `${plan.probe.url} must answer ${plan.probe.expectedStatus}` : "none: Kubernetes readiness only");
            item("Afterwards", "Every worker is started again and every changed Deployment is put back.");
            item("Time", plan.estimate ? `About ${formatSeconds(plan.estimate.estimatedSeconds)}, from ${plan.estimate.basedOnExperiments} earlier experiments on this machine.` : "No estimate yet: no earlier experiment has run on this machine.");
            container.append(summary);

            const issues = make(doc, "ul", undefined, "prf-issues", { "aria-label": "What stops or affects this run" });
            for (const issue of plan.issues || []) {
                const li = make(doc, "li", undefined, `prf-issue prf-issue--${issue.blocking ? "block" : "note"}`);
                li.append(make(doc, "span", issue.blocking ? "Stops the run" : "Note", "prf-issue__tag"), make(doc, "span", issue.message));
                issues.append(li);
            }
            for (const note of plan.notes || []) issues.append(make(doc, "li", note, "prf-issue prf-issue--note"));
            if (issues.children && issues.children.length) container.append(issues);

            const needed = plan.acknowledgementsNeeded || [];
            if (needed.length) {
                const box = make(doc, "fieldset", undefined, "prf-group");
                box.append(make(doc, "legend", "These will go down with a worker. Accept each one to continue.", "prf-group__legend"));
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
                label.append(input, make(doc, "span", "This is my Docker Desktop cluster. Stop its worker nodes on purpose for this run."));
                container.append(label);
            }
            if (plan.faultReadiness && plan.faultReadiness.smokeOnly) {
                container.append(make(doc, "p", "Docker may restart a stopped worker by itself here. A failure that does not stay down is refused, never counted.", "prf-hint"));
            }

            const actions = make(doc, "div", undefined, "approval-actions");
            actions.append(button("Change settings", "secondary-button", again, state.busy, "btn-prf-back"),
                button("Start the test run", "primary-button", startRun, state.busy || !canStart(plan, state.acknowledged, state.desktopAcknowledged), "btn-prf-start"));
            container.append(actions);
        }

        function drawRun(container) {
            const run = state.run;
            drawHead(container, "4. Prove it on a test cluster", null);
            if (!run) { container.append(make(doc, "p", "No run recorded yet.", "prf-hint")); return; }
            // Header: status, target, application
            const head = make(doc, "div", undefined, "prf-runhead");
            head.append(chip(doc, run.statusLabel, run.stale ? "warn" : RUN_TONE[run.status] || "wait"),
                make(doc, "span", `${run.target.name} · ${run.application.namespace} · ${run.mode}${run.preliminary ? " (preliminary)" : ""}`, "prf-runhead__where"));
            container.append(head);
            if (state.failing || run.stale) {
                const since = state.lastOkAt ? `Last update ${Math.max(0, Math.round((now() - state.lastOkAt) / 1000))} s ago.` : "";
                container.append(make(doc, "p", `${run.stale ? "The run has not reported for a while; it may have stopped." : "The backend cannot be reached."} ${since} What is shown may be out of date.`,
                    "approval-note prf-message prf-message--alert", { role: "alert" }));
            }
            drawMessage(container);
            // Metrics and progress by count
            const m = run.metrics;
            const metrics = make(doc, "dl", undefined, "prf-metrics");
            const metric = (label, value) => { const d = make(doc, "div", undefined, "prf-metric"); d.append(make(doc, "dt", label), make(doc, "dd", value)); metrics.append(d); };
            metric("Experiments", `${m.finished} of ${m.plannedExperiments} done${m.skipped ? `, ${m.skipped} skipped` : ""}`);
            metric("Changes tested", `${m.candidatesTested} of ${m.candidatesPlanned}`);
            metric("Workers covered", `${m.workersCovered} of ${m.workersPlanned}`);
            metric("Node stops", `${m.killsUsed}${m.maxFailures ? ` of ${m.maxFailures} allowed` : ""}`);
            container.append(metrics);
            const bar = make(doc, "div", undefined, "prf-progress", { role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(m.progress.total), "aria-valuenow": String(m.progress.done), "aria-label": progressText(m) });
            bar.append(make(doc, "div", undefined, "prf-progress__fill", { style: `width:${m.progress.total ? Math.round((m.progress.done / m.progress.total) * 100) : 0}%` }));
            container.append(bar);
            container.append(make(doc, "p", progressText(m), "prf-progress__text", { role: "status", "aria-live": "polite" }));
            if (run.estimate) container.append(make(doc, "p", `About ${formatSeconds(run.estimate.estimatedSeconds)} left, from ${run.estimate.basedOnExperiments} earlier experiments.`, "prf-hint"));

            // Current operation and stage timeline
            if (run.current) {
                const c = make(doc, "div", undefined, "prf-current", { role: "status", "aria-live": "polite" });
                c.append(make(doc, "div", "Now", "prf-current__title"),
                    make(doc, "div", `${run.current.candidateId ? `Change ${run.current.candidateId}` : "Preparing"}${run.current.worker ? ` · ${run.current.worker}` : ""}${run.current.repeat ? ` · repeat ${run.current.repeat}` : ""}`, "prf-current__what"),
                    make(doc, "div", (run.stages.find((s) => s.active) || {}).label || run.current.stage, "prf-current__stage"),
                    make(doc, "div", run.current.detail || "", "prf-current__detail"));
                container.append(c);
            }
            const stages = make(doc, "ol", undefined, "prf-stages", { "aria-label": "Stages of each experiment" });
            run.stages.forEach((s) => { const li = make(doc, "li", s.label, `prf-stage${s.active ? " prf-stage--on" : ""}`); if (s.active) li.setAttribute("aria-current", "step"); stages.append(li); });
            container.append(stages);

            // Safety and recovery
            const safety = make(doc, "div", undefined, `prf-safety prf-safety--${run.safety.level}`, { role: run.safety.level === "alert" ? "alert" : "status" });
            safety.append(make(doc, "strong", run.safety.message));
            const bits = [];
            const live = run.status === "RUNNING" || run.status === "ABORTING";
            // While a run is going, "verified" can only speak for the tests already finished: one worker may be down right now.
            if (run.safety.workersRestored !== "none-stopped") bits.push(`${live ? "Finished tests, workers" : "Workers"}: ${run.safety.workersRestored === "confirmed" ? "restored (verified)" : "not yet confirmed"}`);
            if (run.safety.deploymentsRestored !== "none-changed") bits.push(`Deployments: ${run.safety.deploymentsRestored === "confirmed" ? "put back (verified)" : "not yet confirmed"}`);
            if (bits.length) safety.append(make(doc, "div", bits.join(" · "), "prf-safety__detail"));
            (run.safety.actions || []).forEach((a) => safety.append(make(doc, "code", a, "prf-safety__action")));
            container.append(safety);

            drawMatrix(container, run);
            const actions = make(doc, "div", undefined, "approval-actions");
            if (run.status === "RUNNING" || run.status === "ABORTING") {
                if (state.confirmAbort) {
                    container.append(make(doc, "p", "Stop this run? The worker being tested is started again and every changed Deployment is put back first.", "approval-note"));
                    actions.append(button("Keep running", "secondary-button", () => { state.confirmAbort = false; draw(); }, state.busy, "btn-prf-keep"),
                        button("Abort run", "danger-button", abortRun, state.busy, "btn-prf-abort-yes"));
                } else {
                    actions.append(button(run.abort.requested ? run.abort.label : "Abort run", "secondary-button", () => { state.confirmAbort = true; draw(); }, state.busy || !run.abort.available, "btn-prf-abort"));
                }
            }
            if (run.status === "NEEDS_MANUAL_RESTORE") actions.append(button("Try recovery again", "primary-button", recoverRun, state.busy, "btn-prf-recover"));
            if (run.canStartAnother) actions.append(button("Plan another run", "secondary-button", again, state.busy, "btn-prf-again"));
            container.append(actions);

            if (run.result) drawResult(container, run);
            if (run.research) drawResearch(container, run);
        }

        function drawMatrix(container, run) {
            const matrix = run.matrix;
            const scroll = make(doc, "div", undefined, "prf-matrix-scroll");
            const table = make(doc, "table", undefined, "prf-matrix", { "aria-label": "Result of each change on each worker" });
            const headRow = make(doc, "tr");
            headRow.append(make(doc, "th", "Change", undefined, { scope: "col" }));
            matrix.workers.forEach((w) => headRow.append(make(doc, "th", w, undefined, { scope: "col" })));
            const thead = make(doc, "thead");
            thead.append(headRow);
            table.append(thead);
            const body = make(doc, "tbody");
            matrix.rows.forEach((row) => {
                const tr = make(doc, "tr");
                tr.append(make(doc, "th", `${row.strategy || row.candidateId} · ${row.service || ""}`, "prf-matrix__row", { scope: "row" }));
                row.cells.forEach((cell) => {
                    const td = make(doc, "td", undefined, "prf-matrix__cell");
                    td.append(stateChip(doc, cell.status));
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
            container.append(scroll);
            if ((run.baseline || []).length) container.append(make(doc, "p", `Baseline (before any change): ${run.baseline.map((b) => `${b.worker} ${(CELL[b.status] || CELL.PENDING).text.toLowerCase()}`).join(", ")}.`, "prf-hint"));
        }

        function drawResult(container, run) {
            const result = run.result;
            const box = make(doc, "div", undefined, `prf-result prf-result--${result.winner ? "pass" : "none"}`, { role: "status" });
            // A model mismatch or a check that disagreed with readiness is part of the result, and it is read BEFORE the action.
            const findings = () => {
                (result.modelMismatches || []).forEach((m) => box.append(make(doc, "p", `Prediction was wrong for ${m.worker}: the model said the workflow ${m.predictedSurvives ? "survives" : "fails"}; readiness showed it ${m.readinessHolds ? "does" : "does not"}.`, "prf-mismatch")));
                (result.probeFindings || []).forEach((m) => box.append(make(doc, "p", `The workflow check and Kubernetes readiness disagreed for ${m.worker}. That is a finding about the check, not a prediction error.`, "prf-hint")));
            };
            if (result.winner) {
                const row = (run.matrix.rows.find((r) => r.candidateId === result.winner)) || {};
                box.append(make(doc, "h4", "Proven", "prf-result__title"),
                    make(doc, "p", `${row.strategy || result.winner} on ${row.service || "the service"} kept the workflow working when each tested worker was stopped.`));
                if (run.preliminary) box.append(make(doc, "p", "Preliminary evidence. Run a full validation for repeated, reproducible results.", "prf-hint"));
                if (result.improvement) {
                    box.append(make(doc, "p", `Worst observed blast radius with the change: ${result.improvement.worstObservedBlastRadiusAfter}% (before: ${result.improvement.observedBefore}%). ${result.improvement.basisNote}`));
                }
                findings();
                const go = make(doc, "div", undefined, "approval-actions");
                go.append(button("Continue to approval", "primary-button", () => onContinue(result.winner), false, "btn-prf-continue"));
                box.append(go, make(doc, "p", "Proving a change applies nothing to your cluster. Approving and applying it is the next step.", "prf-hint"));
            } else {
                box.append(make(doc, "h4", "Not proven", "prf-result__title"),
                    make(doc, "p", run.status === "COMPLETED" ? "No tested change kept the workflow working on every worker, so none is selected." : "The run ended before any change was fully proven, so none is selected."));
                findings();
            }
            const list = make(doc, "ul", undefined, "prf-outcomes");
            (result.candidates || []).forEach((c) => {
                const li = make(doc, "li", undefined, "prf-outcome");
                li.append(chip(doc, c.status, c.status === "PASSED" ? "pass" : c.status === "FAILED" ? "fail" : "warn"),
                    make(doc, "span", ` ${c.candidateId}${c.reason ? ` — ${c.reason}` : ""}`));
                list.append(li);
            });
            box.append(list);
            container.append(box);
        }

        function drawResearch(container, run) {
            const details = make(doc, "details", undefined, "prf-research rec-research-only");
            details.append(make(doc, "summary", "Measurements, probe samples and history"));
            const table = make(doc, "table", undefined, "data-table");
            const head = make(doc, "tr");
            ["Change", "Worker", "Repeat", "Status", "Evidence", "Readiness", "Probe", "Model agreed", "Error (pts)", "Seconds"].forEach((h) => head.append(make(doc, "th", h, undefined, { scope: "col" })));
            const thead = make(doc, "thead");
            thead.append(head);
            table.append(thead);
            const body = make(doc, "tbody");
            (run.research.experiments || []).forEach((e) => {
                const o = e.outcome || {};
                const tr = make(doc, "tr");
                [e.candidateId || "baseline", e.worker, e.repeat, e.status, o.evidenceLevel || "", o.readiness && o.readiness.holds !== undefined ? String(o.readiness.holds) : "",
                    o.probe ? String(o.probe.workflowSurvives) : "none", o.modelAgrees === undefined || o.modelAgrees === null ? "" : String(o.modelAgrees),
                    o.predictionError === undefined || o.predictionError === null ? "" : o.predictionError, e.durationS === null || e.durationS === undefined ? "" : e.durationS]
                    .forEach((v) => tr.append(make(doc, "td", v)));
                body.append(tr);
            });
            table.append(body);
            const scroll = make(doc, "div", undefined, "table-scroll");
            scroll.append(table);
            details.append(scroll, make(doc, "div", "Events", "op-subtitle"));
            const events = make(doc, "ol", undefined, "op-events");
            (run.research.events || []).forEach((e) => events.append(make(doc, "li", `${e.event}${e.detail ? ` — ${e.detail}` : ""}`)));
            details.append(events);
            container.append(details);
        }

        function draw() {
            if (destroyed) return;
            const body = make(doc, "div", undefined, "prf-body");
            if (state.phase === "loading") {
                drawHead(body, "4. Prove it on a test cluster", null);
                body.append(make(doc, "p", "Looking for test clusters and any earlier run…", "prf-hint", { role: "status" }));
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
            start, reviewPlan, startRun, abortRun, recoverRun, testProbe, poll, destroy, draw, again,
            form, state: () => state, current: () => state.run,
            reset() { shown = 0; state.run = null; state.plan = null; state.phase = "loading"; unwatch(); return start(); },
        };
    }

    window.ResilienceRecommendationProof = { createProofPanel, planBody, proofFact, canStart, lines, formatSeconds, progressText, CELL, MODES };
})();
