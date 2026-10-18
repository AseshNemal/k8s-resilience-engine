// Package kubeconfig builds the self-contained, read-only credential the extension backend stores.
//
// The backend runs inside the Docker Desktop VM and cannot reach the host's 127.0.0.1, so the API
// address is rewritten to the VM's host gateway. kind's API certificate lists localhost and
// 127.0.0.1 but not host.docker.internal, so TLS verification is kept ON and pinned to the name the
// certificate does list (tls-server-name). Verification is never disabled.
package kubeconfig

import (
	"errors"
	"fmt"
	"net"
	"net/url"

	"k8s.io/client-go/tools/clientcmd"
	clientcmdapi "k8s.io/client-go/tools/clientcmd/api"

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
	cfg := clientcmdapi.NewConfig()
	cfg.Clusters["cluster"] = &clientcmdapi.Cluster{
		Server:                   backendServer,
		CertificateAuthorityData: c.CAData,
		TLSServerName:            TLSServerName,
	}
	cfg.AuthInfos["user"] = &clientcmdapi.AuthInfo{Token: token.Reveal()}
	cfg.Contexts["context"] = &clientcmdapi.Context{Cluster: "cluster", AuthInfo: "user"}
	cfg.CurrentContext = "context"

	out, err := clientcmd.Write(*cfg)
	if err != nil {
		return secret.Bytes{}, "", errors.New("could not build the credential")
	}
	return secret.NewBytes(out), backendServer, nil
}
