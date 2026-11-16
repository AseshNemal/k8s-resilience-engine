package traceinstall

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

var bg = context.Background()

func tarGz(t *testing.T, entries ...tar.Header) []byte {
	t.Helper()
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	for _, h := range entries {
		body := []byte("")
		if h.Typeflag == tar.TypeReg {
			body = []byte("#!fake-istioctl\n")
			h.Size = int64(len(body))
		}
		if err := tw.WriteHeader(&h); err != nil {
			t.Fatal(err)
		}
		_, _ = tw.Write(body)
	}
	tw.Close()
	gz.Close()
	return buf.Bytes()
}

func zipOf(t *testing.T, names ...string) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for _, n := range names {
		w, err := zw.Create(n)
		if err != nil {
			t.Fatal(err)
		}
		_, _ = w.Write([]byte("MZ-fake-istioctl"))
	}
	zw.Close()
	return buf.Bytes()
}

type fetcher struct {
	body  []byte
	err   error
	calls []string
}

func (f *fetcher) Get(_ context.Context, url string, _ int64) ([]byte, error) {
	f.calls = append(f.calls, url)
	return f.body, f.err
}

// toolEnv pins an artifact whose checksum is that of the archive under test, so the real checksum logic runs.
func toolEnv(t *testing.T, kind string, archive []byte, f *fetcher) ToolEnv {
	t.Helper()
	asset := "istioctl-1.31.2-test.tar.gz"
	binary := "istioctl"
	if kind == "zip" {
		asset, binary = "istioctl-1.31.2-test.zip", "istioctl.exe"
	}
	pinned := Artifact{Asset: asset, SHA256: sha(archive), Kind: kind, Binary: binary}
	return ToolEnv{GOOS: runtime.GOOS, GOARCH: runtime.GOARCH, Dir: filepath.Join(t.TempDir(), "tools"), Fetcher: f,
		Lookup: func(string, string) (Artifact, error) { return pinned, nil }}
}

func good(t *testing.T) []byte {
	return tarGz(t, tar.Header{Name: "istioctl", Typeflag: tar.TypeReg, Mode: 0o755})
}

func TestADownloadWithThePinnedChecksumIsStoredPrivatelyAndReused(t *testing.T) {
	archive := good(t)
	f := &fetcher{body: archive}
	env := toolEnv(t, "tar.gz", archive, f)
	need, _, _ := env.NeedsDownload()
	if !need {
		t.Fatal("nothing is stored yet, so a download is needed")
	}
	bin, err := env.Ensure(bg)
	if err != nil {
		t.Fatal(err)
	}
	if body, _ := os.ReadFile(bin); string(body) != "#!fake-istioctl\n" {
		t.Fatalf("%q", body)
	}
	if runtime.GOOS != "windows" {
		if info, _ := os.Stat(bin); info.Mode().Perm() != 0o700 {
			t.Errorf("tool mode %v", info.Mode().Perm())
		}
		if info, _ := os.Stat(filepath.Dir(bin)); info.Mode().Perm()&0o077 != 0 {
			t.Errorf("tool directory mode %v", info.Mode().Perm())
		}
	}
	if need, _, _ := env.NeedsDownload(); need {
		t.Fatal("a verified tool is stored, so no download is needed")
	}
	if _, err := env.Ensure(bg); err != nil || len(f.calls) != 1 {
		t.Fatalf("second call downloaded again: %v %v", err, f.calls)
	}
	if !strings.HasPrefix(f.calls[0], "https://github.com/istio/istio/releases/download/1.31.2/") {
		t.Fatalf("%s", f.calls[0])
	}
}

func TestAnArchiveThatDoesNotMatchTheChecksumIsDiscardedAndNothingIsStored(t *testing.T) {
	archive := good(t)
	other := tarGz(t, tar.Header{Name: "istioctl", Typeflag: tar.TypeReg, Mode: 0o755}, tar.Header{Name: "extra", Typeflag: tar.TypeReg})
	env := toolEnv(t, "tar.gz", archive, &fetcher{body: other})
	if _, err := env.Ensure(bg); !errors.Is(err, ErrChecksumMismatch) {
		t.Fatalf("%v", err)
	}
	if entries, _ := os.ReadDir(env.Dir); len(entries) != 0 {
		t.Fatalf("something was stored: %v", entries)
	}
}

func TestADownloadFailureIsReportedWithoutDetail(t *testing.T) {
	env := toolEnv(t, "tar.gz", good(t), &fetcher{err: errors.New("dial tcp 10.0.0.1: secret detail")})
	_, err := env.Ensure(bg)
	if !errors.Is(err, ErrDownloadUnavailable) || strings.Contains(err.Error(), "secret") {
		t.Fatalf("%v", err)
	}
}

func TestUnsafeOrUnexpectedArchiveContentsAreRefused(t *testing.T) {
	cases := map[string][]byte{
		"traversal":      tarGz(t, tar.Header{Name: "../istioctl", Typeflag: tar.TypeReg}),
		"absolute":       tarGz(t, tar.Header{Name: "/istioctl", Typeflag: tar.TypeReg}),
		"symlink":        tarGz(t, tar.Header{Name: "istioctl", Typeflag: tar.TypeSymlink, Linkname: "/bin/sh"}),
		"hardlink":       tarGz(t, tar.Header{Name: "istioctl", Typeflag: tar.TypeLink, Linkname: "other"}),
		"wrong name":     tarGz(t, tar.Header{Name: "istioctl2", Typeflag: tar.TypeReg}),
		"extra file":     tarGz(t, tar.Header{Name: "istioctl", Typeflag: tar.TypeReg}, tar.Header{Name: "README", Typeflag: tar.TypeReg}),
		"duplicate":      tarGz(t, tar.Header{Name: "istioctl", Typeflag: tar.TypeReg}, tar.Header{Name: "istioctl", Typeflag: tar.TypeReg}),
		"empty":          tarGz(t),
		"not an archive": []byte("plain text"),
	}
	for name, archive := range cases {
		env := toolEnv(t, "tar.gz", archive, &fetcher{body: archive})
		if _, err := env.Ensure(bg); !errors.Is(err, ErrBadArchive) {
			t.Errorf("%s: %v", name, err)
		}
	}
}

func TestAWindowsZipIsHandledTheSameWay(t *testing.T) {
	archive := zipOf(t, "istioctl.exe")
	env := toolEnv(t, "zip", archive, &fetcher{body: archive})
	bin, err := env.Ensure(bg)
	if err != nil || filepath.Base(bin) != "istioctl.exe" {
		t.Fatalf("%q %v", bin, err)
	}
	for name, names := range map[string][]string{
		"traversal": {`..\istioctl.exe`}, "absolute": {"/istioctl.exe"}, "drive": {`C:\istioctl.exe`},
		"extra": {"istioctl.exe", "notes.txt"}, "wrong": {"istioctl"},
	} {
		bad := zipOf(t, names...)
		env := toolEnv(t, "zip", bad, &fetcher{body: bad})
		if _, err := env.Ensure(bg); !errors.Is(err, ErrBadArchive) {
			t.Errorf("%s: %v", name, err)
		}
	}
}

func TestAStoredToolThatWasChangedAfterVerificationIsReplaced(t *testing.T) {
	archive := good(t)
	f := &fetcher{body: archive}
	env := toolEnv(t, "tar.gz", archive, f)
	bin, err := env.Ensure(bg)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(bin, []byte("malicious"), 0o700); err != nil {
		t.Fatal(err)
	}
	again, err := env.Ensure(bg)
	if err != nil || len(f.calls) != 2 {
		t.Fatalf("expected a fresh verified download: %v %v", err, f.calls)
	}
	if body, _ := os.ReadFile(again); string(body) != "#!fake-istioctl\n" {
		t.Fatalf("%q", body)
	}
}

func TestOnlyThePinnedGitHubAssetCanBeDownloaded(t *testing.T) {
	for _, a := range []Artifact{
		{Asset: "../evil.tar.gz"}, {Asset: "istioctl-1.31.2-x.tar.gz/../../other"},
	} {
		if checkURL(a) == nil {
			t.Errorf("%s was accepted", a.URL())
		}
	}
	real, _ := ArtifactFor("darwin", "arm64")
	if err := checkURL(real); err != nil {
		t.Fatal(err)
	}
}

func TestAnUnsupportedPlatformNeverReachesTheNetwork(t *testing.T) {
	f := &fetcher{}
	env := ToolEnv{GOOS: "windows", GOARCH: "arm64", Dir: t.TempDir(), Fetcher: f}
	var unsupported ErrUnsupportedPlatform
	if _, err := env.Ensure(bg); !errors.As(err, &unsupported) || len(f.calls) != 0 {
		t.Fatalf("%v %v", err, f.calls)
	}
}

// ---- rendering ----

type recordingExec struct {
	out  []byte
	code int
	err  error
	name string
	args []string
	env  []string
	conf []byte
}

func (r *recordingExec) Run(_ context.Context, name string, args, env []string) ([]byte, int, error) {
	r.name, r.args, r.env = name, args, env
	if len(args) == 4 {
		r.conf, _ = os.ReadFile(args[3])
	}
	return r.out, r.code, r.err
}

func renderOf(refs []ObjectRef) []byte {
	var b strings.Builder
	for _, o := range refs {
		ns := ""
		if o.Namespace != "" {
			ns = "\n  namespace: " + o.Namespace
		}
		fmt.Fprintf(&b, "---\napiVersion: %s\nkind: %s\nmetadata:\n  name: %s%s\n", o.APIVersion, o.Kind, o.Name, ns)
	}
	return []byte(b.String())
}

func TestTheRenderRunsOfflineWithACleanEnvironmentAndMatchesTheReviewedList(t *testing.T) {
	rec := &recordingExec{out: renderOf(ExpectedIstioObjects())}
	env := ToolEnv{GOOS: "linux", GOARCH: "amd64", Dir: t.TempDir(), Exec: rec, ReviewedDigest: digestOfRender(t, rec.out)}
	manifests, err := env.Render(bg, "/tools/istioctl")
	if err != nil || len(manifests) != 32 {
		t.Fatalf("%d %v", len(manifests), err)
	}
	if rec.name != "/tools/istioctl" || strings.Join(rec.args[:3], " ") != "manifest generate -f" {
		t.Fatalf("%v %v", rec.name, rec.args)
	}
	if !bytes.Contains(rec.conf, []byte("kind: IstioOperator")) {
		t.Fatal("the reviewed install file was not what was rendered")
	}
	joined := strings.Join(rec.env, "\n")
	if !strings.Contains(joined, "KUBECONFIG=") || strings.Contains(joined, os.Getenv("HOME")+"/.kube") || len(rec.env) > 5 {
		t.Fatalf("environment is not clean: %v", rec.env)
	}
	for _, kv := range rec.env {
		if strings.HasPrefix(kv, "KUBECONFIG=") {
			if _, err := os.Stat(strings.TrimPrefix(kv, "KUBECONFIG=")); err == nil {
				t.Fatal("KUBECONFIG must point at a file that does not exist")
			}
		}
	}
	// creation order: namespace-less CRDs first, webhooks last
	if manifests[0].Ref.Kind != "CustomResourceDefinition" || manifests[31].Ref.Kind != "MutatingWebhookConfiguration" {
		t.Fatalf("%s ... %s", manifests[0].Ref, manifests[31].Ref)
	}
	if entries, _ := os.ReadDir(env.Dir); len(entries) != 0 {
		t.Fatalf("the render left files behind: %v", entries)
	}
}

func TestAWindowsRenderEnvironmentKeepsOnlyWhatWindowsNeeds(t *testing.T) {
	env := ToolEnv{GOOS: "windows", Getenv: func(k string) string { return map[string]string{"SystemRoot": `C:\Windows`}[k] }}
	got := strings.Join(env.renderEnv(`C:\scratch`), ";")
	for _, want := range []string{`SystemRoot=C:\Windows`, `USERPROFILE=C:\scratch`, "KUBECONFIG="} {
		if !strings.Contains(got, want) {
			t.Errorf("missing %s in %s", want, got)
		}
	}
}

func TestARenderThatDiffersFromTheReviewedListIsRefused(t *testing.T) {
	expected := ExpectedIstioObjects()
	extra := append(append([]ObjectRef{}, expected...), ObjectRef{APIVersion: "v1", Kind: "Secret", Namespace: "istio-system", Name: "x"})
	renamed := append([]ObjectRef{}, expected...)
	renamed[0].Name = "something-else"
	for name, refs := range map[string][]ObjectRef{"extra object": extra, "renamed": renamed, "missing": expected[1:]} {
		env := ToolEnv{GOOS: "linux", Dir: t.TempDir(), Exec: &recordingExec{out: renderOf(refs)}}
		if _, err := env.Render(bg, "istioctl"); !errors.Is(err, ErrRenderUnexpected) {
			t.Errorf("%s: %v", name, err)
		}
	}
}

func TestAFailingIstioctlIsAFixedError(t *testing.T) {
	for _, rec := range []*recordingExec{{code: 1}, {err: errors.New("boom")}, {out: []byte("not: [valid")}} {
		env := ToolEnv{GOOS: "linux", Dir: t.TempDir(), Exec: rec}
		if _, err := env.Render(bg, "istioctl"); !errors.Is(err, ErrRenderFailed) {
			t.Errorf("%v", err)
		}
	}
}
