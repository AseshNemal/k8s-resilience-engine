package approve

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"resilience-engine/helper/internal/execx"
)

// Linux shows a dialog through zenity (GNOME and most desktops) or kdialog (KDE). UNVERIFIED on a
// real Linux desktop: it is tested here only against a fake runner.
type Linux struct {
	Runner   execx.Runner
	LookPath func(string) (string, error)
}

func (l Linux) Confirm(ctx context.Context, req Request) (Decision, error) {
	wait := seconds(req.Timeout)
	if path, err := l.LookPath("zenity"); err == nil {
		// --no-markup: the text is shown literally, so < > & in a cluster name are not interpreted.
		args := []string{
			"--question", "--no-markup", "--width", "560",
			"--title", req.Title, "--text", req.Body,
			"--ok-label", "Approve", "--cancel-label", "Cancel",
			"--timeout", strconv.Itoa(wait),
		}
		res, err := l.Runner.Run(ctx, path, args, nil)
		if err != nil {
			return "", fmt.Errorf("could not show the approval dialog: %w", err)
		}
		switch res.ExitCode {
		case 0:
			return Approved, nil
		case 1:
			return Declined, nil
		case 5:
			return TimedOut, nil
		default:
			return "", fmt.Errorf("the approval dialog failed (exit %d)", res.ExitCode)
		}
	}
	if path, err := l.LookPath("kdialog"); err == nil {
		// kdialog has no timeout option, so the context deadline closes it instead.
		ctx, cancel := context.WithTimeout(ctx, time.Duration(wait)*time.Second)
		defer cancel()
		args := []string{"--title", req.Title, "--warningyesno", req.Body, "--yes-label", "Approve", "--no-label", "Cancel"}
		res, err := l.Runner.Run(ctx, path, args, nil)
		if err != nil {
			if ctx.Err() != nil {
				return TimedOut, nil
			}
			return "", fmt.Errorf("could not show the approval dialog: %w", err)
		}
		switch res.ExitCode {
		case 0:
			return Approved, nil
		case 1:
			return Declined, nil
		default:
			return "", fmt.Errorf("the approval dialog failed (exit %d)", res.ExitCode)
		}
	}
	return "", ErrNoApprovalChannel
}
