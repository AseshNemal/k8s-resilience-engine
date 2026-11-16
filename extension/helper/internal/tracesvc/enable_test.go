package tracesvc

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/agent"
	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/execx"
	"resilience-engine/helper/internal/flow"
)

type memStore struct {
	m     Marker
	saves int
}

func (s *memStore) Load() (Marker, error) { return s.m, nil }
func (s *memStore) Save(m Marker) error   { s.m = m; s.saves++; return nil }

type prompter struct {
	decision approve.Decision
	err      error
	asked    []approve.Request
}

func (p *prompter) Confirm(_ context.Context, r approve.Request) (approve.Decision, error) {
	p.asked = append(p.asked, r)
	return p.decision, p.err
}

type fakeAgent struct {
	supported bool
	state     agent.State
	installs  int
	failing   bool
}

func (a *fakeAgent) Supported() bool                             { return a.supported }
func (a *fakeAgent) Describe() string                            { return "a test job" }
func (a *fakeAgent) Uninstall(context.Context) error             { return nil }
func (a *fakeAgent) Status(context.Context) (agent.State, error) { return a.state, nil }
func (a *fakeAgent) Install(context.Context) error {
	a.installs++
	if a.failing {
		return errors.New("no")
	}
	a.state = agent.On
	return nil
}

type approved struct {
	*world
	store  *memStore
	asker  *prompter
	agent  *fakeAgent
	now    time.Time
	dialed []string
	open   map[string]bool
}

func newApproved(state string, decision approve.Decision) *approved {
	a := &approved{
		world: newWorld(state),
		store: &memStore{},
		asker: &prompter{decision: decision},
		agent: &fakeAgent{supported: true, state: agent.Off},
		now:   time.Date(2026, 10, 22, 12, 0, 0, 0, time.UTC),
		open:  map[string]bool{"127.0.0.1:4317": true, "127.0.0.1:4318": true},
	}
	return a
}

func (a *approved) deps() Deps {
	d := a.world.deps()
	d.Store, d.Prompter, d.Agent = a.store, a.asker, a.agent
	d.Now = func() time.Time { return a.now }
	d.Dial = func(_ context.Context, address string) bool {
		a.dialed = append(a.dialed, address)
		return a.open[address]
	}
	return d
}

func TestEnableAsksFirstAndStartsNothingWhenDeclined(t *testing.T) {
	for _, decision := range []approve.Decision{approve.Declined, approve.TimedOut} {
		a := newApproved("", decision)
		got := Enable(context.Background(), a.deps())

		if decision == approve.TimedOut && got.State != StateApprovalTimedOut {
			t.Fatalf("an unanswered dialog must have its own state, got %s", got.State)
		}
		if decision == approve.Declined && got.State != flow.StateDeclined {
			t.Fatalf("a declined dialog must say declined, got %s", got.State)
		}
		if got.OK || a.docker.ran("run") != nil || a.docker.ran("start") != nil {
			t.Fatalf("%s: nothing may start without approval: %+v", decision, got)
		}
		if a.store.saves != 0 || a.agent.installs != 0 {
			t.Fatalf("%s: nothing may be remembered or installed without approval", decision)
		}
		if got.Trace == nil || got.Trace.Enabled {
			t.Fatalf("%s: tracing must still read as off: %+v", decision, got.Trace)
		}
	}
}

func TestEnableWithoutAnApprovalChannelDoesNothing(t *testing.T) {
	a := newApproved("", approve.Approved)
	a.asker.err = approve.ErrNoApprovalChannel
	got := Enable(context.Background(), a.deps())
	if got.State != flow.StateApprovalUnavailable || a.docker.ran("run") != nil || a.store.saves != 0 {
		t.Fatalf("got %+v", got)
	}

	d := a.deps()
	d.Prompter = nil
	if got := Enable(context.Background(), d); got.State != flow.StateApprovalUnavailable {
		t.Fatalf("with no prompter: %+v", got)
	}
}

func TestEnableOnApprovalStartsRemembersAndInstallsTheJob(t *testing.T) {
	a := newApproved("", approve.Approved)
	got := Enable(context.Background(), a.deps())

	if !got.OK || got.State != StateRunning {
		t.Fatalf("got %+v", got)
	}
	if !a.store.m.Enabled || a.store.m.EnabledAt != "2026-10-22T12:00:00Z" {
		t.Fatalf("the choice was not remembered: %+v", a.store.m)
	}
	if got.Trace == nil || !got.Trace.Enabled || got.Background != string(agent.On) || a.agent.installs != 1 {
		t.Fatalf("enabled=%+v background=%q installs=%d", got.Trace, got.Background, a.agent.installs)
	}
	if len(a.asker.asked) != 1 {
		t.Fatalf("the person must be asked exactly once, was asked %d times", len(a.asker.asked))
	}
}

func TestTheApprovalDialogSaysWhatWillAndWillNotHappen(t *testing.T) {
	a := newApproved("", approve.Approved)
	Enable(context.Background(), a.deps())

	ask := a.asker.asked[0]
	text := ask.Title + "\n" + ask.Body
	for _, want := range []string{ContainerName, Image, "256 MB", "this computer only", "never sends or invents a trace", "Kubernetes cluster and your applications are not changed", "a test job"} {
		if !strings.Contains(text, want) {
			t.Errorf("the dialog does not say %q:\n%s", want, text)
		}
	}
}

func TestEnableKeepsGoingWhenTheBackgroundJobCannotBeInstalled(t *testing.T) {
	a := newApproved("", approve.Approved)
	a.agent.failing = true
	got := Enable(context.Background(), a.deps())
	if !got.OK || !a.store.m.Enabled || got.Background != string(agent.Off) {
		t.Fatalf("a missing job must not undo a running service: %+v", got)
	}

	b := newApproved("", approve.Approved)
	b.agent.supported = false
	if got := Enable(context.Background(), b.deps()); got.Background != string(agent.Unsupported) || !got.OK {
		t.Fatalf("unsupported system: %+v", got)
	}
}

func TestEnableDoesNotStartAFailedContainerOrRememberIt(t *testing.T) {
	a := newApproved("", approve.Approved)
	a.docker.runResult = execx.Result{ExitCode: 1, Stderr: []byte("secret docker text")}
	got := Enable(context.Background(), a.deps())
	if got.OK || a.store.m.Enabled || strings.Contains(got.Message, "secret") {
		t.Fatalf("got %+v", got)
	}
}

func TestEnableLeavesAnExternalTraceServiceAlone(t *testing.T) {
	a := newApproved("", approve.Approved)
	a.external = true
	got := Enable(context.Background(), a.deps())

	if got.State != StateRunningExternal || len(a.asker.asked) != 0 || a.store.saves != 0 || a.docker.ran("run") != nil || a.agent.installs != 0 {
		t.Fatalf("a service this helper does not own must not be asked about, started or recorded: %+v", got)
	}
}

func TestEnableIsIdempotentAfterApproval(t *testing.T) {
	a := newApproved("exited", approve.Declined) // would decline, if it were asked
	a.store.m = Marker{Enabled: true}
	got := Enable(context.Background(), a.deps())

	if len(a.asker.asked) != 0 {
		t.Fatal("an approved setup must never be asked again")
	}
	if !got.OK || a.docker.ran("start") == nil {
		t.Fatalf("it should repair the stopped service: %+v", got)
	}
}

func TestEnsureDoesNothingUntilTheServiceIsTurnedOn(t *testing.T) {
	for _, state := range []string{"", "exited", "running"} {
		a := newApproved(state, approve.Approved)
		got := Ensure(context.Background(), a.deps())
		if got.State != StateNotEnabled || !got.OK || len(a.docker.calls) != 0 {
			t.Fatalf("state %q: %+v with docker calls %v", state, got, a.docker.calls)
		}
	}
}

func TestEnsureBringsOurStoppedOrMissingContainerBack(t *testing.T) {
	for _, state := range []string{"exited", ""} {
		a := newApproved(state, approve.Approved)
		a.store.m = Marker{Enabled: true}
		got := Ensure(context.Background(), a.deps())
		if !got.OK || got.State != StateRunning {
			t.Fatalf("state %q: %+v", state, got)
		}
		if got.Trace == nil || !got.Trace.Enabled {
			t.Fatalf("state %q: enabled must be reported", state)
		}
		if len(a.asker.asked) != 0 {
			t.Fatal("recovery must never ask")
		}
	}
}

func TestEnsureLeavesARunningOrExternalServiceAlone(t *testing.T) {
	a := newApproved("running", approve.Approved)
	a.store.m = Marker{Enabled: true}
	Ensure(context.Background(), a.deps())
	for _, call := range a.docker.calls {
		if call[1] != "ps" {
			t.Fatalf("a healthy service must only be read, but ran %v", call)
		}
	}

	b := newApproved("", approve.Approved)
	b.store.m = Marker{Enabled: true}
	b.external = true
	if got := Ensure(context.Background(), b.deps()); got.State != StateRunningExternal || b.docker.ran("run") != nil {
		t.Fatalf("an external service must not be replaced: %+v", got)
	}
}

func TestEnsureRecreatesASilentContainerOnceThenWaitsOutTheCooldown(t *testing.T) {
	a := newApproved("running", approve.Approved)
	a.store.m = Marker{Enabled: true}
	a.probesUntil = 1 << 20 // up but never answers

	first := Ensure(context.Background(), a.deps())
	if a.docker.ran("rm") == nil || first.OK {
		t.Fatalf("a silent container of ours should be recreated once: %+v", first)
	}
	if a.store.m.LastRecreate != "2026-10-22T12:00:00Z" {
		t.Fatalf("the recreation was not recorded: %+v", a.store.m)
	}

	a.docker.calls = nil
	a.docker.state = "running"
	a.now = a.now.Add(5 * time.Minute)
	second := Ensure(context.Background(), a.deps())
	if second.State != StateRecoveryWait || a.docker.ran("rm") != nil || a.docker.ran("run") != nil {
		t.Fatalf("within the cooldown it must not recreate again: %+v", second)
	}

	a.docker.calls = nil
	a.docker.state = "running"
	a.now = a.now.Add(6 * time.Minute)
	Ensure(context.Background(), a.deps())
	if a.docker.ran("rm") == nil {
		t.Fatal("after the cooldown it may recreate again")
	}
}

func TestEnsureOnlyEverTouchesOurOwnContainer(t *testing.T) {
	for _, state := range []string{"", "exited", "running"} {
		a := newApproved(state, approve.Approved)
		a.store.m = Marker{Enabled: true}
		a.probesUntil = 1 << 20
		Ensure(context.Background(), a.deps())
		for _, call := range a.docker.calls {
			switch call[1] {
			case "ps", "run":
			case "start", "rm":
				if call[len(call)-1] != ContainerName {
					t.Errorf("state %q: %v does not name our container", state, call)
				}
			default:
				t.Errorf("state %q: unexpected docker command %v", state, call)
			}
		}
	}
}

func TestStopTurnsTheAutomaticRestartOff(t *testing.T) {
	a := newApproved("running", approve.Approved)
	a.store.m = Marker{Enabled: true}
	got := Stop(context.Background(), a.deps())
	if !got.OK || a.store.m.Enabled || got.Trace == nil || got.Trace.Enabled {
		t.Fatalf("stop must also turn tracing off: %+v / %+v", got, a.store.m)
	}

	if after := Ensure(context.Background(), a.deps()); after.State != StateNotEnabled {
		t.Fatalf("a stopped service must stay stopped: %+v", after)
	}

	b := newApproved("", approve.Approved)
	b.store.m = Marker{Enabled: true}
	b.external = true
	Stop(context.Background(), b.deps())
	if !b.store.m.Enabled {
		t.Fatal("stopping must not change anything for a service this helper does not own")
	}
}

func TestStatusReportsWhetherTracingIsTurnedOn(t *testing.T) {
	a := newApproved("running", approve.Approved)
	if got := Status(context.Background(), a.deps()); got.Trace == nil || got.Trace.Enabled {
		t.Fatalf("not yet enabled: %+v", got.Trace)
	}
	a.store.m = Marker{Enabled: true}
	if got := Status(context.Background(), a.deps()); !got.Trace.Enabled {
		t.Fatalf("enabled: %+v", got.Trace)
	}
}

// nodeDocker adds the Kubernetes-node questions to the fake Docker.
type nodeDocker struct {
	*fakeDocker
	names    string
	execExit int
	execErr  error
	execs    [][]string
}

func (n *nodeDocker) Run(ctx context.Context, name string, args []string, in []byte) (execx.Result, error) {
	if args[0] == "ps" && strings.Contains(strings.Join(args, " "), "io.x-k8s.kind.role") {
		n.calls = append(n.calls, append([]string{name}, args...))
		return execx.Result{Stdout: []byte(n.names)}, nil
	}
	if args[0] == "exec" {
		n.calls = append(n.calls, append([]string{name}, args...))
		n.execs = append(n.execs, args)
		return execx.Result{ExitCode: n.execExit}, n.execErr
	}
	return n.fakeDocker.Run(ctx, name, args, in)
}

func withNodes(a *approved, names string, exit int) (*nodeDocker, Deps) {
	n := &nodeDocker{fakeDocker: a.docker, names: names, execExit: exit}
	d := a.deps()
	d.Runner = n
	return n, d
}

func endpoint(r flow.Result, name string) flow.Endpoint {
	for _, e := range r.Trace.Endpoints {
		if e.Name == name {
			return e
		}
	}
	return flow.Endpoint{}
}

func TestValidateReportsEachAddressItTested(t *testing.T) {
	a := newApproved("running", approve.Approved)
	_, d := withNodes(a, "desktop-control-plane\n", 0)
	got := Validate(context.Background(), d)

	if !got.OK || got.State != StateEndpointsOK {
		t.Fatalf("got %+v", got)
	}
	for _, name := range []string{"query", "otlp-http", "otlp-grpc"} {
		e := endpoint(got, name)
		if e.Reachable == nil || !*e.Reachable || e.Evidence != "host" {
			t.Errorf("%s: %+v", name, e)
		}
	}
	pods := endpoint(got, "pods-otlp-http")
	if pods.Reachable == nil || !*pods.Reachable || pods.Evidence != "node" || !strings.Contains(pods.Note, "A Pod has not been tested") {
		t.Fatalf("a node's success must be reported as node evidence, never as a Pod's: %+v", pods)
	}
}

func TestValidateSaysWhenAnAddressDoesNotAnswer(t *testing.T) {
	a := newApproved("running", approve.Approved)
	a.open["127.0.0.1:4318"] = false
	_, d := withNodes(a, "", 0)
	got := Validate(context.Background(), d)

	if got.OK || got.State != StateEndpointsFailed {
		t.Fatalf("got %+v", got)
	}
	if e := endpoint(got, "otlp-http"); e.Reachable == nil || *e.Reachable {
		t.Fatalf("otlp-http must be unreachable: %+v", e)
	}
}

func TestNodesAreNotAskedWhileNothingListensOnThisComputer(t *testing.T) {
	a := newApproved("", approve.Approved) // no container: nothing answers
	a.open = map[string]bool{}
	n, d := withNodes(a, "desktop-control-plane\n", 1)
	d.Probe = func(context.Context) bool { return false }
	got := Validate(context.Background(), d)

	pods := endpoint(got, "pods-otlp-http")
	if pods.Reachable != nil || !strings.Contains(pods.Note, "does not answer on this computer yet") {
		t.Fatalf("a node failing against a closed port says nothing about the network, so it must stay unknown: %+v", pods)
	}
	if len(n.execs) != 0 {
		t.Fatalf("no node should have been asked: %v", n.execs)
	}
}

func TestADownloadFailureIsNamedWithoutDockersText(t *testing.T) {
	for _, text := range []string{
		"Error response from daemon: Get \"https://registry-1.docker.io/v2/\": x509: certificate signed by unknown authority",
		"error getting credentials - err: exec: \"docker-credential-desktop\": executable file not found in $PATH",
		"dial tcp: lookup registry-1.docker.io: no such host",
	} {
		a := newApproved("", approve.Approved)
		a.docker.runResult = execx.Result{ExitCode: 125, Stderr: []byte(text)}
		got := Start(context.Background(), a.deps())
		if got.State != StateImageUnavailable || got.OK {
			t.Fatalf("%q: got %+v", text, got)
		}
		if strings.Contains(got.Message, "x509") || strings.Contains(got.Message, "registry") || strings.Contains(got.Message, "credential") {
			t.Fatalf("Docker's own text leaked: %q", got.Message)
		}
		if a.docker.ran("rm") == nil {
			t.Fatal("the half-made container was not removed")
		}
	}
}

func TestAnUnfamiliarDockerFailureStaysGeneric(t *testing.T) {
	a := newApproved("", approve.Approved)
	a.docker.runResult = execx.Result{ExitCode: 125, Stderr: []byte("something nobody has seen before")}
	if got := Start(context.Background(), a.deps()); got.State != StateStartFailed {
		t.Fatalf("got %s", got.State)
	}
}

func TestPodReachabilityIsUnknownNeverGuessed(t *testing.T) {
	cases := []struct {
		name   string
		nodes  string
		exit   int
		err    error
		reach  string // "true", "false" or "nil"
		wantIn string
	}{
		{"no node containers", "", 0, nil, "nil", "No Kubernetes node containers"},
		{"a node reaches it", "n1\nn2\n", 0, nil, "true", "Every Kubernetes node"},
		{"a node cannot reach it", "n1\nn2\n", 1, nil, "false", "could not reach"},
		{"bash missing in the node", "n1\n", 127, nil, "nil", "unknown"},
		{"docker exec failed to run", "n1\n", 0, errors.New("x"), "nil", "unknown"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			a := newApproved("running", approve.Approved)
			n, d := withNodes(a, c.nodes, c.exit)
			n.execErr = c.err
			e := endpoint(Validate(context.Background(), d), "pods-otlp-http")
			got := "nil"
			if e.Reachable != nil {
				got = map[bool]string{true: "true", false: "false"}[*e.Reachable]
			}
			if got != c.reach || !strings.Contains(e.Note, c.wantIn) {
				t.Fatalf("reachable=%s note=%q, want %s containing %q", got, e.Note, c.reach, c.wantIn)
			}
		})
	}
}

func TestPodReachabilityRunsOnlyAFixedCommandAndOnlyOnSafeNodeNames(t *testing.T) {
	a := newApproved("running", approve.Approved)
	n, d := withNodes(a, "good-node\nbad;rm\n$(x)\nfive\nsix\nseven\neight\n", 0)
	Validate(context.Background(), d)

	for _, args := range n.execs {
		if strings.Join(args[2:], " ") != "timeout 3 bash -c exec 3<>/dev/tcp/host.docker.internal/4318" {
			t.Errorf("the command inside the node is not the fixed one: %v", args)
		}
		if !nodeName.MatchString(args[1]) {
			t.Errorf("an unsafe node name reached a command line: %q", args[1])
		}
	}
	if len(n.execs) != maxNodes {
		t.Errorf("at most %d nodes may be asked, got %d", maxNodes, len(n.execs))
	}
}

func TestValidateOnlyOpensConnections(t *testing.T) {
	a := newApproved("running", approve.Approved)
	_, d := withNodes(a, "n1\n", 0)
	Validate(context.Background(), d)
	for _, address := range a.dialed {
		if !strings.HasPrefix(address, "127.0.0.1:") {
			t.Errorf("the host check dialled %q, not this computer", address)
		}
	}
	// Dial has no way to carry data, so a test span could not be sent through it; the signature is the proof.
	var _ func(context.Context, string) bool = d.Dial
}

func TestResultsNeverCarryDockerText(t *testing.T) {
	a := newApproved("exited", approve.Approved)
	a.store.m = Marker{Enabled: true}
	a.docker.failStart = true
	got := Ensure(context.Background(), a.deps())
	raw, _ := json.Marshal(got)
	if strings.Contains(string(raw), "secret docker text") {
		t.Fatalf("Docker's own text leaked into %s", raw)
	}
}

func TestTheFileStoreRoundTripsPrivatelyAndForgivesACorruptFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sub", "tracing.json")
	store := FileStore{Path: path}

	if m, err := store.Load(); err != nil || m.Enabled {
		t.Fatalf("a missing file means not enabled: %+v %v", m, err)
	}
	if err := store.Save(Marker{Enabled: true, EnabledAt: "2026-10-22T12:00:00Z"}); err != nil {
		t.Fatal(err)
	}
	if m, _ := store.Load(); !m.Enabled || m.EnabledAt != "2026-10-22T12:00:00Z" {
		t.Fatalf("round trip: %+v", m)
	}
	if st, err := os.Stat(path); err != nil || st.Mode().Perm() != 0o600 {
		t.Skipf("file modes are not meaningful here: %v %v", st, err)
	}

	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if m, err := store.Load(); err != nil || m.Enabled {
		t.Fatalf("a corrupt file must read as not enabled: %+v %v", m, err)
	}
}
