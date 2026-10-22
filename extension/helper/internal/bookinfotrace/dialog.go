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

// DialogExtras are facts the approval must disclose that are not part of the plan itself.
type DialogExtras struct {
	// Download is non-empty when a tool has to be downloaded first: what and from where.
	Download string
	// IstioDigest is the content digest of the Istio objects that will be applied; the setup refuses any render that differs.
	IstioDigest string
}

// ApplyDialog is the approval for the cluster changes. It lists every change in the preview, in order, so what is
// approved is exactly what the Plan showed. It never says "may": these are the changes. It also says which Pods will be
// unavailable and for how long they may be, and that a failed step rolls back what this run created: that consent is
// part of this approval.
func ApplyDialog(p PlanResult, extras DialogExtras) (approve.Request, error) {
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
	if len(p.Disruptions) > 0 {
		b.WriteString("\nDOWNTIME: each Pod above is deleted and unavailable until its replacement is Ready (one replica each, so nothing takes over):")
		for _, d := range p.Disruptions {
			fmt.Fprintf(&b, " %s: %s;", d.Workload, d.Effect)
		}
		b.WriteString(" usually under a minute each (not measured).")
	}
	if extras.IstioDigest != "" {
		fmt.Fprintf(&b, "\n\nThe Istio objects must match the reviewed content (SHA-256 %.12s…); if they differ nothing is applied.", extras.IstioDigest)
	}
	if extras.Download != "" {
		fmt.Fprintf(&b, "\n\nAlso downloads %s", approve.Sanitize(extras.Download, 120))
	}
	b.WriteString("\n\nMemory is checked after the Istio install, while waiting and at the end. If a check fails or any step errors, the setup stops, says why, and removes only what this run created: you approve that here. ")
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
	fmt.Fprintf(&b, "%d. delete the Telemetry and ServiceEntry\n%d. remove the Istio objects this setup created (a definition other resources still use, or a namespace that holds other objects, is kept and reported)\n", step, step+1)
	b.WriteString("\nOnly objects this setup recorded as created are deleted, each by its recorded ID; anything else is left alone and reported. The original Bookinfo Deployments are not edited. Afterwards the result is checked against the snapshot taken before the setup. Jaeger is not changed.")
	body := b.String()
	if len([]rune(body)) > maxBody {
		return approve.Request{}, ErrPreviewTooLong
	}
	return approve.Request{Title: "Resilience Engine: remove Bookinfo tracing?", Body: body, Timeout: 2 * time.Minute}, nil
}

// The temporary client Pod's facts, as they appear in the dialog. A test keeps them equal to the embedded manifest.
const (
	TrafficImage    = "curlimages/curl:8.23.0"
	TrafficRequests = 60
	TrafficPodName  = "trace-demo-client"
)

// TrafficDialog is the approval for "Run test traffic", the only operation after the setup that creates anything. It
// names the one Pod, what it sends, what it does not do, and that it is removed again.
func TrafficDialog(contextName string) (approve.Request, error) {
	var b strings.Builder
	fmt.Fprintf(&b, "Cluster: %s\n\nThis CREATES one temporary Pod in the %s namespace:\n", approve.Sanitize(contextName, 60), Contract.Namespace)
	fmt.Fprintf(&b, "  %s (image %s, downloaded if the cluster does not have it; memory limit 32 MiB plus a sidecar of up to %d MiB)\n\n",
		TrafficPodName, TrafficImage, Contract.MemoryMiB.SidecarLimit)
	fmt.Fprintf(&b, "It sends %d real requests to productpage over about a minute, so Bookinfo answers and logs %d extra page views. ", TrafficRequests, TrafficRequests)
	b.WriteString("It creates no trace of its own and never contacts Jaeger: the traces come from the sidecars of the Pods that serve those requests.\n\n")
	b.WriteString("The Pod is deleted when it finishes, or if anything fails or you stop it. No existing Deployment, Pod, route or setting is changed. ")
	b.WriteString("This uses your own kubeconfig, inside the helper, for this one operation; the extension's read-only Kubernetes access is not changed.")
	body := b.String()
	if len([]rune(body)) > maxBody {
		return approve.Request{}, ErrPreviewTooLong
	}
	return approve.Request{Title: "Resilience Engine: send test traffic to Bookinfo?", Body: body, Timeout: 2 * time.Minute}, nil
}
