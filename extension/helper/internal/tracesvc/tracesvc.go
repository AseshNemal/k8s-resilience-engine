// Package tracesvc starts, stops and reports on the trace service (Jaeger) that the Workflow dependencies
// page reads. It exists so that one click can bring the trace service up; it is not a general container
// manager, and it is deliberately small:
//
//   - every argument given to Docker is a constant in this file; nothing from the page, the request or the
//     environment reaches a Docker command line;
//   - it only ever starts or stops the one container it names (ContainerName), so a Jaeger that someone
//     else started (for example from deploy/docker-compose.jaeger.yml) is reported but never touched;
//   - the container publishes its ports on 127.0.0.1 only, and is limited in memory and in stored traces;
//   - it never starts an application, sends traffic, or changes a Kubernetes workload.
//
// Docker's own error text is never printed: a result carries a fixed state and a fixed message.
package tracesvc

import (
	"context"
	"strings"
	"time"

	"resilience-engine/helper/internal/execx"
	"resilience-engine/helper/internal/flow"
)

const (
	// ContainerName is the only container this package creates, starts or stops.
	ContainerName = "resilience-trace-jaeger"
	// Image is pinned to the version in deploy/docker-compose.jaeger.yml.
	Image = "jaegertracing/all-in-one:1.56"
	// QueryURL answers 200 when a Jaeger query service is up, whoever started it.
	QueryURL = "http://127.0.0.1:16686/api/services"

	// Limits that keep it light: Jaeger's all-in-one keeps traces in memory.
	memoryLimit = "256m"
	maxTraces   = "5000"

	readyTimeout  = 45 * time.Second
	readyInterval = 500 * time.Millisecond
	// A container of ours that is up but does not answer is given this long before it is recreated.
	revivalWait = 10 * time.Second
	stopGrace   = "5"
)

// States are the machine-readable outcomes the extension UI turns into messages.
const (
	StateRunning         flow.State = "trace-running"          // up, and started by this helper
	StateRunningExternal flow.State = "trace-running-external" // up, started by someone else
	StateStopped         flow.State = "trace-stopped"          // our container exists and is not running
	StateNotCreated      flow.State = "trace-not-created"      // nothing answers and our container does not exist
	StateNotReady        flow.State = "trace-not-ready"        // started, but did not answer in time
	StatePortInUse       flow.State = "trace-port-in-use"      // something else holds the Jaeger ports
	StateStartFailed     flow.State = "trace-start-failed"     // Docker refused for another reason
	StateDockerMissing   flow.State = "docker-unavailable"     // the Docker CLI could not be found or run
)

// Deps are the host operations, behind interfaces so tests never touch Docker or the network.
type Deps struct {
	Runner     execx.Runner
	FindDocker func() (string, error)
	// Probe reports whether a Jaeger query service answers on this machine.
	Probe func(ctx context.Context) bool
	// Sleep waits between probes; nil means time.Sleep honouring the context.
	Sleep func(ctx context.Context, d time.Duration)
}

func (d Deps) sleep(ctx context.Context, wait time.Duration) {
	if d.Sleep != nil {
		d.Sleep(ctx, wait)
		return
	}
	select {
	case <-ctx.Done():
	case <-time.After(wait):
	}
}

// runArgs is the whole creation command. It is a fixed list.
func runArgs() []string {
	return []string{
		"run", "-d",
		"--name", ContainerName,
		"--restart", "unless-stopped",
		"--memory", memoryLimit,
		"--label", "com.sliit.resilience-engine=trace-service",
		"-e", "COLLECTOR_OTLP_ENABLED=true",
		"-e", "MEMORY_MAX_TRACES=" + maxTraces,
		"-e", "LOG_LEVEL=warn",
		"-p", "127.0.0.1:16686:16686",
		"-p", "127.0.0.1:4317:4317",
		"-p", "127.0.0.1:4318:4318",
		Image,
	}
}

func result(command string, state flow.State, ok bool, message string) flow.Result {
	return flow.Result{OK: ok, Command: command, State: state, Message: message}
}

// containerState is Docker's state for our container: "running", "exited", "created" ... or "" when it
// does not exist. The name filter is anchored so a similarly named container is never matched.
func containerState(ctx context.Context, d Deps, docker string) (string, error) {
	res, err := d.Runner.Run(ctx, docker, []string{
		"ps", "-a", "--filter", "name=^/" + ContainerName + "$", "--format", "{{.State}}",
	}, nil)
	if err != nil || res.ExitCode != 0 {
		return "", errDocker
	}
	return strings.TrimSpace(string(res.Stdout)), nil
}

type dockerError struct{}

func (dockerError) Error() string { return "docker unavailable" }

var errDocker = dockerError{}

func docker(d Deps) (string, bool) {
	if d.FindDocker == nil {
		return "", false
	}
	path, err := d.FindDocker()
	return path, err == nil && path != ""
}

// Status reports whether the trace service answers, and whether this helper started it.
func Status(ctx context.Context, d Deps) flow.Result { return status(ctx, d, "trace-status") }

func status(ctx context.Context, d Deps, command string) flow.Result {
	answering := d.Probe(ctx)
	path, found := docker(d)
	state := ""
	if found {
		var err error
		if state, err = containerState(ctx, d, path); err != nil {
			found = false
		}
	}
	switch {
	case answering && found && state == "running":
		return result(command, StateRunning, true, "The trace service is running.")
	case answering:
		// Up, but not (provably) ours: report it, never claim it.
		return result(command, StateRunningExternal, true,
			"A trace service is running, but it was not started by the extension. It is left as it is.")
	case !found:
		return result(command, StateDockerMissing, false,
			"Docker could not be reached, so the trace service cannot be started from here.")
	case state == "running":
		return result(command, StateNotReady, false, "The trace service is starting and does not answer yet.")
	case state != "":
		return result(command, StateStopped, true, "The trace service is stopped.")
	default:
		return result(command, StateNotCreated, true, "The trace service has not been started yet.")
	}
}

// Start brings the trace service up and waits until it answers. If one already answers, nothing is done.
func Start(ctx context.Context, d Deps) flow.Result {
	const command = "trace-start"
	current := status(ctx, d, command)
	switch current.State {
	case StateRunning, StateRunningExternal, StateDockerMissing:
		return current
	case StateStopped:
		path, _ := docker(d)
		res, err := d.Runner.Run(ctx, path, []string{"start", ContainerName}, nil)
		if err != nil || res.ExitCode != 0 {
			if portTaken(res) {
				return portInUse(command)
			}
			return result(command, StateStartFailed, false, "Docker could not start the trace service.")
		}
	case StateNotCreated:
		if failed := create(ctx, d, command); failed != nil {
			return *failed
		}
	case StateNotReady:
		// Ours, up, and silent. It may only be starting, so give it a short while; if it stays silent (for
		// example after a failed earlier start left it without its ports) recreate it once.
		if waitFor(ctx, d, int(revivalWait/readyInterval)) {
			return result(command, StateRunning, true, "The trace service is running.")
		}
		path, _ := docker(d)
		_, _ = d.Runner.Run(ctx, path, []string{"rm", "-f", ContainerName}, nil)
		if failed := create(ctx, d, command); failed != nil {
			return *failed
		}
	}
	return waitReady(ctx, d, command)
}

// create makes the container. If Docker refuses, the half-made container it may leave behind (with no
// published ports) is removed, so the next try starts clean. It returns nil on success.
func create(ctx context.Context, d Deps, command string) *flow.Result {
	path, _ := docker(d)
	res, err := d.Runner.Run(ctx, path, runArgs(), nil)
	if err == nil && res.ExitCode == 0 {
		return nil
	}
	_, _ = d.Runner.Run(ctx, path, []string{"rm", "-f", ContainerName}, nil)
	failed := result(command, StateStartFailed, false, "Docker could not start the trace service.")
	if portTaken(res) {
		failed = portInUse(command)
	}
	return &failed
}

func portInUse(command string) flow.Result {
	return result(command, StatePortInUse, false,
		"Another program is using the trace service's ports (16686, 4317 or 4318). Stop it, then try again.")
}

// waitFor probes up to attempts times, sleeping between probes, and reports whether it ever answered.
func waitFor(ctx context.Context, d Deps, attempts int) bool {
	for attempt := 0; attempt < attempts; attempt++ {
		if d.Probe(ctx) {
			return true
		}
		if ctx.Err() != nil {
			return false
		}
		d.sleep(ctx, readyInterval)
	}
	return false
}

func waitReady(ctx context.Context, d Deps, command string) flow.Result {
	// Counted in attempts, not read from a clock, so the wait is the same everywhere and testable.
	if waitFor(ctx, d, int(readyTimeout/readyInterval)) {
		return result(command, StateRunning, true, "The trace service is running.")
	}
	return result(command, StateNotReady, false,
		"The trace service was started but is not answering yet. Give it a moment, then try again.")
}

// Stop stops the trace service this helper started. A Jaeger started by anything else is left alone.
func Stop(ctx context.Context, d Deps) flow.Result {
	const command = "trace-stop"
	current := status(ctx, d, command)
	switch current.State {
	case StateRunningExternal, StateDockerMissing, StateStopped, StateNotCreated:
		return current
	}
	path, _ := docker(d)
	if !succeeded(d.Runner.Run(ctx, path, []string{"stop", "-t", stopGrace, ContainerName}, nil)) {
		return result(command, StateStartFailed, false, "Docker could not stop the trace service.")
	}
	return result(command, StateStopped, true, "The trace service is stopped.")
}

func succeeded(res execx.Result, err error) bool { return err == nil && res.ExitCode == 0 }

// portTaken recognises Docker's "port is already allocated" and "address already in use" failures, so the
// person is told what to do. Only the verdict is used; the text itself is never returned.
func portTaken(res execx.Result) bool {
	text := strings.ToLower(string(res.Stderr) + string(res.Stdout))
	return strings.Contains(text, "port is already allocated") || strings.Contains(text, "address already in use") ||
		strings.Contains(text, "ports are not available")
}
