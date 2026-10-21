package execx

import (
	"context"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"
)

// The tests run real programs, but not ones that only exist on some systems (/bin/cat, /bin/sh): the
// test binary starts itself as the fake program. TestHelperProcess is that program; it does nothing
// unless the environment asks it to.
const helperEnv = "EXECX_TEST_HELPER"

func TestHelperProcess(t *testing.T) {
	mode := os.Getenv(helperEnv)
	if mode == "" {
		return // an ordinary test run
	}
	args := os.Args
	for i, a := range args {
		if a == "--" {
			args = args[i+1:]
			break
		}
	}
	switch mode {
	case "cat":
		_, _ = io.Copy(os.Stdout, os.Stdin)
	case "echo":
		fmt.Fprint(os.Stdout, strings.Join(args, " "))
	case "exit":
		code, _ := strconv.Atoi(args[0])
		os.Exit(code)
	case "sleep":
		time.Sleep(30 * time.Second)
	}
	os.Exit(0)
}

// helper returns the program and arguments that start TestHelperProcess in the given mode.
func helper(t *testing.T, mode string, args ...string) (string, []string) {
	t.Helper()
	t.Setenv(helperEnv, mode)
	return os.Args[0], append([]string{"-test.run=^TestHelperProcess$", "--"}, args...)
}

func TestRunsAProgramWithoutAShellAndPassesStdin(t *testing.T) {
	name, args := helper(t, "cat")
	res, err := Real{}.Run(context.Background(), name, args, []byte("from-stdin"))
	if err != nil || string(res.Stdout) != "from-stdin" || res.ExitCode != 0 {
		t.Fatalf("res=%+v err=%v", res, err)
	}
}

func TestShellMetacharactersInArgumentsAreInert(t *testing.T) {
	const nasty = "$(touch pwned); echo hi | cat"
	name, args := helper(t, "echo", nasty)
	res, err := Real{}.Run(context.Background(), name, args, nil)
	if err != nil || !strings.Contains(string(res.Stdout), nasty) {
		t.Fatalf("argument must be passed literally: %q err=%v", res.Stdout, err)
	}
}

func TestANonZeroExitIsAResultNotAnError(t *testing.T) {
	name, args := helper(t, "exit", "5")
	res, err := Real{}.Run(context.Background(), name, args, nil)
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
	name, args := helper(t, "sleep")
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	start := time.Now()
	_, err := Real{}.Run(ctx, name, args, nil)
	if err == nil || time.Since(start) > 5*time.Second {
		t.Fatalf("must stop promptly, err=%v after %v", err, time.Since(start))
	}
}
