package approve

import (
	"context"
	"errors"
	"os/exec"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/execx"
)

// fakeRunner stands in for osascript, zenity and kdialog: it records the command and answers as told.
type fakeRunner struct {
	res   execx.Result
	err   error
	calls []call
}

type call struct {
	name  string
	args  []string
	stdin []byte
}

func (f *fakeRunner) Run(ctx context.Context, name string, args []string, stdin []byte) (execx.Result, error) {
	f.calls = append(f.calls, call{name, args, stdin})
	return f.res, f.err
}

var ctx = context.Background()

const hostile = `kind-x" & (do shell script "touch /tmp/pwned") & "`

var req = Request{Title: "Approve read-only access?", Body: "Cluster: kind-x\nCreate ServiceAccount", Timeout: 30 * time.Second}

// ---------------------------------------------------------------- macOS (osascript)

func TestMacApprovedOnlyWhenTheApproveButtonWasPressedBeforeTheTimeout(t *testing.T) {
	f := &fakeRunner{res: execx.Result{Stdout: []byte("Approve|false\n")}}
	got, err := OSAScript{Runner: f}.Confirm(ctx, req)
	if err != nil || got != Approved {
		t.Fatalf("got %v err %v", got, err)
	}
}

func TestMacCancelRaisesErrorMinus128AndIsDeclined(t *testing.T) {
	f := &fakeRunner{res: execx.Result{ExitCode: 1, Stderr: []byte("execution error: User canceled. (-128)")}}
	got, err := OSAScript{Runner: f}.Confirm(ctx, req)
	if err != nil || got != Declined {
		t.Fatalf("got %v err %v", got, err)
	}
}

func TestMacUnansweredDialogTimesOutAndApprovesNothing(t *testing.T) {
	f := &fakeRunner{res: execx.Result{Stdout: []byte("|true\n")}}
	got, _ := OSAScript{Runner: f}.Confirm(ctx, req)
	if got != TimedOut {
		t.Fatalf("got %v", got)
	}
}

func TestMacAnythingUnexpectedIsNeverTreatedAsApproval(t *testing.T) {
	for _, out := range []string{"", "Cancel|false", "approve|false", "Approve", "Approve|maybe", "Approve|false\nextra", "garbage"} {
		f := &fakeRunner{res: execx.Result{Stdout: []byte(out)}}
		got, err := OSAScript{Runner: f}.Confirm(ctx, req)
		if got == Approved {
			t.Errorf("output %q was treated as approval (err %v)", out, err)
		}
	}
}

func TestMacAFailedDialogIsAnErrorNotAnApproval(t *testing.T) {
	f := &fakeRunner{res: execx.Result{ExitCode: 7, Stderr: []byte("boom")}}
	got, err := OSAScript{Runner: f}.Confirm(ctx, req)
	if err == nil || got == Approved {
		t.Fatalf("got %v err %v", got, err)
	}
	if _, err := (OSAScript{Runner: &fakeRunner{err: errors.New("no such file")}}).Confirm(ctx, req); err == nil {
		t.Fatal("not being able to run osascript must be an error")
	}
}

func TestMacCancelIsTheDefaultButtonAndApproveIsNeverTheDefault(t *testing.T) {
	f := &fakeRunner{res: execx.Result{Stdout: []byte("Cancel|false")}}
	_, _ = OSAScript{Runner: f}.Confirm(ctx, req)
	script := strings.Join(f.calls[0].args, "\n")
	if !strings.Contains(script, `default button "Cancel"`) || !strings.Contains(script, `cancel button "Cancel"`) {
		t.Fatalf("Cancel must be the default and cancel button:\n%s", script)
	}
}

func TestMacHostileNamesAreArgvItemsNeverPartOfTheScript(t *testing.T) {
	f := &fakeRunner{res: execx.Result{Stdout: []byte("Cancel|false")}}
	_, _ = OSAScript{Runner: f}.Confirm(ctx, Request{Title: hostile, Body: hostile, Timeout: time.Second})

	args := f.calls[0].args
	sep := -1
	for i, a := range args {
		if a == "--" {
			sep = i
		}
	}
	if sep < 0 {
		t.Fatal("untrusted values must come after --")
	}
	for i, a := range args[:sep] {
		if strings.Contains(a, "do shell script") || strings.Contains(a, "pwned") {
			t.Fatalf("untrusted text reached the script source at arg %d: %q", i, a)
		}
	}
	if args[sep+1] != hostile || args[sep+2] != hostile {
		t.Fatalf("untrusted text must be passed unchanged as argv items: %q", args[sep:])
	}
}

// ---------------------------------------------------------------- Linux (zenity / kdialog), fake only

func lookup(available ...string) func(string) (string, error) {
	return func(name string) (string, error) {
		for _, a := range available {
			if a == name {
				return "/usr/bin/" + name, nil
			}
		}
		return "", exec.ErrNotFound
	}
}

func TestZenityExitCodesMapToDecisions(t *testing.T) {
	for code, want := range map[int]Decision{0: Approved, 1: Declined, 5: TimedOut} {
		f := &fakeRunner{res: execx.Result{ExitCode: code}}
		got, err := Linux{Runner: f, LookPath: lookup("zenity")}.Confirm(ctx, req)
		if err != nil || got != want {
			t.Errorf("exit %d: got %v err %v want %v", code, got, err, want)
		}
	}
	f := &fakeRunner{res: execx.Result{ExitCode: 255}}
	if got, err := (Linux{Runner: f, LookPath: lookup("zenity")}).Confirm(ctx, req); err == nil || got == Approved {
		t.Fatalf("an unknown exit code must be an error, got %v %v", got, err)
	}
}

func TestZenityShowsTextLiterallyAndPassesTextAsArguments(t *testing.T) {
	f := &fakeRunner{res: execx.Result{ExitCode: 1}}
	_, _ = Linux{Runner: f, LookPath: lookup("zenity")}.Confirm(ctx, Request{Title: "<b>t</b>", Body: "<script>&amp;", Timeout: 10 * time.Second})
	a := strings.Join(f.calls[0].args, " ")
	if !strings.Contains(a, "--no-markup") {
		t.Fatal("zenity must be told not to interpret markup")
	}
	if !strings.Contains(a, "--timeout 10") || !strings.Contains(a, "--ok-label Approve") || !strings.Contains(a, "--cancel-label Cancel") {
		t.Fatalf("args = %v", f.calls[0].args)
	}
}

func TestKdialogIsUsedWhenZenityIsMissing(t *testing.T) {
	f := &fakeRunner{res: execx.Result{ExitCode: 0}}
	got, err := Linux{Runner: f, LookPath: lookup("kdialog")}.Confirm(ctx, req)
	if err != nil || got != Approved || !strings.HasSuffix(f.calls[0].name, "kdialog") {
		t.Fatalf("got %v err %v calls %v", got, err, f.calls)
	}
}

func TestNoDialogToolMeansNoApprovalNeverASilentApproval(t *testing.T) {
	f := &fakeRunner{}
	got, err := Linux{Runner: f, LookPath: lookup()}.Confirm(ctx, req)
	if !errors.Is(err, ErrNoApprovalChannel) || got == Approved || len(f.calls) != 0 {
		t.Fatalf("got %v err %v calls %v", got, err, f.calls)
	}
}

// ---------------------------------------------------------------- sanitising untrusted names

func TestSanitizeRemovesControlCharactersAndCapsLength(t *testing.T) {
	cases := map[string]string{
		"kind-resilience-research": "kind-resilience-research",
		"a\x00b\x1b[31mred":        "ab[31mred",
		"line1\nline2\ttab":        "line1 line2 tab",
		"  spaced   out  ":         "spaced out",
		"zero​width‮flip":          "zerowidthflip",
	}
	for in, want := range cases {
		if got := Sanitize(in, 100); got != want {
			t.Errorf("Sanitize(%q) = %q, want %q", in, got, want)
		}
	}
	if got := Sanitize(strings.Repeat("x", 500), 20); len([]rune(got)) != 20 || !strings.HasSuffix(got, "…") {
		t.Errorf("not capped: %q", got)
	}
}

func TestDefaultPrompterExistsForThisPlatform(t *testing.T) {
	if Default() == nil {
		t.Fatal("Default must return a prompter")
	}
}
