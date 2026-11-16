/*
 * Workflow display names, shared by the Overview and Recommendations pages.
 *
 * A workflow's identity is its stable id (frozen id, declaration id, editor id). Some also carry the name a person gave them
 * in the workflow editor. This turns an id into that name for DISPLAY ONLY: ids stay the keys everywhere (lookups, requests,
 * proposals, digests), and an id with no name is shown as it is. Two workflows with the same name are told apart by a short
 * id suffix, so a label never points at the wrong workflow. No DOM; nothing is estimated.
 */
(function (global) {
    "use strict";

    let names = new Map();

    function build(detail) {
        const list = detail && detail.portfolio && Array.isArray(detail.portfolio.workflows) ? detail.portfolio.workflows : [];
        const named = list.filter((w) => w && typeof w.workflowId === "string" && typeof w.name === "string" && w.name.trim());
        const count = new Map();
        for (const w of list) {
            const label = named.includes(w) ? w.name.trim() : w.workflowId;
            count.set(label, (count.get(label) || 0) + 1);
        }
        const out = new Map();
        for (const w of named) {
            const label = w.name.trim();
            out.set(w.workflowId, count.get(label) > 1 ? `${label} (${w.workflowId.slice(0, 6)})` : label);
        }
        return out;
    }

    /** Remember the names the engine reported with this detail. Call whenever a new detail arrives. */
    function use(detail) { names = build(detail); }

    /** The name to show for an id (the id itself when it has none). */
    function label(id) { return names.get(id) || id; }

    global.ResilienceWorkflowNames = { use, label, build };
})(typeof window !== "undefined" ? window : globalThis);
