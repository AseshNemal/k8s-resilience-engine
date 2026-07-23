# K8s Resilience Engine — Complete AI Project Context

## 1. Project Identity

- Project ID: J26-SE-311
- Topic: Dependency Aware Failure Domain Risk Assessment for Kubernetes Deployed Microservice Architectures
- Research group: Software Systems & Technologies
- Specialization: Software Engineering
- Team: 4 members, one-year research project
- Primary failure scope: single Kubernetes worker-node failures

## 2. Research Problem

Kubernetes provides replication, scheduling, readiness probes, affinity, anti-affinity, topology spread constraints, and disruption controls. However, several replicas do not guarantee that an end-to-end business workflow can survive a worker-node failure.

A mandatory service may have multiple replicas but all or most replicas can be concentrated on one node or failure domain. If that node fails, the service and every dependent workflow may fail.

The research gap is:

```text
Kubernetes-level availability
            versus
Business-workflow-level resilience
```

Kubernetes can show pods, replicas, readiness, and placement, but it does not directly explain:

- which business workflows depend on a service;
- which dependencies are mandatory;
- which workflows fail when a selected worker node fails;
- the workflow-level blast radius;
- which minimal deployment change gives the best resilience improvement.

## 3. Main Objective

Design and experimentally evaluate a dependency-aware cross-layer risk-assessment system that combines business workflow dependencies with Kubernetes pod placement to predict service survival, workflow survival, and blast radius under single worker-node failures, and to recommend minimal deployment changes that improve workflow resilience.

## 4. Cross-Layer Model

```text
Workflow
  -> Service
    -> Pod
      -> Node
        -> Failure Domain
```

## 5. Final Application Architecture

The project uses one integrated full-stack application.

```text
Browser
  -> Jinja2 + HTML + CSS + JavaScript
  -> FastAPI
  -> Component service interfaces
  -> Research algorithms
  -> External integrations
```

There is no separate React frontend.

Technology stack:

- Python
- FastAPI
- Pydantic
- Jinja2
- HTML, CSS, vanilla JavaScript
- Pytest
- PostgreSQL later when persistence is needed
- Kubernetes Python client
- OpenTelemetry
- Jaeger
- Prometheus
- Chaos Mesh or equivalent controlled failure injection

## 6. Final Component Names

```text
app/engine/
├── workflow_dependency_modeling/
├── deployment_topology_modeling/
├── failure_impact_prediction/
└── recommendation_evaluation_validation/
```

Do not use the older names:

```text
workflow_discovery
deployment_graph
risk_prediction
failure_prediction
recommendation_validation
```

## 7. Team Responsibilities

### Member 1 — Workflow Dependency Modeling

- Name: A.D. Perera
- Registration number: IT23236400
- Folder: `app/engine/workflow_dependency_modeling/`
- API: `app/api/workflows.py`
- Output: `WorkflowGraph`

Responsibilities:

1. Define business workflows and expected success conditions.
2. Execute workflows and collect OpenTelemetry traces.
3. Retrieve and inspect traces through Jaeger.
4. Extract service interactions and causal relationships.
5. Combine workflow definitions with runtime trace evidence.
6. Classify dependencies as mandatory, optional, or supporting.
7. Build workflow dependency graphs.
8. Validate graphs against reference workflows.
9. Measure precision, recall, F1-score, and edge-level accuracy.
10. Export `WorkflowGraph` for Member 3.

Novelty:

Combines business workflow definitions with runtime distributed traces to identify which service dependencies are truly mandatory instead of treating every observed interaction as equally critical.

Suggested files:

```text
workflow_dependency_modeling/
├── __init__.py
├── service.py
├── trace_reader.py
├── interaction_extractor.py
├── dependency_classifier.py
├── graph_builder.py
└── evaluator.py
```

Public interface:

```python
def build_workflow_graph(
    workflow_definition: dict,
    traces: list[dict],
) -> WorkflowGraph:
    ...
```

First milestone:

```text
Saved Jaeger trace JSON + manual reference workflow
  -> extract service edges
  -> classify dependencies
  -> produce one WorkflowGraph
```

### Member 2 — Deployment Topology Modeling

- Name: P.A.M. Minara
- Registration number: IT23154476
- Folder: `app/engine/deployment_topology_modeling/`
- API: `app/api/deployments.py`
- Output: `DeploymentGraph`

Responsibilities:

1. Parse Kubernetes manifests.
2. Extract replicas, selectors, labels, readiness probes, affinity, anti-affinity, and topology spread constraints.
3. Query live Kubernetes state.
4. Collect pod phase, readiness, restart count, node assignment, owner references, and failure-domain labels.
5. Resolve:
   `Service -> Pod -> ReplicaSet -> Deployment -> Node`
6. Build deployment topology.
7. Calculate replica concentration.
8. Detect single-node service exposure.
9. Compare declared placement intent with actual placement.
10. Analyse placement drift during scale up/down, rolling updates, rescheduling, cordon, and drain.
11. Export `DeploymentGraph` for Members 3 and 4.

Novelty:

Validates the gap between declared Kubernetes deployment intent and actual runtime placement, quantifies replica concentration and service exposure, and models placement drift over time.

Suggested files:

```text
deployment_topology_modeling/
├── __init__.py
├── service.py
├── manifest_parser.py
├── cluster_state_collector.py
├── ownership_resolver.py
├── graph_builder.py
├── concentration_analyzer.py
└── drift_analyzer.py
```

Public interface:

```python
def build_deployment_graph(
    manifests: list[dict],
    cluster_state: dict,
) -> DeploymentGraph:
    ...
```

First milestone:

```text
Sample manifests + cluster-state JSON
  -> service/pod/node mapping
  -> concentration calculation
  -> one DeploymentGraph
```

### Member 3 — Failure Impact Prediction

- Name: K.V.D.Y.R. Karawita
- Registration number: IT23236882
- Folder: `app/engine/failure_impact_prediction/`
- API: `app/api/failure_impacts.py`
- Output: `FailureImpactReport`

Responsibilities:

1. Combine `WorkflowGraph` and `DeploymentGraph`.
2. Construct the cross-layer graph.
3. Accept a selected failed worker node.
4. Virtually remove all pods hosted on that node.
5. Calculate service survival.
6. Calculate workflow survival using mandatory services.
7. Calculate node-level blast radius.
8. Rank risky worker nodes.
9. Explain:
   `failed node -> lost pod -> unavailable service -> failed workflow`
10. Export `FailureImpactReport`.
11. Validate predictions with controlled failure injection.
12. Expose a reusable evaluator for Member 4 simulations.

Important terminology:

This component does not predict the probability that a node will fail. It predicts the impact after an assumed node failure.

Novelty:

Deterministic cross-layer graph simulation that translates actual deployment exposure into workflow-level failure impact.

Suggested files:

```text
failure_impact_prediction/
├── __init__.py
├── service.py
├── cross_layer_graph.py
├── node_failure_simulator.py
├── service_survival.py
├── workflow_survival.py
├── blast_radius.py
└── node_ranking.py
```

Public interface:

```python
def predict_failure_impact(
    workflow: WorkflowGraph,
    deployment: DeploymentGraph,
    failed_node: str,
) -> FailureImpactReport:
    ...
```

First milestone:

```text
WorkflowGraph + DeploymentGraph + failed node
  -> remove failed-node pods virtually
  -> service survival
  -> workflow survival
  -> blast radius
```

### Member 4 — Recommendation Evaluation and Validation

- Name: L.A. Nemal
- Registration number: IT23236264
- Folder: `app/engine/recommendation_evaluation_validation/`
- APIs: `app/api/recommendations.py`, `app/api/validations.py`
- Outputs: `RecommendationReport`, `ValidationReport`

Responsibilities:

1. Receive `FailureImpactReport` and `DeploymentGraph`.
2. Identify risk context and root causes.
3. Generate multiple candidates using context-aware rules.
4. Create a deep-copied virtual deployment.
5. Apply each candidate only to the virtual model.
6. Check feasibility against nodes, CPU, memory, labels, taints, affinity, and topology constraints.
7. Call Member 3's evaluator for each simulated deployment.
8. Estimate resilience improvement.
9. Estimate operational and resource cost.
10. Calculate confidence.
11. Rank all candidates.
12. Generate Kubernetes YAML.
13. Require developer approval.
14. Apply only approved configuration in a controlled test cluster.
15. Collect updated deployment and actual risk.
16. Compare predicted and observed improvement.
17. Produce `ValidationReport`.

Primary candidate set:

1. Redistribute an existing replica.
2. Apply pod anti-affinity.
3. Add topology spread constraints.
4. Increase replica count only when required.
5. Test justified compatible combinations.

The rule engine is only the candidate generator. The research contribution is the full what-if evaluation, feasibility, ranking, and post-deployment validation framework.

Suggested files:

```text
recommendation_evaluation_validation/
├── __init__.py
├── service.py
├── candidate_generator.py
├── virtual_model.py
├── simulator.py
├── feasibility_analyzer.py
├── metrics.py
├── simulation_result.py
├── impact_estimator.py
├── ranking.py
├── yaml_generator.py
└── validator.py
```

Public interfaces:

```python
def evaluate_recommendations(
    deployment: DeploymentGraph,
    failure_impact: FailureImpactReport,
) -> RecommendationReport:
    ...
```

```python
def validate_recommendation(
    recommendation_id: str,
    predicted_blast_radius: float,
    observed_blast_radius: float,
) -> ValidationReport:
    ...
```

First milestone:

```text
DeploymentGraph + FailureImpactReport
  -> REPLICA_CONCENTRATION root cause
  -> TOPOLOGY_SPREAD candidate
  -> feasibility check
  -> deep copy
  -> virtual redistribution
  -> concentration before/after
```

## 8. Component Integration Flow

```text
Member 1:
workflow definition + traces -> WorkflowGraph

Member 2:
manifests + cluster state -> DeploymentGraph

Member 3:
WorkflowGraph + DeploymentGraph + failed node
  -> FailureImpactReport

Member 4:
FailureImpactReport + DeploymentGraph
  -> RecommendationReport

After approval and deployment:
predicted result + observed result
  -> ValidationReport
```

Runtime loop:

```text
Member 2 observes deployment
  -> Member 3 recalculates failure impact
  -> Member 4 evaluates recommendations
  -> developer approval
  -> apply in test cluster
  -> Member 2 observes updated deployment
  -> Member 3 recalculates actual impact
  -> Member 4 validates prediction
```

Member 1 is normally outside the fast runtime loop because approved workflow models are comparatively stable.

## 9. Shared Data Contracts

Python uses snake_case. JSON uses camelCase.

### WorkflowGraph

```json
{
  "workflowId": "checkout",
  "criticality": 0.9,
  "mandatoryServices": [
    "cartservice",
    "checkoutservice",
    "paymentservice"
  ],
  "optionalServices": ["emailservice"],
  "edges": [
    {"source": "frontend", "target": "cartservice"},
    {"source": "cartservice", "target": "checkoutservice"},
    {"source": "checkoutservice", "target": "paymentservice"}
  ]
}
```

### DeploymentGraph

```json
{
  "nodes": [
    {
      "name": "node-1",
      "zone": "zone-a",
      "cpuAvailable": 4,
      "memoryAvailable": 8
    }
  ],
  "services": [
    {
      "name": "paymentservice",
      "declaredReplicas": 3,
      "readyReplicas": 3,
      "replicaConcentration": 0.67,
      "pods": [
        {
          "name": "payment-1",
          "node": "node-1",
          "zone": "zone-a",
          "ready": true,
          "restartCount": 0
        }
      ]
    }
  ]
}
```

### FailureImpactReport

```json
{
  "workflowId": "checkout",
  "failedNode": "node-1",
  "blastRadius": 60,
  "survivability": 40,
  "failedServices": ["paymentservice"],
  "failedWorkflows": ["checkout"],
  "rootCauses": [
    {
      "type": "REPLICA_CONCENTRATION",
      "service": "paymentservice",
      "failureDomain": "node-1",
      "concentration": 0.67
    }
  ],
  "nodeRiskRanking": [
    {"node": "node-1", "blastRadius": 60}
  ]
}
```

### RecommendationReport

```json
{
  "workflowId": "checkout",
  "recommendations": [
    {
      "id": "rec-001",
      "type": "TOPOLOGY_SPREAD",
      "service": "paymentservice",
      "feasible": true,
      "predictedBlastRadius": 15,
      "riskReduction": 45,
      "resourceCost": "LOW",
      "confidence": 0.88,
      "score": 0.86
    }
  ],
  "selectedRecommendationId": "rec-001"
}
```

### ValidationReport

```json
{
  "recommendationId": "rec-001",
  "predictedBlastRadius": 15,
  "observedBlastRadius": 20,
  "predictionError": 5,
  "validated": true
}
```

Shared schemas are integration contracts. Do not change them without team approval.

## 10. Repository Structure

```text
K8S-RESILIENCE-ENGINE/
├── app/
│   ├── main.py
│   ├── api/
│   │   ├── health.py
│   │   ├── workflows.py
│   │   ├── deployments.py
│   │   ├── failure_impacts.py
│   │   ├── recommendations.py
│   │   └── validations.py
│   ├── templates/
│   ├── static/
│   ├── engine/
│   │   ├── workflow_dependency_modeling/
│   │   ├── deployment_topology_modeling/
│   │   ├── failure_impact_prediction/
│   │   └── recommendation_evaluation_validation/
│   ├── integrations/
│   │   ├── kubernetes/
│   │   ├── jaeger/
│   │   └── prometheus/
│   ├── shared/
│   │   ├── sample_loader.py
│   │   └── schemas/
│   └── core/
├── tests/
├── sample_data/
├── experiments/
├── deploy/
├── docs/
├── requirements.txt
├── CLAUDE.md
├── AGENTS.md
└── README.md
```

## 11. Coding and Ownership Rules

- API routes remain thin: validate input, call `service.py`, return a schema.
- Research algorithms belong inside the owning component folder.
- Each component exposes its stable public interface through `service.py`.
- External I/O belongs in `app/integrations/`.
- Use sample JSON while upstream components are incomplete.
- Never change the original DeploymentGraph during what-if simulation.
- Never auto-apply generated YAML.
- Require developer approval.
- Keep algorithms deterministic and explainable.
- Do not duplicate another member's research algorithm.
- Member 4 should call Member 3 for blast-radius recalculation rather than recreating Member 3's logic.
- Use type hints and clear errors.
- Use `pytest.approx` for calculated floating-point values.

Each member may freely modify:

- their engine folder;
- their API route;
- their component template and JavaScript;
- their component tests.

Team approval is required for:

- `app/shared/schemas/`;
- `app/main.py`;
- common configuration;
- global dependencies;
- cross-component signatures;
- metric definitions.

## 12. Current Development Status

Completed:

- architecture agreed;
- shared repository created;
- final folder names selected;
- FastAPI + Jinja2 foundation created;
- dashboard created;
- health API created;
- sample component endpoints created;
- shared Pydantic schemas created;
- sample JSON files created;
- shared sample loader created;
- API routes call component services;
- endpoint tests created;
- Member 4 candidate generator created;
- virtual deployment deep copy created;
- topology-spread simulator created;
- feasibility analyser created;
- concentration metric created;
- SimulationResult created;
- original-input immutability test created;
- floating-point assertions fixed with `pytest.approx`.

Most recent known test result:

```text
9 collected
9 passed
0 failed
```

The earlier float-comparison failure (`0.6666...` compared exactly against `0.67`) has been
fixed by asserting with `pytest.approx(..., abs=0.01)` instead of exact equality. Do not
change correct mathematics to force exact equality — always use `pytest.approx` for derived
floating-point values.

## 13. Immediate Next Tasks

Done:

1. ~~Fix the float test with `pytest.approx`.~~
2. ~~Run `python -m pytest -v` and confirm all tests pass.~~
3. ~~Commit the topology-spread checkpoint.~~

Next:

1. Assign component branches.
2. Begin each member's smallest real milestone (Members 1–3 currently have no real algorithm,
   only empty `service.py` stubs).
3. Member 4 adds pod anti-affinity as the second candidate.
4. Evaluate all candidates, not only the first.
5. Integrate Member 3's evaluator before claiming blast-radius improvement.
6. Add ranking only after simulations and feasibility checks work.

Recommended branches:

```text
feature/workflow-dependency-modeling
feature/deployment-topology-modeling
feature/failure-impact-prediction
feature/recommendation-evaluation-validation
```

## 14. Development Commands

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
python -m pytest -v
```

Expected routes:

```text
/
 /docs
 /api/health
 /api/workflows/sample
 /api/deployments/sample
 /api/failure-impacts/sample
 /api/recommendations/sample
 /api/validations/sample
```

## 15. Experiment Plan

Primary comparison baselines:

1. replica-count-only analysis;
2. deployment-only exposure analysis;
3. dependency-only workflow analysis;
4. proposed cross-layer analysis.

Primary failure scenario:

- single worker-node failure.

Supporting controlled scenarios:

- pod deletion/rescheduling;
- scale up/down;
- rolling update;
- node cordon;
- node drain.

Store:

- workflow definitions;
- raw/exported traces;
- reference workflow graphs;
- manifests;
- DeploymentGraph snapshots;
- FailureImpactReports;
- candidates;
- simulated deployments;
- predicted metrics;
- generated YAML;
- approved changes;
- actual post-deployment graphs;
- chaos definitions;
- observed workflow results;
- ValidationReports;
- timestamps and environment configuration.

Example formulas:

```text
Replica concentration =
  maximum ready replicas on one failure domain
  / total ready replicas

Risk reduction =
  baseline blast radius - predicted blast radius

Observed improvement =
  baseline observed blast radius - post-change observed blast radius

Prediction error =
  abs(predicted blast radius - observed blast radius)
```

Ranking may combine benefit, feasibility, resource cost, operational complexity, and confidence. Weights must be justified, configurable, and tested through sensitivity analysis.

## 16. Research Guardrails

Do not:

- build machine learning without a justified dataset and research question;
- claim probability prediction;
- automatically apply Kubernetes changes;
- replace Kubernetes scheduler;
- build a complex React frontend;
- add PostgreSQL before persistence is needed;
- connect all external tools before sample-data algorithms work;
- claim validation before controlled experiments;
- expand to multi-cluster or regional failure in the current scope.

## 17. Viva Summary

System:

> The system connects mandatory business workflow dependencies with actual Kubernetes pod placement to predict which services and workflows fail when a selected worker node fails.

Member 4:

> My component generates several candidate deployment improvements, tests each one virtually without changing the real cluster, checks feasibility and cost, ranks the candidates, generates Kubernetes YAML for developer approval, and validates whether the predicted improvement occurs after deployment.

Novelty:

> The novelty is not a simple if-else rule engine. The rule engine only generates candidates. The contribution is evaluating multiple candidates through cross-layer what-if simulation, feasibility analysis, ranking, and post-deployment validation.
