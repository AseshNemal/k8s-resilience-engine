package transport

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"resilience-engine/helper/internal/execx"
	"resilience-engine/helper/internal/secret"
)

const (
	composeProject = "k8s-resilience-engine-desktop-extension"
	composeService = "resilience-engine"
	backendUser    = "10001"
)

var containerID = regexp.MustCompile(`^[0-9a-f]{12,64}$`)

// DockerExec runs the backend's ingest command inside its container. The credential is sent on
// stdin; every argument is fixed or a validated container id.
type DockerExec struct {
	Runner     execx.Runner
	FindDocker func() (string, error)
}

func (DockerExec) Name() string { return "docker-exec" }

// DockerCandidates lists where a Docker Desktop CLI usually lives, so the helper works without
// docker being on PATH (a GUI-launched helper may not inherit the user's shell PATH).
func DockerCandidates(goos, home string) []string {
	switch goos {
	case "darwin":
		return []string{
			filepath.Join(home, ".docker/bin/docker"),
			"/usr/local/bin/docker",
			"/Applications/Docker.app/Contents/Resources/bin/docker",
		}
	case "windows":
		return []string{
			`C:\Program Files\Docker\Docker\resources\bin\docker.exe`,
			filepath.Join(home, `AppData\Local\Docker\resources\bin\docker.exe`),
		}
	default:
		return []string{"/usr/bin/docker", "/usr/local/bin/docker", "/opt/docker-desktop/bin/docker"}
	}
}

// FindDocker prefers a docker on PATH and falls back to the usual Docker Desktop locations.
func FindDocker(lookPath func(string) (string, error), stat func(string) (os.FileInfo, error), goos, home string) (string, error) {
	if p, err := lookPath("docker"); err == nil {
		return p, nil
	}
	for _, p := range DockerCandidates(goos, home) {
		if st, err := stat(p); err == nil && !st.IsDir() {
			return p, nil
		}
	}
	return "", errors.New("the Docker CLI was not found")
}

func (d DockerExec) docker() (string, error) {
	if d.FindDocker == nil {
		return "", errors.New("the Docker CLI was not found")
	}
	return d.FindDocker()
}

// container finds the one running backend container by its compose labels.
func (d DockerExec) container(ctx context.Context, docker string) (string, error) {
	res, err := d.Runner.Run(ctx, docker, []string{
		"ps", "-q",
		"--filter", "label=com.docker.compose.project=" + composeProject,
		"--filter", "label=com.docker.compose.service=" + composeService,
		"--filter", "status=running",
	}, nil)
	if err != nil || res.ExitCode != 0 {
		return "", fmt.Errorf("%w: could not ask Docker for the backend container", ErrUnavailable)
	}
	var ids []string
	for _, line := range strings.Fields(string(res.Stdout)) {
		if !containerID.MatchString(line) {
			return "", fmt.Errorf("%w: unexpected container id from Docker", ErrUnavailable)
		}
		ids = append(ids, line)
	}
	switch len(ids) {
	case 0:
		return "", fmt.Errorf("%w: the extension backend container is not running", ErrUnavailable)
	case 1:
		return ids[0], nil
	default:
		return "", fmt.Errorf("%w: more than one backend container matched", ErrUnavailable)
	}
}

// run executes the ingest command with args and optional stdin, and parses its one-line JSON answer.
func (d DockerExec) run(ctx context.Context, args []string, stdin secret.Bytes) (Status, error) {
	docker, err := d.docker()
	if err != nil {
		return Status{}, fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	id, err := d.container(ctx, docker)
	if err != nil {
		return Status{}, err
	}
	argv := append([]string{"exec", "-i", "-u", backendUser, id, "python", "-m", "extension.cluster_ingest"}, args...)
	var in []byte
	if !stdin.IsZero() {
		in = stdin.Reveal()
	}
	res, err := d.Runner.Run(ctx, docker, argv, in)
	if err != nil {
		return Status{}, fmt.Errorf("%w: could not run the backend command", ErrUnavailable)
	}
	var out struct {
		OK    bool   `json:"ok"`
		Error string `json:"error"`
		Status
	}
	if jerr := json.Unmarshal(res.Stdout, &out); jerr != nil {
		return Status{}, fmt.Errorf("%w: the backend command gave an unreadable answer", ErrUnavailable)
	}
	if !out.OK {
		msg := out.Error
		if msg == "" {
			msg = "the backend refused the request"
		}
		return Status{}, &RejectedError{Message: msg}
	}
	return out.Status, nil
}

func (d DockerExec) Status(ctx context.Context) (Status, error) {
	return d.run(ctx, []string{"--status"}, secret.Bytes{})
}

func (d DockerExec) Install(ctx context.Context, req InstallRequest) (Status, error) {
	body, err := payload(req)
	if err != nil {
		return Status{}, err
	}
	return d.run(ctx, nil, body)
}

func (d DockerExec) Disconnect(ctx context.Context) error {
	_, err := d.run(ctx, []string{"--disconnect"}, secret.Bytes{})
	return err
}
