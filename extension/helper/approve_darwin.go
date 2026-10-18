//go:build darwin

package main

import (
	"context"
	"os/exec"
	"strings"
	"time"
)

// native approval dialog on macOS, via osascript. Every untrusted value (cluster and context names
// come from the user's kubeconfig) is passed as an argv item to the AppleScript, never spliced into
// its source, so a hostile context name cannot inject script.
func approveNative(title, body string, giveUpAfter time.Duration) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), giveUpAfter+10*time.Second)
	defer cancel()
	script := []string{
		"on run argv",
		"set r to display dialog (item 2 of argv) with title (item 1 of argv) buttons {\"Cancel\", \"Approve\"} default button \"Cancel\" cancel button \"Cancel\" with icon caution giving up after (item 3 of argv as integer)",
		"return (button returned of r) & \"|\" & (gave up of r)",
		"end run",
	}
	args := make([]string, 0, 2*len(script)+4)
	for _, line := range script {
		args = append(args, "-e", line)
	}
	args = append(args, "--", title, body, itoaSeconds(giveUpAfter))
	out, err := exec.CommandContext(ctx, "/usr/bin/osascript", args...).CombinedOutput()
	text := strings.TrimSpace(string(out))
	if err != nil {
		if strings.Contains(text, "User canceled") || strings.Contains(text, "-128") {
			return "declined", nil
		}
		return "", err
	}
	switch {
	case strings.HasPrefix(text, "Approve|false"):
		return "approved", nil
	case strings.HasSuffix(text, "|true"):
		return "timed-out", nil
	default:
		return "declined", nil
	}
}
