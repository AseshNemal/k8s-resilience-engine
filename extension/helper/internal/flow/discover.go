package flow

import (
	"context"
	"errors"
	"time"

	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/rbac"
)

const probeTimeout = 6 * time.Second

// Discover lists the kubeconfig contexts and classifies each. Only supported (local kind) contexts
// are contacted, with read-only calls: the API version and the extension's own RBAC objects. A
// remote or unknown cluster in the user's kubeconfig is never touched.
func Discover(ctx context.Context, d Deps) Result {
	list, current, err := d.Clusters.Contexts()
	if err != nil {
		return fail("discover", StateError, err.Error())
	}
	out := make([]ContextInfo, 0, len(list))
	for _, c := range list {
		info := ContextInfo{Name: c.Name, Server: clusters.HostPort(c.Server), Current: c.Name == current}
		info.Supported, info.Reason = Classify(c.Name, c.Server)
		if info.Supported {
			probe(ctx, d, c, &info)
		}
		out = append(out, info)
	}
	return Result{OK: true, Command: "discover", State: StateConnected, Contexts: out, Message: ""}
}

func probe(ctx context.Context, d Deps, c clusters.Context, info *ContextInfo) {
	cs, _, err := d.Clusters.Client(c.Name)
	if err != nil {
		info.Reason = err.Error()
		return
	}
	ctx, cancel := context.WithTimeout(ctx, probeTimeout)
	defer cancel()
	yes, no := true, false
	ver, err := cs.Discovery().ServerVersion()
	if err != nil {
		info.Reachable = &no
		info.Reason = "the cluster is not reachable"
		return
	}
	info.Reachable, info.Version = &yes, ver.GitVersion

	report, err := rbac.Inspect(ctx, cs)
	switch {
	case errors.Is(err, rbac.ErrForbidden):
		info.Authorization = "unknown (your kubeconfig cannot read RBAC)"
	case err != nil:
		info.Authorization = "unknown"
	default:
		info.Authorization = string(report.Overall())
	}
}
