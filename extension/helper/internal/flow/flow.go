// Package flow orchestrates discover, authorize, renew and revoke. It is where the safety rules meet:
//
//   - authorize never changes a cluster before the user approved in a native dialog;
//   - renew never prompts, never writes to the cluster and never recreates revoked access: it only
//     issues a token, and only when the existing access is exactly what was approved;
//   - a token is only ever handed to the backend, never returned, printed or logged.
//
// Every Result is built from types that cannot hold a credential, so output cannot leak one.
package flow

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"strings"
	"time"

	"k8s.io/client-go/kubernetes"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/kubeconfig"
	"resilience-engine/helper/internal/rbac"
	"resilience-engine/helper/internal/token"
	"resilience-engine/helper/internal/transport"
)

// State is the machine-readable outcome the extension UI turns into a message.
type kubeClient = kubernetes.Interface

const minute = time.Minute

type State string

const (
	StateConnected           State = "connected"
	StateDisconnected        State = "disconnected"
	StateNotConnected        State = "not-connected"
	StateDeclined            State = "declined"
	StateTimedOut            State = "timed-out"
	StateApprovalUnavailable State = "approval-unavailable"
	StateUnsupportedContext  State = "unsupported-context"
	StateClusterUnreachable  State = "cluster-unreachable"
	StateRBACDenied          State = "rbac-denied"
	StateOverGranted         State = "over-granted"
	StateDrifted             State = "drifted"
	StateAuthorizationGone   State = "authorization-revoked"
	StateNotAuthorized       State = "not-authorized"
	StateBackendUnavailable  State = "backend-unavailable"
	StateBackendRejected     State = "backend-rejected"
	StateError               State = "error"
)

type ContextInfo struct {
	Name          string `json:"name"`
	Server        string `json:"server"`
	Supported     bool   `json:"supported"`
	Reason        string `json:"reason,omitempty"`
	Reachable     *bool  `json:"reachable,omitempty"`
	Version       string `json:"serverVersion,omitempty"`
	Authorization string `json:"authorization,omitempty"`
	Current       bool   `json:"current,omitempty"`
}

// Result is the only thing the helper prints. It has no field that can carry a credential.
type Result struct {
	OK        bool              `json:"ok"`
	Command   string            `json:"command"`
	State     State             `json:"state"`
	Message   string            `json:"message,omitempty"`
	Context   string            `json:"context,omitempty"`
	Transport string            `json:"transport,omitempty"`
	ExpiresAt string            `json:"expiresAt,omitempty"`
	RenewAt   string            `json:"renewAt,omitempty"`
	Changes   []string          `json:"changes,omitempty"`
	Contexts  []ContextInfo     `json:"contexts,omitempty"`
	Backend   *transport.Status `json:"backend,omitempty"`
}

type Deps struct {
	Clusters clusters.Loader
	Prompter approve.Prompter
	Backends func() []transport.Backend
	Now      func() time.Time
}

func (d Deps) now() time.Time {
	if d.Now != nil {
		return d.Now()
	}
	return time.Now()
}

func fail(cmd string, state State, msg string) Result {
	return Result{Command: cmd, State: state, Message: msg}
}

// Classify says whether a context is a local cluster this helper may talk to: a kind cluster whose
// API server is https on a loopback address. Nothing else is ever contacted.
func Classify(name, server string) (bool, string) {
	host := hostOf(server)
	switch {
	case host == "":
		return false, "the API server address cannot be read"
	case !strings.HasPrefix(server, "https://"):
		return false, "the API server is not https"
	case !kubeconfig.IsLoopback(host):
		return false, "not a local cluster (only loopback API servers are supported)"
	case !strings.HasPrefix(name, "kind-"):
		return false, "not a kind cluster (only kind-* contexts are supported initially)"
	}
	return true, ""
}

func hostOf(server string) string {
	u, err := url.Parse(server)
	if err != nil {
		return ""
	}
	return u.Hostname()
}

// supported finds a context by name and refuses anything that is not a supported local cluster.
// The name comes from the extension UI, so it is only ever matched against the real kubeconfig.
func (d Deps) supported(cmd, name string) (clusters.Context, *Result) {
	list, _, err := d.Clusters.Contexts()
	if err != nil {
		r := fail(cmd, StateError, err.Error())
		return clusters.Context{}, &r
	}
	for _, c := range list {
		if c.Name != name {
			continue
		}
		if ok, why := Classify(c.Name, c.Server); !ok {
			r := fail(cmd, StateUnsupportedContext, why)
			r.Context = approve.Sanitize(name, 80)
			return clusters.Context{}, &r
		}
		return c, nil
	}
	r := fail(cmd, StateUnsupportedContext, "that context is not in your kubeconfig")
	return clusters.Context{}, &r
}

// pickBackend finds a working transport and its current status.
func (d Deps) pickBackend(ctx context.Context) (transport.Backend, transport.Status, error) {
	return transport.Pick(ctx, d.Backends())
}

func backendFailure(cmd string, err error) Result {
	var rej *transport.RejectedError
	if errors.As(err, &rej) {
		return fail(cmd, StateBackendRejected, rej.Message)
	}
	return fail(cmd, StateBackendUnavailable, "the Resilience Engine extension backend is not reachable; is the extension running?")
}

func iso(t time.Time) string { return t.UTC().Format(time.RFC3339) }

func bullet(lines []string) string {
	var b strings.Builder
	for _, l := range lines {
		fmt.Fprintf(&b, "  • %s\n", l)
	}
	return b.String()
}

func describe(changes []rbac.Change) []string {
	out := make([]string, len(changes))
	for i, c := range changes {
		out[i] = c.String()
	}
	return out
}

// authorizeDialog is composed only from facts the helper read itself, and every value that came from
// the user's kubeconfig is sanitised, so nothing the extension UI sends can appear in it.
func authorizeDialog(c clusters.Context, version string, plan []rbac.Change) approve.Request {
	name := approve.Sanitize(c.Name, 80)
	server := approve.Sanitize(clusters.HostPort(c.Server), 60)
	var b strings.Builder
	fmt.Fprintf(&b, "Cluster: %s\nAPI server: %s", name, server)
	if v := approve.Sanitize(version, 20); v != "" {
		fmt.Fprintf(&b, " (Kubernetes %s)", v)
	}
	b.WriteString("\n\nThe Resilience Engine extension will be able to:\n")
	b.WriteString(bullet(rbac.Permissions()))
	if len(plan) == 0 {
		b.WriteString("\nNo change to the cluster is needed: the access objects already exist and match.\n")
	} else {
		b.WriteString("\nThis approval will change the cluster:\n")
		b.WriteString(bullet(describe(plan)))
	}
	fmt.Fprintf(&b, "\nA read-only token (up to %d hours) is then issued and renewed automatically while the extension is open. You can remove this access at any time.", int(token.RequestedLifetime.Hours()))
	return approve.Request{Title: "Resilience Engine: approve read-only access?", Body: capText(b.String(), 1800), Timeout: 90 * time.Second}
}

func revokeDialog(c clusters.Context, plan rbac.RevokePlan) approve.Request {
	var b strings.Builder
	fmt.Fprintf(&b, "Cluster: %s\nAPI server: %s\n\nThis will DELETE from the cluster:\n",
		approve.Sanitize(c.Name, 80), approve.Sanitize(clusters.HostPort(c.Server), 60))
	b.WriteString(bullet(describe(plan.Delete)))
	if len(plan.Keep) > 0 {
		b.WriteString("\nThis will NOT be deleted:\n")
		kept := make([]string, len(plan.Keep))
		for i, k := range plan.Keep {
			kept[i] = approve.Sanitize(k, 220)
		}
		b.WriteString(bullet(kept))
	}
	b.WriteString("\nEvery token already issued for the extension stops working immediately, and the extension is disconnected.")
	return approve.Request{Title: "Resilience Engine: remove read-only access?", Body: capText(b.String(), 1800), Timeout: 90 * time.Second}
}

func capText(s string, max int) string {
	r := []rune(s)
	if len(r) <= max {
		return s
	}
	return string(r[:max-1]) + "…"
}
