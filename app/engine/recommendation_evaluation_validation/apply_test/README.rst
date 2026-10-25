Apply and test
==============

The explicit Apply button starts one persisted background run. No separate
approval form or acknowledgement text is required. The server records the Apply
action as an audit authorization for the existing ApplicationService; it does
not claim that an authenticated named person signed an approval. Identity is
labelled unverified in that audit record. Eligibility, the reviewed fingerprint,
server-generated YAML digest, workflow-set digest, deployment write arming,
resourceVersion checks, original-state snapshots and Reset remain enforced.

The run captures a baseline, applies the whole selected candidate (including
combinations), waits for Deployment rollout, measures configured HTTP workflow
checks and resource usage, then stores the risk comparison and complete report.
The UI displays recorded API operations and persisted events. It never claims
that shell commands were executed. Status GETs never start Apply or replay it.

Measurements
------------

Each configured workflow gets three GET samples with response-content assertions.
Pod readiness is reported separately. A missing check is NOT_CONFIGURED, never
a passed workflow. Checks are loaded from the server's
RESILIENCE_WORKFLOW_PROBES_PATH JSON file, or from the declaration file when
that setting is absent. Both accept a top-level workflowChecks map::

    {
      "workflowChecks": {
        "your-workflow-id": {
          "url": "http://localhost:30080/your-real-entry-point",
          "expectedStatus": 200,
          "mustContain": ["your actual success marker"],
          "mustNotContain": ["your actual dependency failure marker"]
        }
      }
    }

A worked example for the Bookinfo in the researcher's own k8s-bookinfo repository is
experiments/member4_real_validation/workflow-checks.bookinfo.example.json. Its strings are the product page's
own. The Service is cluster-internal, so a check needs a host port, for example
``kubectl port-forward -n bookinfo svc/productpage 18080:9080``. The checks only count while that port-forward runs.
With reviews-v1 only, no check can prove ratings works, because the page never shows stars; the example says so.

These values must describe the actual application. The product does not infer
URLs or successful response text from workflow names. The existing loopback
probe integration enforces permitted hosts and ports, no redirects, and the
extension's configured host gateway.

CPU and memory snapshots come from metrics.k8s.io, with measured kubelet summary
statistics as fallback. Resource requests and capacities are never presented as
usage. Missing metrics remain unavailable. The report includes sample times and
raw Kubernetes units. It is a snapshot comparison, not a load benchmark.
The UI converts CPU to millicores and memory to MiB; downloads preserve the raw
values. A run without post-apply measurements is shown as a partial report.

Risk is recalculated through Member 3 over freshly observed topology. It is
labelled model-based risk; this run does not stop nodes or inject faults.
Completeness requires before/after HTTP checks, resource measurements and a
completed risk analysis. Otherwise the result is COMPLETED_WITH_GAPS, TESTS_FAILED
or FAILED, with the recorded evidence and reasons.

Persistence and recovery
------------------------

Migration b71d8ea210c4 adds apply_test_runs. A unique active slot prevents
concurrent runs across requests; an idempotency key prevents repeated clicks
and network retries from applying twice. Sessions are owned by individual
repository operations and worker actions, never shared with request threads.
The current deployment runs one backend process. A stale run whose worker is
gone is marked INTERRUPTED after 120 seconds without activity; it is never
replayed. Application snapshots remain available for explicit Reset. Active
Apply/test runs block competing legacy Apply/Reset and fault-injection runs.

Generated hostname spread constraints set nodeTaintsPolicy to Honor. Otherwise
Kubernetes counts an untolerated control-plane node as an empty spread domain,
which can block a rollout even when worker capacity is available. This changes
future proposals only; existing applied snapshots are retained for Reset, never
rewritten or reapplied automatically.
They also set matchLabelKeys to pod-template-hash so old rollout replicas do not
mask a concentration of the new revision. The validation whitelist accepts only
that revision key. API support is required; rejected fields are never silently
removed to force Apply through.

A completed run is not necessarily a successful recommendation. riskTargetMet
compares Member 3's post-rollout model risk with the prediction. A missed target
is highlighted separately from functional HTTP outcomes and missing checks.
Earlier saved reports receive this derived annotation on read without changing
their recorded evidence. The Apply controls offer explicit Reset beside a
blocked Apply and retain the selected next candidate for fresh evaluation after
restoration; neither action starts another Apply automatically.

GET /api/recommendations/apply-tests/current restores the run after a UI reload.
GET /api/recommendations/apply-tests/{run_id}/report downloads the persisted JSON
report, including partial evidence after a failed or interrupted run.

GET /api/recommendations/apply-tests/history lists saved tests with bounded
limit/offset pagination. Historical reports are independent of the current
candidate and remain after Reset and later tests. The history UI only reads;
opening a report cannot apply, reset or select a live change. Selected saved
tests can be compared by cluster UID, workflow-set digest, baseline/predicted/
post-rollout model risk, configured HTTP outcomes and measurement availability.
Different baselines and missing evidence do not establish a controlled ranking.
After an explicit Reset, status reads wait for restoration to complete and
reload live candidate selection. They never start the next Apply.
