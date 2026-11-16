package main

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/kube"
	"resilience-engine/helper/internal/kube/kubetest"
	"resilience-engine/helper/internal/lock"
	"resilience-engine/helper/internal/traceinstall"
)

type staticFetcher struct {
	body  []byte
	calls int
}

func (f *staticFetcher) Get(context.Context, string, int64) ([]byte, error) {
	f.calls++
	return f.body, nil
}

type renderExec struct{ out []byte }

func (r renderExec) Run(context.Context, string, []string, []string) ([]byte, int, error) {
	return r.out, 0, nil
}

func renderAll() []byte {
	var b strings.Builder
	for _, o := range traceinstall.ExpectedIstioObjects() {
		ns := ""
		if o.Namespace != "" {
			ns = "\n  namespace: " + o.Namespace
		}
		fmt.Fprintf(&b, "---\napiVersion: %s\nkind: %s\nmetadata:\n  name: %s%s\n", o.APIVersion, o.Kind, o.Name, ns)
		if o.Kind == "CustomResourceDefinition" {
			plural, group, _ := strings.Cut(o.Name, ".")
			fmt.Fprintf(&b, "spec:\n  group: %s\n  scope: Namespaced\n  names:\n    plural: %s\n    kind: K\n  versions:\n  - name: v1\n    served: true\n", group, plural)
		}
	}
	return []byte(b.String())
}

func tinyArchive() []byte {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	body := []byte("#!fake\n")
	_ = tw.WriteHeader(&tar.Header{Name: "istioctl", Typeflag: tar.TypeReg, Mode: 0o755, Size: int64(len(body))})
	_, _ = tw.Write(body)
	tw.Close()
	gz.Close()
	return buf.Bytes()
}

type noLock struct{ busy bool }

func (l noLock) Acquire() (func(), error) {
	if l.busy {
		return nil, lock.ErrBusy
	}
	return func() {}, nil
}

// useInstall replaces the real directories, tool download, host and timings with fakes for one test.
func useInstall(t *testing.T, decision approve.Decision) (dir string, fetch *staticFetcher) {
	t.Helper()
	useHost(t)
	dir = t.TempDir()
	archive := tinyArchive()
	sum := sha256.Sum256(archive)
	fetch = &staticFetcher{body: archive}
	prevDeps, prevLock, prevDir := installDeps, setupLock, installDir
	installDir = func() string { return dir }
	setupLock = func() lock.Lock { return noLock{} }
	installDeps = func(c traceinstall.Cluster) traceinstall.Deps {
		return traceinstall.Deps{
			Cluster:  c,
			Prompter: prompter{decision},
			Store:    traceinstall.Store{Dir: filepath.Join(dir, "bookinfo-tracing")},
			Tools: traceinstall.ToolEnv{GOOS: runtime.GOOS, GOARCH: runtime.GOARCH, Dir: filepath.Join(dir, "tools"), Fetcher: fetch, Exec: renderExec{renderAll()}, ReviewedDigest: renderDigest(t, renderAll()),
				Lookup: func(string, string) (traceinstall.Artifact, error) {
					return traceinstall.Artifact{Asset: "istioctl-1.31.2-test.tar.gz", SHA256: hex.EncodeToString(sum[:]), Kind: "tar.gz", Binary: "istioctl"}, nil
				}},
			Host:       bookinfoDeps(),
			Timeouts:   traceinstall.Timeouts{Istiod: 60 * time.Millisecond, Pod: 60 * time.Millisecond, Rollback: 30 * time.Second, Poll: 5 * time.Millisecond, MonitorEvery: time.Hour, CRRetries: 1, CRRetryWait: time.Millisecond, Client: time.Second},
			Thresholds: bookinfotrace.DefaultThresholds(),
			Measure: func(context.Context) bookinfotrace.Measurements {
				v := func(x float64) *float64 { return &x }
				return bookinfotrace.Measurements{VMLimitMiB: v(3917), VMUsedMiB: v(2025), SwapUsedMiB: v(2689), IstiodMiB: v(100), SidecarMaxMiB: v(50)}
			},
			Discover: func(context.Context) (bookinfotrace.Address, error) {
				return bookinfotrace.Address{IPv4: "192.168.65.254", Port: "4317", Nodes: []string{"desktop-control-plane"}}, nil
			},
		}
	}
	t.Cleanup(func() { installDeps, setupLock, installDir = prevDeps, prevLock, prevDir })
	return dir, fetch
}

func installCluster() *kubetest.Fake {
	cs := bookinfoCluster()
	cs.Put(kube.Namespaces, kube.Object{Name: "kube-system", UID: "uid-kube-system"})
	return cs
}

func TestTheSetupCommandsTakeExactlyOneContext(t *testing.T) {
	for _, command := range []string{"trace-bookinfo-apply", "trace-bookinfo-undo", "trace-bookinfo-verify", "trace-bookinfo-traffic", "trace-bookinfo-status"} {
		for _, args := range [][]string{{command}, {command, "a", "b"}} {
			code, out, errOut := invokeOn(t, installCluster(), args...)
			if code != 2 || out != "" || !strings.HasPrefix(errOut, "usage:") || !strings.Contains(errOut, command) {
				t.Errorf("%v: code %d stdout %q stderr %q", args, code, out, errOut)
			}
		}
	}
}

func TestDecliningTheApplyDialogChangesNothingAndStoresNothing(t *testing.T) {
	dir, fetch := useInstall(t, approve.Declined)
	cs := installCluster()
	cs.ClearActions()
	code, out, _ := invokeOn(t, cs, "trace-bookinfo-apply", "kind-resilience-research")
	v := parse(t, out)
	if code != 0 || v["ok"] != false || v["state"] != "declined" {
		t.Fatalf("%v", v)
	}
	if writes, tokens := cs.Writes(); len(writes) != 0 || tokens != 0 {
		t.Fatalf("writes %v tokens %d", writes, tokens)
	}
	if fetch.calls != 0 {
		t.Error("the tool was downloaded before approval")
	}
	if entries, _ := os.ReadDir(dir); len(entries) != 0 {
		t.Errorf("files were created before approval: %v", entries)
	}
}

func TestAnUnsupportedContextIsNeverContactedByTheSetupCommands(t *testing.T) {
	useInstall(t, approve.Approved)
	for _, command := range []string{"trace-bookinfo-apply", "trace-bookinfo-undo", "trace-bookinfo-verify", "trace-bookinfo-traffic"} {
		cs := installCluster()
		cs.ClearActions()
		_, out, _ := invokeOn(t, cs, command, "prod")
		if v := parse(t, out); v["ok"] != false || v["state"] != "unsupported-context" || len(cs.Actions()) != 0 {
			t.Errorf("%s: %v %v", command, v, cs.Actions())
		}
	}
}

func TestOnlyOneSetupCommandRunsAtATime(t *testing.T) {
	useInstall(t, approve.Approved)
	setupLock = func() lock.Lock { return noLock{busy: true} }
	cs := installCluster()
	cs.ClearActions()
	_, out, _ := invokeOn(t, cs, "trace-bookinfo-apply", "kind-resilience-research")
	if v := parse(t, out); v["state"] != "busy" || len(cs.Actions()) != 0 {
		t.Fatalf("%v %v", v, cs.Actions())
	}
}

// With the in-memory cluster that never makes istiod Ready, an approved Apply creates the Istio objects, gives up
// waiting, and rolls everything back. That exercises the real wiring (record, guard, writes, rollback) end to end.
func TestAnApprovedApplyThatCannotFinishRollsBackAndSaysSo(t *testing.T) {
	dir, _ := useInstall(t, approve.Approved)
	cs := installCluster()
	cs.ClearActions()
	code, out, errOut := invokeOn(t, cs, "trace-bookinfo-apply", "kind-resilience-research")
	if code != 0 || errOut != "" {
		t.Fatalf("code %d stderr %q", code, errOut)
	}
	v := parse(t, out)
	if v["ok"] != false || v["state"] != "bookinfo-apply-failed" || !strings.Contains(v["message"].(string), "Everything this run created was removed") {
		t.Fatalf("%v", v)
	}
	writes, tokens := cs.Writes()
	if tokens != 0 {
		t.Errorf("a token was requested: the extension's access must not be used or changed")
	}
	creates, deletes := 0, 0
	for _, w := range writes {
		switch {
		case strings.HasPrefix(w, "create"):
			creates++
		case strings.HasPrefix(w, "delete"):
			deletes++
		}
	}
	if creates < 30 || creates != deletes {
		t.Fatalf("created %d, deleted %d: %v", creates, deletes, writes)
	}
	for _, name := range []string{"istio-system"} {
		if _, there := cs.Lookup(kube.Namespaces, "", name); there {
			t.Errorf("%s was left behind", name)
		}
	}
	if _, there := cs.Lookup(kube.Deployments, "bookinfo", "productpage-v1"); !there {
		t.Error("an original Deployment is missing")
	}

	// the record is saved, private, and shows through status without contacting the cluster
	cs.ClearActions()
	_, statusOut, _ := invokeOn(t, cs, "trace-bookinfo-status", "kind-resilience-research")
	status := parse(t, statusOut)
	setup, _ := status["setup"].(map[string]any)
	if status["ok"] != true || setup == nil || setup["phase"] != "rolled-back" || len(cs.Actions()) != 0 {
		t.Fatalf("%v %v", status, cs.Actions())
	}
	if runtime.GOOS != "windows" {
		files, _ := filepath.Glob(filepath.Join(dir, "bookinfo-tracing", "*.json"))
		if len(files) != 1 {
			t.Fatalf("%v", files)
		}
		if info, _ := os.Stat(files[0]); info.Mode().Perm() != 0o600 {
			t.Errorf("record mode %v", info.Mode().Perm())
		}
	}
	for _, secret := range []string{tokenValue, "Bearer", "client-key"} {
		if strings.Contains(out, secret) || strings.Contains(statusOut, secret) {
			t.Errorf("output contains %q", secret)
		}
	}
}

func TestStatusWithoutASetupSaysSoAndReadsNoCluster(t *testing.T) {
	useInstall(t, approve.Approved)
	cs := installCluster()
	cs.ClearActions()
	_, out, _ := invokeOn(t, cs, "trace-bookinfo-status", "kind-resilience-research")
	v := parse(t, out)
	if v["ok"] != true || v["state"] != "bookinfo-status" || v["setup"] != nil || len(cs.Actions()) != 0 {
		t.Fatalf("%v", v)
	}
}

func TestUndoAndVerifyWithoutASetupChangeNothing(t *testing.T) {
	useInstall(t, approve.Approved)
	for command, state := range map[string]string{"trace-bookinfo-undo": "bookinfo-no-record", "trace-bookinfo-verify": "bookinfo-not-applied", "trace-bookinfo-traffic": "bookinfo-not-applied"} {
		cs := installCluster()
		cs.ClearActions()
		_, out, _ := invokeOn(t, cs, command, "kind-resilience-research")
		v := parse(t, out)
		if v["ok"] != false || v["state"] != state {
			t.Errorf("%s: %v", command, v)
		}
		if writes, _ := cs.Writes(); len(writes) != 0 {
			t.Errorf("%s wrote: %v", command, writes)
		}
	}
}

var _ = errors.New

func renderDigest(t *testing.T, out []byte) string {
	t.Helper()
	digest, err := traceinstall.DigestOfRendered(out)
	if err != nil {
		t.Fatal(err)
	}
	return digest
}

func TestProductionUsesTheCompiledInReviewedDigest(t *testing.T) {
	if got := realInstall(nil).Tools.Reviewed(); got != traceinstall.ReviewedRenderDigest {
		t.Fatalf("production must use the reviewed digest, got %s", got)
	}
}
