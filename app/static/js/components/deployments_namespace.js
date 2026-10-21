/**
 * Which namespace of the connected cluster the Deployment Topology page shows.
 * One value for every tab: the header's selector sets it and every request the page makes carries it
 * (?namespace=...). The choice is kept in the page URL so a reload stays on it; the other query
 * parameters (Docker Desktop adds its own to the extension's page) are left as they are.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsNamespace = (function () {
    "use strict";

    const PARAM = "namespace";
    const FALLBACK = "default";
    let current = FALLBACK;

    // The namespace named in the URL when the cluster has it; else the one with the most services (the
    // workload most likely to be studied, same rule as the Live Experiments simulation); else "default".
    function choose(rows, requested) {
        if (requested && rows.some((row) => row.namespace === requested)) return requested;
        let best = null;
        rows.forEach((row) => {
            const count = (row.graph?.services || []).length;
            if (count > 0 && (!best || count > best.count)) best = { name: row.namespace, count };
        });
        return best ? best.name : FALLBACK;
    }

    function withNamespace(url, namespace = current) {
        return `${url}${url.includes("?") ? "&" : "?"}${PARAM}=${encodeURIComponent(namespace)}`;
    }

    function requested() {
        try {
            return new URLSearchParams(window.location.search).get(PARAM);
        } catch (error) {
            console.warn("Could not read the namespace from the page URL:", error);
            return null;
        }
    }

    function remember(namespace) {
        try {
            const params = new URLSearchParams(window.location.search);
            params.set(PARAM, namespace);
            window.history.replaceState(null, "", `${window.location.pathname}?${params}${window.location.hash}`);
        } catch (error) {
            // Keeping the choice in the URL is a convenience; the page works without it.
            console.warn("Could not keep the namespace in the page URL:", error);
        }
    }

    function set(namespace) {
        current = namespace || FALLBACK;
        remember(current);
    }

    function get() {
        return current;
    }

    return { choose, withNamespace, requested, set, get };
})();
