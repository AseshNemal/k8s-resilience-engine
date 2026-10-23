// A small DOM that behaves like a tree, for tests of page lifecycles. TEST-ONLY.
//
// The older page tests use a stub whose querySelector(selector) returns one shared object per selector wherever it is asked, which
// cannot tell a handler attached to a rendered element from one attached to a stale or never-inserted one. This one keeps real
// parents and children: querySelector searches only the subtree it is called on, replaceChildren detaches what it replaces,
// <template> content is cloned (not shared), isConnected follows the parent chain to the document, and events bubble. It parses
// the application's real template markup, so a data-role misspelt between the template and the script fails here.
//
// It implements only what the application's scripts use. It is not a browser: nothing here is layout, focus order or CSS.

const VOID = new Set(["input", "br", "hr", "img", "meta", "link"]);
const BOOLEAN = new Set(["hidden", "disabled", "open", "checked"]);

class TextNode {
    constructor(text) { this.nodeType = 3; this.data = String(text); this.parentNode = null; }
    get textContent() { return this.data; }
    set textContent(value) { this.data = String(value); }
    cloneNode() { return new TextNode(this.data); }
}

function matcher(selector) {
    const parts = [];
    const re = /(#[\w-]+)|(\.[\w-]+)|(\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\])|([a-zA-Z][\w-]*)/g;
    let found;
    while ((found = re.exec(selector)) !== null) {
        if (found[1]) parts.push((el) => el.getAttribute("id") === found[1].slice(1));
        else if (found[2]) { const cls = found[2].slice(1); parts.push((el) => el.className.split(/\s+/).includes(cls)); }
        else if (found[3]) {
            const name = found[4];
            const expected = found[5] ?? found[6] ?? found[7];
            parts.push((el) => (expected === undefined ? el.hasAttribute(name) : el.getAttribute(name) === expected));
        } else if (found[8]) { const tag = found[8].toLowerCase(); parts.push((el) => el.tag === tag); }
    }
    return (el) => parts.every((test) => test(el));
}

export class Element {
    constructor(tag, doc) {
        this.nodeType = 1;
        this.tag = String(tag).toLowerCase();
        this.tagName = this.tag.toUpperCase();
        this.ownerDocument = doc;
        this.attrs = new Map();
        this.childNodes = [];
        this.parentNode = null;
        this.listeners = {};
        this.style = {};
        this._value = "";
        this.content = null;
    }

    get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
    get firstChild() { return this.childNodes[0] || null; }
    get className() { return this.getAttribute("class") || ""; }
    set className(v) { this.setAttribute("class", v); }
    get id() { return this.getAttribute("id") || ""; }
    set id(v) { this.setAttribute("id", v); }
    get type() { return this.getAttribute("type") || (this.tag === "button" ? "submit" : "text"); }
    set type(v) { this.setAttribute("type", v); }
    get placeholder() { return this.getAttribute("placeholder") || ""; }
    set placeholder(v) { this.setAttribute("placeholder", v); }
    get hidden() { return this.attrs.has("hidden"); }
    set hidden(v) { v ? this.setAttribute("hidden", "") : this.removeAttribute("hidden"); }
    get disabled() { return this.attrs.has("disabled"); }
    set disabled(v) { v ? this.setAttribute("disabled", "") : this.removeAttribute("disabled"); }
    get open() { return this.attrs.has("open"); }
    set open(v) { v ? this.setAttribute("open", "") : this.removeAttribute("open"); }
    get value() { return this._value; }
    set value(v) { this._value = String(v); }
    get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === this.ownerDocument.documentElement; }
    get classList() {
        const self = this;
        return {
            add(c) { const s = new Set(self.className.split(/\s+/).filter(Boolean)); s.add(c); self.className = [...s].join(" "); },
            remove(c) { self.className = self.className.split(/\s+/).filter((x) => x && x !== c).join(" "); },
            contains(c) { return self.className.split(/\s+/).includes(c); },
            toggle(c) { const on = !this.contains(c); on ? this.add(c) : this.remove(c); return on; },
        };
    }

    get textContent() { return this.childNodes.map((n) => n.textContent).join(""); }
    set textContent(value) {
        this.childNodes.forEach((n) => { n.parentNode = null; });
        this.childNodes = [];
        if (value !== "" && value !== null && value !== undefined) this.appendChild(new TextNode(value));
    }

    setAttribute(name, value) {
        this.attrs.set(name, BOOLEAN.has(name) ? "" : String(value));
    }
    getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
    hasAttribute(name) { return this.attrs.has(name); }
    removeAttribute(name) { this.attrs.delete(name); }

    _adopt(node) {
        if (node.nodeType === 11) { const moved = [...node.childNodes]; node.childNodes = []; moved.forEach((n) => this._adopt(n)); return; }
        if (typeof node === "string") node = new TextNode(node);
        if (node.parentNode) node.parentNode.childNodes = node.parentNode.childNodes.filter((n) => n !== node);
        node.parentNode = this;
        this.childNodes.push(node);
    }
    appendChild(node) { this._adopt(typeof node === "string" ? new TextNode(node) : node); return node; }
    append(...nodes) { nodes.forEach((n) => this._adopt(typeof n === "string" ? new TextNode(n) : n)); }
    prepend(...nodes) {
        const before = [...this.childNodes];
        this.childNodes = [];
        nodes.forEach((n) => this._adopt(typeof n === "string" ? new TextNode(n) : n));
        before.forEach((n) => { n.parentNode = null; this._adopt(n); });
    }
    replaceChildren(...nodes) {
        this.childNodes.forEach((n) => { n.parentNode = null; });
        this.childNodes = [];
        this.append(...nodes);
    }
    remove() { if (this.parentNode) { this.parentNode.childNodes = this.parentNode.childNodes.filter((n) => n !== this); this.parentNode = null; } }

    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    dispatchEvent(event) {
        event.target = event.target || this;
        let node = this;
        while (node) {
            for (const fn of node.listeners[event.type] || []) fn.call(node, event);
            node = event.bubbles ? node.parentNode : null;
        }
        return true;
    }
    click() { if (!this.disabled) this.dispatchEvent({ type: "click", bubbles: true }); }
    focus() { this.ownerDocument.activeElement = this; }

    _walk(visit) {
        for (const child of this.childNodes) {
            if (child.nodeType !== 1) continue;
            if (visit(child) === false) return false;
            if (child._walk(visit) === false) return false;
        }
        return true;
    }
    querySelector(selector) {
        const test = matcher(selector);
        let found = null;
        this._walk((el) => { if (test(el)) { found = el; return false; } return true; });
        return found;
    }
    querySelectorAll(selector) {
        const test = matcher(selector);
        const all = [];
        this._walk((el) => { if (test(el)) all.push(el); return true; });
        return all;
    }

    cloneNode(deep) {
        const copy = new Element(this.tag, this.ownerDocument);
        this.attrs.forEach((v, k) => copy.attrs.set(k, v));
        copy._value = this._value;
        if (this.content) copy.content = this.content.cloneNode(true);
        if (deep) this.childNodes.forEach((n) => copy._adopt(n.cloneNode(true)));
        return copy;
    }
}

export class Fragment {
    constructor(doc) { this.nodeType = 11; this.ownerDocument = doc; this.childNodes = []; this.parentNode = null; }
    get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
    _adopt(node) { if (node.parentNode) node.parentNode.childNodes = node.parentNode.childNodes.filter((n) => n !== node); node.parentNode = this; this.childNodes.push(node); }
    append(...nodes) { nodes.forEach((n) => this._adopt(n)); }
    appendChild(node) { this._adopt(node); return node; }
    querySelector(selector) { for (const c of this.children) { if (matcher(selector)(c)) return c; const f = c.querySelector(selector); if (f) return f; } return null; }
    cloneNode(deep) { const copy = new Fragment(this.ownerDocument); if (deep) this.childNodes.forEach((n) => copy._adopt(n.cloneNode(true))); return copy; }
}

export class Document {
    constructor() {
        this.documentElement = new Element("html", this);
        this.body = new Element("body", this);
        this.documentElement.appendChild(this.body);
        this.activeElement = null;
        this.listeners = {};
        this.visibilityState = "visible";
    }
    createElement(tag) { const el = new Element(tag, this); if (el.tag === "template") el.content = new Fragment(this); return el; }
    createTextNode(text) { return new TextNode(text); }
    querySelector(selector) { return this.documentElement.querySelector(selector); }
    querySelectorAll(selector) { return this.documentElement.querySelectorAll(selector); }
    getElementById(id) { return this.documentElement.querySelector(`#${id}`); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
}

// ---- parsing the application's own markup ---------------------------------------------------------------------------------------

const TOKEN = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^\s=>\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|[^<]+/g;
const ATTR = /([^\s=>\/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;

/** Parse markup (Jinja tags are dropped) into the document's elements under ``parent``. */
export function parseInto(doc, parent, markup) {
    const source = markup.replace(/\{#[\s\S]*?#\}/g, "").replace(/\{%[\s\S]*?%\}/g, "").replace(/\{\{[\s\S]*?\}\}/g, "");
    const stack = [parent];
    let token;
    TOKEN.lastIndex = 0;
    while ((token = TOKEN.exec(source)) !== null) {
        const [whole, closing, opening, attributes, selfClosing] = token;
        const top = stack[stack.length - 1];
        const target = top.tag === "template" ? top.content : top;
        if (whole.startsWith("<!--")) continue;
        if (closing) {
            const at = stack.map((e) => e.tag).lastIndexOf(closing.toLowerCase());
            if (at > 0) stack.length = at;
        } else if (opening) {
            const el = doc.createElement(opening);
            let attr;
            ATTR.lastIndex = 0;
            while ((attr = ATTR.exec(attributes || "")) !== null) el.setAttribute(attr[1], attr[2] ?? attr[3] ?? attr[4] ?? "");
            target.appendChild(el);
            if (!VOID.has(el.tag) && !selfClosing) stack.push(el);
        } else if (whole.trim() !== "" || (target.childNodes.length && whole.includes(" "))) {
            target.appendChild(new TextNode(whole.replace(/\s+/g, " ")));
        }
    }
}

/** The text of ``html`` between the first ``start`` marker and the matching closing tag, from the real template file. */
export function extractBlock(html, openingPattern, closingTag) {
    const begin = html.search(openingPattern);
    if (begin < 0) throw new Error(`no block matching ${openingPattern}`);
    const end = html.indexOf(closingTag, begin);
    return html.slice(begin, end + closingTag.length);
}

// ---- scripted time and network ---------------------------------------------------------------------------------------------

export class FakeTimers {
    constructor() { this.now = 1_000_000; this.queue = []; this.nextId = 1; }
    schedule = (fn, ms) => { const id = this.nextId++; this.queue.push({ id, at: this.now + ms, fn }); return id; };
    cancel = (id) => { this.queue = this.queue.filter((t) => t.id !== id); };
    clock = () => this.now;
    get pending() { return this.queue.length; }
    /** Move time forward and run what became due (each callback may schedule more). */
    async advance(ms) {
        const until = this.now + ms;
        for (;;) {
            const due = this.queue.filter((t) => t.at <= until).sort((a, b) => a.at - b.at)[0];
            if (!due) break;
            this.queue = this.queue.filter((t) => t !== due);
            this.now = due.at;
            due.fn();
            await flush();
        }
        this.now = until;
    }
}

/** Let promise continuations (and the ones they start) run. */
export async function flush(times = 12) {
    for (let i = 0; i < times; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

/** A fetch whose responses a test hands out one by one, so ordering, delay and loss can be staged. */
export function scriptedFetch() {
    const calls = [];
    const waiting = [];
    const impl = (url, init = {}) => new Promise((resolve, reject) => {
        const call = { method: init.method || "GET", url, body: init.body ? JSON.parse(init.body) : null, resolve, reject };
        calls.push(call);
        waiting.push(call);
    });
    const reply = (call, status, body) => call.resolve({ ok: status < 400, status, json: async () => body, headers: { get: () => null } });
    return {
        impl, calls,
        pending: () => waiting.filter((c) => !c.done),
        /** Answer the oldest unanswered request matching ``method url``; throws if there is none. */
        respond(method, url, status, body) {
            const call = waiting.find((c) => !c.done && c.method === method && c.url === url);
            if (!call) throw new Error(`no pending ${method} ${url}; pending: ${waiting.filter((c) => !c.done).map((c) => `${c.method} ${c.url}`).join(", ")}`);
            call.done = true;
            reply(call, status, body);
            return call;
        },
        fail(method, url) {
            const call = waiting.find((c) => !c.done && c.method === method && c.url === url);
            if (!call) throw new Error(`no pending ${method} ${url}`);
            call.done = true;
            call.reject(new Error("network down"));
            return call;
        },
        count: (method, url) => calls.filter((c) => c.method === method && (url === undefined || c.url === url)).length,
    };
}
