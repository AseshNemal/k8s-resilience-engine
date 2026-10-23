package agent

import (
	"context"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"strings"
	"unicode"

	"resilience-engine/helper/internal/execx"
)

const (
	// UnitName names the service and timer files of the Linux job.
	UnitName = "resilience-engine-renew"

	systemctl = "systemctl"
)

// SystemdTimer is the Linux implementation: a systemd user timer that starts a one-shot user service
// running `resilience-helper renew-managed`. It runs as the person, needs no privileges, and is
// stopped by removing two files. A Linux system without a systemd user session (a container, WSL
// without systemd, Alpine) reports itself unsupported instead of failing.
//
// UNVERIFIED on a real Linux desktop: the units are checked by tests and the package compiles for
// Linux, but they have not been loaded by a real systemd.
type SystemdTimer struct {
	GOOS       string
	Home       string
	Exe        string            // absolute path of the helper binary the job runs
	Env        map[string]string // only an absolute KUBECONFIG and XDG_CONFIG_HOME are ever used
	HasSystemd bool              // a systemd user session is available
	Runner     execx.Runner
}

func (s *SystemdTimer) Supported() bool { return s.GOOS == "linux" && s.HasSystemd }

func (s *SystemdTimer) Describe() string {
	return "a systemd user timer in your user account, in ~/.config/systemd/user"
}

// unitDir is where a user's own units live: $XDG_CONFIG_HOME/systemd/user, or ~/.config/systemd/user.
func (s *SystemdTimer) unitDir() string {
	if base := s.Env["XDG_CONFIG_HOME"]; plainUnixPath(base) {
		return filepath.Join(filepath.FromSlash(base), "systemd", "user")
	}
	return filepath.Join(s.Home, ".config", "systemd", "user")
}

func (s *SystemdTimer) servicePath() string { return filepath.Join(s.unitDir(), UnitName+".service") }
func (s *SystemdTimer) timerPath() string   { return filepath.Join(s.unitDir(), UnitName+".timer") }

// plainUnixPath reports whether p is an absolute, clean path that can sit inside a quoted systemd
// setting uninterpreted: no control characters, and none of the characters systemd treats specially
// there (quotes, backslash escapes, % specifiers and $ expansion). It uses package path, so it gives
// the same answer whatever system runs it.
func plainUnixPath(p string) bool {
	if p == "" || !path.IsAbs(p) || path.Clean(p) != p {
		return false
	}
	return !strings.ContainsFunc(p, unicode.IsControl) && !strings.ContainsAny(p, `"'\%$`)
}

// kubeconfigEnv returns the KUBECONFIG value to pass on, or "" when there is none worth passing.
// A job started by systemd does not see the variables of the person's shell, so one chosen there
// would otherwise be ignored. A relative or odd value is dropped.
func (s *SystemdTimer) kubeconfigEnv() string {
	v := s.Env["KUBECONFIG"]
	if v == "" {
		return ""
	}
	for _, p := range strings.Split(v, ":") {
		if !plainUnixPath(p) {
			return ""
		}
	}
	return v
}

func (s *SystemdTimer) execLine() string { return `ExecStart="` + s.Exe + `" renew-managed` }

func (s *SystemdTimer) service() string {
	var b strings.Builder
	b.WriteString("[Unit]\nDescription=Resilience Engine: renew the cluster token\n\n[Service]\nType=oneshot\n")
	b.WriteString(s.execLine() + "\n")
	if kc := s.kubeconfigEnv(); kc != "" {
		b.WriteString(`Environment="KUBECONFIG=` + kc + `"` + "\n")
	}
	b.WriteString("TimeoutStartSec=5min\n")
	return b.String()
}

func (s *SystemdTimer) timer() string {
	return "[Unit]\nDescription=Resilience Engine: check the cluster token every " +
		fmt.Sprint(int(Interval.Minutes())) + " minutes\n\n[Timer]\n" +
		"OnStartupSec=1min\n" +
		"OnCalendar=*:0/" + fmt.Sprint(int(Interval.Minutes())) + "\n" +
		"Persistent=true\n" +
		"Unit=" + UnitName + ".service\n\n[Install]\nWantedBy=timers.target\n"
}

func (s *SystemdTimer) ctl(ctx context.Context, args ...string) (execx.Result, error) {
	return s.Runner.Run(ctx, systemctl, append([]string{"--user"}, args...), nil)
}

// Install writes both units and enables the timer, replacing any earlier copy, so installing again
// repairs a stale job (for example one that points at a binary that has since moved).
func (s *SystemdTimer) Install(ctx context.Context) error {
	if !s.Supported() {
		return fmt.Errorf("%w: %s", ErrUnsupported, s.GOOS)
	}
	if !plainUnixPath(s.Exe) || s.Home == "" || strings.ContainsFunc(s.Home, unicode.IsControl) {
		return ErrBadPath
	}
	if err := os.MkdirAll(s.unitDir(), 0o755); err != nil {
		return fmt.Errorf("could not create %s: %w", s.unitDir(), err)
	}
	for file, content := range map[string]string{s.servicePath(): s.service(), s.timerPath(): s.timer()} {
		if err := writeFileAtomic(file, content, 0o644); err != nil {
			return fmt.Errorf("could not write the background job: %w", err)
		}
	}
	if res, err := s.ctl(ctx, "daemon-reload"); err != nil || res.ExitCode != 0 {
		return ErrNotLoaded
	}
	if res, err := s.ctl(ctx, "enable", "--now", UnitName+".timer"); err != nil || res.ExitCode != 0 {
		return ErrNotLoaded
	}
	return nil
}

// Uninstall stops and disables the timer and deletes both files. Removing what is already gone succeeds.
func (s *SystemdTimer) Uninstall(ctx context.Context) error {
	if !s.Supported() {
		return nil // nothing was ever installed here
	}
	_, _ = s.ctl(ctx, "disable", "--now", UnitName+".timer") // it may not be enabled
	for _, file := range []string{s.timerPath(), s.servicePath()} {
		if err := os.Remove(file); err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("could not remove the background job: %w", err)
		}
	}
	_, _ = s.ctl(ctx, "daemon-reload")
	return nil
}

// Status reads; it never changes anything.
func (s *SystemdTimer) Status(ctx context.Context) (State, error) {
	if !s.Supported() {
		return Unsupported, nil
	}
	raw, err := os.ReadFile(s.servicePath())
	if os.IsNotExist(err) {
		return Off, nil
	}
	if err != nil {
		return Off, fmt.Errorf("could not read the background job: %w", err)
	}
	if !strings.Contains(string(raw), s.execLine()+"\n") {
		return Stale, nil
	}
	for _, verb := range []string{"is-enabled", "is-active"} {
		res, err := s.ctl(ctx, verb, UnitName+".timer")
		if err != nil || res.ExitCode != 0 {
			return Stale, nil
		}
	}
	return On, nil
}
