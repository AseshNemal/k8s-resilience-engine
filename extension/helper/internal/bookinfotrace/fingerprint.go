package bookinfotrace

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
)

// Fingerprint identifies what a plan would do: the cluster and Istio version, the ordered changes with their details
// (including the Jaeger address and the Pods to be recreated), and the disruptions. It leaves out the memory readings,
// which change from one second to the next. An approval is bound to this value: if the plan made just before applying
// has another one, the cluster changed while the person was deciding, and nothing is applied.
func Fingerprint(p PlanResult) string {
	raw, _ := json.Marshal(struct {
		Context           string
		IstioVersion      string
		KubernetesVersion string
		Preview           []Change
		Disruptions       []Disruption
		Ready             bool
	}{p.Context, p.IstioVersion, p.KubernetesVersion, p.Preview, p.Disruptions, p.Ready})
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}
