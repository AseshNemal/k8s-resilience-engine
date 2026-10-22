# Tracing infrastructure

A shared, read-only layer that gives any component a trace service to read from and applications a place to send
traces. It is **independent of Members 1–4**: it analyses nothing, classifies nothing, builds no `WorkflowGraph`
and changes no shared schema. Member 1 may use it as a trace source; everything Member 1 does with the traces
(analysis, dependency classification, experiments, `WorkflowGraph`) stays in Member 1's engine.

## The experience

> Open the extension → **Enable tracing** once → it runs by itself → see the real traces arriving.

On the extension's **Connect** page, the *Tracing service* card:

1. **Enable tracing** shows a native system dialog (one the page cannot see or answer). It says what will run:
   one container, `resilience-trace-jaeger` (`jaegertracing/all-in-one:1.56`), limited to 256 MB, keeping at most
   5000 traces in memory, reachable from this computer only. Nothing starts unless you approve there.
2. After approval the helper starts the container, remembers your choice, and installs the same per-user
   background job that already renews the cluster credential (LaunchAgent / Task Scheduler / systemd user timer).
3. From then on the service keeps itself up (see *Staying up*), the card shows whether it answers, which
   addresses were tested, whether **real** traces are arriving and from which services, and, for each
   application in the connected cluster, whether it is producing traces.
4. **Turn off tracing** stops the container and stops the automatic restart.

Nothing in this layer sends, seeds or invents a trace. Every figure shown is something Jaeger reported.

## Pieces and owners

| Piece | Where | What it does |
| --- | --- | --- |
| Container lifecycle | `extension/helper/internal/tracesvc` (Go) | Starts, stops, recovers and tests the one container; asks for approval |
| Page bridge | `extension/ui-src/trace-service.js` | The six fixed helper commands, with the page's own wording |
| The card | `extension/ui-src/tracing-panel.js`, `extension/build_ui.py` | Shows state; text-only setup guidance |
| Backend library | `app/integrations/tracing/` | Reads Jaeger: connection, telemetry status, per-application match |
| Backend routes | `extension/tracing_routes.py` (attached in `extension/asgi.py`) | Three read-only `GET` routes |
| Jaeger client | `app/integrations/jaeger/client.py` (existing) | Unchanged; reused |

`app/main.py`, `app/shared/schemas/` and every engine folder are unchanged. A test
(`tests/tracing/test_independence.py`) fails if the tracing package imports an engine or a shared schema, can write
to the trace service, or contains a name suggesting seeded or sample data.

## Connection interface (for other components)

### 1. The query service (what to read traces from)

Use the existing `JaegerClient` with the address from the contract below. Member 1's `TraceSource` port can be
built on it as before. Jaeger's REST API is the interface; this layer adds none of its own.

### 2. `GET /_extension/tracing/connection`

```json
{
  "query": { "url": "http://host.docker.internal:16686" },
  "otlp":  { "http": "http://host.docker.internal:4318", "grpc": "host.docker.internal:4317",
             "source": "same-host-standard-ports" }
}
```

`query.url` is the backend's own setting, `WORKFLOW_JAEGER_URL`. A caller cannot change it (no URL is accepted on
any tracing route), so the routes add no way to make the backend contact another host.

`otlp.source` says how the receivers were derived and is never guessed:

* `configured`: `TRACING_OTLP_HTTP_ENDPOINT` / `TRACING_OTLP_GRPC_ENDPOINT` were set.
* `same-host-standard-ports`: the query service is on port 16686, so the receivers are taken to be on the same host
  at 4318 and 4317. That is how the extension's own container is published; it is a convention, not something
  Jaeger reports. The helper's address check (below) is what proves they answer.
* `unknown`: any other Jaeger. `http` and `grpc` are `null`.

These addresses are the backend's view (a container). **Applications in Kubernetes use the address that the
helper tested from the cluster's nodes**, `host.docker.internal:4318`, shown on the card.

### 3. `GET /_extension/tracing/status`

```json
{
  "query": { "url": "…", "reachable": true },
  "telemetry": {
    "state": "RECEIVING",
    "serviceCount": 3,
    "services": [{ "name": "productpage.bookinfo", "lastTraceAt": "2026-10-22T12:00:00Z" }],
    "newestTraceAt": "2026-10-22T12:00:00Z", "freshSeconds": 300,
    "truncated": false, "observedAt": "2026-10-22T12:00:20Z"
  }
}
```

| `telemetry.state` | Meaning |
| --- | --- |
| `UNREACHABLE` | The query service did not answer. Nothing is known about telemetry; `serviceCount` is `null`, not 0 |
| `NO_SERVICES` | It answers, and no application service has reported. Jaeger's own self-tracing services are excluded (`TRACING_EXCLUDED_SERVICES`) |
| `RECEIVING` | A trace began within `freshSeconds` (default 300) |
| `STALE` | Services have reported, but none within `freshSeconds` |

It reads `/api/services` and, for up to 12 services, the newest trace of each. `truncated` says when more
services exist than were read. An unreachable service is a normal answer, so a page can wait and retry.

### 4. `GET /_extension/tracing/applications?namespace=NS&service=A&service=B`

For each named Kubernetes service: `PRODUCING`, `STALE` or `NOT_SEEN`.

A Jaeger service matches a Kubernetes service when its name is the same (ignoring case) or is
`<name>.<namespace>` (the form Istio's sidecars report). Nothing else matches: a differently named service is
`NOT_SEEN`, never "probably the same". `NOT_SEEN` means Jaeger has no service of that name; it does not prove the
application is not instrumented. Names must be Kubernetes names (at most 50 services); anything else is a coded
`422 INVALID_NAME`. An unreachable trace service is a coded `503 TRACE_SERVICE_UNAVAILABLE`.

### 5. Host helper commands

The page can ask the helper for exactly these, with no argument. All print one JSON line.

| Command | Asks the person? | Effect |
| --- | --- | --- |
| `trace-status` | no | Reads: container state, and whether tracing is turned on |
| `trace-enable` | **yes**, native dialog | Starts the container, remembers the choice, installs the background job |
| `trace-ensure` | no | Does nothing unless tracing is turned on; then restarts or recreates **only our container** |
| `trace-validate` | no | Opens connections to the query service, both receivers and (from the nodes) the receiver; sends no data |
| `trace-start` / `trace-stop` | no | Existing. Stop also turns the automatic restart off |

The background job runs `renew-managed` every 15 minutes, which now also runs `trace-ensure`.

## Staying up

* The container is created with `--restart unless-stopped`, so Docker Desktop brings it back when it restarts.
* `trace-ensure` (page open, and the background job) covers a container that was stopped, removed, or is up but
  silent. A silent container is waited for, then recreated **at most once every 10 minutes**, so one that keeps
  failing cannot keep a small machine busy. Within the cooldown the state is `trace-recovery-wait`.
* It never touches a container it does not own. A Jaeger started another way is reported (`trace-running-external`)
  and left alone: not asked about, not started, not stopped, not recorded.
* Removing the cluster's access also removes the background job (existing behaviour). Tracing then recovers only
  while the extension is open, plus Docker's own restart policy.
* The footprint is one container (limit 256 MB). No gateway, collector or extra service is added.

## Using an existing (external) Jaeger

Point the backend at it with `WORKFLOW_JAEGER_URL` (and `WORKFLOW_JAEGER_ALLOWED_HOSTS` if the host is not
already allowed). If something already answers on `127.0.0.1:16686` the helper reports it as started elsewhere and
never asks to start another. For receivers other than the standard ports set `TRACING_OTLP_HTTP_ENDPOINT` and
`TRACING_OTLP_GRPC_ENDPOINT`. A UI field for the address is not built.

## Can Pods reach the receiver? (what is and is not tested)

Jaeger's ports are published on `127.0.0.1` only. A container or Pod reaches the host through
`host.docker.internal`, and whether that works for a given cluster is **tested, not assumed**.

`trace-validate` asks the Kubernetes node containers it finds (label `io.x-k8s.kind.role`, at most 4) to open a
TCP connection to `host.docker.internal:4318`. The card labels this **node evidence**:

* Pods leaving a node use the node's network path, so a node reaching the address is real evidence for them.
* It is **not** a Pod doing it: a Pod's own DNS and any NetworkPolicy are not exercised. The card says
  "a Pod itself has not been tested". Creating a Pod would change the cluster, which this layer does not do.
* If a node cannot reach it, the result is "does not answer" and the guidance says not to apply anything yet. The
  extension does **not** widen the published ports; that is your decision.
* If no node containers are found, or the node has no `bash`/`timeout`, the result is **unknown**, not reachable.

To test a real Pod yourself (this creates a short-lived Pod, so run it only if you are happy to; I have not run it
and have not verified the expected `405`):

```bash
kubectl run otlp-probe --rm -i --restart=Never --image=curlimages/curl -- \
  curl -sS -m 3 -o /dev/null -w '%{http_code}\n' http://host.docker.internal:4318/v1/traces
```

A `GET` sends no trace. An HTTP status of any kind means the Pod reached the receiver; a timeout means it did not.

## Applications that send no traces

The card lists each namespace's services and whether Jaeger holds traces under that name. For services without
traces, **How to set this up** shows guidance text and the `OTEL_*` variables with the address that was tested
for the cluster. It is text only: the extension changes no Deployment, installs nothing and never applies it.

Environment variables only configure an SDK or agent that is already in the application, or a mesh sidecar that
reports spans. They do not make an application emit traces by themselves.

## Bookinfo benchmark: investigation

**Status: investigation only. Nothing was changed, installed or started.** The `kind-bookinfo-demo` cluster was
not running (connection refused), so everything below comes from your `k8s-bookinfo` repository's files, not from a
live cluster. Anything marked *unverified* needs checking before it is relied on.

### What the sample does today

* `bookinfo.yaml` uses the prebuilt images `registry.istio.io/release/examples-bookinfo-*:1.20.3`. The repository
  contains no tracing configuration of its own.
* The applications **propagate** trace headers in the source files that make outgoing calls: at least
  `x-request-id` and `traceparent` in Python (productpage), Ruby (details) and Java (reviews), plus B3 headers in
  productpage. Node.js ratings was not checked.
* `productpage.py` creates an OpenTelemetry `TracerProvider` with **no exporter**. Its own comment says the
  OpenTelemetry code "only forwards headers". **No Bookinfo application sends spans anywhere**, so
  `OTEL_EXPORTER_OTLP_ENDPOINT` alone produces nothing. (This is the source in the repo; the running image was not
  inspected.)
* `demo-profile-no-gateways.yaml` is an `IstioOperator` profile, which suggests Istio is how the benchmark cluster
  is meant to run. *Unverified*: whether Istio is installed, which version, and whether sidecars are injected.

### Options

| | Approach | Spans come from | Changes the cluster by | Cost on an 8 GB Mac |
| --- | --- | --- | --- | --- |
| **A** | Mesh sidecar tracing (Istio) | Envoy sidecars; Bookinfo's header propagation links them | A tracing provider in Istio's mesh config, a `Telemetry` resource, and a `ServiceEntry` so the mesh can name the receiver on the host; pods restart only if sidecars are not yet injected | Istio control plane plus one sidecar per Pod; *not measured* (your `DEPLOY.md` already budgets about 1.7 GiB for the cluster) |
| **B** | OpenTelemetry Operator auto-instrumentation | Language agents injected into Pods | Operator and its certificate dependency installed; annotations on Deployments (which restarts them) | Extra operator, plus cert-manager. *Unverified:* agents are supported for Java, Python and Node.js; Ruby (details) was not supported when this was written, so details would be missing |
| **C** | Rebuild the images with exporters | The applications | Your own images in `src/`, and Deployments changed to use them | Smallest runtime cost, largest change and maintenance |

**Recommendation:** A, if the benchmark cluster already runs Istio, because Bookinfo already propagates headers and
no image changes. It still has to be checked against the Istio version actually installed, because the
configuration keys for tracing providers have changed between releases. If Istio is not installed, A means
installing it, which is exactly the kind of change that must not happen silently.

### What a one-click version would have to be (not built)

1. **Preview**: show the exact objects (the mesh-config change, `Telemetry`, `ServiceEntry`) as YAML, and which
   Pods would restart, before anything else.
2. **Approval**: a native dialog listing those objects; nothing without it. Approval of tracing in general does not
   cover this.
3. **Scope**: create or patch exactly the previewed objects, nothing else; record what was changed so it can be reverted.
4. **Verify**: afterwards the card's own check, real traces under `<service>.<namespace>` names, is the only proof.

It is not built because (a) the extension's cluster access is read-only by design, and giving it write access to
mesh configuration is a decision for you; (b) the Istio version and state on your cluster are unknown; and (c)
option A's reachability depends on the receiver test above, which cannot be run until the cluster is up.

### Next step you could take

Start `kind-bookinfo-demo`, open the Connect page, **Enable tracing**, run **Check connections**, and see whether the
nodes reach the receiver. That single result decides whether option A is viable on your setup.

## What was and was not verified

* Verified by automated tests: the helper's approval gating, idempotence, recovery cooldown, "only our container",
  stop-as-disable, address checks and the node-evidence labelling (against a fake Docker); the backend state
  machine, matching rule and read-only routes (against a fake Jaeger); the page logic and wording; that the
  tracing package imports no engine, cannot write, and has no seeding.
* **Not verified on a real machine:** the native approval dialog for this command, the real container start, the
  LaunchAgent running `trace-ensure`, `host.docker.internal` from the nodes, and the card in Docker Desktop. The
  Windows and Linux background jobs have never been run on those systems (as for the existing renewal job).
