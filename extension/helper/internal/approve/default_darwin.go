//go:build darwin

package approve

import "resilience-engine/helper/internal/execx"

// Default is the native dialog for this operating system.
func Default() Prompter { return OSAScript{Runner: execx.Real{}} }
