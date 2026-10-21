package agent

import (
	"context"
	"errors"
	"os"
	"strings"
	"testing"
	"unicode/utf16"

	"resilience-engine/helper/internal/execx"
)

// scripted plays an external program: it records every call and answers from a function. When
// onRun is set it also sees the call, so a test can read a file the code deletes afterwards.
type scripted struct {
	calls  [][]string
	answer func(args []string) execx.Result
	onRun  func(args []string)
	err    error
}

func (s *scripted) Run(_ context.Context, name string, args []string, _ []byte) (execx.Result, error) {
	s.calls = append(s.calls, append([]string{name}, args...))
	if s.onRun != nil {
		s.onRun(args)
	}
	if s.err != nil {
		return execx.Result{}, s.err
	}
	if s.answer == nil {
		return execx.Result{}, nil
	}
	return s.answer(args), nil
}

const winExe = `C:\Users\Someone\AppData\Local\Docker\extensions\k8s-resilience-engine\host\resilience-helper.exe`

func newTask(r *scripted) *ScheduledTask {
	return &ScheduledTask{GOOS: "windows", User: `DESKTOP\Someone`, Exe: winExe, Runner: r}
}

func decodeUTF16(t *testing.T, raw []byte) string {
	t.Helper()
	if len(raw) < 2 || raw[0] != 0xFF || raw[1] != 0xFE {
		t.Fatalf("schtasks needs a UTF-16 little endian byte order mark, got % x", raw[:min(4, len(raw))])
	}
	units := make([]uint16, 0, len(raw)/2)
	for i := 2; i+1 < len(raw); i += 2 {
		units = append(units, uint16(raw[i])|uint16(raw[i+1])<<8)
	}
	return string(utf16.Decode(units))
}

// capture returns an onRun hook that reads the XML file handed to schtasks /Create.
func capture(t *testing.T, into *string) func([]string) {
	return func(args []string) {
		if args[0] != "/Create" {
			return
		}
		raw, err := os.ReadFile(args[indexOf(args, "/XML")+1])
		if err != nil {
			t.Fatalf("the task file must exist while schtasks reads it: %v", err)
		}
		*into = decodeUTF16(t, raw)
	}
}

func indexOf(args []string, want string) int {
	for i, a := range args {
		if a == want {
			return i
		}
	}
	return -1
}

func TestTaskInstallRegistersAPerUserTaskThatRunsOnlyRenewManaged(t *testing.T) {
	var xml string
	r := &scripted{onRun: capture(t, &xml)}
	a := newTask(r)
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	want := []string{schtasks, "/Create", "/TN", TaskName}
	for i, w := range want {
		if r.calls[0][i] != w {
			t.Fatalf("call = %v, want it to start with %v", r.calls[0], want)
		}
	}
	if r.calls[0][len(r.calls[0])-1] != "/F" {
		t.Errorf("installing again must replace the task: %v", r.calls[0])
	}
	for _, w := range []string{
		"<Command>" + winExe + "</Command>", "<Arguments>renew-managed</Arguments>",
		"<Interval>PT15M</Interval>", "<StartWhenAvailable>true</StartWhenAvailable>",
		"<RunLevel>LeastPrivilege</RunLevel>", "<LogonType>InteractiveToken</LogonType>",
		`<UserId>DESKTOP\Someone</UserId>`, "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
		"<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
	} {
		if !strings.Contains(xml, w) {
			t.Errorf("task lacks %q:\n%s", w, xml)
		}
	}
	if strings.Contains(xml, "HighestAvailable") || strings.Contains(strings.ToLower(xml), "password") {
		t.Errorf("the task must neither elevate nor hold a password:\n%s", xml)
	}
}

func TestTaskPrefersTheConsoleLessHelperSoNothingFlashesOnScreen(t *testing.T) {
	var xml string
	r := &scripted{onRun: capture(t, &xml)}
	a := newTask(r)
	a.Quiet = `C:\Users\Someone\AppData\Local\Docker\extensions\k8s-resilience-engine\host\resilience-helper-bg.exe`
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(xml, "<Command>"+a.Quiet+"</Command>") || strings.Contains(xml, "<Command>"+winExe) {
		t.Errorf("the task must run the console-less copy:\n%s", xml)
	}
}

func TestTaskXMLEscapesWhatItCarryAndLeavesTheTemporaryFileBehindNowhere(t *testing.T) {
	var xml, file string
	r := &scripted{onRun: func(args []string) {
		capture(t, &xml)(args)
		file = args[indexOf(args, "/XML")+1]
	}}
	a := newTask(r)
	a.User = `CORP\A&B'x`
	if err := a.Install(bg); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(xml, `<UserId>CORP\A&amp;B&apos;x</UserId>`) {
		t.Errorf("user was not escaped:\n%s", xml)
	}
	if _, err := os.Stat(file); !os.IsNotExist(err) {
		t.Errorf("the task file must be deleted after use (err=%v)", err)
	}
}

func TestOnlyPlainWindowsDrivePathsAreAcceptedForTheExecutable(t *testing.T) {
	for _, bad := range []string{
		"", "resilience-helper.exe", `\Users\x\h.exe`, `C:h.exe`, `C:/Users/x/h.exe`, `C:\Users\..\h.exe`,
		`C:\Users\.\h.exe`, `C:\Users\\h.exe`, `\\server\share\h.exe`, `C:\a"b\h.exe`, `C:\a<b>\h.exe`,
		`C:\a|b\h.exe`, `C:\a*\h.exe`, "C:\\a\nb\\h.exe", `C:\a\h.exe\`, `C:\a:b\h.exe`,
	} {
		r := &scripted{}
		a := newTask(r)
		a.Exe = bad
		if err := a.Install(bg); !errors.Is(err, ErrBadPath) {
			t.Errorf("Install accepted %q (err=%v)", bad, err)
		}
		if len(r.calls) != 0 {
			t.Errorf("nothing may run for %q: %v", bad, r.calls)
		}
	}
	r := &scripted{}
	a := newTask(r)
	a.Quiet = `bg.exe`
	if err := a.Install(bg); !errors.Is(err, ErrBadPath) {
		t.Errorf("a relative console-less path must be refused (err=%v)", err)
	}
	a.Quiet, a.User = "", "x\ny"
	if err := a.Install(bg); !errors.Is(err, ErrBadPath) {
		t.Errorf("a user name with control characters must be refused (err=%v)", err)
	}
}

func TestTaskInstallFailsLoudlyWhenSchtasksRefusesIt(t *testing.T) {
	for name, r := range map[string]*scripted{
		"non-zero":     {answer: func([]string) execx.Result { return execx.Result{ExitCode: 1} }},
		"cannot start": {err: errors.New("not found")},
	} {
		if err := newTask(r).Install(bg); !errors.Is(err, ErrNotLoaded) {
			t.Errorf("%s: err = %v, want ErrNotLoaded", name, err)
		}
	}
}

func TestTaskUninstallDeletesWhatExistsAndIsHarmlessTwice(t *testing.T) {
	exists := true
	r := &scripted{answer: func(args []string) execx.Result {
		switch args[0] {
		case "/Query":
			if exists {
				return execx.Result{Stdout: []byte("<Task/>")}
			}
			return execx.Result{ExitCode: 1}
		case "/Delete":
			exists = false
		}
		return execx.Result{}
	}}
	a := newTask(r)
	for i := 0; i < 2; i++ {
		if err := a.Uninstall(bg); err != nil {
			t.Fatalf("run %d: %v", i, err)
		}
	}
	deletes := 0
	for _, c := range r.calls {
		if c[1] == "/Delete" {
			deletes++
			if c[2] != "/TN" || c[3] != TaskName {
				t.Errorf("delete names the wrong task: %v", c)
			}
		}
	}
	if deletes != 1 {
		t.Errorf("deleted %d times, want once", deletes)
	}

	exists = true
	r.answer = func(args []string) execx.Result {
		if args[0] == "/Delete" {
			return execx.Result{ExitCode: 1}
		}
		return execx.Result{}
	}
	if err := a.Uninstall(bg); err == nil {
		t.Error("a task that cannot be deleted must be reported")
	}
}

func TestTaskStatusSaysOffOnOrNeedsRepair(t *testing.T) {
	task := func(cmd string) *scripted {
		return &scripted{answer: func([]string) execx.Result {
			return execx.Result{Stdout: []byte("<Command>" + cmd + "</Command>")}
		}}
	}
	cases := map[string]struct {
		r    *scripted
		exe  string
		want State
	}{
		"not installed":    {&scripted{answer: func([]string) execx.Result { return execx.Result{ExitCode: 1} }}, winExe, Off},
		"installed":        {task(winExe), winExe, On},
		"points elsewhere": {task(`C:\old\resilience-helper.exe`), winExe, Stale},
		"letters beyond ASCII cannot be compared": {task(`C:\Users\Jos?\h.exe`), `C:\Users\José\h.exe`, On},
	}
	for name, c := range cases {
		a := newTask(c.r)
		a.Exe = c.exe
		got, err := a.Status(bg)
		if err != nil || got != c.want {
			t.Errorf("%s: got (%v, %v), want %v", name, got, err, c.want)
		}
	}
	for _, c := range cases {
		for _, call := range c.r.calls {
			if call[1] != "/Query" {
				t.Errorf("Status must only read, ran %v", call)
			}
		}
	}
}

func TestTaskIsRefusedOnOtherSystemsAndNothingRuns(t *testing.T) {
	r := &scripted{}
	a := newTask(r)
	a.GOOS = "linux"
	if a.Supported() {
		t.Error("the Windows task must not claim support elsewhere")
	}
	if err := a.Install(bg); !errors.Is(err, ErrUnsupported) {
		t.Errorf("Install err = %v", err)
	}
	if err := a.Uninstall(bg); err != nil {
		t.Errorf("Uninstall err = %v", err)
	}
	if s, _ := a.Status(bg); s != Unsupported {
		t.Errorf("Status = %v", s)
	}
	if len(r.calls) != 0 {
		t.Errorf("nothing may run: %v", r.calls)
	}
}
