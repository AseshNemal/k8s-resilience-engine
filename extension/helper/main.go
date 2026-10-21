// resilience-helper is the host-side companion of the K8s Resilience Engine Docker Desktop extension.
//
// The extension UI launches it through Docker Desktop (ddClient.extension.host.cli.exec) to find
// local clusters, to set up read-only access once (after a native approval dialog), and to renew the
// short-lived credential. It keeps the user's administrator kubeconfig on the host: it hands the
// extension backend only a read-only token, and prints nothing but a JSON status. No token, no
// kubeconfig content and no error text from the cluster is ever printed or logged.
//
//	resilience-helper discover
//	resilience-helper status
//	resilience-helper authorize <context>
//	resilience-helper renew <context>
//	resilience-helper renew-managed
//	resilience-helper agent-status | agent-enable <context> | agent-disable
//	resilience-helper trace-status | trace-start | trace-stop
//	resilience-helper revoke <context>
//	resilience-helper version
package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"time"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/execx"
	"resilience-engine/helper/internal/flow"
	"resilience-engine/helper/internal/tracesvc"
	"resilience-engine/helper/internal/transport"
)

const version = "0.1.0"

// traceDeps is replaced in tests so no test ever reaches Docker or the network.
var traceDeps = realTrace

// Deadlines. Docker Desktop on a busy machine can need 30 seconds for a single `docker exec`, and one
// renewal makes several Docker and cluster calls, so a short limit turns "slow" into "never renews".
// Nothing waits on these: they only decide when a hung call is given up on and retried later.
const (
	renewTimeout  = 4 * time.Minute
	statusTimeout = time.Minute
)

func realDeps() flow.Deps {
	home, _ := os.UserHomeDir()
	docker := transport.DockerExec{
		Runner: execx.Real{},
		FindDocker: func() (string, error) {
			return transport.FindDocker(exec.LookPath, os.Stat, runtime.GOOS, home)
		},
	}
	return flow.Deps{
		Clusters: clusters.New(),
		Prompter: approve.Default(),
		Backends: func() []transport.Backend { return transport.Defaults(runtime.GOOS, home, docker) },
		Agent:    realAgent(home),
	}
}

// realTrace is the trace service's view of this machine: the Docker CLI, and a probe of the one address
// where Jaeger's query service listens.
func realTrace() tracesvc.Deps {
	home, _ := os.UserHomeDir()
	client := &http.Client{Timeout: 2 * time.Second}
	return tracesvc.Deps{
		Runner: execx.Real{},
		FindDocker: func() (string, error) {
			return transport.FindDocker(exec.LookPath, os.Stat, runtime.GOOS, home)
		},
		Probe: func(ctx context.Context) bool {
			req, err := http.NewRequestWithContext(ctx, http.MethodGet, tracesvc.QueryURL, nil)
			if err != nil {
				return false
			}
			resp, err := client.Do(req)
			if err != nil {
				return false
			}
			defer resp.Body.Close()
			return resp.StatusCode == http.StatusOK
		},
	}
}

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr, realDeps)) }

// run is main without the process: it returns the exit code and writes only JSON to stdout.
// Exit codes: 0 a result was printed (read ok/state in it), 1 internal error, 2 bad usage.
func run(args []string, stdout, stderr io.Writer, deps func() flow.Deps) (code int) {
	defer func() {
		if recover() != nil {
			// A panic message could contain a value from the run, so print nothing from it.
			emit(stdout, flow.Result{Command: "internal", State: flow.StateError, Message: "internal error"})
			code = 1
		}
	}()

	if len(args) == 0 {
		return usage(stderr)
	}
	command, rest := args[0], args[1:]
	needsContext := command == "authorize" || command == "renew" || command == "revoke" || command == "agent-enable"
	switch {
	case needsContext && len(rest) != 1:
		return usage(stderr)
	case !needsContext && len(rest) != 0:
		return usage(stderr)
	}

	var (
		timeout time.Duration
		do      func(context.Context, flow.Deps) flow.Result
	)
	switch command {
	case "discover":
		timeout, do = 60*time.Second, flow.Discover
	case "status":
		timeout, do = statusTimeout, flow.Status
	case "authorize":
		timeout, do = 5*time.Minute, func(ctx context.Context, d flow.Deps) flow.Result { return flow.Authorize(ctx, d, rest[0]) }
	case "renew":
		timeout, do = renewTimeout, func(ctx context.Context, d flow.Deps) flow.Result { return flow.Renew(ctx, d, rest[0]) }
	case "renew-managed":
		timeout, do = renewTimeout, flow.RenewManaged
	case "revoke":
		timeout, do = 5*time.Minute, func(ctx context.Context, d flow.Deps) flow.Result { return flow.Revoke(ctx, d, rest[0]) }
	case "agent-enable":
		timeout, do = 5*time.Minute, func(ctx context.Context, d flow.Deps) flow.Result { return flow.EnableBackground(ctx, d, rest[0]) }
	case "agent-status":
		timeout, do = 30*time.Second, flow.BackgroundStatus
	case "agent-disable":
		timeout, do = 30*time.Second, flow.DisableBackground
	case "trace-status":
		timeout, do = 30*time.Second, func(ctx context.Context, _ flow.Deps) flow.Result { return tracesvc.Status(ctx, traceDeps()) }
	case "trace-start":
		// The first start may pull the image, so this is the longest of the three.
		timeout, do = 5*time.Minute, func(ctx context.Context, _ flow.Deps) flow.Result { return tracesvc.Start(ctx, traceDeps()) }
	case "trace-stop":
		timeout, do = time.Minute, func(ctx context.Context, _ flow.Deps) flow.Result { return tracesvc.Stop(ctx, traceDeps()) }
	case "version":
		emit(stdout, map[string]any{"ok": true, "version": version, "platform": runtime.GOOS + "/" + runtime.GOARCH})
		return 0
	default:
		return usage(stderr)
	}

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	// Exit 0 whenever a result was produced, success or not: the JSON carries ok and state. Docker
	// Desktop's exec may discard the output of a process that exits non-zero, and "you declined" must
	// stay distinguishable from "the helper crashed". Non-zero is reserved for misuse (2) and panics (1).
	emit(stdout, flow.Slow(ctx, do(ctx, deps())))
	return 0
}

func emit(w io.Writer, v any) {
	_ = json.NewEncoder(w).Encode(v)
}

func usage(stderr io.Writer) int {
	_, _ = io.WriteString(stderr, "usage: resilience-helper discover | status | authorize <context> | renew <context> | renew-managed | agent-status | agent-enable <context> | agent-disable | trace-status | trace-start | trace-stop | revoke <context> | version\n")
	return 2
}
