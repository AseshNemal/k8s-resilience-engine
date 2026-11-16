# Operational workflow evidence

Operational predictions use live Kubernetes placement and only exact real workflow definitions explicitly reviewed for the current cluster UID, connection fingerprint and namespace. No file declaration, example, editor revision, discovery candidate or historical freeze becomes operational merely because its service names match.

Member 1 owns `operational_evidence.py` and exposes it through `service.py`. Reviews append to the existing workflow revision table under `operational-review`; existing records are not migrated, relabelled or deleted. A changed definition, new revision, replaced cluster, different connection, missing UID or mismatched namespace invalidates eligibility. A trace-backed frozen definition takes precedence over another reviewed source for the same identity. Operator-reviewed definitions remain declarations: observed interactions do not establish business necessity.

## Reviewing a real definition

In Workflow Discovery, review the exact services and dependency roles, choose **Use this real workflow for operational analysis**, and describe how it was verified against the real application. Operator attribution reuses `resilience.audit.name`; it is audit information, not authentication or permission to change Kubernetes.

The corresponding local API is `POST /api/workflows/management/operational-reviews`. Required fields: `workflowId`, `kind` (`managed` or `frozen`), `sourceVersion` (editor revision string or freeze ID), `namespace`, `connectionFingerprint`, `expectedVersion` (review version, initially 0), `actor`, and `statement` (at least 20 meaningful characters). The backend derives the definition digest and cluster UID. `GET /api/workflows/management/operational-reviews` preserves review history; the current definition must be reviewed again after editing. Frozen definitions can use the same API after the original assessment/approval/freeze process, with their actual trace provenance intact.

The statement must reflect a real application review. An assertion is not a trace observation, an experiment, authentication or an authorization to Apply.

## Missing reliable workflow evidence

Workflow survivability and business blast radius are unknown. Failure Impact still reports service exposure for virtual node failures. Recommendations generates placement suggestions with Member 4's existing candidate generator, feasibility checks, virtual simulation and ranking; every before/after service metric comes from Member 3's deployment predictor. No WorkflowGraph, business dependency, criticality or trace is synthesized. These deployment-only suggestions are read-only and cannot authorize Apply or workflow validation. Support for approving a deployment-only proposal would require its own end-to-end lifecycle contract and is not silently borrowed from the workflow gate.

Overview uses the same scope. Operational Recommendations never automatically selects `/declared` evidence. File/editor declarations remain separately exposed as unverified historical or research inputs. Explicit research endpoints retain their acknowledgement and authorization controls, and their conditional results are not operational workflow claims.

## Approvals, recovery and experiments

Production provenance requires the current review policy and exact definition review. Single-workflow fingerprints include the review and freeze identity; portfolio fingerprints bind the complete reviewed set. Apply compares the reviewed evidence's cluster UID with the gateway UID before writing. Existing approvals without this policy remain saved but cannot authorize a new production Apply. Explicit Reset/Keep recovery addresses its saved application and remains available independently of current recommendation eligibility.

Experimental plans now bind the full workflow graph and its review/declaration into the approval digest. The definition is checked again during approval. A saved experiment proves a current recommendation only when both its exact workflow evidence and candidate YAML match; otherwise it is historical evidence. Historical records are never rewritten to satisfy the new policy. Post-rollout recalculation remains a model calculation over observed placement; HTTP response checks measure only their configured assertions, and controlled node failure evidence retains its own scope and cluster identity.

## Live Bookinfo findings (read-only, 2026-11-16)

Kubernetes placement was read for productpage, details, reviews and ratings. Jaeger answered its query API but listed no application services; application matching returned NOT_SEEN and trace-path inspection returned NO_TRACES. The six Bookinfo pods had no Istio sidecars, OpenTelemetry instrumentation annotations or telemetry environment names, and the namespace had no injection label. There is no observed workflow or frozen graph to use.

The four previous automatic inputs are three file declarations and one reviewed editor revision authored by `verify-agent`, without attached observation evidence. They remain preserved and are not attested as real workflows by this change.

The installed host helper's read-only tracing plan passed its preparation checks, but enabling it would install Istio, change namespace instrumentation, replace pods and alter reviews routing for the benchmark. It was not executed. This plan is not neutral instrumentation of an arbitrary production application. Instrumentation or application traffic, dependency review, workload acceptance operations and fault injection need their own explicit authorization. No traces or dependencies were manufactured.

## Release limitations

The prepared disposable acceptance suite exercises the explicit research approval/lifecycle path with declared test inputs; it does not establish Bookinfo business dependencies or production workflow survivability. Real Kubernetes writes, restart/crash recovery during writes, controlled failure validation and native Docker write interactions still require authorized acceptance. Local and browser verification are recorded separately in `tmp/workflow-evidence-verification.json` after execution; test success is not a production-ready declaration.
