package clusters

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/pem"
	"math/big"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"k8s.io/client-go/tools/clientcmd"
)

const secretToken = "tok-secret-from-the-users-kubeconfig"

// testCA makes a real self-signed certificate: client-go rejects a CA that is not valid PEM, just as
// it would a corrupted kind CA.
func testCA(t *testing.T) []byte {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	tpl := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "test-ca"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	der, err := x509.CreateCertificate(rand.Reader, tpl, tpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
}

// kubeconfigFile writes a kubeconfig like a developer's: a kind cluster with an embedded CA, a remote
// cluster, and a context whose credential is an exec plugin that would run a program if invoked.
func kubeconfigFile(t *testing.T, marker string) (*Kubeconfig, []byte, []byte) {
	t.Helper()
	dir := t.TempDir()
	embedded, fromFile := testCA(t), testCA(t)
	caFile := filepath.Join(dir, "ca.crt")
	if err := os.WriteFile(caFile, fromFile, 0o600); err != nil {
		t.Fatal(err)
	}
	doc := `apiVersion: v1
kind: Config
current-context: kind-resilience-research
clusters:
- name: kind-resilience-research
  cluster: {server: "https://127.0.0.1:58227", certificate-authority-data: ` + base64.StdEncoding.EncodeToString(embedded) + `}
- name: remote
  cluster: {server: "https://prod.example.com:6443", certificate-authority: ` + caFile + `}
users:
- name: kind-user
  user: {token: ` + secretToken + `}
- name: plugin-user
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1
      command: /bin/sh
      args: ["-c", "touch ` + marker + `"]
      interactiveMode: Never
contexts:
- name: kind-resilience-research
  context: {cluster: kind-resilience-research, user: kind-user}
- name: remote
  context: {cluster: remote, user: plugin-user}
`
	path := filepath.Join(dir, "config")
	if err := os.WriteFile(path, []byte(doc), 0o600); err != nil {
		t.Fatal(err)
	}
	return &Kubeconfig{Rules: &clientcmd.ClientConfigLoadingRules{ExplicitPath: path}, Timeout: time.Second}, embedded, fromFile
}

func TestContextsListNamesAndServersOnlyAndNeverACredential(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "plugin-ran")
	k, _, _ := kubeconfigFile(t, marker)
	list, current, err := k.Contexts()
	if err != nil {
		t.Fatal(err)
	}
	if current != "kind-resilience-research" || len(list) != 2 {
		t.Fatalf("current %q list %+v", current, list)
	}
	want := []Context{{Name: "kind-resilience-research", Server: "https://127.0.0.1:58227"}, {Name: "remote", Server: "https://prod.example.com:6443"}}
	if !reflect.DeepEqual(list, want) {
		t.Fatalf("list = %+v", list)
	}
	if strings.Contains(strings.ToLower(reflect.TypeOf(Context{}).String()+" "+strings.Join(fieldNames(Context{}), " ")), "token") {
		t.Fatal("Context must have no field that could carry a credential")
	}
	if _, err := os.Stat(marker); err == nil {
		t.Fatal("listing contexts must never run a credential plugin")
	}
}

func fieldNames(v any) (out []string) {
	t := reflect.TypeOf(v)
	for i := 0; i < t.NumField(); i++ {
		out = append(out, t.Field(i).Name)
	}
	return
}

func TestClientForAKindContextReturnsItsServerAndEmbeddedCA(t *testing.T) {
	k, embedded, _ := kubeconfigFile(t, filepath.Join(t.TempDir(), "x"))
	cs, cluster, err := k.Client("kind-resilience-research")
	if err != nil || cs == nil {
		t.Fatalf("client: %v", err)
	}
	if cluster.Server != "https://127.0.0.1:58227" || string(cluster.CAData) != string(embedded) {
		t.Fatalf("cluster = %+v", cluster)
	}
}

func TestACAGivenAsAFilePathIsReadForTheBackendCredential(t *testing.T) {
	k, _, fromFile := kubeconfigFile(t, filepath.Join(t.TempDir(), "x"))
	_, cluster, err := k.Client("remote")
	if err != nil || string(cluster.CAData) != string(fromFile) {
		t.Fatalf("cluster %+v err %v", cluster, err)
	}
}

func TestBuildingAClientDoesNotRunACredentialPluginUntilARequestIsMade(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "plugin-ran")
	k, _, _ := kubeconfigFile(t, marker)
	if _, _, err := k.Client("remote"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(marker); err == nil {
		t.Fatal("constructing a client ran the exec plugin; callers must only ask for supported contexts, and even then no request is made until used")
	}
}

func TestAnUnknownContextIsAnErrorWithoutEchoingTheKubeconfig(t *testing.T) {
	k, _, _ := kubeconfigFile(t, filepath.Join(t.TempDir(), "x"))
	_, _, err := k.Client("does-not-exist")
	if err == nil || strings.Contains(err.Error(), secretToken) {
		t.Fatalf("err = %v", err)
	}
}

func TestAMissingOrBrokenKubeconfigIsAGenericError(t *testing.T) {
	dir := t.TempDir()
	bad := filepath.Join(dir, "bad")
	os.WriteFile(bad, []byte("not: [valid kubeconfig"), 0o600)
	for _, path := range []string{filepath.Join(dir, "missing"), bad} {
		k := &Kubeconfig{Rules: &clientcmd.ClientConfigLoadingRules{ExplicitPath: path}}
		if _, _, err := k.Contexts(); err == nil {
			t.Errorf("%s: want an error", path)
		}
	}
}

func TestHostPortDropsPathsAndCredentials(t *testing.T) {
	cases := map[string]string{
		"https://127.0.0.1:58227":          "127.0.0.1:58227",
		"https://user:pw@127.0.0.1:1/path": "127.0.0.1:1",
		"https://[::1]:6443":               "[::1]:6443",
		"::::":                             "",
	}
	for in, want := range cases {
		if got := HostPort(in); got != want {
			t.Errorf("HostPort(%q) = %q, want %q", in, got, want)
		}
	}
}
