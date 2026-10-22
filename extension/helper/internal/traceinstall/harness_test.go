package traceinstall

import (
	"context"
	"fmt"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/bookinfotrace"
)

type fakePrompter struct {
	decision approve.Decision
	err      error
	requests []approve.Request
	hook     func() // runs while the person is "deciding"
}

func (p *fakePrompter) Confirm(_ context.Context, req approve.Request) (approve.Decision, error) {
	p.requests = append(p.requests, req)
	if p.hook != nil {
		p.hook()
	}
	return p.decision, p.err
}

// env is one complete, fake world: a cluster, a clock, a person, a private directory, and a pinned tool.
type env struct {
	t        *testing.T
	clock    *fakeClock
	sim      *sim
	prompter *fakePrompter
	fetcher  *fetcher
	exec     *recordingExec
	deps     Deps
	params   Params
	base     bookinfotrace.Snapshot
	meas     bookinfotrace.Measurements
	measures int
	address  bookinfotrace.Address
}

// richRender is what istioctl would print: the reviewed objects, with enough of each CRD for the setup to find its
// instances.
func richRender() []byte {
	var b strings.Builder
	for _, o := range ExpectedIstioObjects() {
		ns := ""
		if o.Namespace != "" {
			ns = "\n  namespace: " + o.Namespace
		}
		fmt.Fprintf(&b, "---\napiVersion: %s\nkind: %s\nmetadata:\n  name: %s%s\n", o.APIVersion, o.Kind, o.Name, ns)
		if o.Kind == "CustomResourceDefinition" {
			plural, group, _ := strings.Cut(o.Name, ".")
			fmt.Fprintf(&b, "spec:\n  group: %s\n  scope: Namespaced\n  names:\n    plural: %s\n    kind: K%s\n  versions:\n  - name: v1\n    served: true\n", group, plural, plural)
		}
	}
	return []byte(b.String())
}

func newEnv(t *testing.T) *env {
	t.Helper()
	clock := &fakeClock{now: time.Date(2026, 10, 22, 12, 0, 0, 0, time.UTC)}
	cluster := newSim(clock)
	archive := good(t)
	fetch := &fetcher{body: archive}
	tools := toolEnv(t, "tar.gz", archive, fetch)
	tools.GOOS, tools.GOARCH = runtime.GOOS, runtime.GOARCH
	rec := &recordingExec{out: richRender()}
	tools.Exec = rec
	e := &env{t: t, clock: clock, sim: cluster, prompter: &fakePrompter{decision: approve.Approved}, fetcher: fetch, exec: rec,
		meas:    bookinfotrace.Measurements{VMLimitMiB: f(3917), VMUsedMiB: f(2025), SwapUsedMiB: f(2689), IstiodMiB: f(150), SidecarMaxMiB: f(60)},
		address: bookinfotrace.Address{IPv4: "192.168.65.254", Port: "4317", Nodes: []string{"desktop-control-plane"}},
		params: Params{ContextName: "docker-desktop", Identity: ClusterIdentity{Context: "docker-desktop", Server: "127.0.0.1:6443",
			CASha256: strings.Repeat("a", 64), KubeSystemUID: "uid-kube-system"}}}
	n := 0
	e.deps = Deps{Cluster: cluster, Prompter: e.prompter, Store: Store{Dir: filepath.Join(t.TempDir(), "state")}, Tools: tools, Clock: clock,
		Timeouts:   Timeouts{Istiod: 2 * time.Minute, Pod: 2 * time.Minute, Rollback: 20 * time.Minute, Poll: 3 * time.Second, MonitorEvery: 15 * time.Second, CRRetries: 3, CRRetryWait: time.Second, Client: 3 * time.Minute},
		Thresholds: bookinfotrace.DefaultThresholds(),
		Measure:    func(context.Context) bookinfotrace.Measurements { e.measures++; return e.meas },
		Discover:   func(context.Context) (bookinfotrace.Address, error) { return e.address, nil },
		NewID:      func() (string, error) { n++; return fmt.Sprintf("%032x", n), nil }}
	facts, err := bookinfotrace.Gather(context.Background(), cluster)
	if err != nil {
		t.Fatal(err)
	}
	e.base = bookinfotrace.TakeSnapshot(facts, "docker-desktop", "base")
	return e
}

func (e *env) apply() Outcome { return Apply(context.Background(), e.deps, e.params) }
func (e *env) undo() Outcome  { return Undo(context.Background(), e.deps, e.params) }

func (e *env) record() *Record {
	e.t.Helper()
	rec, err := e.deps.Store.Load(e.params.Identity)
	if err != nil {
		e.t.Fatalf("the record cannot be loaded: %v", err)
	}
	return rec
}

// assertRestored fails unless the cluster is exactly as it was: nothing left over, and the same labels, Deployment specs
// and Pod containers as the snapshot taken before.
func (e *env) assertRestored() {
	e.t.Helper()
	if left := e.sim.leftovers(); len(left) != 0 {
		e.t.Fatalf("objects left in the cluster: %v", left)
	}
	facts, err := bookinfotrace.Gather(context.Background(), e.sim)
	if err != nil {
		e.t.Fatal(err)
	}
	if diffs := bookinfotrace.Compare(e.base, bookinfotrace.TakeSnapshot(facts, "docker-desktop", "after")); len(diffs) != 0 {
		e.t.Fatalf("the benchmark is not restored: %+v", diffs)
	}
	if e.sim.stuckPods != 0 {
		e.t.Fatalf("%d Pods could not be created while the namespace was labelled and Istio was gone", e.sim.stuckPods)
	}
}

func (e *env) assertNothingWritten() {
	e.t.Helper()
	if len(e.sim.writes) != 0 {
		e.t.Fatalf("the cluster was written to: %v", e.sim.writes)
	}
	if rec := e.record(); rec != nil {
		e.t.Fatalf("a record was saved: %+v", rec.Phase)
	}
}

func bookinfotraceGather(e *env) (bookinfotrace.Snapshot, error) {
	facts, err := bookinfotrace.Gather(context.Background(), e.sim)
	return bookinfotrace.TakeSnapshot(facts, "docker-desktop", "base"), err
}

func f(v float64) *float64 { return &v }
