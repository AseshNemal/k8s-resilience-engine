package traceinstall

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net/http"
	"os/exec"
	"strings"
	"time"
)

// HTTPFetcher downloads over verified TLS. Redirects are followed only to GitHub's own release hosts, so a
// redirect cannot send the request somewhere else. The caller verifies the content against a pinned checksum.
type HTTPFetcher struct{ Client *http.Client }

func allowedHost(host string) bool {
	return host == "github.com" || host == "objects.githubusercontent.com" || host == "release-assets.githubusercontent.com" ||
		strings.HasSuffix(host, ".githubusercontent.com")
}

// NewHTTPFetcher builds the real downloader.
func NewHTTPFetcher() HTTPFetcher {
	return HTTPFetcher{Client: &http.Client{
		Timeout: 5 * time.Minute,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) > 5 || req.URL.Scheme != "https" || !allowedHost(req.URL.Hostname()) {
				return errors.New("redirect refused")
			}
			return nil
		},
	}}
}

func (f HTTPFetcher) Get(ctx context.Context, rawURL string, maxBytes int64) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil || req.URL.Scheme != "https" || !allowedHost(req.URL.Hostname()) {
		return nil, errors.New("address refused")
	}
	resp, err := f.Client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, errors.New("download failed")
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxBytes+1))
	if err != nil || int64(len(body)) > maxBytes {
		return nil, errors.New("download failed")
	}
	return body, nil
}

// RealExec runs a program with exactly the environment it is given.
type RealExec struct{}

func (RealExec) Run(ctx context.Context, name string, args, env []string) ([]byte, int, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Env = env
	var out bytes.Buffer
	cmd.Stdout = &out
	if err := cmd.Run(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			return out.Bytes(), exit.ExitCode(), nil
		}
		return nil, -1, err
	}
	return out.Bytes(), 0, nil
}
