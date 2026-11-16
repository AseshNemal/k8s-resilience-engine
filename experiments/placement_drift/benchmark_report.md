# Component 2: Placement Drift Scenario Report

> **DRY-RUN SIMULATION.** T0 and T1 placements are scripted in the scenario code. These numbers illustrate the metrics and must not be reported as observed scheduler behaviour.

## 1. Aggregate Summary
- **Execution Mode:** DRY_RUN_SIMULATION
- **Total Scenarios:** 8
- **Completed:** 8
- **Mean Placement Drift Rate ($D_{rate}$):** 22.92%
- **Max Placement Drift Rate:** 50.00%
- **Total Runtime:** 1.60 ms

## 2. Scenario Results
| Scenario ID | Disruption Type | Status | $D_{rate}$ | $\Delta E_{rate}$ | Moved Pods | Latency (ms) | Rollback |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `01_initial_deployment` | `INITIAL_DEPLOYMENT` | `COMPLETED` | 0.00% | 0.00% | 0 | 0.2 | Pass |
| `02_scale_up_stress` | `SCALE_UP_STRESS` | `COMPLETED` | 0.00% | 0.00% | 0 | 0.4 | Pass |
| `03_scale_down_loss` | `SCALE_DOWN_LOSS` | `COMPLETED` | 50.00% | 50.00% | 0 | 0.3 | Pass |
| `04_rolling_update` | `ROLLING_UPDATE_CHURN` | `COMPLETED` | 50.00% | 50.00% | 0 | 0.1 | Pass |
| `05_pod_deletion_chaos` | `POD_DELETION_CHAOS` | `COMPLETED` | 50.00% | 50.00% | 0 | 0.1 | Pass |
| `06_node_cordon` | `NODE_CORDON` | `COMPLETED` | 0.00% | 0.00% | 0 | 0.1 | Pass |
| `07_node_drain_eviction` | `NODE_DRAIN_EVICTION` | `COMPLETED` | 33.33% | 33.33% | 2 | 0.1 | Pass |
| `08_resource_contention` | `RESOURCE_CONTENTION` | `COMPLETED` | 0.00% | 0.00% | 0 | 0.1 | Pass |

## 3. Observations (computed from the table above)
- Highest D_rate (50.00%): `03_scale_down_loss`, `04_rolling_update`, `05_pod_deletion_chaos`.
- Exposure rate increased in: `03_scale_down_loss`, `04_rolling_update`, `05_pod_deletion_chaos`, `07_node_drain_eviction`.
- Every scenario reported a successful rollback.