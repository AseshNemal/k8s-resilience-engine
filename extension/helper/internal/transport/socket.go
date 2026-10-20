package transport

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"resilience-engine/helper/internal/secret"
)

const (
	socketName    = "k8s-resilience-engine.sock"
	extensionName = "k8s-resilience-engine"
	// A unix socket path may not exceed 103 bytes on macOS (104 with the terminator).
	maxSocketPath = 103
)

// Socket talks HTTP to the backend over the socket Docker Desktop mirrors onto the host.
//
// VERIFIED ON macOS ONLY. On macOS the mirrored path is longer than the OS allows, so the helper
// connects through a short, private symlink to the directory. Windows and Linux are refused by
// NewSocket until someone has tested where (and whether) the socket appears there.
type Socket struct {
	Dir  string // directory that contains the socket
	File string // socket file name inside Dir
	// Timeout bounds one request. Zero means defaultSocketTimeout.
	Timeout time.Duration
}

// defaultSocketTimeout is short on purpose: a socket that does not answer is not worth waiting for
// when DockerExec can carry the same request.
const defaultSocketTimeout = 15 * time.Second

// NewSocket returns the mirrored-socket transport for a platform, or ErrUnverifiedPlatform.
func NewSocket(goos, home string) (*Socket, error) {
	if goos != "darwin" {
		return nil, fmt.Errorf("%w: %s", ErrUnverifiedPlatform, goos)
	}
	return &Socket{
		Dir:  filepath.Join(home, "Library/Containers/com.docker.docker/Data/ext-sockets", extensionName),
		File: socketName,
	}, nil
}

// Name implements Backend.
func (s *Socket) Name() string { return "socket" }

// shortTempBase prefers /tmp: the default temporary directory on macOS is a long /var/folders path
// that would use most of the socket path budget. MkdirTemp still makes a private (0700) directory.
func shortTempBase() string {
	if st, err := os.Stat("/tmp"); err == nil && st.IsDir() {
		return "/tmp"
	}
	return os.TempDir()
}

// client returns an HTTP client that dials the socket through a short symlink, and a cleanup func.
func (s *Socket) client() (*http.Client, func(), error) {
	direct := filepath.Join(s.Dir, s.File)
	dial := direct
	cleanup := func() {}
	if len(direct) > maxSocketPath {
		tmp, err := os.MkdirTemp(shortTempBase(), "res")
		if err != nil {
			return nil, nil, fmt.Errorf("%w: no temporary directory", ErrUnavailable)
		}
		link := filepath.Join(tmp, "s")
		if err := os.Symlink(s.Dir, link); err != nil {
			_ = os.RemoveAll(tmp)
			return nil, nil, fmt.Errorf("%w: could not shorten the socket path", ErrUnavailable)
		}
		cleanup = func() { _ = os.RemoveAll(tmp) }
		dial = filepath.Join(link, s.File)
		if len(dial) > maxSocketPath {
			cleanup()
			return nil, nil, fmt.Errorf("%w: the socket path is too long even when shortened", ErrUnavailable)
		}
	}
	return &http.Client{
		Timeout: s.timeout(),
		Transport: &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", dial)
		}},
	}, cleanup, nil
}

func (s *Socket) timeout() time.Duration {
	if s.Timeout > 0 {
		return s.Timeout
	}
	return defaultSocketTimeout
}

func (s *Socket) do(ctx context.Context, method string, body secret.Bytes) (Status, error) {
	client, cleanup, err := s.client()
	if err != nil {
		return Status{}, err
	}
	defer cleanup()

	var rdr io.Reader
	if !body.IsZero() {
		rdr = bytes.NewReader(body.Reveal())
	}
	req, err := http.NewRequestWithContext(ctx, method, "http://localhost/_extension/cluster", rdr)
	if err != nil {
		return Status{}, fmt.Errorf("%w: bad request", ErrUnavailable)
	}
	if rdr != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := client.Do(req)
	if err != nil {
		return Status{}, unavailable("could not reach the backend socket", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))

	if resp.StatusCode >= 400 {
		var e struct {
			Detail any `json:"detail"`
		}
		msg := fmt.Sprintf("the backend answered HTTP %d", resp.StatusCode)
		if json.Unmarshal(raw, &e) == nil {
			if text, ok := e.Detail.(string); ok && text != "" {
				msg = text
			}
		}
		return Status{}, &RejectedError{Message: msg}
	}
	var st Status
	if err := json.Unmarshal(raw, &st); err != nil {
		return Status{}, fmt.Errorf("%w: unreadable answer", ErrUnavailable)
	}
	return st, nil
}

func (s *Socket) Status(ctx context.Context) (Status, error) {
	return s.do(ctx, http.MethodGet, secret.Bytes{})
}

func (s *Socket) Install(ctx context.Context, req InstallRequest) (Status, error) {
	body, err := payload(req)
	if err != nil {
		return Status{}, err
	}
	return s.do(ctx, http.MethodPut, body)
}

func (s *Socket) Disconnect(ctx context.Context) error {
	_, err := s.do(ctx, http.MethodDelete, secret.Bytes{})
	return err
}

// Defaults returns the transports to try, in order, for a platform. The socket comes first where it
// is verified because it is faster and needs no Docker CLI; DockerExec is the fallback everywhere.
func Defaults(goos, home string, d DockerExec) []Backend {
	var out []Backend
	if s, err := NewSocket(goos, home); err == nil {
		out = append(out, s)
	}
	return append(out, d)
}
