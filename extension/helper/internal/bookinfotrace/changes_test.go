package bookinfotrace

import (
	"strings"
	"testing"
)

func TestTheContractIsWhatThePlanSays(t *testing.T) {
	c := Contract
	if c.IstioVersion != "1.31.2" || c.Namespace != "bookinfo" || c.IstioNamespace != "istio-system" || c.ManagedBy != "resilience-tracing" {
		t.Fatalf("contract: %+v", c)
	}
	if c.SupportedKubernetesMinors.Min != 32 || c.SupportedKubernetesMinors.Max != 37 {
		t.Fatalf("Istio 1.31 supports Kubernetes 1.32 to 1.37, got %+v", c.SupportedKubernetesMinors)
	}
	if got := c.WorstCaseMiB(); got != 512+4*128+115+144 {
		t.Fatalf("worst case = %d", got)
	}
}

func TestTheWorstCaseFitsInTheMeasuredHeadroomButNotByAMile(t *testing.T) {
	headroom := 3917.0 - 2025.0 // measured: the Docker VM limit and what its containers used
	worst := float64(Contract.WorstCaseMiB())
	if worst >= headroom || worst < headroom/3 {
		t.Fatalf("worst case %.0f against headroom %.0f MiB looks wrong", worst, headroom)
	}
}

func TestThePreviewIsOrderedAndNumbered(t *testing.T) {
	steps := Steps(StepInputs{JaegerIPv4: "192.168.65.254", PodsToRecreate: []string{"details-x", "productpage-y", "ratings-z"}})
	order := []string{"Istio 1.31.2 (minimal profile)", "ServiceEntry", "Telemetry", "Namespace", "Pod", "Deployment", "DestinationRule", "VirtualService"}
	if len(steps) != len(order) {
		t.Fatalf("%d steps", len(steps))
	}
	for n, step := range steps {
		if step.Step != n+1 || step.Kind != order[n] {
			t.Errorf("step %d = %+v", n+1, step)
		}
	}
	// Order is a safety property: the label before the Pods are recreated, and the route only after reviews-v2 exists.
	position := func(action, kind, name string) int {
		for n, s := range steps {
			if s.Action == action && s.Kind == kind && strings.Contains(s.Name, name) {
				return n
			}
		}
		return -1
	}
	if !(position("label", "Namespace", "bookinfo") < position("recreate", "Pod", "details-x")) {
		t.Error("the label must come before the Pods are recreated")
	}
	if !(position("create", "Deployment", "reviews-v2") < position("create", "VirtualService", "trace-demo-reviews")) {
		t.Error("the route must come after reviews-v2 is created")
	}
	if !strings.Contains(steps[1].Detail, "192.168.65.254:4317") {
		t.Errorf("the validated address must be in the preview: %q", steps[1].Detail)
	}
	if len(steps[4].Restarts) != 3 {
		t.Errorf("restarts: %v", steps[4].Restarts)
	}
}

func TestAnUndiscoveredAddressIsShownAsSuchNeverAsADefault(t *testing.T) {
	steps := Steps(StepInputs{})
	if !strings.Contains(steps[1].Detail, "NOT DISCOVERED") || strings.Contains(steps[1].Detail, "192.168") {
		t.Fatalf("got %q", steps[1].Detail)
	}
}

func TestThePreviewNeverContainsTheTestTrafficPod(t *testing.T) {
	for _, s := range Steps(StepInputs{}) {
		if s.Name == "trace-demo-client" {
			t.Fatalf("the client Pod is created by Run test traffic, which has its own approval: %+v", s)
		}
	}
}
