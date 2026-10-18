package execx

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestRunsAProgramWithoutAShellAndPassesStdin(t *testing.T) {
	res, err := Real{}.Run(context.Background(), "/bin/cat", nil, []byte("from-stdin"))
	if err != nil || string(res.Stdout) != "from-stdin" || res.ExitCode != 0 {
		t.Fatalf("res=%+v err=%v", res, err)
	}
}

func TestShellMetacharactersInArgumentsAreInert(t *testing.T) {
	res, err := Real{}.Run(context.Background(), "/bin/echo", []string{"$(touch /tmp/pwned); echo hi | cat"}, nil)
	if err != nil || !strings.Contains(string(res.Stdout), "$(touch /tmp/pwned)") {
		t.Fatalf("argument must be passed literally: %q err=%v", res.Stdout, err)
	}
}

func TestANonZeroExitIsAResultNotAnError(t *testing.T) {
	res, err := Real{}.Run(context.Background(), "/bin/sh", []string{"-c", "exit 5"}, nil)
	if err != nil || res.ExitCode != 5 {
		t.Fatalf("res=%+v err=%v", res, err)
	}
}

func TestAMissingProgramIsAnError(t *testing.T) {
	if _, err := (Real{}).Run(context.Background(), "/nonexistent/program", nil, nil); err == nil {
		t.Fatal("want an error")
	}
}

func TestTheContextStopsALongRunningProgram(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	start := time.Now()
	_, err := Real{}.Run(ctx, "/bin/sleep", []string{"30"}, nil)
	if err == nil || time.Since(start) > 5*time.Second {
		t.Fatalf("must stop promptly, err=%v after %v", err, time.Since(start))
	}
}
