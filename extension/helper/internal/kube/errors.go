package kube

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
)

// APIError is an error response from the API server. Message is the server's own text, which names
// the object and the reason; it never contains the credential used for the request.
type APIError struct {
	Code    int    // HTTP status
	Reason  string // metav1.StatusReason, for example NotFound, Forbidden, AlreadyExists, Conflict
	Message string
}

func (e *APIError) Error() string {
	if e.Message != "" {
		return e.Message
	}
	return fmt.Sprintf("the API server returned HTTP %d", e.Code)
}

// parseStatus turns a non-2xx response into an APIError. A Status body is used when there is one;
// otherwise the reason comes from the HTTP status alone.
func parseStatus(code int, body []byte) *APIError {
	var status struct {
		Kind    string `json:"kind"`
		Reason  string `json:"reason"`
		Message string `json:"message"`
	}
	e := &APIError{Code: code}
	if json.Unmarshal(body, &status) == nil && status.Kind == "Status" {
		e.Reason, e.Message = status.Reason, status.Message
	}
	if e.Reason == "" {
		e.Reason = map[int]string{
			http.StatusNotFound:     "NotFound",
			http.StatusForbidden:    "Forbidden",
			http.StatusUnauthorized: "Unauthorized",
			http.StatusConflict:     "Conflict",
		}[code]
	}
	return e
}

func apiError(err error) *APIError {
	var e *APIError
	if errors.As(err, &e) {
		return e
	}
	return nil
}

// IsNotFound reports whether the server said the object does not exist.
func IsNotFound(err error) bool { e := apiError(err); return e != nil && e.Code == http.StatusNotFound }

// IsForbidden reports whether the credential may not perform the request.
func IsForbidden(err error) bool {
	e := apiError(err)
	return e != nil && e.Code == http.StatusForbidden
}

// IsUnauthorized reports whether the server rejected the credential itself.
func IsUnauthorized(err error) bool {
	e := apiError(err)
	return e != nil && e.Code == http.StatusUnauthorized
}

// IsAlreadyExists reports a create of a name that is taken. It is a 409 like a write conflict, so the
// reason is what tells them apart.
func IsAlreadyExists(err error) bool {
	e := apiError(err)
	return e != nil && e.Code == http.StatusConflict && e.Reason == "AlreadyExists"
}

// IsConflict reports that the object changed since it was read.
func IsConflict(err error) bool {
	e := apiError(err)
	return e != nil && e.Code == http.StatusConflict && e.Reason != "AlreadyExists"
}

// ReasonForError is the server's reason for an error, or "" for an error that did not come from it.
func ReasonForError(err error) string {
	if e := apiError(err); e != nil {
		return e.Reason
	}
	return ""
}

// NewAPIError builds an error as the server would, for fakes and tests.
func NewAPIError(code int, reason, message string) error {
	return &APIError{Code: code, Reason: reason, Message: message}
}
