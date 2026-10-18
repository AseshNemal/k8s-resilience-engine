// Proof of concept for the cross-platform host helper. READ-ONLY: it never creates, updates or
// deletes anything in a cluster, never requests a token, and never writes to the extension backend.
// Output is JSON and never contains a credential.
//
//	discover        list kubeconfig contexts, classify them, and probe only supported ones
//	transport-check test the candidate host-to-backend transports (status reads only)
//	approve-demo    show the native approval dialog (creates nothing) and report the answer
//	version
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"runtime"
	"sort"
	"strings"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"
)

const (
	rbacName     = "resilience-engine-reader"
	managedLabel = "app.kubernetes.io/managed-by"
	probeTimeout = 4 * time.Second
)

type contextInfo struct {
	Name          string `json:"name"`
	Server        string `json:"server"`
	Supported     bool   `json:"supported"`
	Reason        string `json:"reason,omitempty"`
	Probed        bool   `json:"probed"`
	Reachable     *bool  `json:"reachable,omitempty"`
	ServerVersion string `json:"serverVersion,omitempty"`
	Authorization string `json:"authorization,omitempty"` // approved | absent | unknown
	ProbeError    string `json:"probeError,omitempty"`
}

func emit(v any) {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	_ = enc.Encode(v)
}

func fail(msg string) {
	emit(map[string]any{"ok": false, "error": msg})
	os.Exit(1)
}

// classify decides whether a context is a local cluster this helper may talk to. Supported means
// a kind cluster whose API server is https on a loopback address. Nothing else is ever contacted.
func classify(name, server string) (bool, string) {
	u, err := url.Parse(server)
	if err != nil || u.Hostname() == "" {
		return false, "unparseable API server address"
	}
	if u.Scheme != "https" {
		return false, "API server is not https"
	}
	switch u.Hostname() {
	case "127.0.0.1", "localhost", "::1":
	default:
		return false, "API server is not a loopback address (remote clusters are out of scope)"
	}
	if !strings.HasPrefix(name, "kind-") {
		return false, "not a kind context (only kind-* is supported initially)"
	}
	return true, ""
}

func discover() {
	rules := clientcmd.NewDefaultClientConfigLoadingRules()
	raw, err := rules.Load()
	if err != nil {
		fail("could not read kubeconfig: " + err.Error())
	}
	names := make([]string, 0, len(raw.Contexts))
	for n := range raw.Contexts {
		names = append(names, n)
	}
	sort.Strings(names)

	out := make([]contextInfo, 0, len(names))
	for _, n := range names {
		c := raw.Contexts[n]
		cluster := raw.Clusters[c.Cluster]
		info := contextInfo{Name: n}
		if cluster == nil {
			info.Reason = "context references a missing cluster"
			out = append(out, info)
			continue
		}
		if u, err := url.Parse(cluster.Server); err == nil {
			info.Server = u.Host // host:port only, no path or credentials
		}
		info.Supported, info.Reason = classify(n, cluster.Server)
		if info.Supported {
			probe(&info, n, rules)
		}
		out = append(out, info)
	}
	emit(map[string]any{
		"ok":          true,
		"platform":    runtime.GOOS + "/" + runtime.GOARCH,
		"current":     raw.CurrentContext,
		"contexts":    out,
		"noKubectl":   true,
		"noTokenSeen": true,
	})
}

// probe makes two read-only calls against a supported cluster: GET /version and GET one
// ClusterRoleBinding. Authorization state lives in the cluster, so the helper keeps none.
func probe(info *contextInfo, name string, rules *clientcmd.ClientConfigLoadingRules) {
	info.Probed = true
	cfg, err := clientcmd.NewNonInteractiveDeferredLoadingClientConfig(rules, &clientcmd.ConfigOverrides{CurrentContext: name}).ClientConfig()
	if err != nil {
		info.ProbeError = "could not build client config"
		return
	}
	cfg.Timeout = probeTimeout
	cs, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		info.ProbeError = "could not build client"
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), probeTimeout)
	defer cancel()
	yes, no := true, false
	ver, err := cs.Discovery().ServerVersion()
	if err != nil {
		info.Reachable = &no
		info.ProbeError = "API server unreachable"
		info.Authorization = "unknown"
		return
	}
	info.Reachable = &yes
	info.ServerVersion = ver.GitVersion
	crb, err := cs.RbacV1().ClusterRoleBindings().Get(ctx, rbacName, metav1.GetOptions{})
	switch {
	case err == nil && crb.Labels[managedLabel] != "":
		info.Authorization = "approved"
	case err == nil:
		info.Authorization = "present-unlabelled"
	default:
		info.Authorization = "absent"
	}
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: resilience-helper discover|transport-check|approve-demo|version")
		os.Exit(2)
	}
	switch os.Args[1] {
	case "discover":
		discover()
	case "transport-check":
		transportCheck()
	case "approve-demo":
		approveDemo()
	case "version":
		emit(map[string]any{"ok": true, "platform": runtime.GOOS + "/" + runtime.GOARCH, "go": runtime.Version()})
	default:
		fail("unknown command")
	}
}
