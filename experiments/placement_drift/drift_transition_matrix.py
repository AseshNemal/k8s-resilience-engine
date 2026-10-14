"""
Drift Transition Matrix: Formalizes Markov-style pod migration tracking across failure domains.
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
    Constructs and formats a Markov-style migration transition matrix:
        T[i, j] = count of pods migrating from node n_i to node n_j.
    """

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

        all_nodes: Set[str] = set()
        for node in t0_snapshot.nodes:
            all_nodes.add(node.get("name", "unknown"))
        for node in t1_snapshot.nodes:
            all_nodes.add(node.get("name", "unknown"))
        for p in t0_snapshot.pods:
            all_nodes.add(p.node_name)
        for p in t1_snapshot.pods:
            all_nodes.add(p.node_name)

        sorted_nodes = sorted(all_nodes)
        matrix: Dict[str, Dict[str, int]] = {
            src: {dst: 0 for dst in sorted_nodes} for src in sorted_nodes
        }

        transitions_map: Dict[Tuple[str, str], List[str]] = {}

        for pod_name, p0 in t0_pods.items():
            if pod_name in t1_pods:
                p1 = t1_pods[pod_name]
                src, dst = p0.node_name, p1.node_name
                if src in matrix and dst in matrix[src]:
                    matrix[src][dst] += 1

                if src != dst:
                    pair = (src, dst)
                    if pair not in transitions_map:
                        transitions_map[pair] = []
                    transitions_map[pair].append(p0.service_name)

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
