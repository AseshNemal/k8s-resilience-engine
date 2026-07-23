/*
 * Shared fetch helper for sample API endpoints.
 * SHARED FILE — changes require team review.
 *
 * This module only performs HTTP requests and JSON parsing. It does NOT
 * compute any research values.
 */

const DEFAULT_TIMEOUT_MS = 8000;

/**
 * Fetch JSON from an endpoint with timeout and clear error messages.
 * @param {string} endpoint
 * @param {object} [options]
 * @param {number} [options.timeout]
 * @returns {Promise<any>}
 */
async function fetchJson(endpoint, options = {}) {
    const timeout = options.timeout || DEFAULT_TIMEOUT_MS;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);

    try {
        const response = await fetch(endpoint, {
            signal: controller.signal,
            headers: { Accept: "application/json" },
        });

        if (!response.ok) {
            throw new Error(`Request failed with HTTP ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        if (error.name === "AbortError") {
            throw new Error(`Request timed out after ${timeout} ms`);
        }
        throw error;
    } finally {
        clearTimeout(timer);
    }
}

/**
 * Fetch several endpoints together. Rejects if any single request fails.
 * @param {Record<string, string>} endpointMap
 * @returns {Promise<Record<string, any>>}
 */
async function fetchAll(endpointMap) {
    const keys = Object.keys(endpointMap);
    const results = await Promise.all(
        keys.map((key) => fetchJson(endpointMap[key]))
    );

    const output = {};
    keys.forEach((key, index) => {
        output[key] = results[index];
    });
    return output;
}

window.ResilienceApi = { fetchJson, fetchAll };
