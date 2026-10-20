// Package kubeconfig builds the self-contained, read-only credential the extension backend stores.
//
// The backend runs inside the Docker Desktop VM and cannot reach the host's 127.0.0.1, so the API
// address is rewritten to the VM's host gateway. kind's API certificate lists localhost and
// 127.0.0.1 but not host.docker.internal, so TLS verification is kept ON and pinned to the name the
// certificate does list (tls-server-name). Verification is never disabled.
package kubeconfig

import (
	"encoding/base64"
	"errors"
	"fmt"
	"net"
	"net/url"

	"go.yaml.in/yaml/v3"

	"resilience-engine/helper/internal/secret"
)

const (
	// HostGateway is how the Docker Desktop VM reaches the host. Confirmed on macOS; it is an
	// assumption on Windows and Linux until tested there (docs/EXTENSION.md).
	HostGateway = "host.docker.internal"
	// TLSServerName is a name kind's API server certificate is valid for.
	TLSServerName = "localhost"
)

// Cluster is what the helper reads from the user's own kubeconfig.
type Cluster struct {
	Server string // the user's API server URL, for example https://127.0.0.1:58227
	CAData []byte // PEM
}

// IsLoopback reports whether a host is a loopback address or name. Only local clusters are in scope.
func IsLoopback(host string) bool {
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// Build returns the backend's credential document and the address the backend will use.
func Build(c Cluster, token secret.String) (doc secret.Bytes, backendServer string, err error) {
	u, err := url.Parse(c.Server)
	if err != nil || u.Hostname() == "" {
		return secret.Bytes{}, "", errors.New("the cluster's API server address is not valid")
	}
	if u.Scheme != "https" {
		return secret.Bytes{}, "", errors.New("the cluster's API server is not https")
	}
	if !IsLoopback(u.Hostname()) {
		return secret.Bytes{}, "", errors.New("only a local (loopback) cluster is supported")
	}
	if u.Port() == "" {
		return secret.Bytes{}, "", errors.New("the cluster's API server address has no port")
	}
	if len(c.CAData) == 0 {
		return secret.Bytes{}, "", errors.New("the cluster's certificate authority is missing")
	}
	if token.IsZero() {
		return secret.Bytes{}, "", errors.New("no token to store")
	}

	backendServer = fmt.Sprintf("https://%s:%s", HostGateway, u.Port())
	rendered, err := document{
		APIVersion: "v1",
		Kind:       "Config",
		Clusters: []namedCluster{{Name: "cluster", Cluster: clusterEntry{
			CertificateAuthorityData: base64.StdEncoding.EncodeToString(c.CAData),
			Server:                   backendServer,
			TLSServerName:            TLSServerName,
		}}},
		Contexts:       []namedContext{{Name: "context", Context: contextEntry{Cluster: "cluster", User: "user"}}},
		CurrentContext: "context",
		Preferences:    map[string]any{},
		Users:          []namedUser{{Name: "user", User: userEntry{Token: token.Reveal()}}},
	}.render()
	if err != nil {
		return secret.Bytes{}, "", errors.New("could not build the credential")
	}
	return rendered, backendServer, nil
}

// The document is exactly the shape kubectl writes for a one-cluster, one-user, token-only kubeconfig.
// There is deliberately no field for insecure-skip-tls-verify, a proxy, or a credential plugin.
type document struct {
	APIVersion     string         `yaml:"apiVersion"`
	Clusters       []namedCluster `yaml:"clusters"`
	Contexts       []namedContext `yaml:"contexts"`
	CurrentContext string         `yaml:"current-context"`
	Kind           string         `yaml:"kind"`
	Preferences    map[string]any `yaml:"preferences"`
	Users          []namedUser    `yaml:"users"`
}

type namedCluster struct {
	Cluster clusterEntry `yaml:"cluster"`
	Name    string       `yaml:"name"`
}

type clusterEntry struct {
	CertificateAuthorityData string `yaml:"certificate-authority-data"`
	Server                   string `yaml:"server"`
	TLSServerName            string `yaml:"tls-server-name"`
}

type namedContext struct {
	Context contextEntry `yaml:"context"`
	Name    string       `yaml:"name"`
}

type contextEntry struct {
	Cluster string `yaml:"cluster"`
	User    string `yaml:"user"`
}

type namedUser struct {
	Name string    `yaml:"name"`
	User userEntry `yaml:"user"`
}

type userEntry struct {
	Token string `yaml:"token"`
}

// render serialises the document. The error never carries the token.
func (d document) render() (secret.Bytes, error) {
	out, err := yaml.Marshal(d)
	if err != nil {
		return secret.Bytes{}, errors.New("could not serialise the credential")
	}
	return secret.NewBytes(out), nil
}
