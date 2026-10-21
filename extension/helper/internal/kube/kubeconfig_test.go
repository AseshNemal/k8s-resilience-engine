package kube

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func write(t *testing.T, dir, name, content string) string {
	t.Helper()
	path := filepath.Join(dir, name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func b64(s string) string { return base64.StdEncoding.EncodeToString([]byte(s)) }

func load(t *testing.T, explicit string, precedence ...string) *Kubeconfig {
	t.Helper()
	k, err := LoadKubeconfig(explicit, precedence)
	if err != nil {
		t.Fatal(err)
	}
	return k
}

func TestAKindStyleContextYieldsItsServerCAAndClientCertificate(t *testing.T) {
	path := write(t, t.TempDir(), "config", `apiVersion: v1
kind: Config
current-context: kind-research
clusters:
- name: kind-research
  cluster: {server: "https://127.0.0.1:58227", certificate-authority-data: `+b64("CA-PEM")+`}
users:
- name: kind-user
  user: {client-certificate-data: `+b64("CERT-PEM")+`, client-key-data: `+b64("KEY-PEM")+`}
contexts:
- name: kind-research
  context: {cluster: kind-research, user: kind-user}
`)
	k := load(t, path)
	if k.CurrentContext() != "kind-research" || !reflect.DeepEqual(k.ContextNames(), []string{"kind-research"}) {
		t.Fatalf("current %q names %v", k.CurrentContext(), k.ContextNames())
	}
	if k.Server("kind-research") != "https://127.0.0.1:58227" || k.Server("nope") != "" {
		t.Fatalf("server %q", k.Server("kind-research"))
	}
	cfg, err := k.Config("kind-research")
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Server != "https://127.0.0.1:58227" || string(cfg.CAData) != "CA-PEM" ||
		string(cfg.CertData) != "CERT-PEM" || string(cfg.KeyData) != "KEY-PEM" || cfg.BearerToken != "" {
		t.Fatalf("config = %+v", cfg)
	}
}

func TestFilesAreReadFromDiskRelativeToTheKubeconfigThatNamesThem(t *testing.T) {
	dir := t.TempDir()
	write(t, dir, "certs/ca.crt", "CA-FROM-FILE")
	write(t, dir, "certs/client.crt", "CERT-FROM-FILE")
	write(t, dir, "certs/client.key", "KEY-FROM-FILE")
	write(t, dir, "certs/token", "file-token\n")
	path := write(t, dir, "config", `
clusters: [{name: c, cluster: {server: "https://127.0.0.1:1", certificate-authority: certs/ca.crt}}]
users:
- {name: cert-user, user: {client-certificate: certs/client.crt, client-key: certs/client.key}}
- {name: token-user, user: {tokenFile: certs/token}}
contexts:
- {name: with-cert, context: {cluster: c, user: cert-user}}
- {name: with-token, context: {cluster: c, user: token-user}}
`)
	k := load(t, path)
	cert, err := k.Config("with-cert")
	if err != nil || string(cert.CAData) != "CA-FROM-FILE" || string(cert.CertData) != "CERT-FROM-FILE" || string(cert.KeyData) != "KEY-FROM-FILE" {
		t.Fatalf("config %+v err %v", cert, err)
	}
	tok, err := k.Config("with-token")
	if err != nil || tok.BearerToken != "file-token" {
		t.Fatalf("config %+v err %v", tok, err)
	}
}

func TestEmbeddedDataWinsOverAFileOfTheSameKind(t *testing.T) {
	dir := t.TempDir()
	write(t, dir, "ca.crt", "FROM-FILE")
	path := write(t, dir, "config", "clusters: [{name: c, cluster: {server: \"https://127.0.0.1:1\", certificate-authority: ca.crt, certificate-authority-data: "+b64("EMBEDDED")+"}}]\n"+
		"users: [{name: u, user: {token: t}}]\ncontexts: [{name: x, context: {cluster: c, user: u}}]\n")
	cfg, err := load(t, path).Config("x")
	if err != nil || string(cfg.CAData) != "EMBEDDED" || cfg.BearerToken != "t" {
		t.Fatalf("config %+v err %v", cfg, err)
	}
}

func TestTLSServerNameIsCarriedThrough(t *testing.T) {
	path := write(t, t.TempDir(), "config", "clusters: [{name: c, cluster: {server: \"https://host.docker.internal:1\", tls-server-name: localhost}}]\n"+
		"users: [{name: u, user: {token: t}}]\ncontexts: [{name: x, context: {cluster: c, user: u}}]\n")
	cfg, err := load(t, path).Config("x")
	if err != nil || cfg.TLSServerName != "localhost" {
		t.Fatalf("config %+v err %v", cfg, err)
	}
}

// ---------------------------------------------------------------- merging several files

func TestTheFirstFileThatDefinesANameWinsAndTheFirstCurrentContextIsUsed(t *testing.T) {
	dir := t.TempDir()
	first := write(t, dir, "first", `
current-context: from-first
clusters: [{name: shared, cluster: {server: "https://127.0.0.1:1"}}]
users: [{name: u, user: {token: first-token}}]
contexts: [{name: from-first, context: {cluster: shared, user: u}}]
`)
	second := write(t, dir, "second", `
current-context: from-second
clusters: [{name: shared, cluster: {server: "https://127.0.0.1:2"}}, {name: other, cluster: {server: "https://127.0.0.1:3"}}]
users: [{name: u, user: {token: second-token}}]
contexts:
- {name: from-first, context: {cluster: other, user: u}}
- {name: from-second, context: {cluster: other, user: u}}
`)
	k := load(t, "", first, second)

	if k.CurrentContext() != "from-first" {
		t.Fatalf("current = %q", k.CurrentContext())
	}
	if !reflect.DeepEqual(k.ContextNames(), []string{"from-first", "from-second"}) {
		t.Fatalf("names = %v", k.ContextNames())
	}
	if k.Server("from-first") != "https://127.0.0.1:1" {
		t.Fatalf("a later file must not override the first: %s", k.Server("from-first"))
	}
	if k.Server("from-second") != "https://127.0.0.1:3" {
		t.Fatalf("a name only the second file defines is still available: %s", k.Server("from-second"))
	}
	if cfg, _ := k.Config("from-first"); cfg.BearerToken != "first-token" {
		t.Fatalf("token = %q", cfg.BearerToken)
	}
}

func TestCurrentContextFallsThroughToTheFirstFileThatSetsOne(t *testing.T) {
	dir := t.TempDir()
	first := write(t, dir, "first", "clusters: []\n")
	second := write(t, dir, "second", "current-context: later\n")
	if got := load(t, "", first, second).CurrentContext(); got != "later" {
		t.Fatalf("current = %q", got)
	}
}

func TestEachRelativePathIsResolvedAgainstItsOwnFile(t *testing.T) {
	dir := t.TempDir()
	write(t, dir, "a/ca.crt", "CA-A")
	write(t, dir, "b/ca.crt", "CA-B")
	a := write(t, dir, "a/config", "clusters: [{name: ca, cluster: {server: \"https://127.0.0.1:1\", certificate-authority: ca.crt}}]\nusers: [{name: u, user: {token: t}}]\ncontexts: [{name: xa, context: {cluster: ca, user: u}}]\n")
	b := write(t, dir, "b/config", "clusters: [{name: cb, cluster: {server: \"https://127.0.0.1:2\", certificate-authority: ca.crt}}]\ncontexts: [{name: xb, context: {cluster: cb, user: u}}]\n")
	k := load(t, "", a, b)
	if cfg, err := k.Config("xa"); err != nil || string(cfg.CAData) != "CA-A" {
		t.Fatalf("a: %+v %v", cfg, err)
	}
	if cfg, err := k.Config("xb"); err != nil || string(cfg.CAData) != "CA-B" {
		t.Fatalf("b: %+v %v", cfg, err)
	}
}

func TestMissingFilesInThePrecedenceListAreSkippedLikeKubectlDoes(t *testing.T) {
	dir := t.TempDir()
	real := write(t, dir, "config", "current-context: x\ncontexts: [{name: x, context: {cluster: c, user: u}}]\n")
	k := load(t, "", filepath.Join(dir, "missing"), real)
	if !reflect.DeepEqual(k.ContextNames(), []string{"x"}) {
		t.Fatalf("names = %v", k.ContextNames())
	}
	if empty := load(t, "", filepath.Join(dir, "none")); len(empty.ContextNames()) != 0 || empty.CurrentContext() != "" {
		t.Fatal("no kubeconfig at all means no contexts, not an error")
	}
}

func TestAnExplicitFileMustExistAndAnyExistingFileMustBeValid(t *testing.T) {
	dir := t.TempDir()
	broken := write(t, dir, "broken", "this: [is not closed")
	for name, call := range map[string]func() error{
		"explicit but missing": func() error { _, err := LoadKubeconfig(filepath.Join(dir, "missing"), nil); return err },
		"explicit but broken":  func() error { _, err := LoadKubeconfig(broken, nil); return err },
		"listed but broken":    func() error { _, err := LoadKubeconfig("", []string{broken}); return err },
	} {
		err := call()
		if err == nil {
			t.Errorf("%s: want an error", name)
			continue
		}
		if strings.Contains(err.Error(), "is not closed") || strings.Contains(err.Error(), dir) {
			t.Errorf("%s: the error must not echo file content or paths: %v", name, err)
		}
	}
}

// ---------------------------------------------------------------- what is refused

func TestUnsupportedOrUnsafeSettingsAreRefusedNeverIgnored(t *testing.T) {
	dir := t.TempDir()
	marker := filepath.Join(dir, "plugin-ran")
	path := write(t, dir, "config", `
clusters:
- {name: ok, cluster: {server: "https://127.0.0.1:1"}}
- {name: insecure, cluster: {server: "https://127.0.0.1:1", insecure-skip-tls-verify: true}}
- {name: proxied, cluster: {server: "https://127.0.0.1:1", proxy-url: "http://proxy.example:3128"}}
users:
- {name: token, user: {token: t}}
- name: exec
  user: {exec: {apiVersion: client.authentication.k8s.io/v1, command: /bin/sh, args: ["-c", "touch `+filepath.ToSlash(marker)+`"]}}
- {name: provider, user: {auth-provider: {name: gcp}}}
- {name: basic, user: {username: admin, password: pw}}
contexts:
- {name: ok, context: {cluster: ok, user: token}}
- {name: insecure, context: {cluster: insecure, user: token}}
- {name: proxied, context: {cluster: proxied, user: token}}
- {name: exec, context: {cluster: ok, user: exec}}
- {name: provider, context: {cluster: ok, user: provider}}
- {name: basic, context: {cluster: ok, user: basic}}
- {name: no-cluster, context: {cluster: missing, user: token}}
`)
	k := load(t, path)
	if _, err := k.Config("ok"); err != nil {
		t.Fatalf("the control context must work: %v", err)
	}
	for _, name := range []string{"insecure", "proxied", "exec", "provider", "basic", "no-cluster", "not-a-context"} {
		if _, err := k.Config(name); err == nil {
			t.Errorf("%s: want an error", name)
		}
	}
	if _, err := os.Stat(marker); err == nil {
		t.Fatal("a credential plugin must never be run")
	}
}

func TestBadBase64AndUnreadableFilesGiveGenericErrors(t *testing.T) {
	path := write(t, t.TempDir(), "config", `
clusters:
- {name: badb64, cluster: {server: "https://127.0.0.1:1", certificate-authority-data: "!!! not base64 !!!"}}
- {name: nofile, cluster: {server: "https://127.0.0.1:1", certificate-authority: does-not-exist.crt}}
users: [{name: u, user: {token: t}}]
contexts:
- {name: badb64, context: {cluster: badb64, user: u}}
- {name: nofile, context: {cluster: nofile, user: u}}
`)
	k := load(t, path)
	for _, name := range []string{"badb64", "nofile"} {
		_, err := k.Config(name)
		if err == nil || strings.Contains(err.Error(), "does-not-exist") {
			t.Errorf("%s: err = %v", name, err)
		}
	}
}
