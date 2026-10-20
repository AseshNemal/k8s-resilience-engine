"""
Drift Transition Matrix: counts pods that changed node between two snapshots.
Component: Component 2 - Deployment Topology Modeling
"""
from typing import Dict, List, Set, Tuple

from experiments.placement_drift.scenario_models import (
    ClusterSnapshot,
    NodeTransitionRecord,
)

LATEX_HLINE = r"\hline"


class DriftTransitionMatrix:
    """
    Count matrix (not a probability / Markov matrix):
        T[i, j] = number of pods that were on node n_i at T0 and on node n_j at T1.
    Pods are matched by name, so a pod replaced under a new name (delete, rollout) is not counted.
    """

    @classmethod
    def _collect_all_nodes(
        cls, t0_snapshot: ClusterSnapshot, t1_snapshot: ClusterSnapshot
    ) -> List[str]:
        """Collects unique sorted node names across both snapshots."""
        all_nodes: Set[str] = set()
        for snap in (t0_snapshot, t1_snapshot):
            for node in snap.nodes:
                all_nodes.add(node.get("name", "unknown"))
            for p in snap.pods:
                all_nodes.add(p.node_name)
        return sorted(all_nodes)

    @classmethod
    def build_matrix(
        cls, t0_snapshot: ClusterSnapshot, t1_snapshot: ClusterSnapshot
    ) -> Tuple[List[str], Dict[str, Dict[str, int]], List[NodeTransitionRecord]]:
        """
        Builds the transition grid between T0 and T1 snapshots.
        Returns: (sorted_node_names, matrix_dict, transition_records)
        """
        t0_pods = {p.pod_name: p for p in t0_snapshot.pods}
        t1_pods = {p.pod_name: p for p in t1_snapshot.pods}

        sorted_nodes = cls._collect_all_nodes(t0_snapshot, t1_snapshot)
        matrix: Dict[str, Dict[str, int]] = {
            src: dict.fromkeys(sorted_nodes, 0) for src in sorted_nodes
        }

        transitions_map: Dict[Tuple[str, str], List[str]] = {}

        for pod_name, p0 in t0_pods.items():
            p1 = t1_pods.get(pod_name)
            if p1 is None:
                continue

            src, dst = p0.node_name, p1.node_name
            if src in matrix and dst in matrix[src]:
                matrix[src][dst] += 1

            if src != dst:
                transitions_map.setdefault((src, dst), []).append(p0.service_name)

        records = [
            NodeTransitionRecord(
                from_node=src,
                to_node=dst,
                pod_count=len(svcs),
                services_affected=sorted(set(svcs)),
            )
            for (src, dst), svcs in sorted(transitions_map.items())
        ]

        return sorted_nodes, matrix, records

    @classmethod
    def format_ascii_table(
        cls, sorted_nodes: List[str], matrix: Dict[str, Dict[str, int]]
    ) -> str:
        """Formats the transition matrix into an ASCII text table for terminal logs."""
        header = f"{'From \\ To':<18} " + " ".join(f"{n:>12}" for n in sorted_nodes)
        separator = "-" * len(header)
        rows = [header, separator]

        for src in sorted_nodes:
            row_vals = " ".join(f"{matrix[src].get(dst, 0):>12}" for dst in sorted_nodes)
            rows.append(f"{src:<18} {row_vals}")

        return "\n".join(rows)

    @classmethod
    def format_latex_table(
        cls, sorted_nodes: List[str], matrix: Dict[str, Dict[str, int]]
    ) -> str:
        """Formats the transition matrix into publication-ready LaTeX for the dissertation."""
        col_spec = "l" + "c" * len(sorted_nodes)
        headers = " & ".join(sorted_nodes)
        lines = [
            r"\begin{table}[ht]",
            r"\centering",
            r"\caption{Pod Placement Migration Transition Matrix ($T_0 \to T_1$)}",
            rf"\begin{{tabular}}{{{col_spec}}}",
            LATEX_HLINE,
            rf"From / To & {headers} \\",
            LATEX_HLINE,
        ]

        for src in sorted_nodes:
            row_vals = " & ".join(str(matrix[src].get(dst, 0)) for dst in sorted_nodes)
            lines.append(rf"{src} & {row_vals} \\")

        lines.extend([
            LATEX_HLINE,
            r"\end{tabular}",
            r"\end{table}",
        ])
        return "\n".join(lines)
