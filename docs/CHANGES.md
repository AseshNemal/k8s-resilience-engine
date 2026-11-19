# Changes other members should know about

## 2026-11-19 (license): NOTICE and third-party notices

- Added `NOTICE` (Copyright 2026 K8s Resilience Engine Contributors, plus the attribution go.yaml.in/yaml/v3 requires) and `THIRD_PARTY_NOTICES.md` (Go helper
  components with their license texts; Python packages, Debian base image and web pages summarised). Both are copied to `/licenses/` in the extension image.
  README and the Docker Hub overview carry the copyright line. `LICENSE` is the unmodified official Apache-2.0 text. No code changed.

## 2026-11-19 (release): 0.1.0-beta.2, image metadata and Docker Hub overview

- `extension/Dockerfile` labels only: `org.opencontainers.image.version` (build argument `VERSION`), `.source`, `.url`, `.documentation`, and a changelog for beta.2. No functional change from beta.1.
- `docs/DOCKERHUB.md`: the overview text for the Docker Hub repository page. `docs/EXTENSION.md`: publish steps now pass `VERSION` and run `docker scout cves --only-fixed` first.
- Docker Scout on beta.1: 0 findings in our layers; 6 high / 8 medium / 26 low, all Debian packages of the `python:3.13-slim` base, none with a fix available yet.

## 2026-11-19 (license): Apache License 2.0

- Added `LICENSE` (Apache-2.0, unmodified official text), the `org.opencontainers.image.licenses` label and a copy at `/licenses/LICENSE` in the extension image, and a README line. No code changed.

## 2026-11-19 (release): first public beta `0.1.0-beta.1` prepared for Docker Hub

- `extension/Dockerfile` labels only: added the icon, publisher URL, additional URLs and screenshots labels that `docker extension validate` requires, and
  a changelog that no longer says "not published". Screenshots are in `extension/screenshots/` (taken from the real Bookinfo cluster, read-only) and are
  not copied into the image; the labels point at `main` on GitHub. No application, research logic, route or schema changed.
- `docs/EXTENSION.md`: "Beta from Docker Hub" (tester install, limits, maintainer publish commands).
- Product naming: the "Research Environment" badge and the sidebar "Project ID" footer are gone from every page (they were project-course labels), and the image vendor label is now "K8s Resilience Engine".
  Feature names such as the Research view and the research approval tier are unchanged.
- Checked against the installed beta: all pages load through the extension's own adapter with real cluster data; Fail a Node keeps its stop button disabled until the
  "really stopped on my everyday cluster" box is ticked. One earlier real run (one worker, ended early, restored) is recorded in the app's own store.

## 2026-11-19 (later): Fail a Node follow-ups (Member 4)

- **Custom time**: the hold time can be typed (seconds or minutes), validated as it is typed, and the server's bound is now 10 seconds to 30 minutes
  (`LoopSettings.lab_hold_max_s`, was 15 minutes). Longer holds keep an application down longer; **Restore now** still ends it at any moment.
- **"No cluster can be tested right now"** now lists the server's own reason for each cluster and has **Check again** (and a Connect link in the extension).
  Before, it only said that none was available and hid why.
- The Recommendations "Prove it" panel ignores a Failure lab run: it proves no recommendation, and its view has no candidates to draw.
- **Sidebar order**: Fail a Node now comes after Recommendations (fix first, then test it); the page text says so.
- Error sweep of every page against a real, migrated backend: no script errors. The only 404s are the extension-only `/_extension/cluster` (handled) and `favicon.ico`.

## 2026-11-19: Fail a Node page, and the full monitor on "Watch a real failure" (Member 4)

- New sidebar page **Fail a Node** (`/components/fail-a-node`, `fail_node.html`, `fail-visual.js`, `css/pages/fail-node.css`): the Failure lab in a simple, visual form.
  Workers are drawn as small servers with one square per ready replica (observed placement from `/api/failure-impacts/overview`), tap to pick, a
  control plane is locked, a hold time as buttons, a preview with service chips, then the live view with node tiles and the measured charts. It reuses
  `failure-lab.js` (`visual: true`) and the validation-loop API; no safety rule changed. The Failure impact page now only links to it.
  The route lives in Member 4's `app/web/recommendations.py` (`app/main.py` is untouched); the extension build (`extension/build_ui.py`, `shim.js`) lists the page.
- **Watch a real failure** now records a measured timeline (`monitoring/watch.py`, stored as `monitor` on the observation): one sample per status check
  (availability, node state, CPU and memory every third check, warning events, logs of crashing pods, and one real request per check when an optional
  loopback page address is given). A stretch nobody asked is an unknown gap; an unreadable cluster is an unknown sample. The card shows it with the same monitor.
- Fixed: `timers = { set: global.setTimeout }` threw "Illegal invocation" in browsers (polling never started) in `failure-lab.js` and `failure-observation.js`.
- Verified on test doubles and in a headless browser against mocked runs plus the real read-only placement of the Docker Desktop cluster. **No node has been stopped.**


Newest first. Each entry says whose area it touches, what it changed outside its owner's folder, and what it did **not** change.
Shared schemas (`app/shared/schemas/`), `app/main.py` and every other member's algorithm are untouched unless an entry says so.

## 2026-11-19 — Disconnected state, saved-versus-live labelling, failure monitor and recommendation pictures

Local only, nothing committed. Touches Member 4's area plus the shared shell (`base.html`, `topbar.html`, `connection-state.js`) and, by explicit request, four of **Member 2's** Deployment Topology files (wording only; `test_member_boundaries.py` now names them).

**Where data came from with no cluster connected.** The backend was honest (`CLUSTER_NOT_CONNECTED`). What still showed was (1) **stored research recordings** served by `/api/deployments/benchmarks/*` (`experiments/placement_drift/results`, kubectl-inspected Kind recordings) on Deployment Topology → Experiments and Accuracy, worded as "real runs on a live cluster" and "really went down"; (2) **saved database records** (validation runs, apply tests, applied changes, watched failures) drawn with no check against the cluster connected now; (3) a static header chip "Live cluster evidence only" and a "Connected cluster" subtitle that did not depend on any connection.

What changed:

- `GET /api/recommendations/cluster-status`: one identity read, `connected | not-connected | unreachable | access-denied | unidentified`. `static/js/cluster-state.js` (`ResilienceCluster`) is the single source for every page; it never guesses (an unreadable status is `backend-unreachable`, not connected).
- Header chip is dynamic ("Live · <context>" / "Offline · saved results only"). Recommendations shows an offline banner and an honest subtitle.
- Saved records are labelled "Saved result · <age>" with the cluster they came from; a record from another cluster says so. Applied to the validation run (`recommendation-proof.js`) and watched failures (`failure-observation.js`).
- The shared state card is now a friendly "Connect your cluster to begin" panel (picture, steps, Check again, Connect link inside the extension, code folded away) for no-cluster, unreachable and rejected-credential states. Assessment no longer retries coded refusals and shows one message, not two, and assesses by itself when the cluster comes back.
- Deployment Topology Experiments and Accuracy now carry "Recorded research results · not your cluster · not live" and recorded wording. No figure or algorithm changed.

**Failure monitor (new, Member 4).** `recommendation_evaluation_validation/monitoring/`: a read-only sampler records, during each failure of a validation run, ready replicas per service, node readiness, real HTTP requests (with the workflow check), CPU and memory per node (metrics API, else each kubelet; a stopped node is a gap, not zero), Warning events and crashing-pod logs. It is stored with the experiment outcome (no migration), summarised in the run view, and drawn by `failure-monitor.js` (availability lanes, request strip and latency, resources, events, predicted-versus-measured, before-and-after for the same worker when a baseline was measured). It never changes a verdict, and a monitor that fails never changes what is done to the cluster.

**Recommendation pictures.** `recommendation-visual.js`: nodes as boxes, replicas as coloured squares, now versus simulated-after, the picked node broken in both, with the engine's down/degraded rule. Used on Assessment, with a three-step "where to begin" strip.

**Experiments and Accuracy now come from the connected cluster (follow-up, same day).** `GET /api/recommendations/cluster-experiments` (`cluster_experiments.py`, pure) lists the node failures recorded for the connected cluster's identity (approved validation runs and watched failures) with the model's predicted-down services beside the measured-down ones; Accuracy is a count of matches ("2 of 3"), unknown when nothing was compared. Records of other clusters are only counted apart; records with no stored cluster identity are never attributed. With no cluster connected, nothing is drawn. The bundled Bookinfo recordings stay on the page, but in a closed "Research results recorded earlier (not your cluster)" fold under each tab. Member 2's `deployments.html` and `deployments_live_experiments_tab.html` changed (containers, fold); `cluster-experiments.js` is Member 4's. On this cluster the tabs are empty until a failure is recorded, because no worker has been stopped by or watched by this app.

**Failure lab and report (follow-up, same day).** A new card on the Failure impact page (`failure-lab.js`) lets the person choose the cluster (their own Docker Desktop cluster or a separate test cluster), ANY number of workers (a control plane is never offered), and a hold time (10-900 s). The server previews what each service would do (the engine's placement rule, applied to the chosen SET) and lists the other workloads that would go down; the person approves that exact plan once. Built on the validation loop as a new mode, `LAB` (`validation_loop/lab.py`, `ValidationLoop._run_lab`): no change is applied, no candidate, no verdict about a change. New code paths in shared safety pieces: `KindWorkerFaultInjector.inject_together` (each node still needs its own authorization, and ANY earlier pending recovery record still refuses), `KubernetesValidationExecutor.fail_nodes` / `restore_nodes`, and `analyse(..., allow_essential=True)`. **Decision to review:** in the lab only, an essential cluster workload (kube-system, local-path-storage) that would go down is an acknowledgement by name instead of a hard BLOCK, because the person asked for room to test anything; unreadable state is still a BLOCK and the normal loop is unchanged. "Restore now" ends the hold and starts every worker; recovery after a crash is the existing restore-only path.
- **No name is asked** for approvals any more (dock dialogs; the lab never had one). A run approval carries no person; production approvals record "developer" when no name is sent.
- **Report:** `GET /api/recommendations/validation-loop/runs/{key}/report` (`?download=true`) returns one standalone, printable HTML page built from the stored run (what was stopped, predicted versus measured, availability and request charts, resource peaks, cluster events, restoration status, limits). Linked from the lab and from the Prove-it panel.
- The Deployment Topology **Output** tab (an internal component diagram) is hidden from the production tab bar (`hidden`, `data-research-only`); its pane and tests are unchanged.
- Tests added for the injector, executor-level loop, service, API, report and panel; nothing real was stopped.

**Not verified.** No worker was stopped and no disposable cluster was created: the Docker VM (3.8 GiB) cannot hold a second cluster beside the research one (see the kind runbook), so the monitor has never watched a real failure. Its readers were run read-only against the research cluster (identity, per-service readiness, kubelet CPU/memory, events). Charts were checked with a synthetic timeline, which is a rendering check, not a measurement. Tests: Python 3.8k passed; JavaScript 622 passed; extension 265 passed.

## 2026-11-19 — Assessment, ranked placement previews and worker selection

- Assessment now returns all feasible options with the engine's ranks and exposes the selected option's simulated placement. Assessment and Recommendations use the same portfolio candidate IDs for a single reviewed workflow as for multiple workflows. A fresh handoff takes precedence over a previously saved page selection.
- The placement diagram compares ready pods per worker for each affected service. A combination stays one proposal. A service-only assessment can lead into workflow review, and the return link respects the Docker extension's local page URL.
- Member 4's placement search now groups workers by the observed hostname label for hostname spread. Missing required topology labels prevent a positive feasibility verdict. The strategy model versions changed, so an earlier approval may require review against the corrected evidence.
- A Smoke plan returns the eligible workers on its target. Selecting another worker requests a new plan and clears previous acknowledgements. Inconclusive results include the recorded reason, and a readiness-only result identifies the evidence actually obtained.

## 2026-11-17 — Member 4: selectable recommendations and proposal-specific actions

Based on main `6bcf2ae`. No version bump or release.

- **Member 4 UI:** the complete feasible list sits beside the selected change's action dock. Deployment-only variants now share one selection with the dock and map; workflow candidates remain previewable even when approval is blocked. Existing combination recommendations remain intact. Selection persistence includes deployment-only analysis.
- **Approval UX:** exact patch review, required justification for alternatives, fingerprint/YAML matching for saved decisions, and guards against stale reads and confirmations (including A → B → A). An unseen proposal generated during an approval request is never automatically approved. Recovery actions remain available when workflow evidence is missing; settled runs do not obscure another proposal.
- **Member 1 page handoff only:** `workflow-editor.js` and `workflow_editor.html` accept the application namespace and display review instructions when reached from Recommendations. Operational attestations remain explicit and use the existing review API. No Member 1 evidence logic changed.
- **Policy unchanged:** deployment-only candidates remain read-only; business impact stays unknown without a current operational review. No real cluster workloads were changed, no failures were injected, and no deployment-only approval policy was enabled.
- **Tests:** Python **3759 passed, 22 skipped**; JavaScript **865 passed**; every Go package passed. The expanded fake-gateway Apply → Keep → Reset and operational-review tests also passed in a focused backend run (**38 passed**). Native Docker Desktop is unavailable and browser automation could not initialize; visual UI acceptance remains unverified.

Details and optional future policy decision: [WORKFLOW_EVIDENCE_POLICY.md](WORKFLOW_EVIDENCE_POLICY.md#recommendation-selection-and-the-review-handoff-2026-11-17).

## 2026-11-17 — Member 4: Keep refreshes the page, and "Watch a real failure"

Branch `feat/production-readiness`, committed together with the earlier work listed below. Extension build 0.1.710.

| Area | Owner | What changed |
|---|---|---|
| `app/engine/recommendation_evaluation_validation/observation/` (new) | Member 4 | Read-only watch of a worker failure that the **developer** causes. Records the model's prediction for every worker first, then what the cluster does. |
| `app/api/recommendations.py` | Member 4 | Routes `POST /api/recommendations/failure-observations`, `GET .../current`, `GET .../`, `POST .../{id}/stop`. Calls Member 3's public `build_cluster_failure_overview`. |
| `alembic/versions/e5f7a2c91b36_…` | shared persistence | **Additive** table `failure_observations`. `tests/test_migrations.py` now pins this head. |
| `app/templates/components/failure_impacts.html`, `css/pages/failure_impacts.css` | **Member 3's page** | A "Watch a real failure" card and one `<script>` tag were added below the node list; the CSS block `.fo-*` is appended at the end. The card needs nothing from Member 3's script or engine. (The same commit also swaps the page's placement table for the shared placement map; see below.) |
| `static/js/failure-observation.js` (new) | Member 4 | Mounts itself into `[data-role="observation-panel"]`; does nothing until the person presses Start. |
| `static/js/recommendation-apply-test.js`, `recommendation-action.js` | Member 4 | After **Keep** the page re-reads the cluster (risks and changes are recalculated) and the dock offers "Choose another change" beside Reset. |

Behaviour worth knowing:

- This app **stops nothing and writes nothing** here. Node readiness is read through a gateway built with writes off.
- The comparison covers one failed node and service readiness only. Workflow outcomes are shown as **not observed**, never as survived.
- Nothing runs in the background: the record advances when the page asks. An unreadable cluster is *unknown*, never "down" or "recovered".
- Verified on in-memory doubles and by a read-only watch of the real Bookinfo cluster. **No worker has been stopped and no failure has been observed for real.**
- Tests: `tests/recommendation_evaluation_validation/test_failure_observation.py`, `tests/frontend/failure-observation.test.mjs`.

**Review follow-ups (PR 49):**

- **Member 3, `failure_impact_prediction/service.py`:** `load_application_snapshot` now leaves *incomplete* services out of the assessment (a service with a replica on a node discovery does not model, for example the control plane). Before, their truncated placement was scored, so a service could be reported lost while a replica elsewhere survived, contradicting the page's "not assessed" notice. They are still named in `topology.incompleteServices`. Test: `test_a_service_with_a_replica_on_an_unmodelled_node_is_not_assessed_from_its_truncated_placement`.
- **Member 4:** a watch is bound to the cluster it started on (`cluster.clusterUid`). A different or unidentifiable cluster is never read as the watched one (`clusterMatch` `other` / `unknown`), and a watch cannot start while the cluster cannot be identified.

For Member 3 and Member 2: on the discovery path the overview's `nodes[].ready` is `None` (unknown), so Member 4 reads node readiness itself. If the overview later reports it, the watch can use it.

## Earlier work in the same working tree (details in `CLAUDE.md`, "Current State")

- **Placement map** (`static/js/placement-map.js`, `css/placement-map.css`, `placement_view.py`): shared by Assessment, Failure impact and service-level Recommendations. Draws only what it is given; simulated cells are dashed. **Member 3:** on the Failure impact page it replaces the old placement table, so `failure_impacts.js` and `failure_impacts.html` changed (the engine and `/overview` did not).
- **Action dock** (`static/js/recommendation-action.js`): the selected change has one next action (review and approve, apply, keep or reset). With no reviewed workflow there is no approve or apply, only "Review a workflow".
- **Assessment redesign** and **deployment-level recommendations** (`engine/assessment/`, `deployment_recommendations.py`): service-level suggestions when no workflow is reviewed. Read-only; they cannot authorise Apply.

## 2026-11-16 — Member 1: workflow test evidence and the workflow manager (committed, `a35ab54`)

- Workflows can combine **Add Manually**, **Record Activity** and **Add Test Evidence**. Imports never upgrade an observed call to mandatory and cannot claim experimental validation.
- New endpoints under `/api/workflows/management`: `GET /test-evidence`; `POST /test-evidence/import`, `/test-evidence/run`, `/test-evidence/{id}/refresh`. No new migration (existing `workflow_management_revisions` storage).
- Exporters for the application's own Pytest/Playwright suites are in `app/static/test-integrations/`.
- An **operational review** (`docs/WORKFLOW_EVIDENCE_POLICY.md`) is what lets a workflow drive approve and Apply. Without one, Recommendations stays read-only.
- Unverified against a real application: customer test suites with real trace propagation, and concurrent-traffic attribution.

## Open issues for other members

| For | Issue | Where |
|---|---|---|
| Member 3 | A Pending pod makes portfolio prediction raise `CrossLayerGraphError` (the check is still in `cross_layer_graph.py`). | `docs/HANDOFF_MEMBER3_PENDING_POD_CROSS_LAYER_GRAPH.md` |
| Member 2 | Reported 2026-10-22, not re-checked since: an unreadable node capacity becomes `0.0` in `analyze_live_topology` but `None` in `discover_live`. | `docs/HANDOFF_MEMBER2_UNKNOWN_CAPACITY.md` |
