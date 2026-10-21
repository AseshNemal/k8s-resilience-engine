//go:build integration

package agent

// These tests use the real Task Scheduler (Windows) and the real systemd user manager (Linux). They
// install the background job, have the system start it, and remove it again, so they change the
// machine they run on. They therefore run only when asked for:
//
//	RESILIENCE_INTEGRATION=1 go test -tags integration ./internal/agent -run Integration -count=1 -v
//
// The CI workflow (.github/workflows/ci.yml) does this on throwaway Windows and Linux runners. Do not
// run them on a machine where you use the extension: they would replace its real background job.

import (
	"context"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/execx"
)

func needIntegration(t *testing.T) {
	t.Helper()
	if os.Getenv("RESILIENCE_INTEGRATION") != "1" {
		t.Skip("set RESILIENCE_INTEGRATION=1 to install a real background job on this machine")
	}
}

// buildHelper compiles the real helper (optionally without a console window) into a folder of its own
// and returns its path, so the job runs the same program a user would get.
func buildHelper(t *testing.T, name string, ldflags string) string {
	t.Helper()
	out := filepath.Join(t.TempDir(), name)
	args := []string{"build", "-o", out}
	if ldflags != "" {
		args = append(args, "-ldflags", ldflags)
	}
	args = append(args, "resilience-engine/helper")
	cmd := exec.Command("go", args...)
	if raw, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("go build failed: %v\n%s", err, raw)
	}
	return out
}

func eventually(t *testing.T, what string, within time.Duration, ok func() bool) {
	t.Helper()
	deadline := time.Now().Add(within)
	for time.Now().Before(deadline) {
		if ok() {
			return
		}
		time.Sleep(time.Second)
	}
	t.Fatalf("timed out after %s waiting for %s", within, what)
}

// runReal runs a real program and reports its output and whether it exited with 0. (execx returns an
// error only when the program could not run at all, not for a non-zero exit.)
func runReal(name string, args ...string) (out string, ok bool) {
	res, err := execx.Real{}.Run(context.Background(), name, args, nil)
	return string(res.Stdout) + string(res.Stderr), err == nil && res.ExitCode == 0
}

func TestIntegrationWindowsTaskInstallsRunsAndUninstalls(t *testing.T) {
	needIntegration(t)
	if runtime.GOOS != "windows" {
		t.Skip("Task Scheduler exists on Windows only")
	}
	me, err := user.Current()
	if err != nil {
		t.Fatal(err)
	}
	// The console-less build is what real installs schedule, so test that one: it proves a GUI-subsystem
	// program starts and exits cleanly when Task Scheduler launches it.
	quiet := buildHelper(t, "resilience-helper-bg.exe", "-H=windowsgui")
	normal := buildHelper(t, "resilience-helper.exe", "")
	task := &ScheduledTask{GOOS: "windows", User: me.Username, Exe: normal, Quiet: quiet, Runner: execx.Real{}}
	t.Cleanup(func() { _ = task.Uninstall(context.Background()) })

	if s, err := task.Status(bg); err != nil || s != Off {
		t.Fatalf("before installing: (%v, %v), want off", s, err)
	}
	if err := task.Install(bg); err != nil {
		t.Fatalf("Install: %v", err)
	}
	if s, err := task.Status(bg); err != nil || s != On {
		t.Fatalf("after installing: (%v, %v), want on", s, err)
	}
	if err := task.Install(bg); err != nil {
		t.Fatalf("installing again must replace the task: %v", err)
	}

	// Task Scheduler itself must accept the definition the way we wrote it.
	out, ok := runReal(schtasks, "/Query", "/TN", TaskName, "/XML")
	if !ok {
		t.Fatalf("query failed:\n%s", out)
	}
	for _, want := range []string{"renew-managed", "PT15M", "LeastPrivilege", "StartWhenAvailable"} {
		if !strings.Contains(out, want) {
			t.Errorf("the registered task lacks %q:\n%s", want, out)
		}
	}

	// Have the scheduler start the program, then read how it ended. The helper exits 0 whenever it
	// produced a result, and here there is no extension backend, so a clean 0 proves the task launched
	// the program and the program ran to completion.
	if out, ok := runReal(schtasks, "/Run", "/TN", TaskName); !ok {
		t.Fatalf("run failed:\n%s", out)
	}
	lastResult := regexp.MustCompile(`(?m)^Last Result:\s*(-?\d+)`)
	var result string
	eventually(t, "the task to finish", 90*time.Second, func() bool {
		out, _ := runReal(schtasks, "/Query", "/TN", TaskName, "/V", "/FO", "LIST")
		m := lastResult.FindStringSubmatch(out)
		if m == nil {
			return false
		}
		// 267009 means still running, 267011 means it has not run yet.
		if m[1] == "267009" || m[1] == "267011" {
			return false
		}
		result = m[1]
		return true
	})
	if result != "0" {
		t.Errorf("Task Scheduler reports last result %s, want 0", result)
	}

	if err := task.Uninstall(bg); err != nil {
		t.Fatalf("Uninstall: %v", err)
	}
	if s, _ := task.Status(bg); s != Off {
		t.Errorf("after removing: %v, want off", s)
	}
	if err := task.Uninstall(bg); err != nil {
		t.Errorf("removing twice must be harmless: %v", err)
	}
}

func TestIntegrationLinuxTimerInstallsRunsAndUninstalls(t *testing.T) {
	needIntegration(t)
	if runtime.GOOS != "linux" {
		t.Skip("systemd user timers exist on Linux only")
	}
	if _, ok := runReal(systemctl, "--user", "show-environment"); !ok {
		t.Fatal("no systemd user session is available; the CI job must start one (loginctl enable-linger, XDG_RUNTIME_DIR)")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	exe := buildHelper(t, "resilience-helper", "")
	timer := &SystemdTimer{GOOS: "linux", Home: home, Exe: exe, Env: map[string]string{"XDG_CONFIG_HOME": os.Getenv("XDG_CONFIG_HOME")}, HasSystemd: true, Runner: execx.Real{}}
	t.Cleanup(func() { _ = timer.Uninstall(context.Background()) })

	if err := timer.Install(bg); err != nil {
		t.Fatalf("Install: %v", err)
	}
	if s, err := timer.Status(bg); err != nil || s != On {
		t.Fatalf("after installing: (%v, %v), want on", s, err)
	}
	out, ok := runReal(systemctl, "--user", "list-timers", UnitName+".timer", "--no-pager")
	if !ok || !strings.Contains(out, UnitName+".timer") {
		t.Fatalf("systemd does not list the timer:\n%s", out)
	}

	// Start the service once, the way the timer would, and read how it ended.
	if out, ok := runReal(systemctl, "--user", "start", UnitName+".service"); !ok {
		t.Fatalf("starting the service failed:\n%s", out)
	}
	show, _ := runReal(systemctl, "--user", "show", UnitName+".service", "-p", "Result", "-p", "ExecMainStatus")
	if !strings.Contains(show, "Result=success") || !strings.Contains(show, "ExecMainStatus=0") {
		t.Errorf("the service did not end cleanly:\n%s", show)
	}

	if err := timer.Uninstall(bg); err != nil {
		t.Fatalf("Uninstall: %v", err)
	}
	if s, _ := timer.Status(bg); s != Off {
		t.Errorf("after removing: %v, want off", s)
	}
	if _, err := os.Stat(timer.timerPath()); !os.IsNotExist(err) {
		t.Errorf("the timer file must be gone (err=%v)", err)
	}
	if err := timer.Uninstall(bg); err != nil {
		t.Errorf("removing twice must be harmless: %v", err)
	}
}
