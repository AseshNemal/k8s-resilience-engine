package approve

import (
	"context"
	"fmt"
	"strings"
	"time"

	"resilience-engine/helper/internal/execx"
)

// OSAScript shows the macOS dialog through osascript. Untrusted text (cluster and context names)
// is passed as argv items to a fixed script, never spliced into the script's source, so a hostile
// context name cannot inject AppleScript.
type OSAScript struct{ Runner execx.Runner }

const osascriptPath = "/usr/bin/osascript"

var osascriptSource = []string{
	"on run argv",
	`set r to display dialog (item 2 of argv) with title (item 1 of argv) buttons {"Cancel", "Approve"} default button "Cancel" cancel button "Cancel" with icon caution giving up after (item 3 of argv as integer)`,
	`return (button returned of r) & "|" & (gave up of r)`,
	"end run",
}

func (o OSAScript) Confirm(ctx context.Context, req Request) (Decision, error) {
	wait := seconds(req.Timeout)
	ctx, cancel := context.WithTimeout(ctx, time.Duration(wait)*time.Second+10*time.Second)
	defer cancel()

	args := make([]string, 0, 2*len(osascriptSource)+4)
	for _, line := range osascriptSource {
		args = append(args, "-e", line)
	}
	args = append(args, "--", req.Title, req.Body, fmt.Sprint(wait))

	res, err := o.Runner.Run(ctx, osascriptPath, args, nil)
	if err != nil {
		if ctx.Err() != nil {
			return TimedOut, nil
		}
		return "", fmt.Errorf("could not show the approval dialog: %w", err)
	}
	out := strings.TrimSpace(string(res.Stdout))
	text := out + " " + string(res.Stderr)
	if res.ExitCode != 0 {
		if strings.Contains(text, "-128") || strings.Contains(text, "User canceled") {
			return Declined, nil // the Cancel button raises error -128
		}
		return "", fmt.Errorf("the approval dialog failed (exit %d)", res.ExitCode)
	}
	switch {
	case strings.HasSuffix(out, "|true"):
		return TimedOut, nil
	case out == "Approve|false":
		return Approved, nil
	default:
		return Declined, nil // anything unexpected is never treated as approval
	}
}
