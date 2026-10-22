package main

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/bookinfotrace"
	"resilience-engine/helper/internal/execx"
	"resilience-engine/helper/internal/flow"
	"resilience-engine/helper/internal/kube"
	"resilience-engine/helper/internal/kube/kubetest"
	"resilience-engine/helper/internal/transport"
)

// hostRunner is a Docker Desktop that answers like the real one did: three kind nodes that agree on the host address
// and can reach the OTLP receiver, a 4 GiB VM with about 2 GiB in use, and a Mac with swap in use.
type hostRunner struct{ calls [][]string }

func (r *hostRunner) Run(_ context.Context, name string, args []string, _ []byte) (execx.Result, error) {
	r.calls = append(r.calls, append([]string{name}, args...))
	if name == "/usr/sbin/sysctl" {
		return execx.Result{Stdout: []byte("total = 4096.00M  used = 2688.88M  free = 1407.12M  (encrypted)")}, nil
	}
	switch args[0] {
	case "ps":
		return execx.Result{Stdout: []byte("desktop-control-plane\ndesktop-worker\n")}, nil
	case "info":
		return execx.Result{Stdout: []byte("4107005952")}, nil
	case "stats":
		return execx.Result{Stdout: []byte("910.2MiB / 3.825GiB\n384.1MiB / 3.825GiB\n")}, nil
	case "exec":
		if args[2] == "getent" {
			return execx.Result{Stdout: []byte("192.168.65.254  STREAM host.docker.internal\n")}, nil
		}
		if args[2] == "crictl" {
			return execx.Result{Stdout: []byte(`{"stats":[]}`)}, nil
		}
	}
	return execx.Result{}, nil
}

func useHost(t *testing.T) *hostRunner {
	t.Helper()
	runner := &hostRunner{}
	previous := bookinfoDeps
	bookinfoDeps = func() bookinfotrace.Deps {
		return bookinfotrace.Deps{Runner: runner, FindDocker: func() (string, error) { return "/usr/local/bin/docker", nil }, GOOS: "darwin"}
	}
	t.Cleanup(func() { bookinfoDeps = previous })
	return runner
}

func bookinfoCluster() *kubetest.Fake {
	cs := cluster()
	cs.Version = "v1.36.1"
	cs.Put(kube.Namespaces, kube.Object{Name: "bookinfo", Labels: map[string]string{"kubernetes.io/metadata.name": "bookinfo"}})
	one := 1
	ready := true
	for _, app := range []string{"productpage", "details", "ratings", "reviews"} {
		cs.Put(kube.Deployments, kube.Object{Name: app + "-v1", Namespace: "bookinfo", Replicas: &one, ReadyReplicas: &one, Containers: []string{app}, SpecDigest: "d-" + app})
		cs.Put(kube.Pods, kube.Object{Name: app + "-v1-abc", Namespace: "bookinfo", Labels: map[string]string{"app": app, "version": "v1"}, Containers: []string{app}, Ready: &ready, Phase: "Running"})
	}
	return cs
}

func invokeOn(t *testing.T, cs *kubetest.Fake, args ...string) (int, string, string) {
	t.Helper()
	var out, errb bytes.Buffer
	code := run(args, &out, &errb, func() flow.Deps {
		return flow.Deps{Clusters: loader{cs}, Prompter: prompter{approve.Approved}, Backends: func() []transport.Backend { return []transport.Backend{&backend{}} }}
	})
	return code, out.String(), errb.String()
}

func parse(t *testing.T, out string) map[string]any {
	t.Helper()
	var v map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(out)), &v); err != nil || strings.Count(strings.TrimSpace(out), "\n") != 0 {
		t.Fatalf("stdout must be one JSON object: %q (%v)", out, err)
	}
	return v
}

func TestThePlanOfTheRealBenchmarkIsReadyAndChangesNothing(t *testing.T) {
	useHost(t)
	cs := bookinfoCluster()
	cs.ClearActions()
	code, out, errOut := invokeOn(t, cs, "trace-bookinfo-plan", "kind-resilience-research")
	if code != 0 || errOut != "" {
		t.Fatalf("code %d stderr %q", code, errOut)
	}
	v := parse(t, out)
	if v["command"] != "trace-bookinfo-plan" || v["state"] != "bookinfo-plan-ready" || v["ok"] != true {
		t.Fatalf("%v", v)
	}
	plan := v["plan"].(map[string]any)
	if plan["ready"] != true || len(plan["preview"].([]any)) != 9 || len(plan["restarts"].([]any)) != 3 {
		t.Fatalf("%v", plan)
	}
	if jaeger := plan["jaeger"].(map[string]any); jaeger["ipv4"] != "192.168.65.254" {
		t.Fatalf("%v", jaeger)
	}

	// The property that matters: the cluster was only read.
	writes, tokens := cs.Writes()
	if len(writes) != 0 || tokens != 0 {
		t.Fatalf("a plan wrote to the cluster: %v (token requests %d)", writes, tokens)
	}
	for _, a := range cs.Actions() {
		if a.Verb != "get" && a.Verb != "list" {
			t.Errorf("unexpected action %+v", a)
		}
	}
}

func TestThePlanNeverAsksTheDockerHostToChangeAnything(t *testing.T) {
	runner := useHost(t)
	invokeOn(t, bookinfoCluster(), "trace-bookinfo-plan", "kind-resilience-research")
	for _, call := range runner.calls {
		switch {
		case call[0] == "/usr/sbin/sysctl":
		case call[1] == "ps" || call[1] == "info" || call[1] == "stats":
		case call[1] == "exec" && (call[3] == "getent" || call[3] == "crictl" || call[3] == "timeout"):
		default:
			t.Errorf("the plan ran a command it should not: %v", call)
		}
	}
}

func TestABlockedPlanSaysWhyAndStillChangesNothing(t *testing.T) {
	useHost(t)
	cs := bookinfoCluster()
	cs.Put(kube.CustomResourceDefinitions, kube.Object{Name: "virtualservices.networking.istio.io"})
	cs.ClearActions()
	code, out, _ := invokeOn(t, cs, "trace-bookinfo-plan", "kind-resilience-research")
	v := parse(t, out)
	if code != 0 || v["ok"] != false || v["state"] != "bookinfo-plan-blocked" {
		t.Fatalf("%v", v)
	}
	blockers := v["plan"].(map[string]any)["blockers"].([]any)
	if len(blockers) == 0 || blockers[0].(map[string]any)["code"] != "ISTIO_ALREADY_PRESENT" {
		t.Fatalf("%v", blockers)
	}
	if writes, tokens := cs.Writes(); len(writes) != 0 || tokens != 0 {
		t.Fatal("a blocked plan wrote to the cluster")
	}
}

func TestThePlanRefusesAContextThatIsNotASupportedLocalCluster(t *testing.T) {
	useHost(t)
	cs := bookinfoCluster()
	cs.ClearActions()
	code, out, _ := invokeOn(t, cs, "trace-bookinfo-plan", "prod")
	v := parse(t, out)
	if code != 0 || v["ok"] != false || v["plan"] != nil {
		t.Fatalf("%v", v)
	}
	if len(cs.Actions()) != 0 {
		t.Fatalf("an unsupported context must not be contacted: %v", cs.Actions())
	}
}

func TestThePlanCommandTakesExactlyOneContextArgument(t *testing.T) {
	for _, args := range [][]string{{"trace-bookinfo-plan"}, {"trace-bookinfo-plan", "a", "b"}} {
		code, out, errOut := invokeOn(t, bookinfoCluster(), args...)
		if code != 2 || out != "" || !strings.HasPrefix(errOut, "usage:") {
			t.Errorf("%v: code %d stdout %q stderr %q", args, code, out, errOut)
		}
	}
}

func usageText(t *testing.T) string {
	t.Helper()
	_, _, errOut := invokeOn(t, bookinfoCluster(), "bogus")
	return errOut
}
