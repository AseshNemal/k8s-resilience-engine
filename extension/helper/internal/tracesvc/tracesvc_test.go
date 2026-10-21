package tracesvc

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/execx"
	"resilience-engine/helper/internal/flow"
)

// fakeDocker answers `docker ps` from a container state and records every command it is given.
type fakeDocker struct {
	state     string // what `docker ps` reports for our container ("" = none)
	runResult execx.Result
	failStart bool
	startErr  execx.Result // overrides the failure of `docker start` when set
	failStop  bool
	calls     [][]string
	onStart   func() // called when `docker start` or `docker run` succeeds
}

func (f *fakeDocker) Run(_ context.Context, name string, args []string, _ []byte) (execx.Result, error) {
	f.calls = append(f.calls, append([]string{name}, args...))
	switch args[0] {
	case "ps":
		return execx.Result{Stdout: []byte(f.state + "\n")}, nil
	case "start":
		if f.failStart {
			if f.startErr.ExitCode != 0 {
				return f.startErr, nil
			}
			return execx.Result{ExitCode: 1, Stderr: []byte("secret docker text")}, nil
		}
		f.state = "running"
		if f.onStart != nil {
			f.onStart()
		}
	case "run":
		if f.runResult.ExitCode != 0 {
			return f.runResult, nil
		}
		f.state = "running"
		if f.onStart != nil {
			f.onStart()
		}
	case "rm":
		f.state = ""
	case "stop":
		if f.failStop {
			return execx.Result{ExitCode: 1}, nil
		}
		f.state = "exited"
	}
	return execx.Result{}, nil
}

// world wires a fake Docker to a probe that answers once the container is running (plus extra delay).
type world struct {
	docker      *fakeDocker
	answering   bool
	probesUntil int // the probe stays false for this many calls after a start
	probes      int
	sleeps      int
	dockerFound bool
	external    bool // a Jaeger answers although our container is not running
}

func newWorld(state string) *world {
	w := &world{docker: &fakeDocker{state: state}, dockerFound: true}
	w.docker.onStart = func() { w.probes = 0 }
	return w
}

func (w *world) deps() Deps {
	d := Deps{
		Runner: w.docker,
		Probe: func(context.Context) bool {
			w.probes++
			if w.external {
				return true
			}
			return w.docker.state == "running" && w.probes > w.probesUntil
		},
		Sleep: func(context.Context, time.Duration) { w.sleeps++ },
	}
	if w.dockerFound {
		d.FindDocker = func() (string, error) { return "/usr/local/bin/docker", nil }
	} else {
		d.FindDocker = func() (string, error) { return "", errors.New("not found") }
	}
	return d
}

func joined(call []string) string { return strings.Join(call, " ") }

func (f *fakeDocker) ran(verb string) []string {
	for _, call := range f.calls {
		if len(call) > 1 && call[1] == verb {
			return call
		}
	}
	return nil
}

func TestStatusStates(t *testing.T) {
	cases := []struct {
		name     string
		state    string
		external bool
		found    bool
		want     flow.State
		ok       bool
	}{
		{"ours and answering", "running", false, true, StateRunning, true},
		{"answering but started by someone else", "", true, true, StateRunningExternal, true},
		{"answering while docker is missing", "", true, false, StateRunningExternal, true},
		{"our container starting", "running", false, true, StateRunning, true},
		{"stopped", "exited", false, true, StateStopped, true},
		{"never created", "", false, true, StateNotCreated, true},
		{"docker missing and nothing answers", "", false, false, StateDockerMissing, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newWorld(c.state)
			w.external = c.external
			w.dockerFound = c.found
			if c.name == "our container starting" {
				w.probesUntil = 1000 // up, not answering yet
				w.docker.state = "running"
				c.want, c.ok = StateNotReady, false
			}
			got := Status(context.Background(), w.deps())
			if got.State != c.want || got.OK != c.ok {
				t.Fatalf("state=%s ok=%v, want %s ok=%v (%s)", got.State, got.OK, c.want, c.ok, got.Message)
			}
			if got.Command != "trace-status" {
				t.Fatalf("command = %q", got.Command)
			}
		})
	}
}

func TestStartCreatesTheContainerWithTheFixedSafeArguments(t *testing.T) {
	w := newWorld("")
	got := Start(context.Background(), w.deps())

	if got.State != StateRunning || !got.OK {
		t.Fatalf("got %s ok=%v: %s", got.State, got.OK, got.Message)
	}
	run := joined(w.docker.ran("run"))
	for _, want := range []string{
		"run -d", "--name " + ContainerName, "--restart unless-stopped", "--memory 256m",
		"MEMORY_MAX_TRACES=5000", "COLLECTOR_OTLP_ENABLED=true", Image,
		"-p 127.0.0.1:16686:16686", "-p 127.0.0.1:4317:4317", "-p 127.0.0.1:4318:4318",
	} {
		if !strings.Contains(run, want) {
			t.Errorf("docker run is missing %q: %s", want, run)
		}
	}
	// Ports are published on loopback only: never on every interface.
	if strings.Contains(run, "-p 16686") || strings.Contains(run, "-p 0.0.0.0") {
		t.Errorf("a port is published beyond loopback: %s", run)
	}
}

func TestStartOnlyEverRunsTheKnownDockerCommands(t *testing.T) {
	for _, state := range []string{"", "exited", "running"} {
		w := newWorld(state)
		Start(context.Background(), w.deps())
		Stop(context.Background(), w.deps())
		for _, call := range w.docker.calls {
			verb := call[1]
			if verb != "ps" && verb != "run" && verb != "start" && verb != "stop" && verb != "rm" {
				t.Errorf("state %q: unexpected docker command %v", state, call)
			}
			if verb == "rm" && joined(call) != "/usr/local/bin/docker rm -f "+ContainerName {
				t.Errorf("state %q: rm must remove only the extension's own container: %v", state, call)
			}
			if verb == "stop" || verb == "start" || verb == "rm" {
				if call[len(call)-1] != ContainerName {
					t.Errorf("%s names %q, not the extension's own container", verb, call[len(call)-1])
				}
			}
		}
	}
}

func TestStartAnExistingStoppedContainerStartsItWithoutCreatingAnother(t *testing.T) {
	w := newWorld("exited")
	got := Start(context.Background(), w.deps())

	if got.State != StateRunning {
		t.Fatalf("got %s: %s", got.State, got.Message)
	}
	if w.docker.ran("run") != nil {
		t.Fatal("a second container was created")
	}
	if joined(w.docker.ran("start")) != "/usr/local/bin/docker start "+ContainerName {
		t.Fatalf("start = %v", w.docker.ran("start"))
	}
}

func TestStartLeavesAJaegerSomeoneElseStartedAlone(t *testing.T) {
	w := newWorld("")
	w.external = true
	got := Start(context.Background(), w.deps())

	if got.State != StateRunningExternal || !got.OK {
		t.Fatalf("got %s ok=%v", got.State, got.OK)
	}
	if w.docker.ran("run") != nil || w.docker.ran("start") != nil {
		t.Fatal("Docker was asked to start something although a trace service already answers")
	}
}

func TestStartWhenAlreadyRunningDoesNothing(t *testing.T) {
	w := newWorld("running")
	got := Start(context.Background(), w.deps())

	if got.State != StateRunning {
		t.Fatalf("got %s", got.State)
	}
	if w.docker.ran("run") != nil || w.docker.ran("start") != nil {
		t.Fatal("a running trace service was started again")
	}
}

func TestStartWaitsUntilTheServiceAnswers(t *testing.T) {
	w := newWorld("")
	w.probesUntil = 5
	got := Start(context.Background(), w.deps())

	if got.State != StateRunning || !got.OK {
		t.Fatalf("got %s: %s", got.State, got.Message)
	}
	if w.sleeps == 0 {
		t.Fatal("it did not wait between probes")
	}
}

func TestStartGivesUpWithAClearStateWhenTheServiceNeverAnswers(t *testing.T) {
	w := newWorld("")
	w.probesUntil = 1 << 30
	got := Start(context.Background(), w.deps())

	if got.State != StateNotReady || got.OK {
		t.Fatalf("got %s ok=%v", got.State, got.OK)
	}
	if limit := int(readyTimeout / readyInterval); w.sleeps > limit {
		t.Fatalf("waited %d times, more than the %d allowed", w.sleeps, limit)
	}
}

func TestStartReportsAPortAnotherProgramHolds(t *testing.T) {
	for _, text := range []string{
		"Bind for 127.0.0.1:16686 failed: port is already allocated",
		"listen tcp4 127.0.0.1:4317: bind: address already in use",
	} {
		w := newWorld("")
		w.docker.runResult = execx.Result{ExitCode: 125, Stderr: []byte(text)}
		got := Start(context.Background(), w.deps())

		if got.State != StatePortInUse || got.OK {
			t.Fatalf("%q: got %s ok=%v", text, got.State, got.OK)
		}
		if strings.Contains(got.Message, "Bind for") || strings.Contains(got.Message, "tcp4") {
			t.Fatalf("Docker's own text leaked into the message: %q", got.Message)
		}
	}
}

func TestStartNeverPrintsDockersErrorText(t *testing.T) {
	w := newWorld("exited")
	w.docker.failStart = true
	got := Start(context.Background(), w.deps())

	if got.State != StateStartFailed || got.OK {
		t.Fatalf("got %s ok=%v", got.State, got.OK)
	}
	if strings.Contains(got.Message, "secret docker text") {
		t.Fatalf("Docker's text reached the result: %q", got.Message)
	}
}

func TestStartWithoutDockerSaysSo(t *testing.T) {
	w := newWorld("")
	w.dockerFound = false
	got := Start(context.Background(), w.deps())

	if got.State != StateDockerMissing || got.OK {
		t.Fatalf("got %s ok=%v", got.State, got.OK)
	}
}

func TestStopStopsOnlyOurContainer(t *testing.T) {
	w := newWorld("running")
	got := Stop(context.Background(), w.deps())

	if got.State != StateStopped || !got.OK {
		t.Fatalf("got %s: %s", got.State, got.Message)
	}
	if joined(w.docker.ran("stop")) != "/usr/local/bin/docker stop -t 5 "+ContainerName {
		t.Fatalf("stop = %v", w.docker.ran("stop"))
	}
}

func TestStopLeavesAnExternalJaegerAlone(t *testing.T) {
	w := newWorld("")
	w.external = true
	got := Stop(context.Background(), w.deps())

	if got.State != StateRunningExternal {
		t.Fatalf("got %s", got.State)
	}
	if w.docker.ran("stop") != nil {
		t.Fatal("Docker was asked to stop a trace service this helper did not start")
	}
}

func TestStopWhenNothingIsRunningIsAnAnswerNotAnError(t *testing.T) {
	for _, state := range []string{"", "exited"} {
		w := newWorld(state)
		got := Stop(context.Background(), w.deps())
		if !got.OK || w.docker.ran("stop") != nil {
			t.Fatalf("state %q: ok=%v stop=%v", state, got.OK, w.docker.ran("stop"))
		}
	}
}

func TestStopReportsADockerFailureWithoutItsText(t *testing.T) {
	w := newWorld("running")
	w.docker.failStop = true
	got := Stop(context.Background(), w.deps())

	if got.OK || got.State != StateStartFailed {
		t.Fatalf("got %s ok=%v", got.State, got.OK)
	}
}

func TestTheContainerNameFilterIsAnchored(t *testing.T) {
	w := newWorld("")
	Status(context.Background(), w.deps())

	ps := joined(w.docker.ran("ps"))
	if !strings.Contains(ps, "name=^/"+ContainerName+"$") {
		t.Fatalf("the name filter can match other containers: %s", ps)
	}
}

func TestTheImageMatchesTheRepositoryComposeFile(t *testing.T) {
	// The helper and deploy/docker-compose.jaeger.yml must run the same Jaeger version.
	if !strings.HasPrefix(Image, "jaegertracing/all-in-one:") {
		t.Fatalf("unexpected image %q", Image)
	}
}

// A failed `docker run` can leave a half-made container with no published ports. It must not be left behind:
// starting it later would give a container that is "up" but cannot be reached.
func TestAFailedCreateRemovesTheHalfMadeContainer(t *testing.T) {
	w := newWorld("")
	w.docker.runResult = execx.Result{ExitCode: 125, Stderr: []byte("port is already allocated")}
	got := Start(context.Background(), w.deps())

	if got.State != StatePortInUse {
		t.Fatalf("got %s", got.State)
	}
	if joined(w.docker.ran("rm")) != "/usr/local/bin/docker rm -f "+ContainerName {
		t.Fatalf("rm = %v", w.docker.ran("rm"))
	}
	if w.docker.state != "" {
		t.Fatalf("the container is still there: %q", w.docker.state)
	}
}

func TestAnyFailedCreateAlsoCleansUp(t *testing.T) {
	w := newWorld("")
	w.docker.runResult = execx.Result{ExitCode: 1, Stderr: []byte("secret docker text")}
	got := Start(context.Background(), w.deps())

	if got.State != StateStartFailed || strings.Contains(got.Message, "secret docker text") {
		t.Fatalf("got %s: %q", got.State, got.Message)
	}
	if w.docker.ran("rm") == nil {
		t.Fatal("the failed container was not removed")
	}
}

// Our container is up but silent for good (for example it lost its ports in an earlier failed start): after a
// short wait it is recreated once, instead of leaving the person with a service that never answers.
func TestAStuckContainerOfOursIsRecreatedOnce(t *testing.T) {
	w := newWorld("running")
	recreated := false
	w.docker.onStart = func() { recreated = true }
	d := w.deps()
	d.Probe = func(context.Context) bool { return recreated }

	got := Start(context.Background(), d)

	if got.State != StateRunning || !got.OK {
		t.Fatalf("got %s: %s", got.State, got.Message)
	}
	if w.docker.ran("rm") == nil || w.docker.ran("run") == nil {
		t.Fatalf("expected a remove and a create, got %v", w.docker.calls)
	}
	runs := 0
	for _, call := range w.docker.calls {
		if call[1] == "run" {
			runs++
		}
	}
	if runs != 1 {
		t.Fatalf("created %d times, want once", runs)
	}
	if limit := int(revivalWait/readyInterval) + 5; w.sleeps > limit {
		t.Fatalf("waited %d times before recreating, more than the short wait allows", w.sleeps)
	}
}

func TestAContainerThatIsJustSlowToStartIsNotRecreated(t *testing.T) {
	w := newWorld("running")
	w.probesUntil = 3 // it answers after a few probes, well inside the short wait
	got := Start(context.Background(), w.deps())

	if got.State != StateRunning {
		t.Fatalf("got %s", got.State)
	}
	if w.docker.ran("rm") != nil || w.docker.ran("run") != nil {
		t.Fatalf("a container that was only slow was recreated: %v", w.docker.calls)
	}
}

func TestStartingAStoppedContainerReportsAPortConflictToo(t *testing.T) {
	w := newWorld("exited")
	w.docker.failStart = true
	w.docker.startErr = execx.Result{ExitCode: 1, Stderr: []byte("Bind for 127.0.0.1:16686 failed: port is already allocated")}
	got := Start(context.Background(), w.deps())

	if got.State != StatePortInUse || strings.Contains(got.Message, "Bind for") {
		t.Fatalf("got %s: %q", got.State, got.Message)
	}
}
