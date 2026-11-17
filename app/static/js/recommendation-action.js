/*
 * The selected change as the one actionable item on the Recommendations page. Owned by: Member 4.
 *
 * plan(facts)   what the page can do next, as data: a headline, ONE primary action (or none, with the reason), and a secondary one.
 * render(...)   draws it: the change and its predicted effect, the placement map, the progress, the action.
 * dialog(...)   the explicit confirmation before anything is approved, applied or put back.
 *
 * It decides nothing the server decides. Whether a change may be approved, applied or reset is the server's answer, arriving here as
 * facts (approval status, canApply, the run's own flags); a missing fact never becomes an enabled button. Without a reviewed workflow
 * there is no Apply button at all: the page says why and points at the way to establish the evidence. The configured name is
 * attribution for the audit record, not authentication. Text from the API is always written with textContent.
 */
(function (global) {
    "use strict";

    const NAME_KEY = "resilience.audit.name";
    const isNumber = (value) => typeof value === "number" && Number.isFinite(value);
    const percent = (value) => (isNumber(value) ? `${Number(value.toFixed(2))}%` : "not reported");

    const attribution = {
        get() {
            try { return (global.localStorage && global.localStorage.getItem(NAME_KEY)) || ""; } catch (_error) { return ""; }
        },
        set(value) {
            const text = String(value || "").trim();
            if (text.length < 2) return false;
            try { global.localStorage.setItem(NAME_KEY, text); } catch (_error) { /* The name still works for this page view. */ }
            return true;
        },
        /** Header value for the server: attribution only. URL-encoded so any name survives. */
        header() {
            const name = this.get();
            return name ? { "X-Operator-Name": encodeURIComponent(name) } : {};
        },
    };

    // ---- what to do next -------------------------------------------------------------------------------------------------

    /**
     * facts: {
     *   status: "loading" | "needs-input" | "ready" | "blocked",
     *   blockedBy: null | "workflow" | "evidence",  blockedMessage, blockedCode, retryable,
     *   mode: "workflow" | "deployment",  declared: boolean,
     *   candidate: null | { title, target, ... },
     *   approval: "none" | "PENDING_APPROVAL" | "APPROVED" | "REJECTED",
     *   approvalReady: boolean, approvalReason: string,
     *   run: null | { busy, resetting, finished, failed, canReset, canKeep, kept, reset, message },
     *   validation: null | { text, tone: "ok" | "warn" | "plain" } (what the run measured, shown beside the Keep or Reset decision),
     *   runIsOther: boolean (the run belongs to a different change that is still applied), canApply: boolean | null, name: string }
     * -> { phase, title, text, primary, secondary }  where an action is { id, label, disabled, reason }
     */
    function plan(f) {
        const facts = f || {};
        const run = facts.run || null;
        const action = (id, label, extra) => ({ id, label, disabled: false, reason: "", ...(extra || {}) });
        const out = { phase: "ready", title: "", text: "", primary: null, secondary: null };

        if (facts.status === "loading") return { ...out, phase: "loading", title: "Reading the cluster…", text: "Checking placement and the reviewed workflows." };
        if (facts.status === "needs-input") return { ...out, phase: "needs-input", title: "Choose an application", text: "Pick one above to see its best change." };

        if (run && run.busy) {
            return { ...out, phase: "running", title: run.resetting ? "Putting the previous configuration back…" : "Applying and checking…",
                text: run.message || "Follow the rollout below. Nothing else can change the cluster meanwhile.",
                primary: action("busy", run.resetting ? "Restoring…" : "Running…", { disabled: true, reason: "A run is in progress." }) };
        }
        if (run && run.canReset && run.finished) {
            return { ...out, phase: "decide", title: facts.runIsOther ? "A different change is still applied. Keep it or reset it first."
                    : run.failed ? "The run stopped with the change in place" : "The change is on the cluster. Keep it or put it back?",
                text: run.canKeep ? "Reset restores the saved configuration. Keep leaves it as it is and records that you decided so. Results stay in history either way."
                    : "Reset restores the saved configuration. Results stay in history.",
                primary: action("reset", "Reset now"), secondary: run.canKeep ? action("keep", "Keep changes") : null };
        }

        if (facts.blockedBy === "workflow") {
            return { ...out, phase: "needs-workflow", title: "This change can't be applied yet",
                text: facts.mode === "deployment"
                    ? "This is a service-level simulation. Without a current operational workflow review, business impact is unknown and approval and Apply stay blocked. Open Workflow Manager, review or create the real definition and its dependency roles, then use Review & Save → Use this real workflow for operational analysis. Record how it was verified, save, and check again here. Saving a research declaration alone does not establish operational evidence."
                    : (facts.blockedMessage || "A reviewed workflow definition is required before a change can be approved."),
                primary: action("workflows", "Review a workflow"), secondary: action("recheck", "I've reviewed one — check again") };
        }
        if (facts.status !== "ready" || facts.blockedBy) {
            return { ...out, phase: "blocked", title: "Required evidence is not available",
                text: facts.blockedMessage || "A recommendation cannot be generated yet.",
                primary: facts.retryable === false ? null : action("recheck", "Check again") };
        }
        if (!facts.candidate) {
            return { ...out, phase: "no-change", title: "No change can be put forward",
                text: "The engine found no feasible change that helps. That is not a finding that the application is resilient." };
        }

        const status = facts.approval || "none";

        if (run && run.kept) {
            // The change is settled and no longer blocks another one; the list below was recalculated from the cluster as it is now.
            const canReset = run.canReset || run.canResetKept;
            return { ...out, phase: "kept", title: "Change kept",
                text: "It stays on the cluster and in the record of applied changes. The risks and changes below were recalculated from the cluster as it is now. You can still reset it.",
                primary: action("choose", "Choose another change"), secondary: canReset ? action("reset", "Reset change") : null };
        }
        if (run && run.reset) {
            return { ...out, phase: "restored", title: "Previous configuration restored", text: "The results are saved in history. You can apply this change again or choose another.",
                primary: status === "APPROVED" ? action("apply", "Apply again") : action("choose", "Choose another change") };
        }
        if (facts.approvalLoaded === false) {
            return { ...out, phase: "checking-approval", title: "Checking this proposal's saved approval",
                text: "Approval and Apply wait until the saved decision for this selection can be read.",
                primary: facts.message ? action("recheck", "Check again") : null };
        }
        if (facts.approvalStale && status === "PENDING_APPROVAL") {
            return { ...out, phase: "stale", title: "The proposal changed since this request",
                text: "This earlier request cannot authorize the selected proposal. Reject it, then review and request approval for the current change.",
                primary: action("reject", "Reject earlier request"), secondary: action("recheck", "Refresh evidence") };
        }
        if (status === "APPROVED") {
            const blocked = facts.canApply === false;
            return { ...out, phase: "approved", title: "Approved. Ready to apply to the cluster.",
                text: "Applying saves the current configuration, applies exactly the approved change, waits for the rollout and recalculates the risk. You decide afterwards.",
                primary: blocked ? action("apply", "Apply to cluster", { disabled: true, reason: facts.applyReason || "The server says this cannot be applied now." }) : action("apply", "Apply to cluster") };
        }
        if (status === "REJECTED") {
            return { ...out, phase: "rejected", title: "This change was rejected", text: "Choose another change, or refresh if the evidence has changed.", primary: action("choose", "Choose another change") };
        }
        if (status === "PENDING_APPROVAL") {
            return { ...out, phase: "pending", title: "Waiting for your decision",
                text: "Approving authorises exactly this change. Nothing is applied until you apply it.",
                primary: facts.declared ? action("go-approve", "Decide on the request") : action("approve", "Approve this change"),
                secondary: facts.declared ? null : action("reject", "Reject") };
        }
        // No approval yet.
        if (facts.declared) {
            return { ...out, phase: "research", title: "Research approval only",
                text: "The workflow is declared, not observed. A named person can approve it for validation on a test cluster; it is never a production approval.",
                primary: action("go-approve", "Request research approval") };
        }
        if (!facts.approvalReady) {
            return { ...out, phase: "not-approvable", title: "This change can't be approved",
                text: facts.approvalReason || "The server did not accept this change for approval.", primary: action("choose", "Choose another change") };
        }
        return { ...out, phase: "review", title: "Review the change, then approve it",
            text: "Approving authorises exactly this change. Nothing is applied until you apply it, and you can reset it afterwards.",
            primary: action("approve", "Review and approve") };
    }

    // ---- drawing ---------------------------------------------------------------------------------------------------------

    function el(tag, text, className, attributes) {
        const node = global.document.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, String(value));
        return node;
    }

    function figure(candidate) {
        const box = el("div", undefined, "rx-dock__figure");
        if (!candidate || !isNumber(candidate.before) || !isNumber(candidate.after)) return box;
        const gain = candidate.after < candidate.before;
        box.setAttribute("data-gain", String(gain));
        box.append(el("span", candidate.scope, "rx-dock__scope"),
            el("span", `${percent(candidate.before)} → ${percent(candidate.after)}`, "rx-dock__numbers"),
            el("span", "predicted by simulation, not measured", "rx-dock__caveat"));
        return box;
    }

    function nameField(handlers, facts, model) {
        const name = attribution.get();
        const box = el("div", undefined, "rx-dock__who");
        if (name && !facts.editingName) {
            const chip = el("span", undefined, "rx-dock__chip");
            chip.append(el("span", "Acting as ", "rx-dock__chip-label"), el("strong", name));
            const change = el("button", "Change", "rx-dock__link", { type: "button" });
            change.addEventListener("click", () => handlers.editName && handlers.editName());
            box.append(chip, change);
            return box;
        }
        if (!facts.editingName) {
            // Only where a name will be needed: it is asked once, in the dialog, and is a label for the record, not a login.
            if (!["review", "pending", "approved"].includes(model.phase)) return null;
            box.append(el("span", "Your name is asked once, for the audit record. It is a label, not a login.", "rx-dock__fine"));
            return box;
        }
        const label = el("label", "Your name, for the audit record", "rx-dock__chip-label", { for: "rx-dock-name" });
        const input = el("input", undefined, "form-input rx-dock__input", { type: "text", id: "rx-dock-name", autocomplete: "name", placeholder: "Your name" });
        input.value = name;
        const save = el("button", "Save", "secondary-button", { type: "button" });
        const commit = () => { if (attribution.set(input.value)) handlers.nameChanged && handlers.nameChanged(); else input.focus(); };
        save.addEventListener("click", commit);
        input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); commit(); } });
        box.append(label, input, save);
        return box;
    }

    /**
     * Draw the dock into `root`. `facts.candidate` carries { title, target, scope, before, after, workflows, cost, tested };
     * `facts.map` carries { nodes, current, after, failedNode } for the shared placement map.
     * handlers: { onAction(id), editName(), nameChanged() }
     */
    function render(root, facts, handlers) {
        const model = plan(facts);
        const candidate = facts.candidate || null;
        // The template owns the three slots (the rail stays where the page put it); this fills the head and the body around it.
        const headSlot = root.querySelector('[data-role="rec-dock-head"]');
        const bodySlot = root.querySelector('[data-role="rec-dock-body"]');
        headSlot.replaceChildren();
        bodySlot.replaceChildren();
        root.hidden = false;
        root.setAttribute("data-phase", model.phase);

        const head = el("div", undefined, "rx-dock__head");
        const words = el("div", undefined, "rx-dock__words");
        if (candidate) {
            words.append(el("span", "Selected change", "rx-dock__kicker"), el("h3", candidate.title, "rx-dock__title"), el("p", candidate.target, "rx-dock__target"));
            const bits = [];
            if (candidate.workflows) bits.push(candidate.workflows);
            if (candidate.cost) bits.push(candidate.cost);
            if (candidate.tested) bits.push(candidate.tested);
            if (bits.length) words.append(el("p", bits.join(" · "), "rx-dock__facts"));
        } else {
            words.append(el("span", "Next step", "rx-dock__kicker"));
        }
        head.append(words, figure(candidate));
        headSlot.append(head);

        const act = el("div", undefined, "rx-dock__act");
        act.append(el("h4", model.title, "rx-dock__state"), el("p", model.text, "rx-dock__text"));
        if (facts.validation) act.append(el("p", facts.validation.text, "rx-dock__validation", { "data-tone": facts.validation.tone }));
        if (facts.checks && facts.checks.length) {
            const list = el("ul", undefined, "rx-dock__checks");
            for (const check of facts.checks) {
                const item = el("li", undefined, "rx-dock__check", { "data-state": check.state });
                item.append(el("span", check.state === "ok" ? "✓" : check.state === "no" ? "✕" : "○", "rx-dock__mark", { "aria-hidden": "true" }), el("span", check.text));
                list.append(item);
            }
            act.append(list);
        }
        const buttons = el("div", undefined, "rx-dock__buttons");
        const make = (item, primary) => {
            const button = el(item.id === "workflows" ? "a" : "button", item.label, primary ? "primary-button" : "secondary-button", { "data-action": item.id });
            if (item.id === "workflows") button.setAttribute("href", handlers.hrefFor ? handlers.hrefFor("workflows") : "#");
            else button.setAttribute("type", "button");
            if (item.disabled) { button.disabled = true; button.setAttribute("aria-disabled", "true"); if (item.reason) button.setAttribute("title", item.reason); }
            else button.addEventListener("click", (event) => { if (item.id !== "workflows") event.preventDefault(); handlers.onAction(item.id, button); });
            return button;
        };
        if (model.primary) buttons.append(make(model.primary, true));
        if (model.secondary) buttons.append(make(model.secondary, false));
        act.append(buttons);
        const reason = [model.primary, model.secondary].find((item) => item && item.disabled && item.reason);
        if (reason && reason.reason !== "A run is in progress.") act.append(el("p", reason.reason, "rx-dock__why", { role: "status" }));
        if (facts.message) act.append(el("p", facts.message, "rx-dock__alert", { role: "alert" }));
        const who = nameField(handlers, facts, model);
        if (who) act.append(who);
        headSlot.append(act);
        if (facts.map && global.ResiliencePlacement && (facts.map.current || []).length) {
            const details = el("details", undefined, "rx-dock__map", { "data-role": "rec-dock-map" });
            if (["review", "pending", "approved", "research", "restored", "needs-workflow", "not-approvable"].includes(model.phase)) details.open = true;
            const summary = el("summary", facts.map.after ? "Where the pods run: now and after the change" : "Where the pods run");
            const holder = el("div", undefined, "rx-dock__map-body");
            details.append(summary, holder);
            bodySlot.append(details);
            drawMap(holder, facts.map);
        }


        return model;
    }

    function drawMap(holder, map) {
        const Placement = global.ResiliencePlacement;
        const state = { view: map.after ? "after" : "today", failed: map.failedNode || null };
        const bar = el("div", undefined, "rx-dock__map-bar");
        bar.append(el("p", "Simulated placement is a prediction; the scheduler decides where a new pod really lands.", "rx-dock__fine"));
        const grid = el("div");
        const toggle = el("div", undefined, "rx-seg", { role: "group", "aria-label": "Placement shown" });
        const redraw = () => {
            toggle.querySelectorAll("button").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.view === state.view)));
            Placement.draw(grid, { nodes: map.nodes, placement: map.current, after: map.after, view: state.view, failedNode: state.failed,
                onSelect(node) { state.failed = node; redraw(); } });
        };
        if (map.after) {
            for (const [view, label] of [["today", "Now"], ["after", "After the change"]]) {
                const button = el("button", label, "rx-seg__btn", { type: "button", "data-view": view });
                button.addEventListener("click", () => { state.view = view; redraw(); });
                toggle.append(button);
            }
            bar.append(toggle);
        }
        holder.append(bar, grid);
        redraw();
    }

    // ---- the explicit confirmation -----------------------------------------------------------------------------------------

    /**
     * Open a modal and resolve once it is closed: { confirmed: boolean }. `body` is a DOM node. `onConfirm` may be async and return an
     * error message (a string) to keep the dialog open and show it, or nothing to close it.
     */
    function dialog(options) {
        const doc = global.document;
        const previous = doc.activeElement;
        const backdrop = el("div", undefined, "modal-backdrop is-visible", { "aria-hidden": "false", "data-role": "action-dialog" });
        const box = el("div", undefined, "modal rx-dialog", { role: "dialog", "aria-modal": "true", "aria-label": options.title });
        const header = el("div", undefined, "modal__header");
        header.append(el("h3", options.title, "modal__title"));
        const body = el("div", undefined, "modal__body");
        body.append(options.body);
        let nameInput = null;
        if (options.askName && !attribution.get()) {
            const label = el("label", "Your name, for the audit record (asked once)", "rx-dialog__label");
            nameInput = el("input", undefined, "form-input", { type: "text", autocomplete: "name", placeholder: "Your name" });
            label.append(nameInput, el("span", "A label for the record, not a login.", "rx-dock__fine"));
            body.append(label);
        }
        const problem = el("p", "", "approval-note", { role: "alert", hidden: "" });
        problem.hidden = true;
        body.append(problem);
        const footer = el("div", undefined, "modal__footer");
        const cancel = el("button", options.cancelLabel || "Cancel", "secondary-button", { type: "button" });
        const confirm = el("button", options.confirmLabel, options.danger ? "danger-button" : "primary-button", { type: "button", "data-role": "dialog-confirm" });
        footer.append(cancel, confirm);
        box.append(header, body, footer);
        backdrop.append(box);
        doc.body.append(backdrop);
        return new Promise((resolve) => {
            const close = (confirmed) => {
                doc.removeEventListener("keydown", onKey, true);
                backdrop.remove();
                if (previous && previous.focus) previous.focus();
                resolve({ confirmed });
            };
            const onKey = (event) => {
                if (event.key === "Escape") { event.stopPropagation(); close(false); }
                if (event.key === "Tab") {
                    const stops = Array.from(box.querySelectorAll("button, input, textarea, select, a[href]")).filter((node) => !node.disabled);
                    if (!stops.length) return;
                    const first = stops[0], last = stops[stops.length - 1];
                    if (event.shiftKey && doc.activeElement === first) { event.preventDefault(); last.focus(); }
                    else if (!event.shiftKey && doc.activeElement === last) { event.preventDefault(); first.focus(); }
                }
            };
            doc.addEventListener("keydown", onKey, true);
            cancel.addEventListener("click", () => close(false));
            confirm.addEventListener("click", async () => {
                if (nameInput && !attribution.set(nameInput.value)) {
                    problem.textContent = "Enter your name (at least two characters) so the record says who did this.";
                    problem.hidden = false; nameInput.focus(); return;
                }
                confirm.disabled = true; cancel.disabled = true; problem.hidden = true;
                let failure = null;
                try { failure = options.onConfirm ? await options.onConfirm() : null; } catch (error) { failure = (error && error.message) || "That did not work."; }
                if (failure) { problem.textContent = failure; problem.hidden = false; confirm.disabled = false; cancel.disabled = false; confirm.focus(); return; }
                close(true);
            });
            (nameInput || confirm).focus();
        });
    }

    global.ResilienceAction = { plan, render, dialog, attribution };
})(typeof window !== "undefined" ? window : globalThis);
