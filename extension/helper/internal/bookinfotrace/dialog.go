package bookinfotrace

import (
	"fmt"
	"strings"
	"time"

	"resilience-engine/helper/internal/approve"
)

// maxBody is the longest dialog text the native dialogs show reliably. A preview that does not fit is refused, not
// cut: an approval of a list the person could not read in full is not an approval.
const maxBody = 1800

// ErrPreviewTooLong means the exact preview does not fit in the dialog.
var ErrPreviewTooLong = fmt.Errorf("the preview is too long to show in full")

// ApplyDialog is the approval for the cluster changes. It lists every change in the preview, in order, so what is
// approved is exactly what the Plan showed. It never says "may": these are the changes.
func ApplyDialog(p PlanResult) (approve.Request, error) {
	var b strings.Builder
	fmt.Fprintf(&b, "Cluster: %s\n\nThis sets up Istio tracing for the Bookinfo demo. It CHANGES the cluster, in this order:\n", approve.Sanitize(p.Context, 60))
	for _, c := range p.Preview {
		line := fmt.Sprintf("%d. %s %s", c.Step, c.Action, c.Kind)
		if c.Name != "" && c.Action != "recreate" {
			line += " " + c.Name
		}
		if c.Action == "recreate" {
			line += fmt.Sprintf(" (%d): %s", len(c.Restarts), strings.Join(c.Restarts, ", "))
		}
		fmt.Fprintf(&b, "%s\n", line)
	}
	fmt.Fprintf(&b, "\nMemory is checked after the Istio install and again at the end. If it is too high the setup stops, says why, and removes only what this run created. ")
	b.WriteString("Your Jaeger container is not changed. The extension's read-only Kubernetes access is not changed: this uses your own kubeconfig, inside the helper, for this setup only. Everything can be undone from the extension.")
	body := b.String()
	if len([]rune(body)) > maxBody {
		return approve.Request{}, ErrPreviewTooLong
	}
	return approve.Request{Title: "Resilience Engine: set up Bookinfo tracing?", Body: body, Timeout: 2 * time.Minute}, nil
}

// UndoDialog is the approval for the rollback. It lists what will be removed and what is kept.
func UndoDialog(contextName string, removeReviewsV2 bool, restarts []string) (approve.Request, error) {
	var b strings.Builder
	fmt.Fprintf(&b, "Cluster: %s\n\nThis removes the Bookinfo tracing setup and restores the original benchmark:\n", approve.Sanitize(contextName, 60))
	b.WriteString("1. delete the temporary client Pod, if it still exists\n2. delete the DestinationRule and VirtualService for reviews\n")
	step := 3
	if removeReviewsV2 {
		fmt.Fprintf(&b, "%d. delete Deployment reviews-v2 (created by this setup)\n", step)
		step++
	}
	fmt.Fprintf(&b, "%d. remove the istio-injection label from the bookinfo namespace\n", step)
	step++
	fmt.Fprintf(&b, "%d. recreate %d Pods without a sidecar, one at a time: %s\n", step, len(restarts), strings.Join(restarts, ", "))
	step++
	fmt.Fprintf(&b, "%d. delete the Telemetry and ServiceEntry\n%d. uninstall Istio and delete its namespace and CRDs\n", step, step+1)
	b.WriteString("\nThe original Bookinfo Deployments are not edited. Afterwards the result is checked against the snapshot taken before the setup. Jaeger is not changed.")
	body := b.String()
	if len([]rune(body)) > maxBody {
		return approve.Request{}, ErrPreviewTooLong
	}
	return approve.Request{Title: "Resilience Engine: remove Bookinfo tracing?", Body: body, Timeout: 2 * time.Minute}, nil
}
