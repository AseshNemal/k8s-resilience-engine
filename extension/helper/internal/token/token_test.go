package token

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	authv1 "k8s.io/api/authentication/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/kubernetes/fake"
	k8stesting "k8s.io/client-go/testing"
)

var (
	ctx = context.Background()
	now = time.Date(2026, 10, 19, 12, 0, 0, 0, time.UTC)
)

const tokenValue = "eyJ-super-secret-token-value"

// serve makes the fake API answer TokenRequests with a token that expires after `grant`, whatever
// was asked for, and records the request so a test can check what was asked.
func serve(cs *fake.Clientset, grant time.Duration, asked *int64) {
	cs.PrependReactor("create", "serviceaccounts", func(a k8stesting.Action) (bool, runtime.Object, error) {
		if a.GetSubresource() != "token" {
			return false, nil, nil
		}
		req := a.(k8stesting.CreateAction).GetObject().(*authv1.TokenRequest)
		if asked != nil && req.Spec.ExpirationSeconds != nil {
			*asked = *req.Spec.ExpirationSeconds
		}
		return true, &authv1.TokenRequest{Status: authv1.TokenRequestStatus{
			Token:               tokenValue,
			ExpirationTimestamp: metav1.NewTime(now.Add(grant)),
		}}, nil
	})
}

func TestAsksForEightHoursAndReportsWhatKubernetesGranted(t *testing.T) {
	cs := fake.NewClientset()
	var asked int64
	serve(cs, 8*time.Hour, &asked)

	got, err := Issue(ctx, cs, RequestedLifetime, now)
	if err != nil {
		t.Fatal(err)
	}
	if asked != int64((8 * time.Hour).Seconds()) {
		t.Fatalf("asked for %d seconds", asked)
	}
	if got.ExpiresAt != now.Add(8*time.Hour) || got.Lifetime() != 8*time.Hour {
		t.Fatalf("expiry %v lifetime %v", got.ExpiresAt, got.Lifetime())
	}
	if got.Token.Reveal() != tokenValue {
		t.Fatal("the token must be delivered to the caller")
	}
}

func TestTheRealExpiryIsUsedWhenTheClusterShortensTheToken(t *testing.T) {
	cs := fake.NewClientset()
	serve(cs, 2*time.Hour, nil) // asked for 8h, cluster clamps to 2h

	got, err := Issue(ctx, cs, RequestedLifetime, now)
	if err != nil {
		t.Fatal(err)
	}
	if got.Lifetime() != 2*time.Hour {
		t.Fatalf("lifetime = %v, want the granted 2h and not the requested 8h", got.Lifetime())
	}
}

func TestATokenThatExpiresTooSoonIsRefused(t *testing.T) {
	cs := fake.NewClientset()
	serve(cs, 2*time.Minute, nil)
	if _, err := Issue(ctx, cs, RequestedLifetime, now); !errors.Is(err, ErrTooShortLived) {
		t.Fatalf("err = %v", err)
	}
}

func TestAMissingExpiryIsRefusedNotGuessed(t *testing.T) {
	cs := fake.NewClientset()
	cs.PrependReactor("create", "serviceaccounts", func(a k8stesting.Action) (bool, runtime.Object, error) {
		return true, &authv1.TokenRequest{Status: authv1.TokenRequestStatus{Token: tokenValue}}, nil
	})
	if _, err := Issue(ctx, cs, RequestedLifetime, now); !errors.Is(err, ErrNoExpiry) {
		t.Fatalf("err = %v", err)
	}
}

func TestAnEmptyTokenIsRefused(t *testing.T) {
	cs := fake.NewClientset()
	cs.PrependReactor("create", "serviceaccounts", func(a k8stesting.Action) (bool, runtime.Object, error) {
		return true, &authv1.TokenRequest{Status: authv1.TokenRequestStatus{ExpirationTimestamp: metav1.NewTime(now.Add(time.Hour))}}, nil
	})
	if _, err := Issue(ctx, cs, RequestedLifetime, now); err == nil {
		t.Fatal("an empty token must be an error")
	}
}

func TestForbiddenAndMissingServiceAccountAreDistinctErrors(t *testing.T) {
	for name, tc := range map[string]struct {
		err  error
		want error
	}{
		"forbidden": {apierrors.NewForbidden(schema.GroupResource{Resource: "serviceaccounts/token"}, "x", nil), ErrForbidden},
		"not found": {apierrors.NewNotFound(schema.GroupResource{Resource: "serviceaccounts"}, "x"), ErrServiceAccountMissing},
	} {
		t.Run(name, func(t *testing.T) {
			cs := fake.NewClientset()
			cs.PrependReactor("create", "serviceaccounts", func(a k8stesting.Action) (bool, runtime.Object, error) {
				return true, nil, tc.err
			})
			if _, err := Issue(ctx, cs, RequestedLifetime, now); !errors.Is(err, tc.want) {
				t.Fatalf("err = %v, want %v", err, tc.want)
			}
		})
	}
}

func TestANonPositiveRequestIsRejectedBeforeAskingTheCluster(t *testing.T) {
	cs := fake.NewClientset()
	if _, err := Issue(ctx, cs, 0, now); err == nil {
		t.Fatal("want an error")
	}
	if len(cs.Actions()) != 0 {
		t.Fatal("the cluster must not be contacted")
	}
}

func TestTheTokenNeverAppearsInFormattedOutputOrErrors(t *testing.T) {
	cs := fake.NewClientset()
	serve(cs, 8*time.Hour, nil)
	got, _ := Issue(ctx, cs, RequestedLifetime, now)

	for _, verb := range []string{"%v", "%+v", "%#v", "%s", "%q"} {
		if out := fmt.Sprintf(verb, got); strings.Contains(out, tokenValue) {
			t.Fatalf("%s leaked: %s", verb, out)
		}
	}
	cs2 := fake.NewClientset()
	serve(cs2, time.Minute, nil)
	_, err := Issue(ctx, cs2, RequestedLifetime, now)
	if err == nil || strings.Contains(err.Error(), tokenValue) {
		t.Fatalf("error must exist and not contain the token: %v", err)
	}
}
