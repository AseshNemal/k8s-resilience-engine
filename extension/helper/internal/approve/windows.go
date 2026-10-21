//go:build windows

package approve

import (
	"context"
	"fmt"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

// Windows shows the system MessageBox. COMPILE-CHECKED ONLY: it has never been run on Windows.
// A MessageBox cannot relabel its buttons, so the text says what OK and Cancel mean, and Cancel is
// the default button. It cannot be cancelled from outside, so a timeout is reported and the dialog
// is left to close when the helper process exits.
const (
	mbOKCancel      = 0x00000001
	mbIconWarning   = 0x00000030
	mbDefButton2    = 0x00000100
	mbSetForeground = 0x00010000
	mbTopMost       = 0x00040000
	idOK            = 1
	idCancel        = 2
)

var messageBoxW = windows.NewLazySystemDLL("user32.dll").NewProc("MessageBoxW")

type messageBox struct{}

// Default is the native dialog for this operating system (UNVERIFIED on a real Windows machine).
func Default() Prompter { return messageBox{} }

func (messageBox) Confirm(ctx context.Context, req Request) (Decision, error) {
	title, err := windows.UTF16PtrFromString(req.Title)
	if err != nil {
		return "", fmt.Errorf("invalid dialog title: %w", err)
	}
	body, err := windows.UTF16PtrFromString(req.Body + "\n\nClick OK to approve. Click Cancel to decline.")
	if err != nil {
		return "", fmt.Errorf("invalid dialog text: %w", err)
	}
	ctx, cancel := context.WithTimeout(ctx, time.Duration(seconds(req.Timeout))*time.Second)
	defer cancel()

	answer := make(chan uintptr, 1)
	go func() {
		r, _, _ := messageBoxW.Call(0, uintptr(unsafe.Pointer(body)), uintptr(unsafe.Pointer(title)),
			mbOKCancel|mbIconWarning|mbDefButton2|mbSetForeground|mbTopMost)
		answer <- r
	}()
	select {
	case r := <-answer:
		switch r {
		case idOK:
			return Approved, nil
		case idCancel:
			return Declined, nil
		default:
			return "", fmt.Errorf("the approval dialog failed (code %d)", r)
		}
	case <-ctx.Done():
		return TimedOut, nil
	}
}
