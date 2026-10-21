// Package token issues the extension's short-lived read-only bearer token.
//
// The requested lifetime is only a request. The API server may shorten it, so the expiry that
// matters is the one Kubernetes returns in the TokenRequest status. That real value is reported to
// the caller and never replaced by the requested duration.
package token

import (
	"context"
	"errors"
	"fmt"
	"time"

	"resilience-engine/helper/internal/kube"
	"resilience-engine/helper/internal/rbac"
	"resilience-engine/helper/internal/secret"
)

const (
	// RequestedLifetime is what is asked for. Renewal timing is derived from what is returned.
	// A week, not a day: the credential must outlive a long weekend with the laptop shut, so that the
	// next renewal attempt always finds a token that is still valid to renew from. It is read-only
	// and the person can remove the access at any time, which is what bounds the risk.
	RequestedLifetime = 7 * 24 * time.Hour
	// MinimumLifetime guards against a cluster clamping the token to something unusable.
	MinimumLifetime = 10 * time.Minute
)

var (
	ErrForbidden             = errors.New("your kubeconfig is not allowed to issue tokens for the extension's service account")
	ErrServiceAccountMissing = errors.New("the extension's service account does not exist")
	ErrNoExpiry              = errors.New("the cluster did not report when the token expires")
	ErrTooShortLived         = errors.New("the cluster issued a token that expires too soon to be useful")
)

// Issued is a token with the times Kubernetes reported. It prints as redacted.
type Issued struct {
	Token     secret.String
	IssuedAt  time.Time
	ExpiresAt time.Time
}

// Lifetime is the real lifetime Kubernetes granted.
func (i Issued) Lifetime() time.Duration { return i.ExpiresAt.Sub(i.IssuedAt) }

// Issue requests a token for the extension's service account. now is the request time.
func Issue(ctx context.Context, cs kube.Client, requested time.Duration, now time.Time) (Issued, error) {
	if requested <= 0 {
		return Issued{}, errors.New("requested token lifetime must be positive")
	}
	resp, err := cs.CreateToken(ctx, rbac.Namespace, rbac.Name, int64(requested.Seconds()))
	switch {
	case kube.IsForbidden(err):
		return Issued{}, ErrForbidden
	case kube.IsNotFound(err):
		return Issued{}, ErrServiceAccountMissing
	case err != nil:
		return Issued{}, fmt.Errorf("could not issue a token: %w", err)
	}

	if resp.Value == "" {
		return Issued{}, errors.New("the cluster returned an empty token")
	}
	expires := resp.ExpiresAt
	if expires.IsZero() {
		return Issued{}, ErrNoExpiry
	}
	if expires.Sub(now) < MinimumLifetime {
		return Issued{}, fmt.Errorf("%w (%s)", ErrTooShortLived, expires.Sub(now).Round(time.Second))
	}
	return Issued{Token: secret.NewString(resp.Value), IssuedAt: now, ExpiresAt: expires}, nil
}
