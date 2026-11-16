package bookinfotrace

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/kube"
)

// Blocker is a reason the setup must not start. Code is stable for the page; Message is fixed wording.
type Blocker struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// PlanResult is the whole preview. Ready is true only when there is nothing blocking AND every reading that decides
// safety was actually taken.
type PlanResult struct {
	Ready             bool         `json:"ready"`
	Context           string       `json:"context"`
	IstioVersion      string       `json:"istioVersion"`
	KubernetesVersion string       `json:"kubernetesVersion"`
	Blockers          []Blocker    `json:"blockers"`
	Warnings          []string     `json:"warnings"`
	Preview           []Change     `json:"preview"`
	Restarts          []string     `json:"restarts"`
	Disruptions       []Disruption `json:"disruptions"`
	Jaeger            *Address     `json:"jaeger"`
	Baseline          Measurements `json:"baseline"`
	Gate0             Decision     `json:"gate0"`
}

func (p *PlanResult) block(code, format string, args ...any) {
	p.Blockers = append(p.Blockers, Blocker{Code: code, Message: fmt.Sprintf(format, args...)})
}

func hasSidecar(containers []string) bool {
	for _, c := range containers {
		if c == "istio-proxy" {
			return true
		}
	}
	return false
}

// recreatedWorkloads includes an existing reviews-v2: its Pods need sidecars too, but its Deployment stays user-owned.
func recreatedWorkloads(f Facts) []workload {
	workloads := append([]workload(nil), Contract.RecreatedWorkloads...)
	if _, exists := f.Deployments[Contract.CreatedFromOriginal.Name]; exists {
		workloads = append(workloads, workload{App: "reviews", Version: "v2"})
	}
	return workloads
}

// podsToRecreate picks the live Pods of the workloads that need a sidecar, in the contract's order: the workloads
// nothing else depends on first (ratings, then details, then productpage, then an existing reviews-v2), so a Pod that
// is down interrupts as little of the page as possible. Several Pods of one workload are ordered by name.
func podsToRecreate(f Facts) []string {
	var names []string
	for _, w := range recreatedWorkloads(f) {
		var ofWorkload []string
		for _, p := range f.Pods {
			if p.App == w.App && p.Version == w.Version {
				ofWorkload = append(ofWorkload, p.Name)
			}
		}
		sort.Strings(ofWorkload)
		names = append(names, ofWorkload...)
	}
	return names
}

func expectedRestarts(f Facts) int {
	total := 0
	for _, w := range recreatedWorkloads(f) {
		d, ok := f.Deployments[w.App+"-"+w.Version]
		if !ok || d.Replicas == nil {
			return -1
		}
		total += *d.Replicas
	}
	return total
}

// Disruption says what a person using the application will notice while one Pod is recreated. The estimate of how
// long is deliberately not a number: it was not measured on this machine.
type Disruption struct {
	Workload string `json:"workload"`
	Pod      string `json:"pod"`
	Effect   string `json:"effect"`
}

var effects = map[string]string{
	"ratings":     "the reviews show \"ratings unavailable\"",
	"details":     "the product page shows no book details",
	"productpage": "the Bookinfo web page does not load",
}

func disruptionsFor(f Facts, pods []string) []Disruption {
	out := []Disruption{}
	for _, name := range pods {
		for _, p := range f.Pods {
			if p.Name != name {
				continue
			}
			replicas := 0
			for _, candidate := range f.Pods {
				if candidate.App == p.App && candidate.Version == p.Version {
					replicas++
				}
			}
			effect := effects[p.App]
			if replicas > 1 {
				effect = fmt.Sprintf("one of %d replicas is replaced at a time; the other replicas stay available", replicas)
			} else if effect == "" {
				effect = "requests to it fail"
			}
			out = append(out, Disruption{Workload: p.App, Pod: name, Effect: effect})
		}
	}
	return out
}

// BuildPlan is pure: it turns facts and readings into a preview and the reasons to stop. addrErr is the reason the
// Jaeger address could not be discovered and validated (nil when addr is set).
func BuildPlan(f Facts, addr *Address, addrErr error, baseline Measurements, contextName string) PlanResult {
	c := Contract
	p := PlanResult{Context: contextName, IstioVersion: c.IstioVersion, KubernetesVersion: f.ServerVersion, Baseline: baseline, Jaeger: addr,
		Blockers: []Blocker{}, Warnings: []string{}, Disruptions: []Disruption{}}

	switch {
	case f.KubernetesMinor == 0:
		p.block("KUBERNETES_VERSION_UNKNOWN", "The cluster's Kubernetes version could not be read, so compatibility with Istio %s cannot be confirmed.", c.IstioVersion)
	case f.KubernetesMinor < c.SupportedKubernetesMinors.Min || f.KubernetesMinor > c.SupportedKubernetesMinors.Max:
		p.block("KUBERNETES_VERSION_UNSUPPORTED", "Istio %s supports Kubernetes 1.%d to 1.%d; this cluster is %s.", c.IstioVersion,
			c.SupportedKubernetesMinors.Min, c.SupportedKubernetesMinors.Max, approve.Sanitize(f.ServerVersion, 40))
	}

	if !f.NamespaceExists {
		p.block("BOOKINFO_NAMESPACE_MISSING", "The %s namespace does not exist, so there is no benchmark to trace.", c.Namespace)
	}
	if f.IstioNamespaceExists || len(f.IstioCRDs) > 0 {
		p.block("ISTIO_ALREADY_PRESENT", "Istio is already installed in this cluster (%s namespace or Istio CRDs found). This setup never touches an existing Istio.", c.IstioNamespace)
	}
	if _, ok := f.NamespaceLabels["istio-injection"]; ok {
		p.block("NAMESPACE_ALREADY_INJECTED", "The %s namespace already has an istio-injection label.", c.Namespace)
	}
	if _, ok := f.NamespaceLabels["istio.io/rev"]; ok {
		p.block("NAMESPACE_ALREADY_INJECTED", "The %s namespace already has an istio.io/rev label.", c.Namespace)
	}

	v2, hasV2 := f.Deployments[c.CreatedFromOriginal.Name]
	required := []string{}
	for _, name := range c.RequiredWorkloads {
		if name == "reviews-v1" && hasV2 {
			continue // the connected layout serves reviews entirely from v2
		}
		required = append(required, name)
	}
	if hasV2 {
		required = append(required, c.CreatedFromOriginal.Name)
	}
	var missing, notReady []string
	for _, name := range required {
		d, ok := f.Deployments[name]
		switch {
		case !ok:
			missing = append(missing, name)
		case d.Replicas == nil || d.ReadyReplicas == nil || *d.ReadyReplicas < *d.Replicas || *d.Replicas == 0:
			notReady = append(notReady, name) // unknown readiness is not ready
		}
	}
	if len(missing) > 0 {
		p.block("REQUIRED_WORKLOAD_MISSING", "These Bookinfo Deployments are missing: %s.", strings.Join(missing, ", "))
	}
	if len(notReady) > 0 {
		p.block("WORKLOAD_NOT_READY", "These Deployments are not fully Ready (or their state is unknown): %s. The setup does not start on an unhealthy baseline.", strings.Join(notReady, ", "))
	}
	for _, pod := range f.Pods {
		if hasSidecar(pod.Containers) {
			p.block("NAMESPACE_ALREADY_INJECTED", "Pod %s already has a sidecar.", pod.Name)
			break
		}
	}

	recreate := podsToRecreate(f)
	expected := expectedRestarts(f)
	if expected < 0 || len(recreate) != expected {
		p.block("WORKLOAD_NOT_READY", "Expected %d Pods matching the declared replicas to recreate, found %d.", expected, len(recreate))
	}
	p.Restarts = recreate
	p.Disruptions = disruptionsFor(f, recreate)

	address := ""
	switch {
	case addrErr != nil:
		p.block("JAEGER_ADDRESS_UNAVAILABLE", "The Jaeger receiver could not be validated from the cluster: %s.", addrErr.Error())
	case addr == nil:
		p.block("JAEGER_ADDRESS_UNAVAILABLE", "The Jaeger receiver address was not discovered.")
	default:
		address = addr.IPv4
	}

	worstCase := c.WorstCaseMiB()
	v2Replicas := 0
	if hasV2 && v2.Replicas != nil {
		v2Replicas = *v2.Replicas
		worstCase = c.WorstCaseForReviewsV2(v2Replicas, true)
	}
	p.Gate0 = Evaluate(Gate0, baseline, baseline, DefaultThresholds(), worstCase)
	if !p.Gate0.Proceed {
		for _, reason := range p.Gate0.Reasons {
			p.block("RESOURCES_NOT_SAFE", "%s.", reason)
		}
	}
	if baseline.SwapUsedMiB != nil && *baseline.SwapUsedMiB > 2048 {
		p.Warnings = append(p.Warnings, fmt.Sprintf("This computer is already using %.0f MiB of swap. The setup is gated on how much more it swaps, but close other applications first.", *baseline.SwapUsedMiB))
	}

	p.Preview = Steps(StepInputs{JaegerIPv4: address, PodsToRecreate: recreate, ExistingReviewsV2: hasV2, ExistingReviewsV2Replicas: v2Replicas})
	p.Ready = len(p.Blockers) == 0
	return p
}

// Plan reads, measures and rediscovers, and changes nothing: the cluster is only reachable through a kube.Reader.
func Plan(ctx context.Context, d Deps, r kube.Reader, contextName string) PlanResult {
	facts, err := Gather(ctx, r)
	if err != nil {
		p := PlanResult{Context: contextName, IstioVersion: Contract.IstioVersion, Blockers: []Blocker{}, Warnings: []string{}, Preview: []Change{}}
		p.block("CLUSTER_READ_FAILED", "The cluster could not be read completely, so nothing can be planned.")
		return p
	}
	var addr *Address
	found, addrErr := RediscoverJaegerOTLP(ctx, d)
	if addrErr == nil {
		addr = &found
	}
	return BuildPlan(facts, addr, addrErr, Measure(ctx, d), contextName)
}
