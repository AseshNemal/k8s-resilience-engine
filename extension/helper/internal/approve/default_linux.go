//go:build linux

package approve

import (
	"os/exec"

	"resilience-engine/helper/internal/execx"
)

// Default is the native dialog for this operating system (UNVERIFIED on a real desktop).
func Default() Prompter { return Linux{Runner: execx.Real{}, LookPath: exec.LookPath} }
