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
)

const secretToken = "tok-secret-from-the-users-kubeconfig"

// testCA makes a real self-signed certificate: the helper rejects a CA that is not valid PEM, just as
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

// kubeconfigFile writes a kubeconfig like a developer's: a kind cluster with an embedded CA, a cluster
// whose CA is a file, a remote cluster, and a context whose credential is an exec plugin that would run
// a program if invoked.
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
- name: file-ca
  cluster: {server: "https://127.0.0.1:58228", certificate-authority: ` + caFile + `}
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
- name: file-ca
  context: {cluster: file-ca, user: kind-user}
`
	path := filepath.Join(dir, "config")
	if err := os.WriteFile(path, []byte(doc), 0o600); err != nil {
		t.Fatal(err)
	}
	return &Kubeconfig{ExplicitPath: path, Timeout: time.Second}, embedded, fromFile
}

func TestContextsListNamesAndServersOnlyAndNeverACredential(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "plugin-ran")
	k, _, _ := kubeconfigFile(t, marker)
	list, current, err := k.Contexts()
	if err != nil {
		t.Fatal(err)
	}
	if current != "kind-resilience-research" || len(list) != 3 {
		t.Fatalf("current %q list %+v", current, list)
	}
	want := []Context{
		{Name: "file-ca", Server: "https://127.0.0.1:58228"},
		{Name: "kind-resilience-research", Server: "https://127.0.0.1:58227"},
		{Name: "remote", Server: "https://prod.example.com:6443"},
	}
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
	_, cluster, err := k.Client("file-ca")
	if err != nil || string(cluster.CAData) != string(fromFile) {
		t.Fatalf("cluster %+v err %v", cluster, err)
	}
}

func TestAContextWithACredentialPluginIsRefusedAndThePluginIsNeverRun(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "plugin-ran")
	k, _, _ := kubeconfigFile(t, marker)
	cs, _, err := k.Client("remote")
	if err == nil || cs != nil {
		t.Fatalf("a context that authenticates through a plugin must be refused, got client %v err %v", cs, err)
	}
	if !strings.Contains(err.Error(), "credential plugin") {
		t.Fatalf("the reason should name the plugin: %v", err)
	}
	if _, statErr := os.Stat(marker); statErr == nil {
		t.Fatal("the exec plugin ran; the helper must never run a program named in a kubeconfig")
	}
}

func TestMergingSeveralKubeconfigFilesIsHowTheEnvironmentVariableWorks(t *testing.T) {
	dir := t.TempDir()
	one := filepath.Join(dir, "one")
	two := filepath.Join(dir, "two")
	os.WriteFile(one, []byte("current-context: a\nclusters: [{name: ca, cluster: {server: \"https://127.0.0.1:1\"}}]\ncontexts: [{name: a, context: {cluster: ca, user: u}}]\n"), 0o600)
	os.WriteFile(two, []byte("clusters: [{name: cb, cluster: {server: \"https://127.0.0.1:2\"}}]\ncontexts: [{name: b, context: {cluster: cb, user: u}}]\n"), 0o600)
	list, current, err := (&Kubeconfig{Precedence: []string{filepath.Join(dir, "absent"), one, two}}).Contexts()
	if err != nil || current != "a" || len(list) != 2 || list[1].Server != "https://127.0.0.1:2" {
		t.Fatalf("list %+v current %q err %v", list, current, err)
	}
}

func TestTheDefaultLocationsFollowTheEnvironmentVariableThenTheHomeDirectory(t *testing.T) {
	first, second := filepath.Join("x", "one"), filepath.Join("y", "two")
	t.Setenv("KUBECONFIG", first+string(os.PathListSeparator)+second)
	if got := defaultPaths(); len(got) != 2 || got[0] != first || got[1] != second {
		t.Fatalf("paths = %v", got)
	}
	t.Setenv("KUBECONFIG", "")
	t.Setenv("HOME", "/home/someone")
	t.Setenv("USERPROFILE", "/home/someone")
	if got := defaultPaths(); len(got) != 1 || got[0] != filepath.Join("/home/someone", ".kube", "config") {
		t.Fatalf("paths = %v", got)
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
		k := &Kubeconfig{ExplicitPath: path}
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
