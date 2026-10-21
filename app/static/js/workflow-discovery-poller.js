/*
 * Schedules the Workflow dependencies page's automatic discovery of observed workflows.
 *
 * It decides only WHEN to ask: a lightweight health check every few seconds while the trace service is
 * unreachable, discovery as soon as it answers, and a slower refresh afterwards for newly arriving traces.
 * One request cycle runs at a time, nothing is scheduled while the page is hidden, and a refresh when the
 * tab becomes visible again is rate-limited. While the answer stays the same (same `fingerprint`) the wait
 * between cycles doubles up to a ceiling, so a stable system is read less and less often; a change, or an
 * explicit refresh, goes back to the base cadence. What to ask and how to show the answer come from the page, so
 * this module never supplies, estimates or substitutes data.
 */
(function () {
    const HEALTH_RETRY_MS = 10000; // while the trace service is unreachable
    const WAITING_FOR_TRACES_MS = 10000; // connected, but no usable workflow evidence yet
    const EVIDENCE_REFRESH_MS = 30000; // connected with workflows: pick up newly arriving traces
    const FOCUS_MIN_GAP_MS = 5000; // a tab focus or visibility change never refreshes more often than this
    const MAX_REFRESH_MS = 120000; // ceiling for the refresh wait while the workflows are unchanged
    const MAX_WAITING_MS = 60000; // ceiling for the wait while nothing usable has arrived and nothing changes

    function create(options) {
        const {
            checkHealth, // () => Promise<boolean>; a rejection counts as "unreachable"
            discover, // () => Promise<report>; the report carries a `state`
            onWaiting, // () => void: the trace service is unreachable
            onChecking, // () => void: a cycle has started and nothing has been shown yet
            onReport, // (report) => void
            onError, // (error) => void: discovery itself failed although the service answered a health check
            isHidden = () => false,
            now = () => Date.now(),
            setTimer = (fn, ms) => setTimeout(fn, ms),
            clearTimer = (id) => clearTimeout(id),
            healthRetryMs = HEALTH_RETRY_MS,
            waitingForTracesMs = WAITING_FOR_TRACES_MS,
            refreshMs = EVIDENCE_REFRESH_MS,
            maxRefreshMs = MAX_REFRESH_MS,
            maxWaitingMs = MAX_WAITING_MS,
            focusMinGapMs = FOCUS_MIN_GAP_MS,
        } = options;

        let timer = null;
        let running = false;
        let inFlight = false;
        let rerunRequested = false;
        let generation = 0;
        let lastFinishedAt = -Infinity;
        let hasShownResult = false;
        let lastDelay = healthRetryMs;
        let lastFingerprint = "";
        let unchangedRuns = 0;

        function cancelTimer() {
            if (timer !== null) clearTimer(timer);
            timer = null;
        }

        function schedule(delay) {
            cancelTimer();
            // Nothing is scheduled for a hidden page; becoming visible again starts a cycle.
            if (!running || isHidden()) return;
            timer = setTimer(() => {
                timer = null;
                cycle();
            }, delay);
        }

        function nextDelay(report) {
            if (report.state === "jaeger_unavailable") return healthRetryMs;
            const discovered = report.state === "discovered";
            const base = discovered ? refreshMs : waitingForTracesMs;
            const ceiling = discovered ? maxRefreshMs : maxWaitingMs;
            // A report without a fingerprint cannot be shown to be unchanged, so it never backs off.
            if (report.fingerprint && report.fingerprint === lastFingerprint) {
                unchangedRuns += 1;
            } else {
                unchangedRuns = 0;
            }
            lastFingerprint = report.fingerprint || "";
            return Math.min(base * 2 ** unchangedRuns, ceiling);
        }

        async function cycle() {
            if (!running || inFlight || isHidden()) return;
            inFlight = true;
            const mine = generation;
            let delay = healthRetryMs;
            try {
                if (!hasShownResult && onChecking) onChecking();
                let healthy = false;
                try {
                    healthy = Boolean(await checkHealth());
                } catch (_error) {
                    healthy = false;
                }
                if (mine !== generation) return;
                if (!healthy) {
                    onWaiting();
                } else {
                    try {
                        const report = await discover();
                        if (mine !== generation) return;
                        hasShownResult = true;
                        onReport(report);
                        delay = nextDelay(report);
                    } catch (error) {
                        if (mine !== generation) return;
                        if (onError) onError(error);
                    }
                }
            } finally {
                inFlight = false;
                lastFinishedAt = now();
                lastDelay = delay;
                if (running) {
                    // A refresh asked for during this cycle, or a stop and start that this cycle outlived,
                    // runs straight away; otherwise the next cycle waits its normal delay.
                    if (rerunRequested || mine !== generation) {
                        rerunRequested = false;
                        cycle();
                    } else {
                        schedule(delay);
                    }
                }
            }
        }

        // Starts discovering; asking again while it is already running changes nothing.
        function start() {
            if (running) return;
            running = true;
            generation += 1;
            cycle();
        }

        function stop() {
            running = false;
            generation += 1;
            rerunRequested = false;
            cancelTimer();
        }

        // An explicit request (a button, a changed time window): run now, or straight after the current cycle.
        function refresh() {
            unchangedRuns = 0;
            if (!running) {
                start();
                return;
            }
            if (inFlight) {
                rerunRequested = true;
                return;
            }
            cancelTimer();
            cycle();
        }

        // Tab focus or visibility: pause while hidden, and refresh on return unless one just finished.
        function pageVisibilityChanged() {
            if (!running) return;
            if (isHidden()) {
                cancelTimer();
                return;
            }
            if (inFlight) return;
            if (now() - lastFinishedAt < focusMinGapMs) {
                // Too soon for a refresh; make sure a later cycle is still scheduled.
                if (timer === null) schedule(lastDelay);
                return;
            }
            cancelTimer();
            cycle();
        }

        return { start, stop, refresh, pageVisibilityChanged, isRunning: () => running };
    }

    window.WorkflowDiscoveryPoller = {
        create,
        HEALTH_RETRY_MS,
        WAITING_FOR_TRACES_MS,
        EVIDENCE_REFRESH_MS,
        FOCUS_MIN_GAP_MS,
        MAX_REFRESH_MS,
        MAX_WAITING_MS,
    };
})();
