// Package clusters reads the user's own kubeconfig. The administrator credentials in it stay in the
// helper process: they are used to talk to the cluster and are never copied to the backend, the UI
// or any output.
package clusters

import (
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"resilience-engine/helper/internal/kube"
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
	Client(name string) (kube.Client, kubeconfig.Cluster, error)
}

// Kubeconfig loads contexts from the standard locations ($KUBECONFIG, then ~/.kube/config), on every
// operating system.
type Kubeconfig struct {
	// ExplicitPath, when set, is the only file read and it must exist.
	ExplicitPath string
	// Precedence lists the files to merge, first one winning. Files that do not exist are skipped.
	Precedence []string
	Timeout    time.Duration
}

// New reads $KUBECONFIG (a list, separated as the operating system separates paths) or, when it is
// unset, ~/.kube/config.
func New() *Kubeconfig {
	return &Kubeconfig{Precedence: defaultPaths(), Timeout: 8 * time.Second}
}

func defaultPaths() []string {
	if env := os.Getenv("KUBECONFIG"); env != "" {
		return filepath.SplitList(env)
	}
	if home, err := os.UserHomeDir(); err == nil {
		return []string{filepath.Join(home, ".kube", "config")}
	}
	return nil
}

func (k *Kubeconfig) load() (*kube.Kubeconfig, error) {
	return kube.LoadKubeconfig(k.ExplicitPath, k.Precedence)
}

func (k *Kubeconfig) Contexts() ([]Context, string, error) {
	loaded, err := k.load()
	if err != nil {
		return nil, "", errors.New("could not read your kubeconfig")
	}
	names := loaded.ContextNames()
	out := make([]Context, 0, len(names))
	for _, name := range names {
		out = append(out, Context{Name: name, Server: loaded.Server(name)})
	}
	return out, loaded.CurrentContext(), nil
}

func (k *Kubeconfig) Client(name string) (kube.Client, kubeconfig.Cluster, error) {
	loaded, err := k.load()
	if err != nil {
		return nil, kubeconfig.Cluster{}, errors.New("could not read your kubeconfig")
	}
	cfg, err := loaded.Config(name)
	if err != nil {
		// These messages name the unsupported setting (for example a credential plugin). They never
		// contain a credential, a path or file content.
		return nil, kubeconfig.Cluster{}, errors.New("could not use that kubeconfig context: " + err.Error())
	}
	cfg.Timeout = k.Timeout
	client, err := kube.NewClient(cfg)
	if err != nil {
		return nil, kubeconfig.Cluster{}, errors.New("could not create a cluster client: " + err.Error())
	}
	return client, kubeconfig.Cluster{Server: cfg.Server, CAData: cfg.CAData}, nil
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
