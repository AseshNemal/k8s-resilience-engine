package secret

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"testing"
)

const marker = "tok-SHOULD-NEVER-APPEAR-9f3a"

type holder struct {
	Name  string `json:"name"`
	Token String `json:"token"`
	Doc   Bytes  `json:"doc"`
}

func TestNoFormattingVerbRevealsTheValue(t *testing.T) {
	s := NewString(marker)
	b := NewBytes([]byte(marker))
	for _, verb := range []string{"%v", "%+v", "%#v", "%s", "%q", "%x", "%X", "%d", "%T%v"} {
		for _, v := range []any{s, b, &s, &b, []any{s, b}, map[string]any{"k": s}} {
			out := fmt.Sprintf(verb, v)
			if strings.Contains(out, marker) {
				t.Fatalf("verb %s leaked the secret: %q", verb, out)
			}
		}
	}
}

func TestJSONAndTextEncodingAreRedacted(t *testing.T) {
	out, err := json.Marshal(holder{Name: "n", Token: NewString(marker), Doc: NewBytes([]byte(marker))})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(out), marker) {
		t.Fatalf("JSON leaked: %s", out)
	}
	text, _ := NewString(marker).MarshalText()
	if strings.Contains(string(text), marker) {
		t.Fatal("MarshalText leaked")
	}
}

func TestStructuredLoggingIsRedacted(t *testing.T) {
	var buf bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&buf, nil))
	logger.Info("issued", "token", NewString(marker), "doc", NewBytes([]byte(marker)))
	logger.Info("issued", slog.Any("token", NewString(marker)))
	if strings.Contains(buf.String(), marker) {
		t.Fatalf("slog leaked: %s", buf.String())
	}
}

func TestWrappingInAnErrorDoesNotLeak(t *testing.T) {
	err := fmt.Errorf("install failed for %v: %w", NewString(marker), errors.New("boom"))
	if strings.Contains(err.Error(), marker) {
		t.Fatalf("error leaked: %v", err)
	}
}

func TestRevealIsTheOnlyWayToReadItAndIsExplicit(t *testing.T) {
	if NewString(marker).Reveal() != marker || string(NewBytes([]byte(marker)).Reveal()) != marker {
		t.Fatal("Reveal must return the value")
	}
	if !(String{}).IsZero() || !(Bytes{}).IsZero() || NewString("x").IsZero() {
		t.Fatal("IsZero is wrong")
	}
}
