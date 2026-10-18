package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// PoC-only evidence log. It records HOW the helper was launched and WHAT it printed (as a hash),
// so a test can prove the extension UI really launched it and received exactly its output. It
// never records kubeconfig contents, and nothing here is intended to ship.

func pocDir() string {
	base, err := os.UserCacheDir()
	if err != nil {
		base = os.TempDir()
	}
	dir := filepath.Join(base, "resilience-helper-poc")
	_ = os.MkdirAll(dir, 0o700)
	return dir
}

func logEvent(kind string, fields map[string]any) {
	fields["time"] = time.Now().UTC().Format(time.RFC3339Nano)
	fields["event"] = kind
	line, err := json.Marshal(fields)
	if err != nil {
		return
	}
	f, err := os.OpenFile(filepath.Join(pocDir(), "events.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.Write(append(line, '\n'))
}

func sha(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

func run(name string, args ...string) string {
	out, err := exec.Command(name, args...).Output()
	if err != nil {
		return "error: " + err.Error()
	}
	return strings.TrimSpace(string(out))
}

// logInvocation captures the launch context once per process.
func logInvocation() {
	ppid := os.Getppid()
	_, kubectlErr := exec.LookPath("kubectl")
	cwd, _ := os.Getwd()
	logEvent("invoked", map[string]any{
		"args":           os.Args[1:],
		"uid":            os.Getuid(),
		"ppid":           ppid,
		"parent":         run("/bin/ps", "-o", "comm=", "-p", strconv.Itoa(ppid)),
		"path":           os.Getenv("PATH"),
		"kubectlOnPath":  kubectlErr == nil,
		"guiSession":     run("/bin/launchctl", "managername"),
		"hasTTY":         isTTY(),
		"cwd":            cwd,
		"executablePath": executablePath(),
	})
}

func executablePath() string {
	p, err := os.Executable()
	if err != nil {
		return ""
	}
	return p
}

func isTTY() bool {
	st, err := os.Stdin.Stat()
	return err == nil && st.Mode()&os.ModeCharDevice != 0
}

// record stores what the extension UI says it received from an earlier call (base64 so the
// payload survives argv unchanged), so the test can compare it with the hash the helper logged.
func recordReceived() {
	if len(os.Args) < 3 {
		fail("record needs one base64 argument")
	}
	raw, err := base64.StdEncoding.DecodeString(os.Args[2])
	if err != nil {
		fail("bad base64")
	}
	logEvent("ui-received", map[string]any{"sha256": sha(raw), "bytes": len(raw), "content": string(raw)})
	emit(map[string]any{"ok": true, "recorded": len(raw)})
}

// note lets the PoC UI log a short, non-secret breadcrumb (a click, a step) so a test can see what
// the page did even when the helper was never asked to do anything.
func recordNote() {
	text := ""
	if len(os.Args) > 2 {
		text = os.Args[2]
	}
	if len(text) > 200 {
		text = text[:200]
	}
	logEvent("ui-note", map[string]any{"note": text})
	emit(map[string]any{"ok": true})
}
