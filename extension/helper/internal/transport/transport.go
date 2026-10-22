// Package transport delivers the credential from the host helper to the extension backend.
//
// Two candidates exist because Docker's documentation does not say how a backend socket reaches the
// host on every operating system, so the transport is verified per OS rather than assumed:
//
//   - Socket: the backend socket Docker Desktop mirrors onto the host. Verified on macOS only.
//   - DockerExec: `docker exec -i` into the backend container, payload on stdin. Verified on macOS.
//     It needs only the Docker Engine API that every Docker Desktop host exposes, so it is the one
//     most likely to carry over to Windows and Linux, but that is NOT yet verified there.
//
// In both, the credential travels in a request body or on stdin. It is never a command-line
// argument (visible in the process list), never logged, and never returned to the caller.
package transport

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"time"

	"resilience-engine/helper/internal/secret"
)

var (
	// ErrUnavailable means no backend could be reached at all.
	ErrUnavailable = errors.New("the extension backend is not reachable")
	// ErrUnverifiedPlatform means this transport has not been verified on this operating system.
	ErrUnverifiedPlatform = errors.New("this transport is not verified on this operating system")
)

// RejectedError means the backend refused the request. Message is the backend's own wording,
// which never echoes credentials.
type RejectedError struct{ Message string }

func (e *RejectedError) Error() string { return e.Message }

// Identity is what makes a cluster THE cluster a person approved: the API endpoint they connected to and the
// fingerprint of the certificate authority that signs it. Neither is secret.
type Identity struct {
	Server   string `json:"server"`   // host:port of the API server in the person's kubeconfig
	CASha256 string `json:"caSha256"` // hex SHA-256 of the cluster's certificate authority
}

// Approval is the backend's record of the one cluster the person approved. Reconnecting automatically is
// allowed only for this cluster, and never once the person has disconnected it.
type Approval struct {
	Context            string `json:"context"`
	Server             string `json:"server"`
	CASha256           string `json:"caSha256"`
	DisconnectedByUser bool   `json:"disconnectedByUser,omitempty"`
}

// Status mirrors the backend's GET /_extension/cluster. It contains no credential.
type Status struct {
	Connected bool   `json:"connected"`
	Managed   bool   `json:"managed,omitempty"`
	Context   string `json:"context,omitempty"`
	State     string `json:"state,omitempty"` // ok | renew-soon | expired
	Server    string `json:"server,omitempty"`
	ExpiresAt string `json:"expiresAt,omitempty"`
	RenewAt   string `json:"renewAt,omitempty"`
	Expired   bool   `json:"expired,omitempty"`
	// Approved is set whenever the person has approved a cluster, whether or not it is connected now.
	Approved *Approval `json:"approved,omitempty"`
}

// InstallRequest is a credential for the backend. Kubeconfig holds the token, so it is a secret.
type InstallRequest struct {
	Kubeconfig secret.Bytes
	Context    string
	ExpiresAt  time.Time // what Kubernetes reported, a fallback if the token has no readable exp
	// Origin says why the credential is being installed: authorize (the person just approved), renew or
	// reconnect (nobody was asked). The backend refuses the last two unless they match the approval it holds
	// and the person has not disconnected.
	Origin   string
	Identity *Identity
}

type Backend interface {
	Name() string
	Status(ctx context.Context) (Status, error)
	Install(ctx context.Context, req InstallRequest) (Status, error)
	Disconnect(ctx context.Context) error
}

// payload is the JSON body shared by both transports (and accepted by the Python ingest command).
func payload(req InstallRequest) (secret.Bytes, error) {
	managed := map[string]any{"context": req.Context}
	if !req.ExpiresAt.IsZero() {
		managed["expiresAt"] = req.ExpiresAt.UTC().Format(time.RFC3339)
	}
	if req.Origin != "" {
		managed["origin"] = req.Origin
	}
	if req.Identity != nil {
		managed["identity"] = req.Identity
	}
	body, err := json.Marshal(map[string]any{
		"kubeconfig": string(req.Kubeconfig.Reveal()),
		"managed":    managed,
	})
	if err != nil {
		return secret.Bytes{}, errors.New("could not encode the credential")
	}
	return secret.NewBytes(body), nil
}

// unavailable builds an ErrUnavailable. When the cause was a timeout it keeps that cause, so a caller
// can tell a Docker that is slow (a busy Docker Desktop VM) from one that is not there, and retry
// patiently instead of reporting the extension as down.
func unavailable(what string, cause error) error {
	if isTimeout(cause) {
		return fmt.Errorf("%w: %s: %w", ErrUnavailable, what, context.DeadlineExceeded)
	}
	return fmt.Errorf("%w: %s", ErrUnavailable, what)
}

func isTimeout(err error) bool {
	var ne net.Error
	return errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &ne) && ne.Timeout())
}

// Pick returns the first candidate whose Status call works, with that status.
func Pick(ctx context.Context, candidates []Backend) (Backend, Status, error) {
	var last error
	for _, c := range candidates {
		st, err := c.Status(ctx)
		if err == nil {
			return c, st, nil
		}
		last = fmt.Errorf("%s: %w", c.Name(), err)
		if ctx.Err() != nil {
			break // out of time: another candidate would only start already late
		}
	}
	if last == nil {
		last = ErrUnavailable
	}
	return nil, Status{}, fmt.Errorf("%w (%w)", ErrUnavailable, last)
}
