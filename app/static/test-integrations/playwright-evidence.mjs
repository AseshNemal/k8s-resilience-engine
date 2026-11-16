/* Copy into the application test project. No package dependency beyond Node. */
import { randomBytes, randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";

export class Evidence {
    constructor({ testName, namespace, applicationUrl, entryService, successCondition = null }) {
        const target = new URL(applicationUrl);
        if (
            !["http:", "https:"].includes(target.protocol) ||
            target.username ||
            target.password ||
            target.search ||
            target.hash
        )
            throw new Error("Use an HTTP(S) base URL without credentials, query or fragment");
        this.namespace = namespace;
        this.origin = target.origin;
        this.execution = {
            testName,
            executionId: randomUUID(),
            runner: "playwright",
            applicationUrl,
            entryService,
            startedAt: new Date().toISOString(),
            traceIds: [],
            assertions: [],
            reportedStatus: "unknown",
            successCondition,
        };
    }
    headers() {
        if (this.execution.traceIds.length >= 20)
            throw new Error("Export at most 20 request traces per execution");
        const traceId = randomBytes(16).toString("hex");
        this.execution.traceIds.push(traceId);
        return { traceparent: `00-${traceId}-${randomBytes(8).toString("hex")}-01` };
    }
    async attach(context) {
        const handler = async (route) => {
            const request = route.request();
            if (
                new URL(request.url()).origin !== this.origin ||
                !["document", "xhr", "fetch"].includes(request.resourceType())
            )
                return route.continue();
            await route.continue({ headers: { ...request.headers(), ...this.headers() } });
        };
        await context.route("**/*", handler);
        return () => context.unroute("**/*", handler);
    }
    assertion(name, passed) {
        if (typeof passed !== "boolean") throw new Error("Assertion result must be a boolean");
        this.execution.assertions.push({ name, passed });
    }
    outcomeTrace(traceId) {
        if (!this.execution.traceIds.includes(traceId))
            throw new Error("Outcome trace must belong to this execution");
        this.execution.outcomeTraceId = traceId;
    }
    async write(path, status = "unknown") {
        this.execution.finishedAt = new Date().toISOString();
        this.execution.reportedStatus = ["passed", "failed", "skipped"].includes(status) ? status : "unknown";
        await writeFile(
            path,
            JSON.stringify({ namespace: this.namespace, executions: [this.execution], traces: [] }, null, 2),
        );
    }
}
