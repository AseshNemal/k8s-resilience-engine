// Package approve asks the person at the keyboard to authorise a cluster change, through a native
// operating-system dialog that the extension UI cannot see or answer.
//
// That separation is the point: the dialog belongs to the helper process, so code running inside the
// extension UI (even a compromised page) cannot approve on the user's behalf. Whatever the UI sends
// is only a request; the helper acts only on an Approved decision.
//
// Verification status (be honest about it):
//   - macOS: shown and answered by a person through Docker Desktop on macOS 26 arm64 (approve and
//     time-out paths). An explicit Cancel click was not yet observed there.
//   - Linux (zenity/kdialog) and Windows (MessageBox): written and unit tested against fakes or
//     compile-checked ONLY. They have never been shown on a real Linux or Windows desktop.
package approve

import (
	"context"
	"errors"
	"strings"
	"time"
	"unicode"
)

type Decision string

const (
	Approved Decision = "approved"
	Declined Decision = "declined"
	TimedOut Decision = "timed-out" // nobody answered: nothing is approved
)

var (
	// ErrNoApprovalChannel means no dialog is available (for example Linux without zenity or
	// kdialog). The caller must not proceed; the manual kubeconfig connection remains available.
	ErrNoApprovalChannel = errors.New("no native approval dialog is available on this system")
	// ErrUnsupportedPlatform means this operating system has no approval implementation.
	ErrUnsupportedPlatform = errors.New("native approval is not implemented for this operating system")
)

// Request is what the dialog shows. Title and Body are composed by the helper from facts it read
// from the cluster; they are never text supplied by the extension UI.
type Request struct {
	Title   string
	Body    string
	Timeout time.Duration
}

type Prompter interface {
	Confirm(ctx context.Context, req Request) (Decision, error)
}

// Sanitize makes an untrusted string safe to show: it removes control and non-printing characters,
// collapses whitespace and caps the length. Kubeconfig context and cluster names are free text.
func Sanitize(s string, max int) string {
	var b strings.Builder
	space := false
	for _, r := range s {
		switch {
		case unicode.IsSpace(r):
			space = true
		case unicode.IsPrint(r) && !unicode.Is(unicode.Cf, r):
			if space && b.Len() > 0 {
				b.WriteRune(' ')
			}
			space = false
			b.WriteRune(r)
		}
	}
	out := b.String()
	if max > 0 && len([]rune(out)) > max {
		out = string([]rune(out)[:max-1]) + "…"
	}
	return out
}

func seconds(d time.Duration) int {
	if d <= 0 {
		return 90
	}
	return int(d.Seconds())
}
