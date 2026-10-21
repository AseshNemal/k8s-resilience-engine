package agent

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"resilience-engine/helper/internal/execx"
)

var bg = context.Background()

// recorder plays launchctl: it records every call and answers from a script.
type recorder struct {
	calls   [][]string
	exit    map[string]int // first argument -> exit code
	runErr  error
	printed bool // whether `launchctl print` finds the job
}

func (r *recorder) Run(_ context.Context, name string, args []string, _ []byte) (execx.Result, error) {
	r.calls = append(r.calls, append([]string{name}, args...))
	if r.runErr != nil {
		return execx.Result{}, r.runErr
	}
	if args[0] == "print" && !r.printed {
		return execx.Result{ExitCode: 113}, nil
	}
	return execx.Result{ExitCode: r.exit[args[0]]}, nil
}

func (r *recorder) verbs() []string {
	var out []string
	for _, c := range r.calls {
		out = append(out, c[1])
	}
	return out
}

func newAgent(t *testing.T, r *recorder) (*LaunchAgent, string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("the LaunchAgent is macOS-only and its tests use POSIX paths")
	}
	home := t.TempDir()
	return &LaunchAgent{
		GOOS:   "darwin",
		Home:   home,
		UID:    501,
		Exe:    "/Users/someone/Library/Containers/com.docker.docker/Data/extensions/k8s-resilience-engine/host/resilience-helper",
		Runner: r,
	}, home
}

func plistPath(home string) string {
	return filepath.Join(home, "Library", "LaunchAgents", Label+".plist")
}

func TestInstallWritesAPlistThatRunsOnlyRenewManagedOnAnInterval(t *testing.T) {
	r := &recorder{}
	a, home := newAgent(t, r)
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(plistPath(home))
	if err != nil {
		t.Fatal(err)
	}
	plist := string(raw)
	for _, want := range []string{
		"<string>" + Label + "</string>",
		"<string>" + a.Exe + "</string>",
		"<string>renew-managed</string>",
		"<key>StartInterval</key>", "<integer>900</integer>",
		"<key>RunAtLoad</key>", "<key>ProcessType</key>", "<string>Background</string>",
	} {
		if !strings.Contains(plist, want) {
			t.Errorf("plist lacks %q:\n%s", want, plist)
		}
	}
	if n := strings.Count(plist, "<string>renew-managed</string>"); n != 1 || strings.Contains(plist, "authorize") || strings.Contains(plist, "revoke") {
		t.Fatalf("the job may only ever run renew-managed:\n%s", plist)
	}
	st, err := os.Stat(plistPath(home))
	if err != nil || st.Mode().Perm() != 0o644 {
		t.Fatalf("mode %v err %v", st.Mode(), err)
	}
}

func TestInstallLoadsTheJobIntoTheUsersOwnSessionAndReplacesAnOldOne(t *testing.T) {
	r := &recorder{}
	a, home := newAgent(t, r)
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(r.verbs(), ","); got != "bootout,bootstrap" {
		t.Fatalf("launchctl verbs = %s (an older copy is removed first so a re-install always takes effect)", got)
	}
	last := r.calls[len(r.calls)-1]
	if last[0] != "/bin/launchctl" || last[2] != "gui/501" || last[3] != plistPath(home) {
		t.Fatalf("bootstrap call = %v", last)
	}
}

func TestInstallFailsLoudlyWhenLaunchctlRefusesTheJob(t *testing.T) {
	r := &recorder{exit: map[string]int{"bootstrap": 5}}
	a, _ := newAgent(t, r)
	if err := a.Install(bg); !errors.Is(err, ErrNotLoaded) {
		t.Fatalf("err = %v", err)
	}
}

func TestAPlistNeverCarriesASecretAndEscapesWhatItDoesCarry(t *testing.T) {
	r := &recorder{}
	a, home := newAgent(t, r)
	a.Exe = `/Applications/R&D <1>/resilience-helper`
	a.Env = map[string]string{"KUBECONFIG": "/Users/someone/.kube/a&b.yaml"}
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(plistPath(home))
	plist := string(raw)
	if strings.Contains(plist, "R&D <1>") || !strings.Contains(plist, "R&amp;D &lt;1&gt;") {
		t.Fatalf("paths must be XML-escaped:\n%s", plist)
	}
	if !strings.Contains(plist, "<key>KUBECONFIG</key>") || !strings.Contains(plist, "a&amp;b.yaml") {
		t.Fatalf("the chosen kubeconfig path must reach the job:\n%s", plist)
	}
	for _, bad := range []string{"token", "eyJ", "BEGIN CERTIFICATE", "password"} {
		if strings.Contains(plist, bad) {
			t.Fatalf("a plist must never contain %q", bad)
		}
	}
}

func TestOnlyAbsoluteCleanPathsAreAcceptedForTheExecutable(t *testing.T) {
	for _, exe := range []string{"", "resilience-helper", "./resilience-helper", "/a/../b/helper", "/a/b\nc", "/a/b\x00c"} {
		r := &recorder{}
		a, home := newAgent(t, r)
		a.Exe = exe
		if err := a.Install(bg); !errors.Is(err, ErrBadPath) {
			t.Errorf("%q: err = %v", exe, err)
		}
		if _, err := os.Stat(plistPath(home)); err == nil || len(r.calls) != 0 {
			t.Errorf("%q: nothing may be written or run for a refused path", exe)
		}
	}
}

func TestEnvironmentValuesThatAreNotAbsolutePathsAreDropped(t *testing.T) {
	r := &recorder{}
	a, home := newAgent(t, r)
	a.Env = map[string]string{"KUBECONFIG": "relative/config", "PATH": "/usr/bin", "OTHER": "/x"}
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(plistPath(home))
	if strings.Contains(string(raw), "EnvironmentVariables") {
		t.Fatalf("only an absolute KUBECONFIG may be passed on:\n%s", raw)
	}
}

func TestUninstallUnloadsTheJobAndDeletesTheFileAndIsHarmlessTwice(t *testing.T) {
	r := &recorder{}
	a, home := newAgent(t, r)
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	r.calls = nil
	if err := a.Uninstall(bg); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(plistPath(home)); !os.IsNotExist(err) {
		t.Fatalf("the plist must be gone: %v", err)
	}
	if got := strings.Join(r.verbs(), ","); got != "bootout" {
		t.Fatalf("verbs = %s", got)
	}
	if err := a.Uninstall(bg); err != nil {
		t.Fatalf("removing something already removed must succeed: %v", err)
	}
}

func TestStatusSaysOffOnOrNeedsRepair(t *testing.T) {
	r := &recorder{}
	a, _ := newAgent(t, r)
	if got, _ := a.Status(bg); got != Off {
		t.Fatalf("before install: %v", got)
	}
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	r.printed = true
	if got, _ := a.Status(bg); got != On {
		t.Fatalf("after install: %v", got)
	}
	r.printed = false // the file is there but launchd lost the job (for example after a reboot into a new user session)
	if got, _ := a.Status(bg); got != Stale {
		t.Fatalf("file but no loaded job: %v", got)
	}
	a.Exe = "/somewhere/else/resilience-helper"
	r.printed = true
	if got, _ := a.Status(bg); got != Stale {
		t.Fatalf("the job runs a binary that is no longer this one: %v", got)
	}
}

func TestOtherPlatformsAreRefusedAndNothingIsWritten(t *testing.T) {
	for _, goos := range []string{"windows", "linux"} {
		r := &recorder{}
		a, home := newAgent(t, r)
		a.GOOS = goos
		if a.Supported() {
			t.Errorf("%s must not be supported until it has been tested there", goos)
		}
		if err := a.Install(bg); !errors.Is(err, ErrUnsupported) {
			t.Errorf("%s: err = %v", goos, err)
		}
		if got, _ := a.Status(bg); got != Unsupported {
			t.Errorf("%s: status = %v", goos, got)
		}
		if _, err := os.Stat(filepath.Join(home, "Library")); err == nil || len(r.calls) != 0 {
			t.Errorf("%s: nothing may be touched", goos)
		}
	}
}
