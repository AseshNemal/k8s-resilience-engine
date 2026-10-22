package tracesvc

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"resilience-engine/helper/internal/agent"
	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/flow"
)

// This file is the "turn it on once" part. Three rules keep it inside the helper's safety model:
//
//   - Enable asks in a native dialog the extension page cannot answer, and only then starts anything;
//   - after that, Ensure may only restart or recreate this package's own container (ContainerName), never
//     anything else, and never more than once per recreateCooldown, so a container that keeps failing cannot
//     keep a small machine busy;
//   - none of it touches Kubernetes, sends a trace, or changes an application.

const (
	// recreateCooldown is how long Ensure waits after recreating our container before it may do so again.
	recreateCooldown = 10 * time.Minute

	// PodHost is the name a Pod would use for this computer. It is tested, never assumed to work.
	PodHost = "host.docker.internal"

	otlpHTTPPort = "4318"
	otlpGRPCPort = "4317"

	// maxNodes bounds how many Kubernetes node containers are asked, so a large cluster cannot make one
	// check slow or expensive.
	maxNodes = 4
)

// Marker is the one fact the helper keeps: the person turned tracing on (and when our container was last
// recreated). It holds no address, credential or trace.
type Marker struct {
	Enabled      bool   `json:"enabled"`
	EnabledAt    string `json:"enabledAt,omitempty"`
	LastRecreate string `json:"lastRecreate,omitempty"`
}

// Store keeps the Marker. Load returns the zero Marker (not enabled) when nothing was saved.
type Store interface {
	Load() (Marker, error)
	Save(Marker) error
}

// FileStore keeps the Marker in one small private file in the user's configuration directory.
type FileStore struct{ Path string }

func (f FileStore) Load() (Marker, error) {
	raw, err := os.ReadFile(f.Path)
	if errors.Is(err, os.ErrNotExist) {
		return Marker{}, nil
	}
	if err != nil {
		return Marker{}, err
	}
	var m Marker
	if err := json.Unmarshal(raw, &m); err != nil {
		return Marker{}, nil // an unreadable file means "not turned on": the person is simply asked again
	}
	return m, nil
}

func (f FileStore) Save(m Marker) error {
	if err := os.MkdirAll(filepath.Dir(f.Path), 0o700); err != nil {
		return err
	}
	raw, err := json.Marshal(m)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(f.Path), ".tracing-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(raw); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmp.Name(), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), f.Path)
}

func (d Deps) marker() Marker {
	if d.Store == nil {
		return Marker{}
	}
	m, _ := d.Store.Load()
	return m
}

func (d Deps) save(m Marker) {
	if d.Store != nil {
		_ = d.Store.Save(m)
	}
}

// turnOff records that the person stopped tracing, so the automatic restart stops too.
func turnOff(d Deps) {
	m := d.marker()
	if m.Enabled {
		m.Enabled = false
		d.save(m)
	}
}

// attach adds whether tracing is turned on to a result.
func attach(d Deps, r flow.Result) flow.Result {
	r.Trace = &flow.TraceDetails{Enabled: d.marker().Enabled}
	return r
}

func enableDialog(agentLine string) approve.Request {
	body := "This starts one container on this computer, " + ContainerName + " (" + Image + "), that collects the traces " +
		"your applications send. Nothing else is installed.\n\n" +
		"  • It is limited to 256 MB, keeps at most 5000 traces in memory, and listens on this computer only " +
		"(ports 16686, 4317 and 4318).\n" +
		"  • Docker Desktop restarts it with Docker Desktop. " + agentLine + "\n" +
		"  • It receives only what your applications send it. The extension never sends or invents a trace.\n" +
		"  • Your Kubernetes cluster and your applications are not changed. Setting an application up to send " +
		"traces is a separate step that asks again.\n\n" +
		"You can turn it off from the extension at any time."
	return approve.Request{Title: "Resilience Engine: turn on the trace service?", Body: body, Timeout: 90 * time.Second}
}

func (d Deps) agentLine() string {
	if d.Agent == nil || !d.Agent.Supported() {
		return "While the extension is open it also checks that it is running."
	}
	return "A small background job (" + d.Agent.Describe() + ") also checks every few minutes that it is running."
}

// Enable is the one-time step. If a trace service already answers (ours or someone else's) it is only reported:
// nothing is asked, started or recorded for a service this package does not own. Otherwise the person is asked
// once; on approval the container is started, the choice is remembered and the background job is installed.
func Enable(ctx context.Context, d Deps) flow.Result {
	const command = "trace-enable"
	current := status(ctx, d, command)
	switch current.State {
	case StateRunningExternal, StateDockerMissing:
		return attach(d, current)
	}
	if d.marker().Enabled {
		return attach(d, ensure(ctx, d, command)) // already approved: repair, never ask again
	}
	if d.Prompter == nil || d.Store == nil {
		return attach(d, result(command, flow.StateApprovalUnavailable, false, "No approval dialog is available on this system."))
	}

	decision, err := d.Prompter.Confirm(ctx, enableDialog(d.agentLine()))
	switch {
	case errors.Is(err, approve.ErrNoApprovalChannel), errors.Is(err, approve.ErrUnsupportedPlatform):
		return attach(d, result(command, flow.StateApprovalUnavailable, false, "No approval dialog is available on this system."))
	case err != nil:
		return attach(d, result(command, flow.StateError, false, "The approval dialog could not be shown."))
	}
	switch decision {
	case approve.Approved:
	case approve.TimedOut:
		return attach(d, result(command, StateApprovalTimedOut, false, "No answer was given, so nothing was started."))
	default:
		return attach(d, result(command, flow.StateDeclined, false, "You declined, so nothing was started."))
	}

	started := start(ctx, d, command, func() bool { return true })
	if !started.OK {
		return attach(d, started)
	}
	d.save(Marker{Enabled: true, EnabledAt: d.now().UTC().Format(time.RFC3339)})
	started = attach(d, started)
	started.Background = d.startAgent(ctx)
	return started
}

// startAgent installs the per-user job after approval. A failure is reported, never fatal: the service is
// running, and the extension page keeps it checked while it is open.
func (d Deps) startAgent(ctx context.Context) string {
	if d.Agent == nil {
		return ""
	}
	if !d.Agent.Supported() {
		return string(agent.Unsupported)
	}
	if st, err := d.Agent.Status(ctx); err == nil && st == agent.On {
		return string(agent.On)
	}
	if err := d.Agent.Install(ctx); err != nil {
		return string(agent.Off)
	}
	return string(agent.On)
}

// Ensure is what runs automatically (from the page and from the background job). It does nothing unless the
// person turned tracing on, and then it only brings our own container back.
func Ensure(ctx context.Context, d Deps) flow.Result {
	return attach(d, ensure(ctx, d, "trace-ensure"))
}

func ensure(ctx context.Context, d Deps, command string) flow.Result {
	m := d.marker()
	if !m.Enabled {
		return result(command, StateNotEnabled, true, "Tracing is not turned on, so nothing was started.")
	}
	current := status(ctx, d, command)
	switch current.State {
	case StateRunning, StateRunningExternal, StateDockerMissing:
		return current
	}
	return start(ctx, d, command, func() bool {
		if last, err := time.Parse(time.RFC3339, m.LastRecreate); err == nil && d.now().Sub(last) < recreateCooldown {
			return false
		}
		m.LastRecreate = d.now().UTC().Format(time.RFC3339)
		d.save(m)
		return true
	})
}

// Validate tests the addresses an application and the extension use. It only opens connections (no bytes are
// sent), so it can never create a trace.
func Validate(ctx context.Context, d Deps) flow.Result {
	const command = "trace-validate"
	reach := func(ok bool) *bool { return &ok }
	endpoints := []flow.Endpoint{
		{Name: "query", Address: QueryURL, Reachable: reach(d.Probe != nil && d.Probe(ctx)), Evidence: "host"},
		{Name: "otlp-http", Address: "127.0.0.1:" + otlpHTTPPort, Reachable: reach(dial(ctx, d, "127.0.0.1:"+otlpHTTPPort)), Evidence: "host"},
		{Name: "otlp-grpc", Address: "127.0.0.1:" + otlpGRPCPort, Reachable: reach(dial(ctx, d, "127.0.0.1:"+otlpGRPCPort)), Evidence: "host"},
	}
	hostOK := true
	for _, e := range endpoints {
		hostOK = hostOK && *e.Reachable
	}
	endpoints = append(endpoints, podsReach(ctx, d))

	r := result(command, StateEndpointsOK, true, "The trace service's query and OTLP receivers answer on this computer.")
	if !hostOK {
		r = result(command, StateEndpointsFailed, false, "The trace service does not answer on every address, so applications cannot send traces yet.")
	}
	r = attach(d, r)
	r.Trace.Endpoints = endpoints
	return r
}

func dial(ctx context.Context, d Deps, address string) bool {
	return d.Dial != nil && d.Dial(ctx, address)
}

var nodeName = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$`)

// podsReach asks whether the Kubernetes nodes can open a connection to the OTLP receiver at PodHost. Pods
// leaving a node use the node's network path, so this is evidence for them, but it is not a Pod doing it: a
// Pod's own DNS and network policy are not exercised, and creating a Pod would change the cluster, which this
// never does. The result says so (Evidence "node") and stays unknown (nil) whenever the test cannot be made.
func podsReach(ctx context.Context, d Deps) flow.Endpoint {
	ep := flow.Endpoint{Name: "pods-otlp-http", Address: PodHost + ":" + otlpHTTPPort, Evidence: "node"}
	path, found := docker(d)
	if !found {
		ep.Note = "Docker could not be reached, so reachability from Pods was not tested."
		return ep
	}
	res, err := d.Runner.Run(ctx, path, []string{"ps", "--filter", "label=io.x-k8s.kind.role", "--format", "{{.Names}}"}, nil)
	if err != nil || res.ExitCode != 0 {
		ep.Note = "Docker could not list the Kubernetes nodes, so reachability from Pods was not tested."
		return ep
	}
	var nodes []string
	for _, line := range strings.Fields(string(res.Stdout)) {
		if nodeName.MatchString(line) && len(nodes) < maxNodes {
			nodes = append(nodes, line)
		}
	}
	if len(nodes) == 0 {
		ep.Note = "No Kubernetes node containers were found on this computer, so reachability from Pods was not tested."
		return ep
	}
	failed, unknown := 0, 0
	for _, node := range nodes {
		// The address is a constant of this package; only the node's own name (checked above) varies.
		out, err := d.Runner.Run(ctx, path, []string{"exec", node, "timeout", "3", "bash", "-c", "exec 3<>/dev/tcp/" + PodHost + "/" + otlpHTTPPort}, nil)
		switch {
		case err != nil:
			unknown++
		case out.ExitCode == 0:
		case out.ExitCode == 126 || out.ExitCode == 127: // no bash or no timeout in the node: nothing was tested
			unknown++
		default:
			failed++
		}
	}
	switch {
	case failed > 0:
		no := false
		ep.Reachable = &no
		ep.Note = "A Kubernetes node could not reach this address. Applications in the cluster cannot send traces to it as things are."
	case unknown > 0:
		ep.Note = "The nodes could not be asked, so reachability from Pods is unknown."
	default:
		yes := true
		ep.Reachable = &yes
		ep.Note = "Every Kubernetes node reached this address. A Pod has not been tested."
	}
	return ep
}
