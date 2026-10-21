package agent

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"resilience-engine/helper/internal/execx"
)

const linuxExe = "/home/someone/.docker/desktop/extensions/k8s-resilience-engine/host/resilience-helper"

func newTimer(t *testing.T, r *scripted) (*SystemdTimer, string) {
	t.Helper()
	home := t.TempDir()
	return &SystemdTimer{GOOS: "linux", Home: home, Exe: linuxExe, HasSystemd: true, Runner: r}, home
}

func unitDirOf(home string) string { return filepath.Join(home, ".config", "systemd", "user") }

func read(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func verbs(r *scripted) []string {
	var out []string
	for _, c := range r.calls {
		out = append(out, strings.Join(c[1:], " "))
	}
	return out
}

func TestTimerInstallWritesUnitsThatRunOnlyRenewManagedAndEnablesTheTimer(t *testing.T) {
	r := &scripted{}
	a, home := newTimer(t, r)
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	service := read(t, filepath.Join(unitDirOf(home), UnitName+".service"))
	for _, w := range []string{"Type=oneshot", `ExecStart="` + linuxExe + `" renew-managed`, "TimeoutStartSec=5min"} {
		if !strings.Contains(service, w) {
			t.Errorf("service lacks %q:\n%s", w, service)
		}
	}
	timer := read(t, filepath.Join(unitDirOf(home), UnitName+".timer"))
	for _, w := range []string{"OnCalendar=*:0/15", "Persistent=true", "OnStartupSec=1min", "Unit=" + UnitName + ".service", "WantedBy=timers.target"} {
		if !strings.Contains(timer, w) {
			t.Errorf("timer lacks %q:\n%s", w, timer)
		}
	}
	got := strings.Join(verbs(r), " | ")
	want := "--user daemon-reload | --user enable --now " + UnitName + ".timer"
	if got != want {
		t.Errorf("systemctl calls = %q, want %q", got, want)
	}
	for _, c := range r.calls {
		if c[0] != systemctl || c[1] != "--user" {
			t.Errorf("only the user manager may be driven, ran %v", c)
		}
	}
}

func TestTimerHonoursXDGConfigHomeAndPassesOnAnAbsoluteKubeconfig(t *testing.T) {
	r := &scripted{}
	a, home := newTimer(t, r)
	xdg := filepath.Join(home, "xdg")
	a.Env = map[string]string{"XDG_CONFIG_HOME": filepath.ToSlash(xdg), "KUBECONFIG": "/home/someone/a.yaml:/home/someone/b.yaml"}
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	service := read(t, filepath.Join(xdg, "systemd", "user", UnitName+".service"))
	if !strings.Contains(service, `Environment="KUBECONFIG=/home/someone/a.yaml:/home/someone/b.yaml"`) {
		t.Errorf("KUBECONFIG was not passed on:\n%s", service)
	}
}

func TestTimerDropsEnvironmentValuesThatAreNotPlainAbsolutePaths(t *testing.T) {
	for _, bad := range []string{"relative.yaml", "/ok.yaml:rel.yaml", `/a"b.yaml`, "/a%h.yaml", "/a$HOME.yaml", "/a/../b.yaml", "/a\nb.yaml"} {
		a, _ := newTimer(t, &scripted{})
		a.Env = map[string]string{"KUBECONFIG": bad}
		if strings.Contains(a.service(), "KUBECONFIG") {
			t.Errorf("KUBECONFIG %q must be dropped:\n%s", bad, a.service())
		}
	}
	a, home := newTimer(t, &scripted{})
	a.Env = map[string]string{"XDG_CONFIG_HOME": "relative/dir"}
	if a.unitDir() != unitDirOf(home) {
		t.Errorf("a relative XDG_CONFIG_HOME must be ignored, got %s", a.unitDir())
	}
}

func TestOnlyPlainUnixPathsAreAcceptedForTheTimerExecutable(t *testing.T) {
	for _, bad := range []string{"", "resilience-helper", "/usr/../bin/h", "/usr//bin/h", `/a"b/h`, `/a\b/h`, "/a%b/h", "/a$b/h", "/a'b/h", "/a\nb/h", "/usr/bin/"} {
		r := &scripted{}
		a, home := newTimer(t, r)
		a.Exe = bad
		if err := a.Install(bg); !errors.Is(err, ErrBadPath) {
			t.Errorf("Install accepted %q (err=%v)", bad, err)
		}
		if len(r.calls) != 0 {
			t.Errorf("nothing may run for %q: %v", bad, r.calls)
		}
		if _, err := os.Stat(unitDirOf(home)); !os.IsNotExist(err) {
			t.Errorf("nothing may be written for %q", bad)
		}
	}
}

func TestTimerInstallFailsLoudlyWhenSystemdRefusesIt(t *testing.T) {
	for name, fail := range map[string]string{"daemon-reload": "daemon-reload", "enable": "enable"} {
		r := &scripted{answer: func(args []string) execx.Result {
			for _, a := range args {
				if a == fail {
					return execx.Result{ExitCode: 1}
				}
			}
			return execx.Result{}
		}}
		a, _ := newTimer(t, r)
		if err := a.Install(bg); !errors.Is(err, ErrNotLoaded) {
			t.Errorf("%s: err = %v, want ErrNotLoaded", name, err)
		}
	}
}

func TestTimerUninstallStopsTheTimerDeletesBothFilesAndIsHarmlessTwice(t *testing.T) {
	r := &scripted{}
	a, home := newTimer(t, r)
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	r.calls = nil
	for i := 0; i < 2; i++ {
		if err := a.Uninstall(bg); err != nil {
			t.Fatalf("run %d: %v", i, err)
		}
	}
	for _, ext := range []string{".service", ".timer"} {
		if _, err := os.Stat(filepath.Join(unitDirOf(home), UnitName+ext)); !os.IsNotExist(err) {
			t.Errorf("%s was not removed", ext)
		}
	}
	if verbs(r)[0] != "--user disable --now "+UnitName+".timer" {
		t.Errorf("the timer must be stopped first: %v", verbs(r))
	}
}

func TestTimerStatusSaysOffOnOrNeedsRepair(t *testing.T) {
	healthy := func([]string) execx.Result { return execx.Result{} }
	notActive := func(args []string) execx.Result {
		if args[1] == "is-active" {
			return execx.Result{ExitCode: 3}
		}
		return execx.Result{}
	}
	r := &scripted{answer: healthy}
	a, home := newTimer(t, r)
	if s, err := a.Status(bg); err != nil || s != Off {
		t.Fatalf("before installing: (%v, %v)", s, err)
	}
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	if s, _ := a.Status(bg); s != On {
		t.Errorf("installed and running: %v", s)
	}
	r.answer = notActive
	if s, _ := a.Status(bg); s != Stale {
		t.Errorf("timer not active: %v", s)
	}
	r.answer = healthy
	moved := *a
	moved.Exe = "/somewhere/else/resilience-helper"
	if s, _ := moved.Status(bg); s != Stale {
		t.Errorf("points at another binary: %v", s)
	}
	r.calls = nil
	_, _ = a.Status(bg)
	for _, v := range verbs(r) {
		if !strings.HasPrefix(v, "--user is-") {
			t.Errorf("Status must only read, ran %q", v)
		}
	}
	_ = home
}

func TestTimerIsUnsupportedWithoutASystemdUserSession(t *testing.T) {
	for name, set := range map[string]func(*SystemdTimer){
		"no systemd":     func(a *SystemdTimer) { a.HasSystemd = false },
		"another system": func(a *SystemdTimer) { a.GOOS = "darwin" },
	} {
		r := &scripted{}
		a, home := newTimer(t, r)
		set(a)
		if a.Supported() {
			t.Errorf("%s: must not claim support", name)
		}
		if err := a.Install(bg); !errors.Is(err, ErrUnsupported) {
			t.Errorf("%s: Install err = %v", name, err)
		}
		if err := a.Uninstall(bg); err != nil {
			t.Errorf("%s: Uninstall err = %v", name, err)
		}
		if s, _ := a.Status(bg); s != Unsupported {
			t.Errorf("%s: Status = %v", name, s)
		}
		if len(r.calls) != 0 {
			t.Errorf("%s: nothing may run: %v", name, r.calls)
		}
		if _, err := os.Stat(unitDirOf(home)); !os.IsNotExist(err) {
			t.Errorf("%s: nothing may be written", name)
		}
	}
}

func TestNewPicksTheJobForTheHost(t *testing.T) {
	cases := map[string]struct {
		o   Options
		typ string
		sup bool
	}{
		"darwin":        {Options{GOOS: "darwin"}, "*agent.LaunchAgent", true},
		"windows":       {Options{GOOS: "windows"}, "*agent.ScheduledTask", true},
		"linux systemd": {Options{GOOS: "linux", Systemd: true}, "*agent.SystemdTimer", true},
		"linux none":    {Options{GOOS: "linux"}, "*agent.SystemdTimer", false},
		"other":         {Options{GOOS: "plan9"}, "*agent.LaunchAgent", false},
	}
	for name, c := range cases {
		got := New(c.o)
		if typ := fmt.Sprintf("%T", got); typ != c.typ {
			t.Errorf("%s: type %s, want %s", name, typ, c.typ)
		}
		if got.Supported() != c.sup {
			t.Errorf("%s: Supported = %v, want %v", name, got.Supported(), c.sup)
		}
		if got.Supported() && got.Describe() == "" {
			t.Errorf("%s: the dialog needs a description", name)
		}
	}
}
