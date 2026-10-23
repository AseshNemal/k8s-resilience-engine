package agent

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"strings"
	"unicode"
	"unicode/utf16"

	"resilience-engine/helper/internal/execx"
)

const (
	// TaskName names the job in Windows Task Scheduler.
	TaskName = "ResilienceEngine-Renew"

	schtasks = "schtasks.exe"
)

// ScheduledTask is the Windows implementation: a per-user Task Scheduler task. It runs as the person
// with their ordinary rights (no administrator, no stored password), so it works while they are
// logged in, and it catches up after the computer slept (StartWhenAvailable). Like the other
// implementations it holds a path and an interval, never a credential.
//
// UNVERIFIED on a real Windows machine: the generated task is checked by tests and the package
// compiles for Windows, but it has not been registered with a real Task Scheduler.
type ScheduledTask struct {
	GOOS   string
	User   string // DOMAIN\name the task runs as; empty lets Task Scheduler use the registering user
	Exe    string // absolute path of the helper binary
	Quiet  string // a copy of Exe built without a console window; used instead of Exe when set
	Runner execx.Runner
}

func (t *ScheduledTask) Supported() bool { return t.GOOS == "windows" }

func (t *ScheduledTask) Describe() string {
	return "a scheduled task named " + TaskName + " in your user account, in Windows Task Scheduler"
}

// command is the program the task starts. The console-less copy is preferred so that nothing
// flashes on screen every few minutes.
func (t *ScheduledTask) command() string {
	if t.Quiet != "" {
		return t.Quiet
	}
	return t.Exe
}

// plainWindowsPath reports whether p is an absolute drive path with no dot segments, wildcard,
// quote, redirection or control characters, so it can sit in the task definition uninterpreted.
// It does not use package filepath, so the check gives the same answer whatever system runs it.
func plainWindowsPath(p string) bool {
	if len(p) < 4 || p[1] != ':' || p[2] != '\\' || !unicode.IsLetter(rune(p[0])) || p[0] > 'z' {
		return false
	}
	if strings.ContainsFunc(p, unicode.IsControl) || strings.ContainsAny(p[2:], `<>"|?*/:`) {
		return false
	}
	for _, seg := range strings.Split(p[3:], `\`) {
		if seg == "" || seg == "." || seg == ".." {
			return false
		}
	}
	return true
}

func plainUser(u string) bool {
	return !strings.ContainsFunc(u, unicode.IsControl) && !strings.ContainsAny(u, `<>"|?*/`)
}

func (t *ScheduledTask) taskXML() string {
	var b strings.Builder
	b.WriteString(`<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Keeps the Resilience Engine extension's cluster token renewed. Removing the access removes this task.</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT` + fmt.Sprint(int(Interval.Minutes())) + `M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2000-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
`)
	if t.User != "" {
		b.WriteString("      <UserId>" + xmlEscape(t.User) + "</UserId>\n")
	}
	b.WriteString(`      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>` + xmlEscape(t.command()) + `</Command>
      <Arguments>renew-managed</Arguments>
    </Exec>
  </Actions>
</Task>
`)
	return b.String()
}

// utf16File is what schtasks insists on for /XML: UTF-16 little endian with a byte order mark.
func utf16File(s string) []byte {
	units := utf16.Encode([]rune(s))
	out := make([]byte, 0, 2+2*len(units))
	out = append(out, 0xFF, 0xFE)
	for _, u := range units {
		out = append(out, byte(u), byte(u>>8))
	}
	return out
}

// Install registers the task, replacing any earlier one, so installing again repairs a stale task
// (for example one that points at a binary that has since moved).
func (t *ScheduledTask) Install(ctx context.Context) error {
	if !t.Supported() {
		return fmt.Errorf("%w: %s", ErrUnsupported, t.GOOS)
	}
	if !plainWindowsPath(t.Exe) || (t.Quiet != "" && !plainWindowsPath(t.Quiet)) || !plainUser(t.User) {
		return ErrBadPath
	}
	file, err := os.CreateTemp("", "resilience-renew-*.xml")
	if err != nil {
		return fmt.Errorf("could not write the background job: %w", err)
	}
	defer os.Remove(file.Name())
	if _, err := file.Write(utf16File(t.taskXML())); err != nil {
		file.Close()
		return fmt.Errorf("could not write the background job: %w", err)
	}
	if err := file.Close(); err != nil {
		return fmt.Errorf("could not write the background job: %w", err)
	}
	res, err := t.Runner.Run(ctx, schtasks, []string{"/Create", "/TN", TaskName, "/XML", file.Name(), "/F"}, nil)
	if err != nil || res.ExitCode != 0 {
		return ErrNotLoaded
	}
	return nil
}

// query returns the registered task definition, or ok=false when there is none.
func (t *ScheduledTask) query(ctx context.Context) (xml []byte, ok bool) {
	res, err := t.Runner.Run(ctx, schtasks, []string{"/Query", "/TN", TaskName, "/XML"}, nil)
	if err != nil || res.ExitCode != 0 {
		return nil, false
	}
	return res.Stdout, true
}

// Uninstall deletes the task. Removing what is already gone succeeds.
func (t *ScheduledTask) Uninstall(ctx context.Context) error {
	if !t.Supported() {
		return nil // nothing was ever installed here
	}
	if _, ok := t.query(ctx); !ok {
		return nil
	}
	res, err := t.Runner.Run(ctx, schtasks, []string{"/Delete", "/TN", TaskName, "/F"}, nil)
	if err != nil || res.ExitCode != 0 {
		return fmt.Errorf("could not remove the background job")
	}
	return nil
}

func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

// Status reads; it never changes anything.
func (t *ScheduledTask) Status(ctx context.Context) (State, error) {
	if !t.Supported() {
		return Unsupported, nil
	}
	xml, ok := t.query(ctx)
	if !ok {
		return Off, nil
	}
	// schtasks prints in the console's code page, so a path with other letters cannot be compared
	// byte for byte; for those the task being present is all that can be checked.
	if cmd := t.command(); isASCII(cmd) && !bytes.Contains(xml, []byte(xmlEscape(cmd))) {
		return Stale, nil
	}
	return On, nil
}
