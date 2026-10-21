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
	} {
		code, out, errOut := invoke(t, approve.Approved, &backend{}, args...)
		if code != 2 || out != "" || !strings.HasPrefix(errOut, "usage:") {
			t.Errorf("%v: code %d stdout %q stderr %q", args, code, out, errOut)
		}
	}
}

func TestTheUsageLineNamesTheTraceCommands(t *testing.T) {
	_, _, errOut := invoke(t, approve.Approved, &backend{}, "bogus")
	for _, name := range []string{"trace-status", "trace-start", "trace-stop"} {
		if !strings.Contains(errOut, name) {
			t.Errorf("usage does not mention %s: %q", name, errOut)
		}
	}
}
