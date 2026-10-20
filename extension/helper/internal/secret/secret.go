// Package secret holds values that must never reach a log, an error message or JSON output.
//
// A token wrapped in these types prints as "[redacted]" under every standard formatting verb, in
// JSON and in structured logs. The only way to read the value is the explicit Reveal call, which a
// reviewer can grep for: it is called where a credential is genuinely handed to its destination.
package secret

import (
	"fmt"
	"log/slog"
)

const redacted = "[redacted]"

// String is a secret text value such as a bearer token.
type String struct{ value string }

func NewString(value string) String { return String{value: value} }

// Reveal returns the secret. Call it only where it is handed to its destination.
func (s String) Reveal() string { return s.value }

func (s String) IsZero() bool { return s.value == "" }

func (String) String() string               { return redacted }
func (String) GoString() string             { return redacted }
func (String) Format(f fmt.State, _ rune)   { _, _ = f.Write([]byte(redacted)) }
func (String) MarshalJSON() ([]byte, error) { return []byte(`"` + redacted + `"`), nil }
func (String) MarshalText() ([]byte, error) { return []byte(redacted), nil }
func (String) LogValue() slog.Value         { return slog.StringValue(redacted) }

// Bytes is a secret document such as a kubeconfig that embeds a token.
type Bytes struct{ value []byte }

func NewBytes(value []byte) Bytes { return Bytes{value: value} }

// Reveal returns the secret bytes. Call it only where they are handed to their destination.
func (b Bytes) Reveal() []byte { return b.value }

func (b Bytes) IsZero() bool { return len(b.value) == 0 }

func (Bytes) String() string               { return redacted }
func (Bytes) GoString() string             { return redacted }
func (Bytes) Format(f fmt.State, _ rune)   { _, _ = f.Write([]byte(redacted)) }
func (Bytes) MarshalJSON() ([]byte, error) { return []byte(`"` + redacted + `"`), nil }
func (Bytes) MarshalText() ([]byte, error) { return []byte(redacted), nil }
func (Bytes) LogValue() slog.Value         { return slog.StringValue(redacted) }
