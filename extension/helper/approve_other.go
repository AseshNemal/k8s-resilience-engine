//go:build !darwin

package main

import (
	"errors"
	"time"
)

// Windows and Linux native dialogs are designed but NOT implemented in this proof of concept, and
// nothing here may be read as support for those systems.
func approveNative(title, body string, giveUpAfter time.Duration) (string, error) {
	return "", errors.New("native approval is not implemented on this platform in the proof of concept")
}
