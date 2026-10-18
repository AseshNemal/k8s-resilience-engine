package kubeconfig

import (
	"fmt"
	"strings"
	"testing"

	"k8s.io/client-go/tools/clientcmd"

	"resilience-engine/helper/internal/secret"
)

var (
	ca    = []byte("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
	tok   = secret.NewString("tok-secret-123")
	local = Cluster{Server: "https://127.0.0.1:58227", CAData: ca}
)

func TestBuildsAKubeconfigThatPointsAtTheHostGatewayWithTLSVerificationOn(t *testing.T) {
	doc, server, err := Build(local, tok)
	if err != nil {
		t.Fatal(err)
	}
	if server != "https://host.docker.internal:58227" {
		t.Fatalf("server = %s", server)
	}
	cfg, err := clientcmd.Load(doc.Reveal())
	if err != nil {
		t.Fatalf("not a valid kubeconfig: %v", err)
	}
	cl := cfg.Clusters[cfg.Contexts[cfg.CurrentContext].Cluster]
	au := cfg.AuthInfos[cfg.Contexts[cfg.CurrentContext].AuthInfo]
	switch {
	case cl.Server != server:
		t.Errorf("server %s", cl.Server)
	case cl.InsecureSkipTLSVerify:
		t.Error("TLS verification must never be disabled")
	case cl.TLSServerName != "localhost":
		t.Errorf("tls-server-name = %q", cl.TLSServerName)
	case string(cl.CertificateAuthorityData) != string(ca):
		t.Error("the CA must be embedded unchanged")
	case au.Token != "tok-secret-123":
		t.Error("the token must be embedded")
	case au.Exec != nil || au.AuthProvider != nil || au.TokenFile != "" || au.Username != "":
		t.Error("only an embedded bearer token is allowed")
	case cl.ProxyURL != "":
		t.Error("no proxy is allowed")
	}
}

func TestOnlyLocalHttpsClustersWithAPortAndACAAreAccepted(t *testing.T) {
	for name, c := range map[string]Cluster{
		"remote host":      {Server: "https://prod.example.com:6443", CAData: ca},
		"remote ip":        {Server: "https://10.0.0.5:6443", CAData: ca},
		"plain http":       {Server: "http://127.0.0.1:8080", CAData: ca},
		"no port":          {Server: "https://127.0.0.1", CAData: ca},
		"no CA":            {Server: "https://127.0.0.1:6443"},
		"garbage":          {Server: "::::", CAData: ca},
		"empty":            {},
		"localhost prefix": {Server: "https://localhost.evil.com:6443", CAData: ca},
		"userinfo trick":   {Server: "https://127.0.0.1@evil.com:6443", CAData: ca},
	} {
		t.Run(name, func(t *testing.T) {
			if _, _, err := Build(c, tok); err == nil {
				t.Fatalf("%q must be refused", c.Server)
			}
		})
	}
}

func TestLoopbackNamesAreAccepted(t *testing.T) {
	for _, server := range []string{"https://127.0.0.1:1", "https://localhost:1", "https://[::1]:1", "https://127.0.0.5:1"} {
		if _, _, err := Build(Cluster{Server: server, CAData: ca}, tok); err != nil {
			t.Errorf("%s: %v", server, err)
		}
	}
}

func TestAnEmptyTokenIsRefused(t *testing.T) {
	if _, _, err := Build(local, secret.String{}); err == nil {
		t.Fatal("want an error")
	}
}

func TestTheResultAndTheErrorsNeverPrintTheToken(t *testing.T) {
	doc, _, _ := Build(local, tok)
	for _, verb := range []string{"%v", "%s", "%q", "%+v", "%#v"} {
		if strings.Contains(fmt.Sprintf(verb, doc), "tok-secret-123") {
			t.Fatalf("%s leaked the token", verb)
		}
	}
	_, _, err := Build(Cluster{Server: "https://evil.example:1", CAData: ca}, tok)
	if err == nil || strings.Contains(err.Error(), "tok-secret-123") {
		t.Fatalf("error must exist and not carry the token: %v", err)
	}
}
