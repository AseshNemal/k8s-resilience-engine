package main

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	authv1 "k8s.io/api/authentication/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	kversion "k8s.io/apimachinery/pkg/version"
	fakediscovery "k8s.io/client-go/discovery/fake"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/kubernetes/fake"
	k8stesting "k8s.io/client-go/testing"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/clusters"
	"resilience-engine/helper/internal/flow"
	"resilience-engine/helper/internal/kubeconfig"
	"resilience-engine/helper/internal/transport"
)

const tokenValue = "eyJ-CLI-TOKEN-MUST-NEVER-BE-PRINTED"

type loader struct{ cs *fake.Clientset }

func (l loader) Contexts() ([]clusters.Context, string, error) {
	return []clusters.Context{{Name: "kind-resilience-research", Server: "https://127.0.0.1:58227"}, {Name: "prod", Server: "https://prod.example.com:6443"}}, "kind-resilience-research", nil
}
func (l loader) Client(string) (kubernetes.Interface, kubeconfig.Cluster, error) {
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

func cluster() *fake.Clientset {
	cs := fake.NewClientset()
	cs.Discovery().(*fakediscovery.FakeDiscovery).FakedServerVersion = &kversion.Info{GitVersion: "v1.37.0"}
	cs.PrependReactor("create", "serviceaccounts", func(a k8stesting.Action) (bool, runtime.Object, error) {
		if a.GetSubresource() != "token" {
			return false, nil, nil
		}
		return true, &authv1.TokenRequest{Status: authv1.TokenRequestStatus{Token: tokenValue, ExpirationTimestamp: metav1.NewTime(time.Now().Add(8 * time.Hour))}}, nil
	})
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
		{"discover"}, {"status"}, {"version"},
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
		{}, {"bogus"}, {"authorize"}, {"renew"}, {"revoke"}, {"authorize", "a", "b"}, {"discover", "extra"}, {"status", "--x"}, {"version", "1"},
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
