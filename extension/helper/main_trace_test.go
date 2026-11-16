package main

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"resilience-engine/helper/internal/approve"
	"resilience-engine/helper/internal/execx"
	"resilience-engine/helper/internal/tracesvc"
)

// No test in this package may reach a real Docker daemon or the network: from the moment the package
// loads, the trace commands see a machine with no Docker. A test that wants more replaces traceDeps itself.
func init() {
	traceDeps = func() tracesvc.Deps { return noDocker() }
}

func noDocker() tracesvc.Deps {
	return tracesvc.Deps{
		Runner:     failingRunner{},
		FindDocker: func() (string, error) { return "", context.Canceled },
		Probe:      func(context.Context) bool { return false },
	}
}

type failingRunner struct{}

func (failingRunner) Run(context.Context, string, []string, []byte) (execx.Result, error) {
	panic("a test reached the Docker runner")
}

func TestTheTraceCommandsPrintOneJSONLineAndExitZero(t *testing.T) {
	for _, command := range []string{"trace-status", "trace-start", "trace-stop"} {
		code, out, errOut := invoke(t, approve.Approved, &backend{}, command)
		var v map[string]any
		if err := json.Unmarshal([]byte(strings.TrimSpace(out)), &v); err != nil || strings.Count(strings.TrimSpace(out), "\n") != 0 {
			t.Fatalf("%s: stdout must be one JSON object: %q (%v)", command, out, err)
		}
		if code != 0 || errOut != "" {
			t.Errorf("%s: code %d stderr %q", command, code, errOut)
		}
		if v["command"] != command {
			t.Errorf("%s: command field = %v", command, v["command"])
		}
		if v["state"] != "docker-unavailable" || v["ok"] != false {
			t.Errorf("%s: with no Docker the answer must say so, got %v", command, v)
		}
	}
}

func TestTheTraceCommandsTakeNoArguments(t *testing.T) {
	for _, args := range [][]string{
		{"trace-status", "x"}, {"trace-start", "--image", "evil"}, {"trace-stop", "other-container"},
		{"trace-enable", "yes"}, {"trace-ensure", "x"}, {"trace-validate", "host.example"},
	} {
		code, out, errOut := invoke(t, approve.Approved, &backend{}, args...)
		if code != 2 || out != "" || !strings.HasPrefix(errOut, "usage:") {
			t.Errorf("%v: code %d stdout %q stderr %q", args, code, out, errOut)
		}
	}
}

func TestTheUsageLineNamesTheTraceCommands(t *testing.T) {
	_, _, errOut := invoke(t, approve.Approved, &backend{}, "bogus")
	for _, name := range []string{"trace-status", "trace-enable", "trace-ensure", "trace-validate", "trace-start", "trace-stop"} {
		if !strings.Contains(errOut, name) {
			t.Errorf("usage does not mention %s: %q", name, errOut)
		}
	}
}

// With no Docker the new commands say so (or, for the ones that need nothing, that there was nothing to do),
// as one JSON line, and none of them reaches Docker or asks anyone anything.
func TestTheNewTraceCommandsAnswerWithOneJSONLineWhenThereIsNoDocker(t *testing.T) {
	want := map[string]struct {
		state string
		ok    bool
	}{
		"trace-enable":   {"docker-unavailable", false},
		"trace-ensure":   {"trace-not-enabled", true}, // never turned on, so it does not even look at Docker
		"trace-validate": {"trace-endpoints-failed", false},
	}
	for command, expect := range want {
		code, out, errOut := invoke(t, approve.Declined, &backend{}, command)
		var v map[string]any
		if err := json.Unmarshal([]byte(strings.TrimSpace(out)), &v); err != nil || code != 0 || errOut != "" {
			t.Fatalf("%s: code %d stdout %q stderr %q (%v)", command, code, out, errOut, err)
		}
		if v["command"] != command || v["state"] != expect.state || v["ok"] != expect.ok {
			t.Errorf("%s: got %v, want state %s ok %v", command, v, expect.state, expect.ok)
		}
		trace, _ := v["trace"].(map[string]any)
		if trace == nil || trace["enabled"] != false {
			t.Errorf("%s: the answer must say tracing is not turned on: %v", command, v["trace"])
		}
	}
}

// The background job runs renew-managed. When tracing was never turned on it must not reach Docker.
func TestTheBackgroundTickDoesNotTouchDockerWhenTracingIsOff(t *testing.T) {
	code, _, _ := invoke(t, approve.Approved, &backend{}, "renew-managed")
	if code != 0 {
		t.Fatalf("renew-managed exited %d", code)
	}
}
