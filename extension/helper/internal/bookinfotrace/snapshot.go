package bookinfotrace

import (
	"fmt"
	"sort"
	"strings"
)

// WorkloadSnap is what has to come back unchanged.
type WorkloadSnap struct {
	Replicas   *int   `json:"replicas"`
	SpecDigest string `json:"specDigest"`
}

// Snapshot is taken before Apply and compared after Undo. It holds facts, not credentials or object bodies.
type Snapshot struct {
	TakenAt              string                  `json:"takenAt"`
	Context              string                  `json:"context"`
	NamespaceLabels      map[string]string       `json:"namespaceLabels"`
	Deployments          map[string]WorkloadSnap `json:"deployments"`
	PodContainers        map[string][]string     `json:"podContainers"` // "app/version" -> container names
	ReviewsV2Preexisting bool                    `json:"reviewsV2Preexisting"`
}

// TakeSnapshot records what a rollback has to restore.
func TakeSnapshot(f Facts, contextName, takenAt string) Snapshot {
	s := Snapshot{TakenAt: takenAt, Context: contextName, NamespaceLabels: map[string]string{}, Deployments: map[string]WorkloadSnap{}, PodContainers: map[string][]string{}}
	for k, v := range f.NamespaceLabels {
		s.NamespaceLabels[k] = v
	}
	for name, d := range f.Deployments {
		s.Deployments[name] = WorkloadSnap{Replicas: d.Replicas, SpecDigest: d.SpecDigest}
	}
	for _, p := range f.Pods {
		containers := append([]string(nil), p.Containers...)
		sort.Strings(containers)
		s.PodContainers[p.App+"/"+p.Version] = containers
	}
	_, s.ReviewsV2Preexisting = f.Deployments[Contract.CreatedFromOriginal.Name]
	return s
}

// Diff is one way the cluster is not back to the snapshot.
type Diff struct {
	What   string `json:"what"`
	Before string `json:"before"`
	After  string `json:"after"`
}

func intText(v *int) string {
	if v == nil {
		return "unknown"
	}
	return fmt.Sprint(*v)
}

// Compare says how the cluster after an Undo differs from the snapshot taken before the setup. An empty result is
// the proof that the benchmark was restored: the same namespace labels, the same Deployment specs (by digest, so
// a stray rollout-restart annotation shows up), and the same containers in each Pod, with no sidecar left.
func Compare(before, after Snapshot) []Diff {
	var diffs []Diff
	for _, k := range keys(before.NamespaceLabels, after.NamespaceLabels) {
		if before.NamespaceLabels[k] != after.NamespaceLabels[k] {
			diffs = append(diffs, Diff{What: "namespace label " + k, Before: before.NamespaceLabels[k], After: after.NamespaceLabels[k]})
		}
	}
	names := map[string]bool{}
	for n := range before.Deployments {
		names[n] = true
	}
	for n := range after.Deployments {
		names[n] = true
	}
	for _, n := range sortedKeys(names) {
		b, hadB := before.Deployments[n]
		a, hasA := after.Deployments[n]
		switch {
		case hadB && !hasA:
			diffs = append(diffs, Diff{What: "Deployment " + n, Before: "present", After: "missing"})
		case !hadB && hasA:
			diffs = append(diffs, Diff{What: "Deployment " + n, Before: "absent", After: "present"})
		case b.SpecDigest != a.SpecDigest:
			diffs = append(diffs, Diff{What: "Deployment " + n + " spec", Before: short(b.SpecDigest), After: short(a.SpecDigest)})
		case intText(b.Replicas) != intText(a.Replicas):
			diffs = append(diffs, Diff{What: "Deployment " + n + " replicas", Before: intText(b.Replicas), After: intText(a.Replicas)})
		}
	}
	pods := map[string]bool{}
	for k := range before.PodContainers {
		pods[k] = true
	}
	for k := range after.PodContainers {
		pods[k] = true
	}
	for _, k := range sortedKeys(pods) {
		b, hadB := before.PodContainers[k]
		a, hasA := after.PodContainers[k]
		switch {
		case hadB && !hasA:
			diffs = append(diffs, Diff{What: "Pod " + k, Before: "running", After: "missing"})
		case !hadB && hasA:
			diffs = append(diffs, Diff{What: "Pod " + k, Before: "absent", After: "running"})
		case strings.Join(b, ",") != strings.Join(a, ","):
			diffs = append(diffs, Diff{What: "Pod " + k + " containers", Before: strings.Join(b, ","), After: strings.Join(a, ",")})
		}
	}
	return diffs
}

func short(digest string) string {
	if len(digest) > 12 {
		return digest[:12]
	}
	return digest
}

func keys(a, b map[string]string) []string {
	set := map[string]bool{}
	for k := range a {
		set[k] = true
	}
	for k := range b {
		set[k] = true
	}
	return sortedKeys(set)
}

func sortedKeys(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
