# Member 2 – Demo Commands (Deployment Topology Modeling)

Every command here was tested on the `kind-resilience-research` cluster in PowerShell (VS Code terminal).
Run all commands from the project folder.

**Safety rules**

- The team's app is in namespace **`bookinfo`**. On `bookinfo` we only **read**, plus one scale that we undo (section 3).
- All disruptions run in namespace **`bookinfo-exp`** (your experiment copy of Bookinfo, 3 copies of each service).
- Every change has an **Undo** step right after it. Always run it.

---

## 1. Before the demo (about 10 minutes before)

```powershell
# Go to the project folder. All other commands must run from here.
cd C:\Users\HP\Videos\k8s-resilience-engine

# Check that Docker is on and the 4 cluster nodes (containers) are running.
# You should see 4 lines, each with "Up".
docker ps --filter "name=resilience-research"

# Point kubectl at the experiment cluster.
# You should see: Switched to context "kind-resilience-research"
kubectl config use-context kind-resilience-research

# Check that all 4 nodes are Ready. If one is NotReady, wait one minute and run it again.
kubectl get nodes

# Check that the team's Bookinfo pods are running (4 pods, all "1/1 Running").
kubectl -n bookinfo get pods -o wide

# Check that your experiment copy is running (16 pods: 4 services x 3 copies, all Running).
kubectl -n bookinfo-exp get pods -o wide

# Start the web app. Keep this terminal open.
# Open a NEW terminal (the "+" in VS Code) for every other command.
# If you see "address already in use", the app is already running. That is fine.
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000

# Open the Deployment Topology page in the browser.
Start-Process "http://127.0.0.1:8000/components/deployments?namespace=bookinfo"
```

Optional, if you show the Docker Desktop extension:

```powershell
# You should see k8s-resilience-engine in the list.
# Then open Docker Desktop and click "Resilience Engine" on the left.
docker extension ls
```

---

## 2. If the panel says "show me" (read-only, changes nothing)

```powershell
# "Where do the pods really run?"
# The NODE column shows the worker node of every pod. The Topology tab shows the same.
kubectl -n bookinfo get pods -o wide

# "What are labels?"
# Shows the key=value tags on every pod, for example app=reviews,version=v1.
kubectl -n bookinfo get pods --show-labels

# "How does a Service find its pods?"
# Shows the Service's selector: {"app":"reviews"}
kubectl -n bookinfo get svc reviews -o jsonpath='{.spec.selector}'

# The same selector, used by hand. These are exactly the pods the Service sends traffic to.
kubectl -n bookinfo get pods -l app=reviews -o wide

# "Show me the owner references."
# For every pod: its owner (a ReplicaSet) and its node.
kubectl -n bookinfo get pods -o custom-columns=POD:.metadata.name,OWNER:.metadata.ownerReferences[0].name,NODE:.spec.nodeName

# The next link in the chain: each ReplicaSet's owner is a Deployment.
kubectl -n bookinfo get rs -o custom-columns=REPLICASET:.metadata.name,OWNER:.metadata.ownerReferences[0].kind,OWNER_NAME:.metadata.ownerReferences[0].name,WANTED:.spec.replicas

# One pod in detail: its node, its labels, and "Controlled By" (its owner).
kubectl -n bookinfo describe pod -l app=reviews | Select-String "^Node:|^Labels|Controlled By"

# "What is the difference between a Deployment and a ReplicaSet?"
# The Deployment asks for N copies. The ReplicaSet keeps exactly N copies running.
kubectl -n bookinfo get deployments,replicasets

# "Which pods are ready behind each Service?"
# EndpointSlices are Kubernetes' own list. The accuracy test used them as the truth.
kubectl -n bookinfo get endpointslices

# "Where does the page get the YAML from?"
# The YAML that was last applied with kubectl. kubectl scale does not change it.
kubectl -n bookinfo apply view-last-applied deployment/reviews-v1

# "How much CPU is booked on a node?" (look at the cpu line under Allocated resources)
kubectl describe node resilience-research-worker3 | Select-String -Context 0,6 "Allocated resources"

# "Show me the tool's output." A summary of the DeploymentGraph from the API.
(Invoke-RestMethod "http://127.0.0.1:8000/api/deployments/discovery?namespace=bookinfo").graph.services | Select-Object name, readyReplicas, replicaConcentration, exposedFailureDomains | Format-Table -AutoSize

# "Does it only work for Bookinfo?" Show a different app (Boutique-style stand-in services).
# Then pick "placement-drift-exp" in the page's Namespace dropdown. No code change needed.
kubectl -n placement-drift-exp get pods -o wide
```

---

## 3. Live drift on the team's Bookinfo (one change, then undo)

```powershell
# Change the cluster by hand: 3 copies of reviews. The YAML still asks for 1.
kubectl -n bookinfo scale deployment reviews-v1 --replicas=3

# Wait until the new copies are ready. You should see: successfully rolled out
kubectl -n bookinfo rollout status deployment reviews-v1 --timeout=120s

# See where the new copies went. Then refresh the page:
# "Declared vs live" shows YAML 1 -> cluster 3.
# "Exposed services" shows whether reviews is still exposed.
kubectl -n bookinfo get pods -l app=reviews -o wide

# UNDO (always): back to 1 copy, as the team's YAML says.
kubectl -n bookinfo scale deployment reviews-v1 --replicas=1
```

---

## 4. The 8 disruption scenarios on `reviews` (namespace `bookinfo-exp`)

These are the same 8 disruptions as the stored experiments, done by hand so the panel can watch.
Each one takes about 30–60 seconds.

**Watch them on the page:** open
`http://127.0.0.1:8000/components/deployments?namespace=bookinfo-exp`
and press the reload button (↻) after each scenario. Look at **Topology** and **Exposed services**.

**Every scenario follows the same steps:** Reset → Before → Action → After → Undo.

### 4.0 Reset (run this before EVERY scenario)

```powershell
# Put Config A back (3 copies, no placement rule) and restart reviews,
# so the copies start spread over the nodes again.
kubectl apply -f deploy\experiments\placement-drift-bookinfo\config-a-no-rule.yaml
kubectl -n bookinfo-exp rollout restart deployment reviews-v1
kubectl -n bookinfo-exp rollout status deployment reviews-v1 --timeout=180s
```

### 4.L Look (run this as "Before" and "After" in every scenario)

```powershell
# Shows each reviews pod, its STATUS and its NODE.
# "Terminating" = an old copy shutting down. It disappears after about 30 seconds.
# Wait and run the command again until only "Running" pods are left.
kubectl -n bookinfo-exp get pods -l app=reviews -o wide
```

### Scenario 1 – Initial deployment

*Question: where does the scheduler put a fresh deployment?*

```powershell
# Action: apply the YAML. Nothing else changes.
kubectl apply -f deploy\experiments\placement-drift-bookinfo\config-a-no-rule.yaml
kubectl -n bookinfo-exp rollout status deployment reviews-v1 --timeout=120s

# After: run the Look command (4.L).
```

**What you should see:** 3 copies on 3 different nodes. Not exposed.
**Undo:** nothing to undo.

### Scenario 2 – Scale up 3 → 6

*Question: do the new copies stay spread?*

```powershell
# Action: 6 copies instead of 3.
kubectl -n bookinfo-exp scale deployment reviews-v1 --replicas=6
kubectl -n bookinfo-exp rollout status deployment reviews-v1 --timeout=120s

# After: run the Look command (4.L).

# Undo: back to 3 copies.
kubectl -n bookinfo-exp scale deployment reviews-v1 --replicas=3
```

**What you should see (tested):** 6 copies, 2 on each node. Not exposed.

### Scenario 3 – Scale down 3 → 2

*Question: do the remaining copies stay spread?*

```powershell
# Action: only 2 copies.
kubectl -n bookinfo-exp scale deployment reviews-v1 --replicas=2

# After: wait about 30 seconds (the removed copy shows "Terminating" first), then run the Look command (4.L).

# Undo: back to 3 copies.
kubectl -n bookinfo-exp scale deployment reviews-v1 --replicas=3
```

**What you should see:** 2 copies on 2 different nodes. Not exposed.

### Scenario 4 – Rolling update

*Question: where do the replacement copies land?*

```powershell
# Action: replace every copy with a new one, one by one (what happens on a new release).
kubectl -n bookinfo-exp rollout restart deployment reviews-v1
kubectl -n bookinfo-exp rollout status deployment reviews-v1 --timeout=180s

# After: wait about 30 seconds for the old copies to finish "Terminating", then run the Look command (4.L).
```

**What you should see (tested):** 3 new copies (a new name hash), on 3 different nodes. Not exposed.
**Undo:** nothing to undo.

### Scenario 5 – Pod deletion

*Question: where does the replacement copy land?*

```powershell
# Action: pick the first reviews pod and delete it. Kubernetes starts a replacement.
$pod = kubectl -n bookinfo-exp get pods -l app=reviews -o jsonpath='{.items[0].metadata.name}'
kubectl -n bookinfo-exp delete pod $pod
kubectl -n bookinfo-exp rollout status deployment reviews-v1 --timeout=120s

# After: run the Look command (4.L). One pod has a new name.
```

**What you should see (tested):** still 3 copies. The new one usually lands on the same node the old one left. Not exposed.
**Undo:** nothing to undo.

### Scenario 6 – Node cordon + restart

*Question: what happens when one node may not take new pods?*

```powershell
# Before: run the Look command (4.L) and note a node that has a reviews copy.
# The example uses resilience-research-worker.

# Action: block new pods on that node (existing pods stay), then restart reviews,
# so its new copies must be placed while that node is blocked.
kubectl cordon resilience-research-worker
kubectl -n bookinfo-exp rollout restart deployment reviews-v1
kubectl -n bookinfo-exp rollout status deployment reviews-v1 --timeout=180s

# After: wait about 30 seconds, then run the Look command (4.L).

# UNDO (always): allow new pods on the node again.
kubectl uncordon resilience-research-worker
```

**What you should see (tested):** all 3 copies on the other 2 nodes (2 + 1). Concentration goes from 33% to 67%. Still not exposed, because 2 nodes are used.

### Scenario 7 – Node drain

*Question: where do the copies go when a node is emptied for maintenance?*

```powershell
# Safety check first: the node must NOT have pods from the team's "bookinfo" namespace.
# Look at the first column. If you see "bookinfo" (not "bookinfo-exp"), pick another worker node.
# On 24 Oct, resilience-research-worker had no "bookinfo" pods.
kubectl get pods -A -o wide --field-selector spec.nodeName=resilience-research-worker

# Before: run the Look command (4.L). Make sure one reviews copy is on this node
# (if not, run Reset 4.0 again).

# Action: move every pod off the node and block it.
kubectl drain resilience-research-worker --ignore-daemonsets --delete-emptydir-data --timeout=180s
kubectl -n bookinfo-exp rollout status deployment reviews-v1 --timeout=180s

# After: run the Look command (4.L).

# UNDO (always): open the node again.
kubectl uncordon resilience-research-worker
```

**What you should see:** `node/resilience-research-worker drained` (tested). The reviews copy that was on it moves to another node, so you get 2 copies on one node and 1 on the other. Not exposed (the same as all 5 stored drain runs).
The drain moves only `bookinfo-exp` and `placement-drift-exp` pods. `kindnet` and `kube-proxy` stay (they run on every node).

### Scenario 8 – Constrained resources

*Question: when only one node has free CPU, do all copies end up on that node?*

This one fills two nodes with "filler" pods. It is safer to let your experiment code do it, because the code
calculates the filler sizes and always removes the fillers afterwards. It runs ONE run only, and saves the
result in a temporary folder (not in the project).

```powershell
# Action + Undo in one command (about 65 seconds).
# The last line printed should be:  run 1: D_rate=0.25  target NOT_EXPOSED -> EXPOSED
.\.venv\Scripts\python.exe experiments\placement_drift\run_live_experiments.py --context kind-resilience-research --workload bookinfo --scenarios 08_constrained_resources --configs A-no-rule --repetitions 1 --results-dir "$env:TEMP\member2-demo-run"

# After: run the Look command (4.L). All 3 reviews copies are on ONE node.
# Refresh the page: reviews is EXPOSED.

# Check the fillers are gone. You should see: No resources found
kubectl get deploy -A -l experiment=placement-drift-filler
```

**What you should see (tested):** `target NOT_EXPOSED -> EXPOSED`, D_rate 25% (1 of 4 services changed). Same result as the stored runs.
**Undo:** run Reset (4.0). It spreads the copies again.

**The same with the spread rule (Config B):**

```powershell
# Same scenario, with the topology spread rule.
# Expected: the rule keeps 2 copies Pending, only 1 runs, so reviews is still EXPOSED (compliance 100%).
.\.venv\Scripts\python.exe experiments\placement_drift\run_live_experiments.py --context kind-resilience-research --workload bookinfo --scenarios 08_constrained_resources --configs B-spread --repetitions 1 --results-dir "$env:TEMP\member2-demo-run-b"

# Undo: run Reset (4.0). It puts Config A back (removes the spread rule).
```

### Any scenario, automatically

The same command runs any scenario once, with the measurements (D_rate, exposure) printed at the end.
Swap the scenario name. Each takes about 1 minute.

| Scenario | Name to use after `--scenarios` |
| --- | --- |
| 1 Initial deployment | `01_initial_deployment` |
| 2 Scale up | `02_scale_up` |
| 3 Scale down | `03_scale_down` |
| 4 Rolling update | `04_rolling_update` |
| 5 Pod deletion | `05_pod_deletion` |
| 6 Node cordon | `06_node_cordon` |
| 7 Node drain | `07_node_drain` |
| 8 Constrained resources | `08_constrained_resources` |

```powershell
# Example: scenario 7, one run, result saved in a temporary folder.
.\.venv\Scripts\python.exe experiments\placement_drift\run_live_experiments.py --context kind-resilience-research --workload bookinfo --scenarios 07_node_drain --configs A-no-rule --repetitions 1 --results-dir "$env:TEMP\member2-demo-run"
```

### 4.Z Clean up after the scenarios

```powershell
# 1. All 4 nodes must be Ready and none "SchedulingDisabled".
#    If one says SchedulingDisabled, run: kubectl uncordon <that node name>
kubectl get nodes

# 2. No filler pods left. You should see: No resources found
kubectl get deploy -A -l experiment=placement-drift-filler

# 3. Put the experiment copy back to its normal state.
kubectl apply -f deploy\experiments\placement-drift-bookinfo\config-a-no-rule.yaml
kubectl -n bookinfo-exp rollout restart deployment reviews-v1

# 4. The team's Bookinfo is untouched: every deployment shows 1/1.
kubectl -n bookinfo get deployments
```

---

## 5. Evidence: the stored experiments (80 runs)

```powershell
# Number of files in the Bookinfo results folder.
# You should see 82 = 80 run files + summary.json + summary.md.
(Get-ChildItem experiments\placement_drift\results\bookinfo-five-repetitions).Count

# The summary table of all 80 runs (mean D_rate, exposure, compliance, 95% CI).
Get-Content experiments\placement_drift\results\bookinfo-five-repetitions\summary.md

# Open one run in VS Code: the real kubectl action, the placement before (t0) and after (t1),
# and the measurements. In this run, two reviews pods are "unscheduled" (Pending).
code experiments\placement_drift\results\bookinfo-five-repetitions\08_constrained_resources__B-spread__run1.json

# The only difference between Config A and Config B: the spread rule (green lines).
# PowerShell may report "exit 1". That only means the files differ.
git --no-pager diff --no-index deploy\experiments\placement-drift-bookinfo\config-a-no-rule.yaml deploy\experiments\placement-drift-bookinfo\config-b-topology-spread.yaml

# Open Config B at the spread rule (line 216). Config A's reviews replicas are at line 163.
code -g deploy\experiments\placement-drift-bookinfo\config-b-topology-spread.yaml:216
```

---

## 6. Evidence: accuracy (84 / 84)

```powershell
# The four scores from the API: service_pod 24/24, pod_node 24/24, owner 24/24, exposure 12/12.
(Invoke-RestMethod "http://127.0.0.1:8000/api/deployments/benchmarks/ground-truth?recordings=bookinfo-exp").counts | Format-List

# What really happened when worker2 was switched off:
# details and ratings went down, productpage and reviews survived. The model predicted the same.
Get-Content experiments\single-node-failure\bookinfo-exp-20261022-171837\resilience-research-worker2\resilience-research-worker2-failure\service-result.txt

# Truth for pod -> node (the saved output of kubectl get pods -o wide).
Get-Content experiments\single-node-failure\bookinfo-exp-20261022-171837\resilience-research-worker2\pod-placement.txt

# Truth for pod -> Deployment (from the API server's selector query, not from owner references).
Get-Content experiments\single-node-failure\bookinfo-exp-20261022-171837\resilience-research-worker2\owner-truth.txt

# Proof that the node really was down (STATUS NotReady).
Get-Content experiments\single-node-failure\bookinfo-exp-20261022-171837\resilience-research-worker2\resilience-research-worker2-failure\nodes-after-detection.txt
```

---

## 7. Code and tests

```powershell
# Open a file in VS Code at the exact line (code -g file:line).

# The selector rule: a pod belongs to a Service when it has every label of the selector.
code -g app\engine\deployment_topology_modeling\live_state\ownership_resolver.py:21

# Pod -> ReplicaSet -> Deployment, by owner-reference UID.
code -g app\engine\deployment_topology_modeling\live_state\ownership_resolver.py:46

# The exposure and concentration formulas.
code -g app\engine\deployment_topology_modeling\placement_metrics.py:55

# "Effective ready": a pod on a NotReady node is not counted as ready.
code -g app\engine\deployment_topology_modeling\live_state\concentration_analyzer.py:24

# Reading the YAML that was last applied (the declared intent).
code -g app\engine\deployment_topology_modeling\service.py:289

# The 8 scenarios and their real kubectl actions (constrained resources is at line 133).
code -g experiments\placement_drift\live\scenarios.py:166

# One experiment run: reset -> before (T0) -> action -> after (T1) -> measure -> undo.
code -g experiments\placement_drift\live\runner.py:124

# Run all Component 2 tests (about 20 seconds). You should see: 386 passed.
# The tests use a fake cluster, so they do not need the real one.
.\.venv\Scripts\python.exe -m pytest tests\deployment_topology_modeling -q

# Run only the ownership resolver tests, with every test name shown.
.\.venv\Scripts\python.exe -m pytest tests\deployment_topology_modeling\test_phase1_ownership_resolver.py -v
```

---

## 8. If you get stuck

```powershell
# Problem: "failed to connect to the docker API" or the page shows CLUSTER_NOT_CONNECTED.
# Fix: open Docker Desktop from the Start menu, then wait until all nodes are Ready
# (you should see "condition met" 4 times). Then refresh the page.
# Meanwhile, show the Experiments, Output and Accuracy tabs. They use stored results.
kubectl wait --for=condition=Ready nodes --all --timeout=180s

# Problem: kubectl shows a different cluster.
# Fix: check the current cluster, and if it is wrong, run the use-context command from section 1.
kubectl config current-context

# Problem: "address already in use" when starting the app.
# Fix: if this prints 8000, the app is already running. Just open the browser.
Get-NetTCPConnection -LocalPort 8000 -State Listen

# If you need a second copy of the app anyway, start it on another port.
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8001

# Problem: a node shows "SchedulingDisabled" (a cordon or drain was not undone).
kubectl uncordon resilience-research-worker

# Problem: a pod is not Running. The Events lines at the end show why.
kubectl -n bookinfo-exp describe pod -l app=reviews | Select-String -Context 0,8 "^Events"

# Problem: "rollout status" waits for a long time.
# Press Ctrl + C, then run the Look command (4.L) to see what is happening.
# Pods in "Pending" usually mean a node is still cordoned: run the uncordon command above.

# Problem: the page shows old data.
# Fix: press the reload button (↻) on the page, or Ctrl + F5 in the browser.
```

---

## 9. Never run during the demo

- A full experiment run (`run_live_experiments.py` **without** `--scenarios ... --repetitions 1`): 80 runs take hours.
- `record_ground_truth.py` or `docker stop ...`: they switch nodes off. Pods in **every** namespace,
  including the team's `bookinfo`, go down for a few minutes.
- `kubectl drain` or `kubectl cordon` on a node that has `bookinfo` pods (check first, see scenario 7).
- `kubectl delete` on anything in the `bookinfo` namespace.

If the panel asks you to run the full experiments, say:
"A full run takes hours and changes the cluster, so here are the stored results of all 80 runs" (section 5),
or run one scenario once (section 4).

---

## 10. After the demo

```powershell
# The team's Bookinfo must be back to normal: every deployment 1/1.
# If reviews-v1 still shows 3/3, run: kubectl -n bookinfo scale deployment reviews-v1 --replicas=1
kubectl -n bookinfo get deployments

# No node may be left blocked: none should say SchedulingDisabled.
kubectl get nodes

# Stop the web app: go to the terminal where it runs and press Ctrl + C.
```
