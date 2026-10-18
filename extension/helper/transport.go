package main

import (
	"context"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const (
	extensionName = "k8s-resilience-engine"
	socketName    = "k8s-resilience-engine.sock"
	composeLabel  = "com.docker.compose.project=k8s-resilience-engine-desktop-extension"
)

type transportResult struct {
	Name     string `json:"name"`
	Verified bool   `json:"verified"`
	Detail   string `json:"detail"`
	Millis   int64  `json:"millis,omitempty"`
}

// Candidate A: the backend socket that Docker Desktop mirrors onto the host. The location is only
// known for macOS (measured). Windows and Linux are deliberately reported as unverified: Docker's
// documentation does not describe where, or whether, the socket is mirrored there.
func checkMirroredSocket() transportResult {
	res := transportResult{Name: "A: mirrored extension socket"}
	if runtime.GOOS != "darwin" {
		res.Detail = "unverified on " + runtime.GOOS + ": host-side socket/pipe location is undocumented and untested"
		return res
	}
	home, err := os.UserHomeDir()
	if err != nil {
		res.Detail = "no home directory"
		return res
	}
	dir := filepath.Join(home, "Library/Containers/com.docker.docker/Data/ext-sockets", extensionName)
	full := filepath.Join(dir, socketName)
	// macOS limits a unix socket path to 104 bytes; the real path is longer, so connect by a
	// relative name from inside the directory.
	if len(full) >= 104 {
		if err := os.Chdir(dir); err != nil {
			res.Detail = "cannot enter socket directory: " + err.Error()
			return res
		}
	}
	dialPath := full
	if len(full) >= 104 {
		dialPath = "./" + socketName
	}
	client := &http.Client{
		Timeout: 5 * time.Second,
		Transport: &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", dialPath)
		}},
	}
	start := time.Now()
	resp, err := client.Get("http://localhost/api/health")
	res.Millis = time.Since(start).Milliseconds()
	if err != nil {
		res.Detail = "connect failed: " + err.Error()
		return res
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	res.Verified = resp.StatusCode == 200
	res.Detail = "GET /api/health -> " + resp.Status + "; path is " + strconv.Itoa(len(full)) + " bytes (limit 104), connected by relative name"
	return res
}

func dockerBinary() string {
	if p, err := exec.LookPath("docker"); err == nil {
		return p
	}
	home, _ := os.UserHomeDir()
	for _, p := range []string{
		filepath.Join(home, ".docker/bin/docker"),
		"/usr/local/bin/docker",
		"/Applications/Docker.app/Contents/Resources/bin/docker",
	} {
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			return p
		}
	}
	return ""
}

// Candidate B: the Docker Engine API (exec into the backend container, payload on stdin). Works
// through whatever socket or pipe the local Docker CLI is configured for, and does not depend on
// the mirrored-socket location. The PoC drives the docker CLI and sends only a harmless marker;
// the real helper would use the Docker SDK so that no CLI has to be on PATH.
func checkDockerExec() transportResult {
	res := transportResult{Name: "B: docker exec, payload on stdin"}
	bin := dockerBinary()
	if bin == "" {
		res.Detail = "no docker CLI found"
		return res
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	idOut, err := exec.CommandContext(ctx, bin, "ps", "-a", "-q", "--filter", "label="+composeLabel).Output()
	id := strings.TrimSpace(string(idOut))
	if err != nil || id == "" {
		res.Detail = "backend container not found by label"
		return res
	}
	marker := "not-a-secret-marker"
	cmd := exec.CommandContext(ctx, bin, "exec", "-i", "-u", "10001", id, "sh", "-c", "cat >/dev/null; echo stdin-received")
	cmd.Stdin = strings.NewReader(marker)
	start := time.Now()
	out, err := cmd.Output()
	res.Millis = time.Since(start).Milliseconds()
	if err != nil {
		res.Detail = "exec failed: " + err.Error()
		return res
	}
	res.Verified = strings.TrimSpace(string(out)) == "stdin-received"
	res.Detail = "found container by compose label, ran as UID 10001, payload delivered via stdin (never in argv)"
	return res
}

func transportCheck() {
	emit(map[string]any{
		"ok":       true,
		"platform": runtime.GOOS + "/" + runtime.GOARCH,
		"transports": []transportResult{
			checkMirroredSocket(),
			checkDockerExec(),
		},
	})
}
