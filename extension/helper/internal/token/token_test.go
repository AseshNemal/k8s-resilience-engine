package token

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/kube"
	"resilience-engine/helper/internal/kube/kubetest"
)

var (
	ctx = context.Background()
	now = time.Date(2026, 10, 19, 12, 0, 0, 0, time.UTC)
)

const tokenValue = "eyJ-super-secret-token-value"

// serve makes the fake API answer TokenRequests with a token that expires after `grant`, whatever
// was asked for, and records the request so a test can check what was asked.
func serve(cs *kubetest.Fake, grant time.Duration, asked *int64) {
	cs.IssueTokens(func(namespace, serviceAccount string, seconds int64) (kube.Token, error) {
		if namespace != "resilience-engine" || serviceAccount != "resilience-engine-reader" {
			return kube.Token{}, kube.NewAPIError(404, "NotFound", "serviceaccounts not found")
		}
		if asked != nil {
			*asked = seconds
		}
		return kube.Token{Value: tokenValue, ExpiresAt: now.Add(grant)}, nil
	})
}

func TestAsksForSevenDaysAndReportsWhatKubernetesGranted(t *testing.T) {
	const week = 7 * 24 * time.Hour
	cs := kubetest.New()
	var asked int64
	serve(cs, week, &asked)

	got, err := Issue(ctx, cs, RequestedLifetime, now)
	if err != nil {
		t.Fatal(err)
	}
	if asked != int64(week.Seconds()) {
		t.Fatalf("asked for %d seconds", asked)
	}
	if got.ExpiresAt != now.Add(week) || got.Lifetime() != week {
		t.Fatalf("expiry %v lifetime %v", got.ExpiresAt, got.Lifetime())
	}
	if got.Token.Reveal() != tokenValue {
		t.Fatal("the token must be delivered to the caller")
	}
}

func TestTheRealExpiryIsUsedWhenTheClusterShortensTheToken(t *testing.T) {
	cs := kubetest.New()
	serve(cs, 2*time.Hour, nil) // asked for 7 days, cluster clamps to 2h

	got, err := Issue(ctx, cs, RequestedLifetime, now)
	if err != nil {
		t.Fatal(err)
	}
	if got.Lifetime() != 2*time.Hour {
		t.Fatalf("lifetime = %v, want the granted 2h and not the requested 8h", got.Lifetime())
	}
}

func TestATokenThatExpiresTooSoonIsRefused(t *testing.T) {
	cs := kubetest.New()
	serve(cs, 2*time.Minute, nil)
	if _, err := Issue(ctx, cs, RequestedLifetime, now); !errors.Is(err, ErrTooShortLived) {
		t.Fatalf("err = %v", err)
	}
}

func TestAMissingExpiryIsRefusedNotGuessed(t *testing.T) {
	cs := kubetest.New()
	cs.IssueTokens(func(string, string, int64) (kube.Token, error) { return kube.Token{Value: tokenValue}, nil })
	if _, err := Issue(ctx, cs, RequestedLifetime, now); !errors.Is(err, ErrNoExpiry) {
		t.Fatalf("err = %v", err)
	}
}

func TestAnEmptyTokenIsRefused(t *testing.T) {
	cs := kubetest.New()
	cs.IssueTokens(func(string, string, int64) (kube.Token, error) { return kube.Token{ExpiresAt: now.Add(time.Hour)}, nil })
	if _, err := Issue(ctx, cs, RequestedLifetime, now); err == nil {
		t.Fatal("an empty token must be an error")
	}
}

func TestForbiddenAndMissingServiceAccountAreDistinctErrors(t *testing.T) {
	for name, tc := range map[string]struct {
		err  error
		want error
	}{
		"forbidden": {kube.NewAPIError(403, "Forbidden", "x"), ErrForbidden},
		"not found": {kube.NewAPIError(404, "NotFound", "x"), ErrServiceAccountMissing},
	} {
		t.Run(name, func(t *testing.T) {
			cs := kubetest.New()
			cs.IssueTokens(func(string, string, int64) (kube.Token, error) { return kube.Token{}, tc.err })
			if _, err := Issue(ctx, cs, RequestedLifetime, now); !errors.Is(err, tc.want) {
				t.Fatalf("err = %v, want %v", err, tc.want)
			}
		})
	}
}

func TestANonPositiveRequestIsRejectedBeforeAskingTheCluster(t *testing.T) {
	cs := kubetest.New()
	if _, err := Issue(ctx, cs, 0, now); err == nil {
		t.Fatal("want an error")
	}
	if len(cs.Actions()) != 0 {
		t.Fatal("the cluster must not be contacted")
	}
}

func TestTheTokenNeverAppearsInFormattedOutputOrErrors(t *testing.T) {
	cs := kubetest.New()
	serve(cs, 8*time.Hour, nil)
	got, _ := Issue(ctx, cs, RequestedLifetime, now)

	for _, verb := range []string{"%v", "%+v", "%#v", "%s", "%q"} {
		if out := fmt.Sprintf(verb, got); strings.Contains(out, tokenValue) {
			t.Fatalf("%s leaked: %s", verb, out)
		}
	}
	cs2 := kubetest.New()
	serve(cs2, time.Minute, nil)
	_, err := Issue(ctx, cs2, RequestedLifetime, now)
	if err == nil || strings.Contains(err.Error(), tokenValue) {
		t.Fatalf("error must exist and not contain the token: %v", err)
	}
}
