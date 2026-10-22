/*
 * The "Bookinfo tracing" card on the Connect page (extension-only): Plan, Enable tracing, Verify and Undo.
 *
 * It sets up Istio sidecar tracing for the Bookinfo benchmark so that real requests produce real, correlated traces in
 * the Jaeger container this extension already manages. Everything that touches the cluster is done by the host helper
 * (resilience-helper, through Docker Desktop), which uses your own kubeconfig inside its own process. This page never
 * holds a credential and cannot change the cluster itself: Plan only reads; Enable tracing and Undo make the helper
 * show a system dialog that lists every change and that this page cannot see or answer; Verify sends real requests
 * from a temporary Pod and then reads the traces Jaeger actually received.
 *
 * What it never does: invent, seed or send a trace; say "verified" for anything but traces that really arrived and are
 * linked; hide a blocker; turn a reading that could not be taken into a pass.
 *
 * Everything shown from the helper or from Jaeger is untrusted text and is written with textContent only.
 */
(function () {
    "use strict";

    const BACKEND = "/_extension/tracing";
    const NAMESPACE = "bookinfo";
    const STATUS_POLL_MS = 3000;
    const TRACE_POLL_MS = 4000;
    const TRACE_WAIT_MS = 120000;

    // How long to wait for each helper command. They sit above the helper's own limits (apply 45 min, undo 35 min).
    const WAIT_MS = {
        "trace-bookinfo-plan": 150000,
        "trace-bookinfo-apply": 2820000,
        "trace-bookinfo-undo": 2220000,
        "trace-bookinfo-verify": 780000,
        "trace-bookinfo-status": 45000,
    };

    // ---- reducing what the helper prints to what this page knows ------------------------------------------------------

    const text = (value, max) => (typeof value === "string" ? value.slice(0, max) : "");
    const flag = (value) => value === true;
    const count = (value) => (Number.isInteger(value) && value >= 0 ? value : 0);
    const list = (value, max) => (Array.isArray(value) ? value.slice(0, max) : []);

    const PHASES = new Set(["applying", "applied", "rolling-back", "rolled-back", "rollback-failed"]);

    function cleanPlan(plan) {
        if (!plan || typeof plan !== "object") return null;
        const jaeger = plan.jaeger && typeof plan.jaeger === "object" ? plan.jaeger : null;
        const gate0 = plan.gate0 && typeof plan.gate0 === "object" ? plan.gate0 : null;
        return {
            ready: flag(plan.ready),
            kubernetesVersion: text(plan.kubernetesVersion, 40),
            istioVersion: text(plan.istioVersion, 20),
            blockers: list(plan.blockers, 20).map((b) => ({ code: text(b && b.code, 60), message: text(b && b.message, 400) })),
            warnings: list(plan.warnings, 10).map((w) => text(w, 400)),
            preview: list(plan.preview, 20).map((c) => ({
                step: count(c && c.step), action: text(c && c.action, 20), kind: text(c && c.kind, 80),
                namespace: text(c && c.namespace, 63), name: text(c && c.name, 300), detail: text(c && c.detail, 400),
            })),
            disruptions: list(plan.disruptions, 10).map((d) => ({ workload: text(d && d.workload, 63), pod: text(d && d.pod, 253), effect: text(d && d.effect, 200) })),
            jaeger: jaeger ? { ipv4: text(jaeger.ipv4, 45), port: text(jaeger.port, 6), nodes: list(jaeger.nodes, 4).map((n) => text(n, 128)) } : null,
            gate0Reasons: gate0 ? list(gate0.reasons, 10).map((r) => text(r, 300)) : [],
        };
    }

    function cleanSetup(setup) {
        if (!setup || typeof setup !== "object" || !PHASES.has(setup.phase)) return null;
        const failure = setup.failure && typeof setup.failure === "object" ? setup.failure : null;
        return {
            phase: setup.phase,
            startedAt: text(setup.startedAt, 40),
            updatedAt: text(setup.updatedAt, 40),
            steps: list(setup.steps, 60).slice(-14).map((s) => ({ step: text(s && s.step, 100), status: text(s && s.status, 20), detail: text(s && s.detail, 300) })),
            created: count(setup.created), deleted: count(setup.deleted), kept: count(setup.kept), foreign: count(setup.foreign),
            failure: failure ? { stage: text(failure.stage, 60), reason: text(failure.reason, 500) } : null,
            remaining: list(setup.remaining, 20).map((r) => text(r, 300)),
        };
    }

    function cleanVerify(verify) {
        if (!verify || typeof verify !== "object") return null;
        return {
            appliedAddress: text(verify.appliedAddress, 45), currentAddress: text(verify.currentAddress, 45),
            addressDrift: flag(verify.addressDrift), requestsSent: flag(verify.requestsSent), readings: text(verify.readings, 300),
            instrumented: list(verify.instrumented, 12).map((p) => ({ workload: text(p && p.workload, 80), pod: text(p && p.pod, 253), sidecar: flag(p && p.sidecar), ready: flag(p && p.ready) })),
        };
    }

    function parseHelperOutput(output) {
        if (typeof output !== "string") return null;
        const line = output.trim().split("\n").pop();
        try {
            const value = JSON.parse(line);
            return value && typeof value === "object" && typeof value.state === "string" ? value : null;
        } catch (_error) {
            return null;
        }
    }

    function cleanResult(parsed) {
        return {
            ok: parsed.ok === true,
            state: text(parsed.state, 60),
            message: text(parsed.message, 900),
            plan: cleanPlan(parsed.plan),
            setup: cleanSetup(parsed.setup),
            verify: cleanVerify(parsed.verify),
        };
    }

    // ---- what the buttons may do --------------------------------------------------------------------------------------

    // Enable only for a ready plan and no setup; Verify only for an applied one; Undo for anything that is not removed.
    function availability(view) {
        const idle = view.busy === "" && view.helperAvailable && view.context !== "";
        const phase = view.setup ? view.setup.phase : "";
        const present = phase !== "" && phase !== "rolled-back";
        return {
            plan: idle && !present,
            enable: idle && !present && Boolean(view.plan && view.plan.ready),
            verify: idle && phase === "applied",
            undo: idle && present,
        };
    }

    const PHASE_TEXT = {
        "applying": "A setup did not finish. Undo removes what it created.",
        "applied": "Tracing is set up.",
        "rolling-back": "A removal did not finish. Undo continues it.",
        "rolled-back": "No tracing setup is present.",
        "rollback-failed": "Part of the setup could not be removed. Undo tries again.",
    };

    function headline(view) {
        if (!view.helperAvailable) return "The extension helper is not available, so the tracing setup cannot be changed from here.";
        if (view.context === "") return "Connect a cluster first.";
        if (view.busy === "plan") return "Reading the cluster… nothing is changed.";
        if (view.busy === "enable") return "Waiting for your approval in the system dialog, then setting up. This can take several minutes.";
        if (view.busy === "verify") return "Sending real requests from a temporary Pod…";
        if (view.busy === "undo") return "Waiting for your approval in the system dialog, then removing the setup.";
        if (view.setup) return PHASE_TEXT[view.setup.phase] || "";
        return "Tracing is not set up. Plan shows exactly what would change.";
    }

    // The honest reading of the trace verdict from the backend. Only VERIFIED with every edge counts as verified.
    function describeTrace(trace) {
        if (!trace) return { kind: "idle", lines: [] };
        const edges = Array.isArray(trace.edges) ? trace.edges : [];
        const lines = edges.map((e) => `${e.from} → ${e.to}: ${e.traces} trace${e.traces === 1 ? "" : "s"}`);
        const reporting = Array.isArray(trace.servicesReporting) ? trace.servicesReporting : [];
        const missingServices = Array.isArray(trace.missingServices) ? trace.missingServices : [];
        const missingEdges = Array.isArray(trace.missingEdges) ? trace.missingEdges : [];
        if (reporting.length) lines.push(`Services that delivered spans from their own Pods: ${reporting.join(", ")}`);
        if (missingServices.length) lines.push(`No span from: ${missingServices.join(", ")}`);
        if (missingEdges.length) lines.push(`Calls not seen: ${missingEdges.join(", ")}`);
        const v = trace.reviewsVersion || {};
        if (trace.tracesExamined) lines.push(`reviews-v2 evidence in ${v.v2 || 0} of ${trace.tracesExamined} traces (reviews-v1: ${v.v1 || 0})`);
        switch (trace.verdict) {
            case "VERIFIED": return { kind: "ok", headline: "Verified: real, correlated traces show productpage → details and productpage → reviews-v2 → ratings.", lines };
            case "PARTIAL": return { kind: "warn", headline: "Traces arrived, but not every expected call is linked yet.", lines };
            case "NO_TRACES": return { kind: "idle", headline: "No productpage traces have arrived yet.", lines };
            case "UNREACHABLE": return { kind: "warn", headline: "The trace service did not answer.", lines };
            default: return { kind: "idle", headline: "", lines };
        }
    }

    // ---- controller (no DOM) ------------------------------------------------------------------------------------------

    // env: { helper: {available, run(command, context) -> cleaned result}, context() -> string, fetchJson(url), sleep(ms),
    //        every(fn, ms) -> stop(), onUpdate(view) }
    function createController(env) {
        const s = { context: "", plan: null, setup: null, verify: null, trace: null, busy: "", message: "", outcome: "" };

        function view() {
            return {
                helperAvailable: Boolean(env.helper && env.helper.available),
                context: s.context, plan: s.plan, setup: s.setup, verify: s.verify, trace: s.trace,
                busy: s.busy, message: s.message, outcome: s.outcome,
            };
        }
        function publish() { if (env.onUpdate) env.onUpdate({ ...view(), headline: headline(view()), can: availability(view()), traceView: describeTrace(s.trace) }); }

        async function refresh() {
            s.context = (await env.context()) || "";
            if (s.context !== "" && view().helperAvailable) {
                const r = await env.helper.run("trace-bookinfo-status", s.context);
                if (r && r.ok) s.setup = r.setup;
            }
            publish();
        }

        // Runs one helper command that may take long, polling the saved record so the page shows what is happening.
        async function longCommand(command, busy) {
            s.busy = busy; s.message = ""; s.outcome = "";
            publish();
            const stop = env.every(async () => {
                const r = await env.helper.run("trace-bookinfo-status", s.context);
                if (r && r.ok) { s.setup = r.setup; publish(); }
            }, STATUS_POLL_MS);
            let result;
            try { result = await env.helper.run(command, s.context); } finally { stop(); }
            s.busy = "";
            s.message = result ? result.message : "";
            s.outcome = result && result.ok ? "ok" : "fail";
            if (result && result.setup) s.setup = result.setup;
            return result;
        }

        async function plan() {
            if (!availability(view()).plan) return view();
            s.busy = "plan"; s.message = ""; s.outcome = "";
            publish();
            const r = await env.helper.run("trace-bookinfo-plan", s.context);
            s.busy = "";
            s.plan = r && r.plan ? r.plan : null;
            s.message = r ? r.message : "";
            s.outcome = r && r.ok ? "ok" : "fail";
            publish();
            return view();
        }

        async function readTraces() {
            const deadline = env.now() + TRACE_WAIT_MS;
            for (;;) {
                const answer = await env.fetchJson(`${BACKEND}/trace-path?namespace=${NAMESPACE}`);
                s.trace = answer && answer.status === 200 && answer.body ? answer.body : null;
                publish();
                if (s.trace && s.trace.verdict === "VERIFIED") return;
                if (env.now() >= deadline) return;
                await env.sleep(TRACE_POLL_MS);
            }
        }

        async function enable() {
            if (!availability(view()).enable) return view();
            await longCommand("trace-bookinfo-apply", "enable");
            s.plan = null;   // the plan described the cluster before the change
            publish();
            return view();
        }

        async function verify() {
            if (!availability(view()).verify) return view();
            s.trace = null;
            const r = await longCommand("trace-bookinfo-verify", "verify");
            s.verify = r ? r.verify : null;
            publish();
            if (r && r.ok && r.state === "bookinfo-traffic-sent") await readTraces();
            publish();
            return view();
        }

        async function undo() {
            if (!availability(view()).undo) return view();
            await longCommand("trace-bookinfo-undo", "undo");
            s.verify = null; s.trace = null; s.plan = null;
            await refresh();
            return view();
        }

        return { refresh, plan, enable, verify, undo, view };
    }

    function helperFile(platform) { return platform === "win32" ? "resilience-helper.exe" : "resilience-helper"; }

    function platformOf(dd, nav) {
        const given = dd && dd.host && typeof dd.host.platform === "string" ? dd.host.platform : "";
        if (given) return given;
        const hint = String((nav && nav.platform) || "");
        if (/Mac/i.test(hint)) return "darwin";
        if (/Win/i.test(hint)) return "win32";
        if (/Linux/i.test(hint)) return "linux";
        return "";
    }

    // A context name is only ever passed as one element of an argument list (no shell), and the helper matches it against
    // the real kubeconfig. This only refuses what cannot be a name at all.
    function validContext(name) {
        return typeof name === "string" && name.length > 0 && name.length <= 253 && !/[\u0000-\u001f\u007f]/.test(name);
    }

    // env: { platform, exec(file, args), sleep(ms) }. Without env.exec nothing is available.
    function createHelper(env) {
        const available = Boolean(env && typeof env.exec === "function");
        async function run(command, context) {
            const unavailable = { ok: false, state: "helper-unavailable", message: "The extension helper could not be started.", plan: null, setup: null, verify: null };
            if (!available || !(command in WAIT_MS) || !validContext(context)) return unavailable;
            let work;
            try { work = Promise.resolve(env.exec(helperFile(env.platform), [command, context])); } catch (_error) { return unavailable; }
            const timeout = env.sleep ? env.sleep(WAIT_MS[command]).then(() => ({ timedOut: true })) : null;
            let raced;
            try { raced = await (timeout ? Promise.race([work, timeout]) : work); } catch (error) { raced = { stdout: error && typeof error.stdout === "string" ? error.stdout : "" }; }
            if (raced && raced.timedOut) return { ...unavailable, state: "timed-out", message: "Docker Desktop did not answer in time. The saved record shows how far it got; use Undo if it did not finish." };
            const parsed = parseHelperOutput(raced && typeof raced.stdout === "string" ? raced.stdout : "");
            return parsed ? cleanResult(parsed) : unavailable;
        }
        return { available, run };
    }

    window.__resilienceBookinfo = { createController, createHelper, availability, headline, describeTrace, cleanPlan, cleanSetup, cleanVerify, cleanResult, parseHelperOutput, validContext, helperFile, platformOf, WAIT_MS };
    if (!window.document || !window.document.addEventListener) return;

    // ---- browser wiring -----------------------------------------------------------------------------------------------

    const card = document.querySelector('[data-role="bookinfo-card"]');
    if (!card) return;
    const part = (role) => card.querySelector(`[data-role="${role}"]`);
    const nodes = {
        headline: part("bookinfo-headline"), message: part("bookinfo-message"), plan: part("bookinfo-plan"), enable: part("bookinfo-enable"),
        verify: part("bookinfo-verify"), undo: part("bookinfo-undo"), details: part("bookinfo-details"), steps: part("bookinfo-steps"),
        trace: part("bookinfo-trace"), traceHeadline: part("bookinfo-trace-headline"), traceLines: part("bookinfo-trace-lines"),
    };

    function el(tag, className, content) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (content !== undefined) node.textContent = content;
        return node;
    }

    const LABELS = { plan: "Plan", enable: "Enable Bookinfo tracing", verify: "Verify", undo: "Undo" };

    function renderPlan(plan) {
        const out = [];
        if (!plan) return out;
        out.push(el("h4", "section-header__title", plan.ready ? "The plan is ready. These changes would be made, in this order:" : "The plan is blocked:"));
        if (plan.blockers.length) {
            const ul = el("ul", "connect__apps");
            plan.blockers.forEach((b) => { const li = el("li", "connect__app", b.message); li.dataset.state = "fail"; ul.append(li); });
            out.push(ul);
        }
        if (plan.preview.length) {
            const ol = el("ol", "connect__steps");
            plan.preview.forEach((c) => ol.append(el("li", "", `${c.action} ${c.kind}${c.name ? " " + c.name : ""}${c.detail ? " — " + c.detail : ""}`)));
            out.push(ol);
        }
        if (plan.disruptions.length) {
            out.push(el("p", "ui-note", "Expected downtime (one replica of each, usually under a minute, not measured): " +
                plan.disruptions.map((d) => `${d.workload}: ${d.effect}`).join("; ") + "."));
        }
        if (plan.warnings.length) plan.warnings.forEach((w) => out.push(el("p", "ui-note", w)));
        if (plan.jaeger) out.push(el("p", "ui-note", `Jaeger receiver found at ${plan.jaeger.ipv4}:${plan.jaeger.port} from ${plan.jaeger.nodes.length} node(s).`));
        return out;
    }

    function render(v) {
        card.hidden = false;
        nodes.headline.textContent = v.headline;
        nodes.message.textContent = v.message;
        nodes.message.dataset.state = v.outcome;
        for (const name of ["plan", "enable", "verify", "undo"]) {
            const button = nodes[name];
            button.disabled = !v.can[name];
            button.textContent = v.busy !== "" && ((name === "enable" && v.busy === "enable") || v.busy === name) ? "Working…" : LABELS[name];
        }
        nodes.details.replaceChildren(...renderPlan(v.plan));
        const steps = v.setup ? v.setup.steps : [];
        nodes.steps.replaceChildren(...steps.map((s) => { const li = el("li", "connect__app", `${s.step}: ${s.status}${s.detail ? " (" + s.detail + ")" : ""}`); li.dataset.state = s.status === "failed" ? "fail" : s.status === "done" ? "ok" : "unknown"; return li; }));
        if (v.setup && v.setup.remaining.length) v.setup.remaining.forEach((r) => { const li = el("li", "connect__app", "Remaining: " + r); li.dataset.state = "fail"; nodes.steps.append(li); });
        nodes.trace.hidden = !v.traceView.headline;
        nodes.traceHeadline.textContent = v.traceView.headline || "";
        nodes.traceHeadline.dataset.kind = v.traceView.kind;
        nodes.traceLines.replaceChildren(...v.traceView.lines.map((line) => el("li", "connect__app", line)));
    }

    async function fetchJson(url) {
        try {
            const response = await fetch(url);
            let body = null;
            try { body = await response.json(); } catch (_error) { body = null; }
            return { status: response.status, body };
        } catch (_error) {
            return { status: 0, body: null };
        }
    }

    const dd = window.ddClient;
    const execFn = dd && dd.extension && dd.extension.host && dd.extension.host.cli && dd.extension.host.cli.exec;
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const helper = createHelper({ platform: platformOf(dd, window.navigator), exec: execFn ? (file, args) => execFn(file, args) : null, sleep });
    const controller = createController({
        helper,
        context: async () => {
            const answer = await fetchJson("/_extension/cluster");
            const name = answer && answer.status === 200 && answer.body ? answer.body.context : "";
            return typeof name === "string" ? name : "";
        },
        fetchJson, sleep, now: () => Date.now(),
        every: (fn, ms) => { const id = setInterval(fn, ms); return () => clearInterval(id); },
        onUpdate: render,
    });
    nodes.plan.addEventListener("click", () => { controller.plan(); });
    nodes.enable.addEventListener("click", () => { controller.enable(); });
    nodes.verify.addEventListener("click", () => { controller.verify(); });
    nodes.undo.addEventListener("click", () => { controller.undo(); });
    document.addEventListener("DOMContentLoaded", () => { controller.refresh(); });
    window.addEventListener("resilience-cluster-changed", () => { controller.refresh(); });
})();
