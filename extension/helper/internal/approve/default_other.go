//go:build !darwin && !linux && !windows

package approve

import "context"

type unsupported struct{}

func (unsupported) Confirm(context.Context, Request) (Decision, error) {
	return "", ErrUnsupportedPlatform
}

// Default is a prompter that always refuses, because this operating system has no dialog.
func Default() Prompter { return unsupported{} }
