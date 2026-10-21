package main

import (
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"

	"resilience-engine/helper/internal/agent"
	"resilience-engine/helper/internal/execx"
)

// quietName is the Windows helper built without a console window. The background task runs it so
// nothing flashes on screen every few minutes; it sits next to the normal helper.
const quietName = "resilience-helper-bg.exe"

// quietSibling returns the console-less helper next to exe, or "" when it is not there (the task then
// runs the normal helper, which only means a console window may appear briefly).
func quietSibling(exe string, stat func(string) (os.FileInfo, error)) string {
	candidate := filepath.Join(filepath.Dir(exe), quietName)
	if st, err := stat(candidate); err == nil && !st.IsDir() {
		return candidate
	}
	return ""
}

// hasSystemdUserSession reports whether a systemd user manager can be driven from here. /run/systemd/system
// exists only when systemd is the running init, so containers and WSL without systemd are excluded.
func hasSystemdUserSession() bool {
	if st, err := os.Stat("/run/systemd/system"); err != nil || !st.IsDir() {
		return false
	}
	_, err := exec.LookPath("systemctl")
	return err == nil
}

// realAgent is the background renewal job for this user. It points at this very binary, resolved
// through any symlink, so the job keeps working wherever Docker Desktop keeps its host binaries.
func realAgent(home string) agent.Controller {
	exe, err := os.Executable()
	if err == nil {
		if resolved, rerr := filepath.EvalSymlinks(exe); rerr == nil {
			exe = resolved
		}
	}
	opts := agent.Options{
		GOOS: runtime.GOOS, Home: home, UID: os.Getuid(), Exe: exe,
		Env:    map[string]string{"KUBECONFIG": os.Getenv("KUBECONFIG"), "XDG_CONFIG_HOME": os.Getenv("XDG_CONFIG_HOME")},
		Runner: execx.Real{},
	}
	switch runtime.GOOS {
	case "windows":
		if u, uerr := user.Current(); uerr == nil {
			opts.User = u.Username // DOMAIN\name on Windows
		}
		opts.Quiet = quietSibling(exe, os.Stat)
	case "linux":
		opts.Systemd = hasSystemdUserSession()
	}
	return agent.New(opts)
}
