// Package agent keeps the extension's credential renewed when nobody has the extension open.
//
// The extension page renews the token while it is open, but a closed Docker Desktop window, a laptop
// that slept, or a restart can leave a gap longer than the token lasts. On macOS a LaunchAgent closes
// that gap: every few minutes launchd runs `resilience-helper renew-managed`, which does nothing
// unless the stored token is due and otherwise follows exactly the rules of a manual renewal (no
// dialog, no cluster writes, never recreating revoked access).
//
// The job is a per-user LaunchAgent, not a system daemon: it runs as the person, needs no privileges
// and is stopped by removing one file. It is installed only after the person approved in the native
// dialog, and removed when they remove the access. The plist holds a path and an interval; it never
// holds a credential.
//
// Each operating system has its own per-user job with the same rules: a LaunchAgent on macOS
// (this file), a Task Scheduler task on Windows (taskscheduler.go) and a systemd user timer on Linux
// (systemd.go). New picks the one for the host.
//
// Verification status: macOS is the only one tried on a real machine. The Windows and Linux jobs are
// covered by tests against a fake runner and compile for their systems, but have not been run there.
package agent

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"

	"resilience-engine/helper/internal/execx"
)

const (
	// Label identifies the job to launchd and names the plist file.
	Label = "com.sliit.resilience-engine.renew"
	// Interval is how often launchd runs the renewal check. The check itself is cheap and does
	// nothing while the token is not due, so frequent runs cost one status read.
	Interval = 15 * time.Minute

	launchctl = "/bin/launchctl"
)

// State is what Status reports.
type State string

const (
	On          State = "on"          // installed, loaded, and pointing at this helper
	Off         State = "off"         // not installed
	Stale       State = "stale"       // installed but not loaded, or pointing at another binary: installing again repairs it
	Unsupported State = "unsupported" // this operating system has no verified background job
)

var (
	ErrUnsupported = errors.New("a background renewal job is not verified on this operating system")
	ErrBadPath     = errors.New("the helper's own path is not a plain absolute path")
	ErrNotLoaded   = errors.New("the system did not accept the background job")
)

// Controller is what the flows need, so tests never touch launchd.
type Controller interface {
	Supported() bool
	// Describe says, in one phrase, what the job is and where it lives, for the approval dialog.
	Describe() string
	Install(ctx context.Context) error
	Uninstall(ctx context.Context) error
	Status(ctx context.Context) (State, error)
}

// Options describes the host the job is for. Fields an operating system does not use are ignored.
type Options struct {
	GOOS    string
	Home    string
	UID     int               // macOS: the launchd domain
	User    string            // Windows: DOMAIN\name the task runs as
	Exe     string            // absolute path of the helper binary the job runs
	Quiet   string            // Windows: a copy of Exe built without a console window, preferred when set
	Env     map[string]string // only an absolute KUBECONFIG (or XDG_CONFIG_HOME) is ever used
	Systemd bool              // Linux: a systemd user session is available
	Runner  execx.Runner
}

// New returns the background job for the host in o. Any other system gets a LaunchAgent that reports
// itself unsupported, so callers never need a nil check.
func New(o Options) Controller {
	switch o.GOOS {
	case "windows":
		return &ScheduledTask{GOOS: o.GOOS, User: o.User, Exe: o.Exe, Quiet: o.Quiet, Runner: o.Runner}
	case "linux":
		return &SystemdTimer{GOOS: o.GOOS, Home: o.Home, Exe: o.Exe, Env: o.Env, HasSystemd: o.Systemd, Runner: o.Runner}
	default:
		return &LaunchAgent{GOOS: o.GOOS, Home: o.Home, UID: o.UID, Exe: o.Exe, Env: o.Env, Runner: o.Runner}
	}
}

// LaunchAgent is the macOS implementation.
type LaunchAgent struct {
	GOOS   string
	Home   string
	UID    int
	Exe    string            // absolute path of the helper binary the job runs
	Env    map[string]string // only an absolute KUBECONFIG is ever passed on
	Runner execx.Runner
}

func (a *LaunchAgent) Supported() bool { return a.GOOS == "darwin" }

func (a *LaunchAgent) Describe() string {
	return "a macOS LaunchAgent in your user account, in ~/Library/LaunchAgents"
}

func (a *LaunchAgent) plistPath() string {
	return filepath.Join(a.Home, "Library", "LaunchAgents", Label+".plist")
}

func (a *LaunchAgent) logPath() string {
	return filepath.Join(a.Home, "Library", "Logs", "ResilienceEngine", "renew.log")
}

func (a *LaunchAgent) domain() string { return fmt.Sprintf("gui/%d", a.UID) }

// plain reports whether p is an absolute, already-clean path with no control characters, so it can
// be written into a plist and run without any interpretation.
func plain(p string) bool {
	if p == "" || !filepath.IsAbs(p) || filepath.Clean(p) != p {
		return false
	}
	return !strings.ContainsFunc(p, unicode.IsControl)
}

// writeFileAtomic writes content next to path and renames it into place, so a reader never sees a
// half-written file.
func writeFileAtomic(path, content string, mode os.FileMode) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), ".renew-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.WriteString(content); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmp.Name(), mode); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

func xmlEscape(s string) string {
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&apos;").Replace(s)
}

// kubeconfigEnv returns the KUBECONFIG value to pass on, or "" when there is none worth passing.
// launchd starts jobs with a bare environment, so a person who selects their kubeconfig through the
// variable would otherwise have the job read a different one. A relative or odd value is dropped.
func (a *LaunchAgent) kubeconfigEnv() string {
	v := a.Env["KUBECONFIG"]
	if v == "" {
		return ""
	}
	for _, p := range filepath.SplitList(v) {
		if !plain(p) {
			return ""
		}
	}
	return v
}

func (a *LaunchAgent) plist() string {
	var b strings.Builder
	b.WriteString(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>` + Label + `</string>
	<key>ProgramArguments</key>
	<array>
		<string>` + xmlEscape(a.Exe) + `</string>
		<string>renew-managed</string>
	</array>
	<key>StartInterval</key>
	<integer>` + fmt.Sprint(int(Interval.Seconds())) + `</integer>
	<key>RunAtLoad</key>
	<true/>
	<key>ProcessType</key>
	<string>Background</string>
	<key>StandardOutPath</key>
	<string>` + xmlEscape(a.logPath()) + `</string>
	<key>StandardErrorPath</key>
	<string>` + xmlEscape(a.logPath()) + `</string>
`)
	if kc := a.kubeconfigEnv(); kc != "" {
		b.WriteString(`	<key>EnvironmentVariables</key>
	<dict>
		<key>KUBECONFIG</key>
		<string>` + xmlEscape(kc) + `</string>
	</dict>
`)
	}
	b.WriteString("</dict>\n</plist>\n")
	return b.String()
}

// Install writes the plist and loads it. It replaces any earlier copy, so installing again always
// repairs a stale job (for example one that points at a binary that has since moved).
func (a *LaunchAgent) Install(ctx context.Context) error {
	if !a.Supported() {
		return fmt.Errorf("%w: %s", ErrUnsupported, a.GOOS)
	}
	if !plain(a.Exe) || !plain(a.Home) {
		return ErrBadPath
	}
	for _, dir := range []string{filepath.Dir(a.plistPath()), filepath.Dir(a.logPath())} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return fmt.Errorf("could not create %s: %w", dir, err)
		}
	}
	tmp, err := os.CreateTemp(filepath.Dir(a.plistPath()), ".renew-*.plist")
	if err != nil {
		return fmt.Errorf("could not write the background job: %w", err)
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.WriteString(a.plist()); err != nil {
		tmp.Close()
		return fmt.Errorf("could not write the background job: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("could not write the background job: %w", err)
	}
	if err := os.Chmod(tmp.Name(), 0o644); err != nil {
		return fmt.Errorf("could not write the background job: %w", err)
	}
	if err := os.Rename(tmp.Name(), a.plistPath()); err != nil {
		return fmt.Errorf("could not write the background job: %w", err)
	}

	_, _ = a.Runner.Run(ctx, launchctl, []string{"bootout", a.domain() + "/" + Label}, nil) // an older copy may not exist
	res, err := a.Runner.Run(ctx, launchctl, []string{"bootstrap", a.domain(), a.plistPath()}, nil)
	if err != nil || res.ExitCode != 0 {
		return ErrNotLoaded
	}
	return nil
}

// Uninstall unloads the job and deletes its file. Removing what is already gone succeeds.
func (a *LaunchAgent) Uninstall(ctx context.Context) error {
	if !a.Supported() {
		return nil // nothing was ever installed here
	}
	_, _ = a.Runner.Run(ctx, launchctl, []string{"bootout", a.domain() + "/" + Label}, nil)
	if err := os.Remove(a.plistPath()); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("could not remove the background job: %w", err)
	}
	return nil
}

// Status reads; it never changes anything.
func (a *LaunchAgent) Status(ctx context.Context) (State, error) {
	if !a.Supported() {
		return Unsupported, nil
	}
	raw, err := os.ReadFile(a.plistPath())
	if os.IsNotExist(err) {
		return Off, nil
	}
	if err != nil {
		return Off, fmt.Errorf("could not read the background job: %w", err)
	}
	if !strings.Contains(string(raw), "<string>"+xmlEscape(a.Exe)+"</string>") {
		return Stale, nil
	}
	res, err := a.Runner.Run(ctx, launchctl, []string{"print", a.domain() + "/" + Label}, nil)
	if err != nil || res.ExitCode != 0 {
		return Stale, nil
	}
	return On, nil
}
