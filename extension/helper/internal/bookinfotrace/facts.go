package bookinfotrace

import (
	"context"
	"regexp"
	"strconv"
	"strings"

	"resilience-engine/helper/internal/kube"
)

// DeploymentFact is what the cluster reported about one Deployment. Unknown stays nil.
type DeploymentFact struct {
	Replicas      *int     `json:"replicas"`
	ReadyReplicas *int     `json:"readyReplicas"`
	Containers    []string `json:"containers"`
	SpecDigest    string   `json:"specDigest"`
}

// PodFact is what the cluster reported about one Pod.
type PodFact struct {
	Name       string   `json:"name"`
	App        string   `json:"app"`
	Version    string   `json:"version"`
	Containers []string `json:"containers"`
	Ready      *bool    `json:"ready"`
	Phase      string   `json:"phase"`
}

// Facts is everything Plan needs from the cluster, read-only.
type Facts struct {
	ServerVersion        string                    `json:"serverVersion"`
	KubernetesMinor      int                       `json:"kubernetesMinor"` // 0 when it could not be read
	NamespaceExists      bool                      `json:"namespaceExists"`
	NamespaceLabels      map[string]string         `json:"namespaceLabels"`
	IstioNamespaceExists bool                      `json:"istioNamespaceExists"`
	IstioCRDs            []string                  `json:"istioCRDs"`
	Deployments          map[string]DeploymentFact `json:"deployments"`
	Pods                 []PodFact                 `json:"pods"`
}

var minorOf = regexp.MustCompile(`^v?\d+\.(\d+)`)

// kubernetesMinor turns "v1.36.1" (or "v1.36.1+k3s1") into 36, and anything else into 0.
func kubernetesMinor(version string) int {
	m := minorOf.FindStringSubmatch(strings.TrimSpace(version))
	if m == nil {
		return 0
	}
	n, _ := strconv.Atoi(m[1])
	return n
}

// Gather reads the cluster. Only a missing object is a normal answer (it means "absent"); any other error is
// returned, because a plan made from a partial read would be a guess.
func Gather(ctx context.Context, r kube.Reader) (Facts, error) {
	c := Contract
	f := Facts{NamespaceLabels: map[string]string{}, Deployments: map[string]DeploymentFact{}}

	version, err := r.ServerVersion(ctx)
	if err != nil {
		return f, err
	}
	f.ServerVersion, f.KubernetesMinor = version, kubernetesMinor(version)

	ns, err := r.Get(ctx, kube.Namespaces, "", c.Namespace)
	switch {
	case err == nil:
		f.NamespaceExists = true
		for k, v := range ns.Labels {
			f.NamespaceLabels[k] = v
		}
	case !kube.IsNotFound(err):
		return f, err
	}

	if _, err := r.Get(ctx, kube.Namespaces, "", c.IstioNamespace); err == nil {
		f.IstioNamespaceExists = true
	} else if !kube.IsNotFound(err) {
		return f, err
	}

	crds, err := r.List(ctx, kube.CustomResourceDefinitions, "")
	if err != nil {
		return f, err
	}
	for _, crd := range crds {
		if strings.HasSuffix(crd.Name, ".istio.io") {
			f.IstioCRDs = append(f.IstioCRDs, crd.Name)
		}
	}

	if !f.NamespaceExists {
		return f, nil
	}
	deployments, err := r.List(ctx, kube.Deployments, c.Namespace)
	if err != nil {
		return f, err
	}
	for _, d := range deployments {
		f.Deployments[d.Name] = DeploymentFact{Replicas: d.Replicas, ReadyReplicas: d.ReadyReplicas, Containers: d.Containers, SpecDigest: d.SpecDigest}
	}
	pods, err := r.List(ctx, kube.Pods, c.Namespace)
	if err != nil {
		return f, err
	}
	for _, p := range pods {
		f.Pods = append(f.Pods, PodFact{Name: p.Name, App: p.Labels["app"], Version: p.Labels["version"], Containers: p.Containers, Ready: p.Ready, Phase: p.Phase})
	}
	return f, nil
}
