package main

import (
	"context"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"syscall"
	"time"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/flow"
	"resilience-engine/helper/internal/lock"
	"resilience-engine/helper/internal/traceinstall"
)

// The commands that change the cluster for the Bookinfo tracing setup. Each one:
//
//   - takes exactly one argument, the kubeconfig context, and nothing else from the page;
//   - works only on a verified local cluster, with the person's own kubeconfig credential held in memory for the
//     length of the command (never stored, printed or sent anywhere);
//   - (apply, undo and traffic) changes nothing before a native approval dialog, which the page cannot see or answer.
//     Verify and status are read-only: they create no Pod and ask nothing;
//   - prints one JSON object built from fixed wording and names the setup chose.
const (
	cmdApply   = "trace-bookinfo-apply"
	cmdUndo    = "trace-bookinfo-undo"
	cmdVerify  = "trace-bookinfo-verify"
	cmdTraffic = "trace-bookinfo-traffic"
	cmdStatus  = "trace-bookinfo-status"
)

func isBookinfoInstall(command string) bool {
	return command == cmdApply || command == cmdUndo || command == cmdVerify || command == cmdTraffic || command == cmdStatus
}

// installDeps and setupLock are replaced in tests, so no test touches the user's directories or a real cluster.
var (
	installDeps = realInstall
	setupLock   = realSetupLock
	installDir  = realInstallDir
)

func realInstallDir() string {
	dir, err := os.UserConfigDir()
	if err != nil {
		dir = os.TempDir()
	}
	return filepath.Join(dir, "resilience-engine")
}

func realInstall(cluster traceinstall.Cluster) traceinstall.Deps {
	base := installDir()
	return traceinstall.Deps{
		Cluster:  cluster,
		Prompter: approve.Default(),
		Store:    traceinstall.Store{Dir: filepath.Join(base, "bookinfo-tracing")},
		Tools: traceinstall.ToolEnv{GOOS: runtime.GOOS, GOARCH: runtime.GOARCH, Dir: filepath.Join(base, "tools", "istioctl"),
			Fetcher: traceinstall.NewHTTPFetcher(), Exec: traceinstall.RealExec{}, Getenv: os.Getenv},
		Host:       bookinfoDeps(),
		Timeouts:   traceinstall.DefaultTimeouts(),
		Thresholds: bookinfotrace.DefaultThresholds(),
	}
}

// realSetupLock lets only one setup command run at a time, across processes. Its stale age is above the longest
// command, so a crash frees it but a running command never loses it.
func realSetupLock() lock.Lock {
	return &lock.File{Path: filepath.Join(installDir(), "bookinfo-tracing.lock"), Stale: 90 * time.Minute}
}

// installOutput is what these commands print: the usual Result plus the pieces the page shows.
type installOutput struct {
	flow.Result
	Plan   *bookinfotrace.PlanResult  `json:"plan,omitempty"`
	Setup  *traceinstall.Summary      `json:"setup,omitempty"`
	Diffs  []bookinfotrace.Diff       `json:"diffs,omitempty"`
	Verify *traceinstall.VerifyResult `json:"verify,omitempty"`
}

func fromOutcome(command, contextName string, o traceinstall.Outcome) installOutput {
	return installOutput{
		Result: flow.Result{OK: o.OK, Command: command, State: flow.State(o.State), Message: o.Message, Context: contextName},
		Plan:   o.Plan, Setup: o.Setup, Diffs: o.Diffs, Verify: o.Verify,
	}
}

var commandTimeout = map[string]time.Duration{cmdApply: 45 * time.Minute, cmdUndo: 35 * time.Minute, cmdVerify: 3 * time.Minute, cmdTraffic: 12 * time.Minute, cmdStatus: 15 * time.Second}

// bookinfoStatus reads the saved record for a context, without contacting the cluster, so the page can poll it while
// a setup is running. It is informational: Apply, Undo and Verify act only on the record of the cluster they reached.
func bookinfoStatus(name string) installOutput {
	rec, err := installDeps(nil).Store.LatestFor(name)
	if err != nil {
		return installOutput{Result: flow.Result{Command: cmdStatus, State: flow.State(traceinstall.StateRecordUnusable), Message: err.Error(), Context: name}}
	}
	return installOutput{Result: flow.Result{OK: true, Command: cmdStatus, State: flow.State(traceinstall.StateStatus), Context: name}, Setup: traceinstall.SummaryOf(rec)}
}

// bookinfoInstall runs apply, undo, traffic or verify.
func bookinfoInstall(ctx context.Context, d flow.Deps, command, name string) installOutput {
	release, err := setupLock().Acquire()
	if err != nil {
		return installOutput{Result: flow.Result{Command: command, State: flow.StateBusy, Message: "another tracing setup command is already running"}}
	}
	defer release()

	admin, refusal := flow.AdminCluster(ctx, d, command, name)
	if refusal != nil {
		return installOutput{Result: *refusal}
	}
	cluster, ok := admin.Client.(traceinstall.Cluster)
	if !ok {
		return installOutput{Result: flow.Result{Command: command, State: flow.StateError, Message: "this build cannot change a cluster"}}
	}
	identity, err := traceinstall.ResolveIdentity(ctx, cluster, admin.ContextName, admin.Server, admin.CASha256)
	if err != nil {
		return installOutput{Result: flow.Result{Command: command, State: flow.StateClusterUnreachable, Message: err.Error(), Context: admin.ContextName}}
	}
	deps := installDeps(cluster)
	params := traceinstall.Params{ContextName: admin.ContextName, Identity: identity}
	var outcome traceinstall.Outcome
	switch command {
	case cmdApply:
		outcome = traceinstall.Apply(ctx, deps, params)
	case cmdUndo:
		outcome = traceinstall.Undo(ctx, deps, params)
	case cmdTraffic:
		outcome = traceinstall.RunTraffic(ctx, deps, params)
	default:
		outcome = traceinstall.Verify(ctx, deps, params)
	}
	return fromOutcome(command, admin.ContextName, outcome)
}

// runBookinfoInstall is the entry from run(). Interrupting the command (Ctrl+C, Docker Desktop closing it) cancels the
// context; an Apply that is cancelled still rolls back what it created, with its own deadline.
func runBookinfoInstall(command, name string, deps func() flow.Deps) installOutput {
	if command == cmdStatus {
		return bookinfoStatus(name)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, commandTimeout[command])
	defer cancel()
	return bookinfoInstall(ctx, deps(), command, name)
}
