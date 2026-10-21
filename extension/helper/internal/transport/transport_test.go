package transport

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/execx"
	"resilience-engine/helper/internal/secret"
)

var ctx = context.Background()

const (
	tokenValue = "tok-SHOULD-NEVER-APPEAR-IN-ARGV-OR-OUTPUT"
	containerA = "a1b2c3d4e5f6"
)

func request() InstallRequest {
	doc := "apiVersion: v1\nusers:\n- name: user\n  user:\n    token: " + tokenValue + "\n"
	return InstallRequest{Kubeconfig: secret.NewBytes([]byte(doc)), Context: "kind-resilience-research", ExpiresAt: time.Date(2026, 10, 19, 20, 0, 0, 0, time.UTC)}
}

// ---------------------------------------------------------------- docker exec, with a fake runner

type recorded struct {
	name  string
	args  []string
	stdin []byte
}

// dockerFake plays Docker: `ps` lists containers, `exec` runs the ingest command.
type dockerFake struct {
	containers []string
	psExit     int
	execOut    string
	execExit   int
	execErr    error
	psErr      error
	calls      []recorded
}

func (d *dockerFake) Run(_ context.Context, name string, args []string, stdin []byte) (execx.Result, error) {
	d.calls = append(d.calls, recorded{name, args, stdin})
	switch args[0] {
	case "ps":
		return execx.Result{Stdout: []byte(strings.Join(d.containers, "\n")), ExitCode: d.psExit}, d.psErr
	case "exec":
		return execx.Result{Stdout: []byte(d.execOut), ExitCode: d.execExit}, d.execErr
	}
	return execx.Result{}, errors.New("unexpected docker command")
}

func (d *dockerFake) execCall(t *testing.T) recorded {
	t.Helper()
	for _, c := range d.calls {
		if c.args[0] == "exec" {
			return c
		}
	}
	t.Fatal("docker exec was never called")
	return recorded{}
}

func newExec(d *dockerFake) DockerExec {
	return DockerExec{Runner: d, FindDocker: func() (string, error) { return "/usr/bin/docker", nil }}
}

const okStatus = `{"ok": true, "connected": true, "managed": true, "context": "kind-resilience-research", "state": "ok", "server": "host.docker.internal:58227", "expiresAt": "2026-10-19T20:00:00Z"}`

func TestInstallSendsTheCredentialOnStdinNeverInArguments(t *testing.T) {
	d := &dockerFake{containers: []string{containerA}, execOut: okStatus}
	st, err := newExec(d).Install(ctx, request())
	if err != nil {
		t.Fatal(err)
	}
	call := d.execCall(t)

	want := []string{"exec", "-i", "-u", "10001", containerA, "python", "-m", "extension.cluster_ingest"}
	if strings.Join(call.args, " ") != strings.Join(want, " ") {
		t.Fatalf("argv = %v, want %v", call.args, want)
	}
	for _, c := range d.calls {
		if strings.Contains(strings.Join(c.args, " "), tokenValue) {
			t.Fatalf("the token appeared in argv of %v", c.args)
		}
	}
	var body struct {
		Kubeconfig string            `json:"kubeconfig"`
		Managed    map[string]string `json:"managed"`
	}
	if err := json.Unmarshal(call.stdin, &body); err != nil {
		t.Fatalf("stdin is not the expected JSON: %v", err)
	}
	if !strings.Contains(body.Kubeconfig, tokenValue) || body.Managed["context"] != "kind-resilience-research" || body.Managed["expiresAt"] != "2026-10-19T20:00:00Z" {
		t.Fatalf("stdin payload wrong: %+v", body)
	}
	if !st.Connected || !st.Managed || st.State != "ok" {
		t.Fatalf("status = %+v", st)
	}
}

func TestStatusAndDisconnectUseFixedArgumentsAndNoStdin(t *testing.T) {
	d := &dockerFake{containers: []string{containerA}, execOut: `{"ok": true, "connected": false}`}
	if _, err := newExec(d).Status(ctx); err != nil {
		t.Fatal(err)
	}
	if got := d.execCall(t).args[len(d.execCall(t).args)-1]; got != "--status" || d.execCall(t).stdin != nil {
		t.Fatalf("status call: %v stdin %v", d.execCall(t).args, d.execCall(t).stdin)
	}
	d2 := &dockerFake{containers: []string{containerA}, execOut: `{"ok": true, "connected": false}`}
	if err := newExec(d2).Disconnect(ctx); err != nil {
		t.Fatal(err)
	}
	if got := d2.execCall(t).args[len(d2.execCall(t).args)-1]; got != "--disconnect" {
		t.Fatalf("disconnect call: %v", d2.execCall(t).args)
	}
}

func TestOnlyARunningLabelledContainerIsUsed(t *testing.T) {
	d := &dockerFake{containers: []string{containerA}, execOut: okStatus}
	_, _ = newExec(d).Status(ctx)
	ps := strings.Join(d.calls[0].args, " ")
	for _, want := range []string{"label=com.docker.compose.project=k8s-resilience-engine-desktop-extension", "label=com.docker.compose.service=resilience-engine", "status=running"} {
		if !strings.Contains(ps, want) {
			t.Fatalf("container lookup lacks %q: %s", want, ps)
		}
	}
}

func TestNoContainerManyContainersOrAMalformedIdAreRefusedBeforeAnythingIsSent(t *testing.T) {
	for name, ids := range map[string][]string{
		"none":      {},
		"two":       {containerA, "f6e5d4c3b2a1"},
		"injection": {"abc; rm -rf /"},
		"uppercase": {"A1B2C3D4E5F6"},
		"too short": {"abc"},
	} {
		t.Run(name, func(t *testing.T) {
			d := &dockerFake{containers: ids, execOut: okStatus}
			_, err := newExec(d).Install(ctx, request())
			if !errors.Is(err, ErrUnavailable) {
				t.Fatalf("err = %v", err)
			}
			for _, c := range d.calls {
				if c.args[0] == "exec" {
					t.Fatal("nothing may be executed or sent when the container is not exactly one valid id")
				}
			}
		})
	}
}

func TestABackendRefusalIsReportedWithItsOwnWording(t *testing.T) {
	d := &dockerFake{containers: []string{containerA}, execExit: 1, execOut: `{"ok": false, "status": 422, "error": "This credential can change the cluster (create pods)."}`}
	_, err := newExec(d).Install(ctx, request())
	var rej *RejectedError
	if !errors.As(err, &rej) || !strings.Contains(rej.Message, "can change the cluster") {
		t.Fatalf("err = %v", err)
	}
}

func TestAnUnreadableAnswerIsNotEchoed(t *testing.T) {
	d := &dockerFake{containers: []string{containerA}, execOut: "Traceback ... " + tokenValue}
	_, err := newExec(d).Install(ctx, request())
	if !errors.Is(err, ErrUnavailable) || strings.Contains(err.Error(), tokenValue) || strings.Contains(err.Error(), "Traceback") {
		t.Fatalf("err must be generic: %v", err)
	}
}

func TestAMissingDockerCLIIsUnavailable(t *testing.T) {
	d := DockerExec{Runner: &dockerFake{}, FindDocker: func() (string, error) { return "", errors.New("nope") }}
	if _, err := d.Status(ctx); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("err = %v", err)
	}
}

func TestFindDockerPrefersPathThenKnownLocationsPerOS(t *testing.T) {
	notOnPath := func(string) (string, error) { return "", errors.New("no") }
	exists := map[string]bool{}
	stat := func(p string) (os.FileInfo, error) {
		if exists[filepath.ToSlash(p)] { // the code joins with the host's separator; the fixtures use "/"
			return fakeInfo{}, nil
		}
		return nil, os.ErrNotExist
	}
	if p, _ := FindDocker(func(string) (string, error) { return "/on/path/docker", nil }, stat, "darwin", "/Users/x"); p != "/on/path/docker" {
		t.Fatalf("PATH must win, got %s", p)
	}
	exists[filepath.ToSlash("/Users/x/.docker/bin/docker")] = true
	if p, err := FindDocker(notOnPath, stat, "darwin", "/Users/x"); err != nil || filepath.ToSlash(p) != "/Users/x/.docker/bin/docker" {
		t.Fatalf("darwin: %s %v", p, err)
	}
	exists[filepath.ToSlash(`C:\Program Files\Docker\Docker\resources\bin\docker.exe`)] = true
	if p, err := FindDocker(notOnPath, stat, "windows", `C:\Users\x`); err != nil || !strings.HasSuffix(p, "docker.exe") {
		t.Fatalf("windows: %s %v", p, err)
	}
	exists[filepath.ToSlash("/opt/docker-desktop/bin/docker")] = true
	if p, err := FindDocker(notOnPath, stat, "linux", "/home/x"); err != nil || filepath.ToSlash(p) != "/opt/docker-desktop/bin/docker" {
		t.Fatalf("linux: %s %v", p, err)
	}
	if _, err := FindDocker(notOnPath, func(string) (os.FileInfo, error) { return nil, os.ErrNotExist }, "linux", "/home/x"); err == nil {
		t.Fatal("must fail when docker is nowhere")
	}
}

type fakeInfo struct{ os.FileInfo }

func (fakeInfo) IsDir() bool { return false }

// ---------------------------------------------------------------- the mirrored socket

// listenLong binds a unix socket inside a directory whose full socket path exceeds the macOS limit,
// which is exactly the real situation (Docker Desktop's path is 120 bytes). It binds by relative
// name from inside the directory, the same trick a client cannot use concurrently.
func listenLong(t *testing.T, handler http.Handler) (dir string) {
	t.Helper()
	dir = filepath.Join(t.TempDir(), strings.Repeat("d", 70))
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if full := filepath.Join(dir, socketName); len(full) <= maxSocketPath {
		t.Fatalf("test setup: path is only %d bytes, it must exceed %d to reproduce the macOS limit", len(full), maxSocketPath)
	}
	t.Chdir(dir)
	ln, err := net.Listen("unix", "./"+socketName)
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	t.Cleanup(func() { _ = ln.Close() })
	go func() { _ = http.Serve(ln, handler) }()
	return dir
}

func TestSocketReachesAServerWhosePathIsTooLongForTheOS(t *testing.T) {
	var gotBody []byte
	var gotMethod string
	dir := listenLong(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotBody, _ = io.ReadAll(r.Body)
		_, _ = w.Write([]byte(`{"connected": true, "managed": true, "context": "kind-resilience-research", "state": "ok"}`))
	}))
	t.Chdir(t.TempDir()) // prove it does not rely on the working directory
	s := &Socket{Dir: dir, File: socketName}

	st, err := s.Install(ctx, request())
	if err != nil {
		t.Fatal(err)
	}
	if gotMethod != http.MethodPut || !strings.Contains(string(gotBody), tokenValue) {
		t.Fatalf("method %s body %q", gotMethod, gotBody)
	}
	if !st.Connected || st.Context != "kind-resilience-research" {
		t.Fatalf("status = %+v", st)
	}
}

func TestSocketStatusAndDisconnectUseTheRightMethods(t *testing.T) {
	var methods []string
	dir := listenLong(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		methods = append(methods, r.Method+" "+r.URL.Path)
		_, _ = w.Write([]byte(`{"connected": false}`))
	}))
	s := &Socket{Dir: dir, File: socketName}
	if _, err := s.Status(ctx); err != nil {
		t.Fatal(err)
	}
	if err := s.Disconnect(ctx); err != nil {
		t.Fatal(err)
	}
	if strings.Join(methods, ",") != "GET /_extension/cluster,DELETE /_extension/cluster" {
		t.Fatalf("calls = %v", methods)
	}
}

func TestSocketMapsABackendRefusalToRejectedError(t *testing.T) {
	dir := listenLong(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(422)
		_, _ = w.Write([]byte(`{"detail": "A managed connection must use host.docker.internal as the API server."}`))
	}))
	_, err := (&Socket{Dir: dir, File: socketName}).Install(ctx, request())
	var rej *RejectedError
	if !errors.As(err, &rej) || !strings.Contains(rej.Message, "host.docker.internal") {
		t.Fatalf("err = %v", err)
	}
	if strings.Contains(err.Error(), tokenValue) {
		t.Fatal("error leaked the token")
	}
}

func TestSocketThatIsNotThereIsUnavailable(t *testing.T) {
	s := &Socket{Dir: t.TempDir(), File: socketName}
	if _, err := s.Status(ctx); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("err = %v", err)
	}
}

func TestSocketIsOnlyOfferedWhereItWasVerified(t *testing.T) {
	s, err := NewSocket("darwin", "/Users/x")
	if err != nil || !strings.HasSuffix(filepath.ToSlash(s.Dir), "Library/Containers/com.docker.docker/Data/ext-sockets/k8s-resilience-engine") {
		t.Fatalf("darwin: %+v %v", s, err)
	}
	for _, goos := range []string{"windows", "linux", "freebsd"} {
		if _, err := NewSocket(goos, "/home/x"); !errors.Is(err, ErrUnverifiedPlatform) {
			t.Errorf("%s must be refused as unverified, got %v", goos, err)
		}
	}
}

func TestDefaultsPutTheSocketFirstOnlyOnMacAndAlwaysEndWithDockerExec(t *testing.T) {
	d := DockerExec{}
	mac := Defaults("darwin", "/Users/x", d)
	if len(mac) != 2 || mac[0].Name() != "socket" || mac[1].Name() != "docker-exec" {
		t.Fatalf("darwin = %v", names(mac))
	}
	for _, goos := range []string{"windows", "linux"} {
		got := Defaults(goos, "/h", d)
		if len(got) != 1 || got[0].Name() != "docker-exec" {
			t.Fatalf("%s = %v", goos, names(got))
		}
	}
}

func names(bs []Backend) (out []string) {
	for _, b := range bs {
		out = append(out, b.Name())
	}
	return
}

// ---------------------------------------------------------------- choosing a transport

type stub struct {
	name string
	err  error
}

func (s stub) Name() string                                            { return s.name }
func (s stub) Status(context.Context) (Status, error)                  { return Status{Connected: true}, s.err }
func (s stub) Install(context.Context, InstallRequest) (Status, error) { return Status{}, nil }
func (s stub) Disconnect(context.Context) error                        { return nil }

func TestPickUsesTheFirstTransportThatWorks(t *testing.T) {
	b, _, err := Pick(ctx, []Backend{stub{name: "a", err: ErrUnavailable}, stub{name: "b"}, stub{name: "c"}})
	if err != nil || b.Name() != "b" {
		t.Fatalf("picked %v err %v", b, err)
	}
}

func TestPickReportsUnavailableWhenNoneWork(t *testing.T) {
	_, _, err := Pick(ctx, []Backend{stub{name: "a", err: errors.New("x")}, stub{name: "b", err: errors.New("y")}})
	if !errors.Is(err, ErrUnavailable) {
		t.Fatalf("err = %v", err)
	}
	if _, _, err := Pick(ctx, nil); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("no candidates: %v", err)
	}
}

// A Docker that is merely slow (a busy Docker Desktop VM) must not look like a Docker that is down:
// the caller keeps the cause so it can say "too slow" and retry, instead of "not running".
func TestASlowDockerKeepsTheDeadlineAsTheCause(t *testing.T) {
	for name, d := range map[string]*dockerFake{
		"ps":   {psErr: context.DeadlineExceeded},
		"exec": {containers: []string{containerA}, execErr: context.DeadlineExceeded},
	} {
		_, err := newExec(d).Status(ctx)
		if !errors.Is(err, ErrUnavailable) || !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("%s: err must be unavailable AND a deadline, got %v", name, err)
		}
	}
}

func TestADockerThatFailsForAnotherReasonIsNotCalledSlow(t *testing.T) {
	d := &dockerFake{containers: []string{containerA}, execErr: errors.New("boom")}
	if _, err := newExec(d).Status(ctx); errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("a plain failure must not be reported as a timeout: %v", err)
	}
}

func TestASocketThatNeverAnswersKeepsTheDeadlineAsTheCause(t *testing.T) {
	// A short base keeps the socket path under the unix limit (macOS: 104 bytes); where there is no /tmp
	// (Windows) the default temporary folder is used.
	base := "/tmp"
	if st, statErr := os.Stat(base); statErr != nil || !st.IsDir() {
		base = ""
	}
	dir, err := os.MkdirTemp(base, "slow")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	l, err := net.Listen("unix", filepath.Join(dir, "s.sock"))
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	go func() {
		for {
			c, err := l.Accept()
			if err != nil {
				return
			}
			defer c.Close() // accepts and then says nothing
		}
	}()
	s := &Socket{Dir: dir, File: "s.sock", Timeout: 150 * time.Millisecond}
	_, err = s.Status(ctx)
	if !errors.Is(err, ErrUnavailable) || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("err must be unavailable AND a deadline, got %v", err)
	}
}
