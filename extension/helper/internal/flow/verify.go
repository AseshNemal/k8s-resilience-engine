package flow

import (
	"context"
	"strings"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/kube"
	"resilience-engine/helper/internal/kubeconfig"
)

// kindProviderPrefix is what every node of a kind cluster reports in spec.providerID.
const kindProviderPrefix = "kind://docker/"

// verdict is what the cluster itself says about whether it is a local kind cluster. It is built only from
// what the helper read over a TLS connection verified against the kubeconfig's own certificate authority;
// the context name has no part in it.
//
// The provider IDs are SUPPORTING evidence. They separate a kind cluster from a cloud cluster that is
// reached through a loopback tunnel (its nodes report aws://, gce://, azure://...), but a kind cluster on
// another machine, tunnelled to this one, would look the same. The loopback address, the verified TLS, and the
// native approval dialog that names the cluster and its address are what the person still decides on.
type verdict struct {
	Contacted bool   // the cluster was asked something
	Reachable bool   // it answered over verified TLS
	Supported bool   // reachable, and every node reports a kind provider ID
	Version   string // the server's version, when reachable
	Nodes     int    // how many nodes reported a kind provider ID
	Reason    string // why it is not supported; never contains anything the cluster sent
}

func verifyLocalKind(ctx context.Context, cs kube.Client, cluster kubeconfig.Cluster) verdict {
	if len(cluster.CAData) == 0 {
		return verdict{Reason: "the kubeconfig has no certificate authority for this cluster, so its TLS identity cannot be verified"}
	}
	v := verdict{Contacted: true}
	version, err := cs.ServerVersion(ctx)
	if err != nil {
		v.Reason = "the cluster is not reachable"
		return v
	}
	v.Reachable, v.Version = true, version

	nodes, err := cs.List(ctx, kube.Nodes, "")
	switch {
	case err != nil:
		v.Reason = "could not read the cluster's nodes, so it cannot be confirmed as a local kind cluster"
		return v
	case len(nodes) == 0:
		v.Reason = "the cluster reports no nodes, so it cannot be confirmed as a local kind cluster"
		return v
	}
	for _, n := range nodes {
		if !strings.HasPrefix(n.ProviderID, kindProviderPrefix) {
			v.Reason = "its nodes do not all report a kind provider ID (kind://docker/…), so it is not recognised as a local kind cluster"
			return v
		}
	}
	v.Supported, v.Nodes = true, len(nodes)
	return v
}

// verified runs verifyLocalKind and turns a cluster that is not usable into the Result every command
// returns for it: unreachable, or unsupported with the reason. A nil Result means the cluster passed.
func verified(ctx context.Context, cmd string, name string, cs kube.Client, cluster kubeconfig.Cluster) (verdict, *Result) {
	v := verifyLocalKind(ctx, cs, cluster)
	switch {
	case v.Supported:
		return v, nil
	case v.Contacted && !v.Reachable:
		r := fail(cmd, StateClusterUnreachable, "the cluster is not reachable; is Docker Desktop running and the kind cluster started?")
		return v, &r
	}
	r := fail(cmd, StateUnsupportedContext, v.Reason)
	r.Context = approve.Sanitize(name, 80)
	return v, &r
}
