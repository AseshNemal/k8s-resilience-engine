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
//	resilience-helper revoke <context>
//	resilience-helper version
package main

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"os/exec"
	"runtime"
	"time"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/execx"
	"resilience-engine/helper/internal/flow"
	"resilience-engine/helper/internal/transport"
)

const version = "0.1.0"

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
	}
}

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr, realDeps)) }

// run is main without the process: it returns the exit code and writes only JSON to stdout.
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
	needsContext := command == "authorize" || command == "renew" || command == "revoke"
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
		timeout, do = 30*time.Second, flow.Status
	case "authorize":
		timeout, do = 5*time.Minute, func(ctx context.Context, d flow.Deps) flow.Result { return flow.Authorize(ctx, d, rest[0]) }
	case "renew":
		timeout, do = 90*time.Second, func(ctx context.Context, d flow.Deps) flow.Result { return flow.Renew(ctx, d, rest[0]) }
	case "revoke":
		timeout, do = 5*time.Minute, func(ctx context.Context, d flow.Deps) flow.Result { return flow.Revoke(ctx, d, rest[0]) }
	case "version":
		emit(stdout, map[string]any{"ok": true, "version": version, "platform": runtime.GOOS + "/" + runtime.GOARCH})
		return 0
	default:
		return usage(stderr)
	}

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	result := do(ctx, deps())
	emit(stdout, result)
	if result.OK {
		return 0
	}
	return 1
}

func emit(w io.Writer, v any) {
	_ = json.NewEncoder(w).Encode(v)
}

func usage(stderr io.Writer) int {
	_, _ = io.WriteString(stderr, "usage: resilience-helper discover | status | authorize <context> | renew <context> | revoke <context> | version\n")
	return 2
}
