package kubeconfig

import (
	"encoding/base64"
	"fmt"
	"os"
	"strings"
	"testing"

	"go.yaml.in/yaml/v3"

	"resilience-engine/helper/internal/secret"
)

var (
	ca    = []byte("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
	tok   = secret.NewString("tok-secret-123")
	local = Cluster{Server: "https://127.0.0.1:58227", CAData: ca}
)

// parsed is a kubeconfig document read back the way any consumer (kubectl, the Python backend) reads it.
type parsed struct {
	APIVersion     string         `yaml:"apiVersion"`
	Kind           string         `yaml:"kind"`
	CurrentContext string         `yaml:"current-context"`
	Preferences    map[string]any `yaml:"preferences"`
	Clusters       []struct {
		Name    string         `yaml:"name"`
		Cluster map[string]any `yaml:"cluster"`
	} `yaml:"clusters"`
	Users []struct {
		Name string         `yaml:"name"`
		User map[string]any `yaml:"user"`
	} `yaml:"users"`
	Contexts []struct {
		Name    string            `yaml:"name"`
		Context map[string]string `yaml:"context"`
	} `yaml:"contexts"`
}

func TestBuildsAKubeconfigThatPointsAtTheHostGatewayWithTLSVerificationOn(t *testing.T) {
	doc, server, err := Build(local, tok)
	if err != nil {
		t.Fatal(err)
	}
	if server != "https://host.docker.internal:58227" {
		t.Fatalf("server = %s", server)
	}
	var cfg parsed
	if err := yaml.Unmarshal(doc.Reveal(), &cfg); err != nil {
		t.Fatalf("not a valid kubeconfig: %v", err)
	}
	if cfg.APIVersion != "v1" || cfg.Kind != "Config" || len(cfg.Clusters) != 1 || len(cfg.Users) != 1 || len(cfg.Contexts) != 1 {
		t.Fatalf("shape = %+v", cfg)
	}
	if cfg.CurrentContext != cfg.Contexts[0].Name || cfg.Contexts[0].Context["cluster"] != cfg.Clusters[0].Name || cfg.Contexts[0].Context["user"] != cfg.Users[0].Name {
		t.Fatalf("the context must tie the cluster to the user: %+v", cfg)
	}
	cl, au := cfg.Clusters[0].Cluster, cfg.Users[0].User
	caBytes, _ := base64.StdEncoding.DecodeString(fmt.Sprint(cl["certificate-authority-data"]))
	switch {
	case cl["server"] != server:
		t.Errorf("server %v", cl["server"])
	case cl["insecure-skip-tls-verify"] != nil:
		t.Error("TLS verification must never be disabled")
	case cl["tls-server-name"] != "localhost":
		t.Errorf("tls-server-name = %v", cl["tls-server-name"])
	case string(caBytes) != string(ca):
		t.Error("the CA must be embedded unchanged")
	case au["token"] != "tok-secret-123":
		t.Error("the token must be embedded")
	case len(au) != 1:
		t.Errorf("only an embedded bearer token is allowed, user = %v", au)
	case cl["proxy-url"] != nil:
		t.Error("no proxy is allowed")
	case len(cl) != 3:
		t.Errorf("cluster carries unexpected settings: %v", cl)
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

// The Python backend reads what Build writes. This golden file is the contract between the two: the
// Python tests load the very same file, so a format change that breaks the backend fails a test on
// both sides instead of only after install.
func TestOutputMatchesTheGoldenFileThePythonBackendIsTestedAgainst(t *testing.T) {
	dir := "../../../../tests/fixtures/tls/"
	caPEM, err := os.ReadFile(dir + "ca.pem")
	if err != nil {
		t.Skipf("fixtures not available from here: %v", err)
	}
	doc, _, err := Build(Cluster{Server: "https://127.0.0.1:58227", CAData: caPEM}, secret.NewString("golden-token-not-a-secret"))
	if err != nil {
		t.Fatal(err)
	}
	if os.Getenv("UPDATE_GOLDEN") != "" {
		if err := os.WriteFile(dir+"backend-kubeconfig.yaml", doc.Reveal(), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	want, err := os.ReadFile(dir + "backend-kubeconfig.yaml")
	if err != nil {
		t.Fatalf("golden file missing (run with UPDATE_GOLDEN=1): %v", err)
	}
	if string(doc.Reveal()) != string(want) {
		t.Fatalf("the credential format changed. If intended, regenerate with UPDATE_GOLDEN=1.\n got:\n%s\nwant:\n%s", doc.Reveal(), want)
	}
}
