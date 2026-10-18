package main

import (
	"runtime"
	"strconv"
	"time"
)

func itoaSeconds(d time.Duration) string { return strconv.Itoa(int(d.Seconds())) }

// approveDemo shows exactly what a real authorization request would show, then reports the answer.
// It creates nothing and changes nothing: it only proves that a native dialog can be shown and
// that its answer is readable by the helper and not by the extension UI.
func approveDemo() {
	title := "Resilience Engine: approve read-only access? (DEMO, nothing will be created)"
	body := "Cluster: kind-resilience-research (127.0.0.1)\n\n" +
		"If this were real, the helper would CREATE or UPDATE in that cluster:\n" +
		"  - Namespace resilience-engine\n" +
		"  - ServiceAccount resilience-engine-reader\n" +
		"  - ClusterRole: get, list on nodes, pods, deployments (no secrets, no writes)\n" +
		"  - ClusterRoleBinding to that service account\n\n" +
		"Then it would issue an 8-hour token and renew it automatically.\n\n" +
		"This is a demonstration. Click Cancel or wait; nothing will be changed."
	answer, err := approveNative(title, body, 30*time.Second)
	if err != nil {
		fail(err.Error())
	}
	emit(map[string]any{"ok": true, "platform": runtime.GOOS, "answer": answer, "changedAnything": false})
}
