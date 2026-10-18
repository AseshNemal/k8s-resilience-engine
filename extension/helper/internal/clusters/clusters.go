// Package clusters reads the user's own kubeconfig. The administrator credentials in it stay in the
// helper process: they are used to talk to the cluster and are never copied to the backend, the UI
// or any output.
package clusters

import (
	"errors"
	"net/url"
	"os"
	"sort"
	"time"

	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"

	"resilience-engine/helper/internal/kubeconfig"
)

// Context is one entry of the user's kubeconfig, without any credential.
type Context struct {
	Name   string
	Server string // full API server URL
}

type Loader interface {
	Contexts() (contexts []Context, current string, err error)
	// Client returns a client for one context and the cluster's address and CA, which the helper
	// needs to build the backend's credential. Callers must only ask for supported contexts.
	Client(name string) (kubernetes.Interface, kubeconfig.Cluster, error)
}

// Kubeconfig loads contexts from the standard locations ($KUBECONFIG, then ~/.kube/config), on every
// operating system.
type Kubeconfig struct {
	Rules   *clientcmd.ClientConfigLoadingRules
	Timeout time.Duration
}

func New() *Kubeconfig {
	return &Kubeconfig{Rules: clientcmd.NewDefaultClientConfigLoadingRules(), Timeout: 8 * time.Second}
}

func (k *Kubeconfig) Contexts() ([]Context, string, error) {
	raw, err := k.Rules.Load()
	if err != nil {
		return nil, "", errors.New("could not read your kubeconfig")
	}
	names := make([]string, 0, len(raw.Contexts))
	for n := range raw.Contexts {
		names = append(names, n)
	}
	sort.Strings(names)
	out := make([]Context, 0, len(names))
	for _, n := range names {
		c := Context{Name: n}
		if cl := raw.Clusters[raw.Contexts[n].Cluster]; cl != nil {
			c.Server = cl.Server
		}
		out = append(out, c)
	}
	return out, raw.CurrentContext, nil
}

func (k *Kubeconfig) Client(name string) (kubernetes.Interface, kubeconfig.Cluster, error) {
	cfg, err := clientcmd.NewNonInteractiveDeferredLoadingClientConfig(
		k.Rules, &clientcmd.ConfigOverrides{CurrentContext: name}).ClientConfig()
	if err != nil {
		return nil, kubeconfig.Cluster{}, errors.New("could not use that kubeconfig context")
	}
	cfg.Timeout = k.Timeout
	cs, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		return nil, kubeconfig.Cluster{}, errors.New("could not create a cluster client")
	}
	ca := cfg.CAData
	if len(ca) == 0 && cfg.CAFile != "" {
		if ca, err = os.ReadFile(cfg.CAFile); err != nil {
			return nil, kubeconfig.Cluster{}, errors.New("could not read the cluster's certificate authority")
		}
	}
	return cs, kubeconfig.Cluster{Server: cfg.Host, CAData: ca}, nil
}

// HostPort returns host:port of a server URL for display, or "" if it cannot be parsed. Paths and
// credentials in the URL are dropped.
func HostPort(server string) string {
	u, err := url.Parse(server)
	if err != nil {
		return ""
	}
	return u.Host
}
