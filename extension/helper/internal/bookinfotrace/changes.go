package bookinfotrace

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"strings"
)

//go:embed changeset.json
var changesetJSON []byte

// ChangesetObject is one object the setup creates, and the file in k8s-bookinfo/tracing it comes from.
type ChangesetObject struct {
	Kind     string `json:"kind"`
	Name     string `json:"name"`
	Source   string `json:"source"`
	Selector string `json:"selector,omitempty"`
}

type workload struct {
	App     string `json:"app"`
	Version string `json:"version"`
}

// Changeset is the contract of what may be touched. changeset.json is compiled in and a Python test compares it with
// the YAML files in k8s-bookinfo/tracing, so this list and the files cannot drift apart unnoticed.
type Changeset struct {
	IstioVersion              string                 `json:"istioVersion"`
	SupportedKubernetesMinors struct{ Min, Max int } `json:"supportedKubernetesMinors"`
	Namespace                 string                 `json:"namespace"`
	IstioNamespace            string                 `json:"istioNamespace"`
	ManagedBy                 string                 `json:"managedBy"`
	Install                   struct {
		Source                    string `json:"source"`
		RenderedObjects           int    `json:"renderedObjects"`
		CustomResourceDefinitions int    `json:"customResourceDefinitions"`
		Webhooks                  int    `json:"webhooks"`
	} `json:"install"`
	Objects             []ChangesetObject `json:"objects"`
	CreatedFromOriginal ChangesetObject   `json:"createdFromOriginal"`
	RecreatedWorkloads  []workload        `json:"recreatedWorkloads"`
	RequiredWorkloads   []string          `json:"requiredWorkloads"`
	MemoryMiB           struct {
		IstiodLimit       int `json:"istiodLimit"`
		SidecarLimit      int `json:"sidecarLimit"`
		Sidecars          int `json:"sidecars"`
		ReviewsV2Estimate int `json:"reviewsV2Estimate"`
		ClientPod         int `json:"clientPod"`
	} `json:"memoryMiB"`
}

// Contract is the parsed changeset.json. A malformed one would be a build error caught by the tests, so it panics.
var Contract = mustChangeset()

func mustChangeset() Changeset {
	var c Changeset
	if err := json.Unmarshal(changesetJSON, &c); err != nil {
		panic("bookinfotrace: changeset.json is malformed: " + err.Error())
	}
	return c
}

// WorstCaseMiB is the original create-reviews-v2 plan's memory bound.
func (c Changeset) WorstCaseMiB() int { return c.WorstCaseForReviewsV2(1, false) }

// WorstCaseForReviewsV2 accounts for the actual sidecars. An existing reviews-v2 is already included in Docker's
// current memory use, so only its new sidecars are added; a created one also needs its application memory estimate.
func (c Changeset) WorstCaseForReviewsV2(replicas int, preexisting bool) int {
	m := c.MemoryMiB
	if replicas < 1 {
		replicas = 1
	}
	sidecars := len(c.RecreatedWorkloads) + replicas
	worst := m.IstiodLimit + sidecars*m.SidecarLimit + m.ClientPod
	if !preexisting {
		worst += m.ReviewsV2Estimate
	}
	return worst
}

// Change is one line of the exact preview: what will happen to what.
type Change struct {
	Step      int      `json:"step"`
	Action    string   `json:"action"` // install, create, keep, label, recreate
	Kind      string   `json:"kind"`
	Namespace string   `json:"namespace,omitempty"`
	Name      string   `json:"name"`
	Detail    string   `json:"detail,omitempty"`
	Restarts  []string `json:"restarts,omitempty"`
}

// StepInputs are the facts a preview needs that are not constants.
type StepInputs struct {
	JaegerIPv4                string   // the rediscovered, validated address; empty when it could not be
	PodsToRecreate            []string // the live Pod names that would be deleted so they come back with a sidecar
	ExistingReviewsV2         bool     // adopt the user's Deployment; never create, edit or delete it
	ExistingReviewsV2Replicas int
}

// Steps is the exact, ordered preview of the setup. The order is the order they would run in: the route goes after
// reviews-v2 is Ready, and the label goes before the Pods are recreated. The temporary client Pod that sends test traffic
// is not a step of the setup: it is created only by "Run test traffic", which has its own approval.
func Steps(in StepInputs) []Change {
	c := Contract
	address := in.JaegerIPv4
	if address == "" {
		address = "NOT DISCOVERED"
	}
	steps := []Change{
		{Action: "install", Kind: "Istio " + c.IstioVersion + " (minimal profile)", Namespace: c.IstioNamespace, Name: "istiod",
			Detail: fmt.Sprintf("%d objects: istiod, %d CRDs, %d webhooks, RBAC; no gateways; istiod limited to %d MiB",
				c.Install.RenderedObjects, c.Install.CustomResourceDefinitions, c.Install.Webhooks, c.MemoryMiB.IstiodLimit)},
		{Action: "create", Kind: "ServiceEntry", Namespace: c.Namespace, Name: c.object("ServiceEntry").Name,
			Detail: "Jaeger OTLP gRPC receiver at " + address + ":4317 (validated from the cluster nodes)"},
		{Action: "create", Kind: "Telemetry", Namespace: c.Namespace, Name: c.object("Telemetry").Name,
			Detail: "tracing on for this namespace only, 100% sampling"},
		{Action: "label", Kind: "Namespace", Name: c.Namespace, Detail: "istio-injection=enabled"},
		{Action: "recreate", Kind: "Pod", Namespace: c.Namespace, Name: strings.Join(in.PodsToRecreate, ", "),
			Detail: "deleted one at a time, each waits for its replacement to be Ready; they return with a sidecar", Restarts: in.PodsToRecreate},
	}
	if in.ExistingReviewsV2 {
		steps = append(steps, Change{Action: "keep", Kind: "Deployment", Namespace: c.Namespace, Name: c.CreatedFromOriginal.Name,
			Detail: fmt.Sprintf("already exists with %d replicas; its Deployment spec and replica count are unchanged, and Undo restarts its Pods back without sidecars", in.ExistingReviewsV2Replicas)})
	} else {
		steps = append(steps, Change{Action: "create", Kind: "Deployment", Namespace: c.Namespace, Name: c.CreatedFromOriginal.Name,
			Detail: "from the unmodified bookinfo.yaml (" + c.CreatedFromOriginal.Selector + "); gets a sidecar at creation"})
	}
	steps = append(steps,
		Change{Action: "create", Kind: "DestinationRule", Namespace: c.Namespace, Name: c.object("DestinationRule").Name, Detail: "subsets v1 and v2 of reviews"},
		Change{Action: "create", Kind: "VirtualService", Namespace: c.Namespace, Name: c.object("VirtualService").Name,
			Detail: "every request for reviews goes to reviews-v2, so each trace includes ratings"},
	)
	for i := range steps {
		steps[i].Step = i + 1
	}
	return steps
}

func (c Changeset) object(kind string) ChangesetObject {
	for _, o := range c.Objects {
		if o.Kind == kind {
			return o
		}
	}
	return ChangesetObject{Kind: kind, Name: "<missing from changeset.json>"}
}
