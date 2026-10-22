package main

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/flow"
	"resilience-engine/helper/internal/kube"
	"resilience-engine/helper/internal/kube/kubetest"
	"resilience-engine/helper/internal/kubeconfig"
	"resilience-engine/helper/internal/transport"
)

const tokenValue = "eyJ-CLI-TOKEN-MUST-NEVER-BE-PRINTED"

type loader struct{ cs *kubetest.Fake }

func (l loader) Contexts() ([]clusters.Context, string, error) {
	return []clusters.Context{{Name: "kind-resilience-research", Server: "https://127.0.0.1:58227"}, {Name: "prod", Server: "https://prod.example.com:6443"}}, "kind-resilience-research", nil
}
func (l loader) Client(string) (kube.Client, kubeconfig.Cluster, error) {
	return l.cs, kubeconfig.Cluster{Server: "https://127.0.0.1:58227", CAData: []byte("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")}, nil
}

type prompter struct{ d approve.Decision }

func (p prompter) Confirm(context.Context, approve.Request) (approve.Decision, error) {
	return p.d, nil
}

type backend struct{ got []transport.InstallRequest }

func (b *backend) Name() string { return "fake" }
func (b *backend) Status(context.Context) (transport.Status, error) {
	return transport.Status{Connected: false}, nil
}
func (b *backend) Install(_ context.Context, r transport.InstallRequest) (transport.Status, error) {
	b.got = append(b.got, r)
	return transport.Status{Connected: true, Managed: true, Context: r.Context, State: "ok"}, nil
}
func (b *backend) Disconnect(context.Context) error { return nil }

func cluster() *kubetest.Fake {
	cs := kubetest.New()
	cs.GrantTokens(tokenValue, time.Now(), 8*time.Hour)
	cs.Put(kube.Nodes, kube.Object{Name: "kind-control-plane", ProviderID: "kind://docker/resilience-research/kind-control-plane"})
	return cs
}

func invoke(t *testing.T, d approve.Decision, b *backend, args ...string) (code int, stdout, stderr string) {
	t.Helper()
	var out, errb bytes.Buffer
	code = run(args, &out, &errb, func() flow.Deps {
		return flow.Deps{Clusters: loader{cluster()}, Prompter: prompter{d}, Backends: func() []transport.Backend { return []transport.Backend{b} }}
	})
	return code, out.String(), errb.String()
}

func TestStdoutIsOneLineOfJSONAndStderrIsEmptyOnEveryOutcome(t *testing.T) {
	for _, args := range [][]string{
		{"discover"}, {"status"}, {"version"}, {"renew-managed"}, {"reconnect"}, {"agent-status"}, {"agent-disable"}, {"agent-enable", "kind-resilience-research"},
		{"authorize", "kind-resilience-research"}, {"renew", "kind-resilience-research"}, {"revoke", "kind-resilience-research"},
		{"authorize", "prod"}, {"authorize", "no-such"},
	} {
		code, out, errOut := invoke(t, approve.Approved, &backend{}, args...)
		if errOut != "" {
			t.Errorf("%v: stderr must be empty, got %q", args, errOut)
		}
		var v map[string]any
		if err := json.Unmarshal([]byte(strings.TrimSpace(out)), &v); err != nil || strings.Count(strings.TrimSpace(out), "\n") != 0 {
			t.Errorf("%v: stdout must be exactly one JSON object: %q (%v)", args, out, err)
		}
		if code != 0 {
			t.Errorf("%v: a printed result must exit 0 (ok=%v state=%v), got %d", args, v["ok"], v["state"], code)
		}
	}
}

func TestAnApprovedAuthorizeConnectsAndThePrintedOutputHasNoCredential(t *testing.T) {
	b := &backend{}
	code, out, errOut := invoke(t, approve.Approved, b, "authorize", "kind-resilience-research")

	if code != 0 || !strings.Contains(out, `"state":"connected"`) {
		t.Fatalf("code %d out %s", code, out)
	}
	if len(b.got) != 1 || !strings.Contains(string(b.got[0].Kubeconfig.Reveal()), tokenValue) {
		t.Fatal("positive control: the backend must have received the token")
	}
	for _, bad := range []string{tokenValue, "eyJ", "BEGIN CERTIFICATE", "host.docker.internal"} {
		if strings.Contains(out, bad) || strings.Contains(errOut, bad) {
			t.Fatalf("output leaked %q: %s %s", bad, out, errOut)
		}
	}
}

func TestADeclinedAuthorizeStillExitsZeroWithTheOutcomeInTheJSONAndSendsNothing(t *testing.T) {
	b := &backend{}
	code, out, _ := invoke(t, approve.Declined, b, "authorize", "kind-resilience-research")
	if code != 0 || !strings.Contains(out, `"ok":false`) || !strings.Contains(out, `"state":"declined"`) || len(b.got) != 0 {
		t.Fatalf("code %d out %s installs %d", code, out, len(b.got))
	}
}

func TestBadUsageIsExitCode2WithAShortMessageOnStderrOnly(t *testing.T) {
	for _, args := range [][]string{
		{}, {"bogus"}, {"authorize"}, {"renew"}, {"revoke"}, {"renew-managed", "kind-resilience-research"}, {"reconnect", "kind-resilience-research"}, {"agent-enable"}, {"agent-status", "x"}, {"agent-disable", "x"}, {"authorize", "a", "b"}, {"discover", "extra"}, {"status", "--x"}, {"version", "1"},
	} {
		code, out, errOut := invoke(t, approve.Approved, &backend{}, args...)
		if code != 2 || out != "" || !strings.HasPrefix(errOut, "usage:") {
			t.Errorf("%v: code %d stdout %q stderr %q", args, code, out, errOut)
		}
	}
}

func TestAPanicIsContainedAndPrintsNothingFromTheRun(t *testing.T) {
	var out, errb bytes.Buffer
	code := run([]string{"discover"}, &out, &errb, func() flow.Deps { panic("boom " + tokenValue) })
	if code != 1 || strings.Contains(out.String(), tokenValue) || strings.Contains(errb.String(), tokenValue) || strings.Contains(out.String(), "boom") {
		t.Fatalf("code %d out %q err %q", code, out.String(), errb.String())
	}
	if !strings.Contains(out.String(), `"state":"error"`) {
		t.Fatalf("a JSON error is expected: %q", out.String())
	}
}

func TestHostileContextArgumentsAreHandledAsDataNeverExecuted(t *testing.T) {
	for _, name := range []string{`$(touch /tmp/pwned-by-cli)`, "kind-x; rm -rf /", "`id`", "--help", "-", "\x00"} {
		code, out, _ := invoke(t, approve.Approved, &backend{}, "authorize", name)
		if code != 0 || !strings.Contains(out, `"state":"unsupported-context"`) {
			t.Errorf("%q: code %d out %s", name, code, out)
		}
	}
}

// With nothing connected there is nothing to renew: the background job must be able to run this every
// few minutes forever and have no effect, no dialog and no failure.
func TestRenewManagedWithNothingConnectedIsAQuietSuccess(t *testing.T) {
	code, out, errOut := invoke(t, approve.Declined, &backend{}, "renew-managed")
	var v map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(out)), &v); err != nil {
		t.Fatal(err)
	}
	if code != 0 || errOut != "" || v["ok"] != true || v["state"] != "idle" {
		t.Fatalf("code %d stderr %q result %v", code, errOut, v)
	}
}

// The deadlines are what let a very slow Docker still finish. They are checked here so a later
// "tidy up" cannot quietly put the 90 second limit that made renewal fail back.
func TestSlowDockerDeadlinesAreGenerousForUnattendedRenewal(t *testing.T) {
	if renewTimeout < 4*time.Minute || statusTimeout < time.Minute {
		t.Fatalf("renew %v status %v are too short for a busy Docker Desktop", renewTimeout, statusTimeout)
	}
}

func TestAnAuthorizedInstallCarriesItsOriginAndTheApprovedIdentityButNoClusterIdentityLeaksToOutput(t *testing.T) {
	b := &backend{}
	_, out, _ := invoke(t, approve.Approved, b, "authorize", "kind-resilience-research")

	if len(b.got) != 1 || b.got[0].Origin != "authorize" || b.got[0].Identity == nil || len(b.got[0].Identity.CASha256) != 64 {
		t.Fatalf("install = %+v", b.got)
	}
	if !strings.Contains(out, `"tokenRequest":true`) {
		t.Fatalf("the output must say a token was requested: %s", out)
	}
}

func TestReconnectWithNothingApprovedChangesNothingAndSaysSo(t *testing.T) {
	b := &backend{}
	code, out, _ := invoke(t, approve.Approved, b, "reconnect")

	if code != 0 || !strings.Contains(out, `"state":"idle"`) || len(b.got) != 0 {
		t.Fatalf("code %d out %s installs %d", code, out, len(b.got))
	}
}
