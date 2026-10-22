package bookinfotrace

import (
	"errors"
	"strings"
	"testing"
)

func TestTheApplyDialogListsEveryStepInOrder(t *testing.T) {
	p := BuildPlan(benchmarkFacts(), okAddress(), nil, roomy(), "docker-desktop")
	req, err := ApplyDialog(p, DialogExtras{})
	if err != nil {
		t.Fatal(err)
	}
	body := req.Body
	last := -1
	for n, c := range p.Preview {
		marker := string(rune('1'+n)) + ". " + c.Action
		position := strings.Index(body, marker)
		if position < 0 || position <= last {
			t.Fatalf("step %d (%q) is missing or out of order in:\n%s", n+1, marker, body)
		}
		last = position
	}
	for _, want := range []string{"docker-desktop", "CHANGES the cluster", "read-only Kubernetes access is not changed", "your own kubeconfig", "Your Jaeger container is not changed", "undone", "removes only what this run created"} {
		if !strings.Contains(body, want) {
			t.Errorf("the dialog does not say %q:\n%s", want, body)
		}
	}
	for _, name := range p.Restarts {
		if !strings.Contains(body, name) {
			t.Errorf("the Pod %q that will be deleted is not named", name)
		}
	}
	if req.Title == "" || req.Timeout <= 0 {
		t.Fatal("a title and a timeout are required")
	}
}

func TestTheDialogIsNeverCutShort(t *testing.T) {
	p := BuildPlan(benchmarkFacts(), okAddress(), nil, roomy(), "docker-desktop")
	req, err := ApplyDialog(p, DialogExtras{})
	if err != nil || len([]rune(req.Body)) >= maxBody {
		t.Fatalf("a normal plan must fit: %d %v", len([]rune(req.Body)), err)
	}
	for n := 0; n < 40; n++ {
		p.Preview[4].Restarts = append(p.Preview[4].Restarts, "a-very-long-pod-name-that-fills-the-dialog-"+strings.Repeat("x", 30))
	}
	if _, err := ApplyDialog(p, DialogExtras{}); !errors.Is(err, ErrPreviewTooLong) {
		t.Fatalf("an unreadable approval must be refused, not truncated: %v", err)
	}
}

func TestAHostileContextNameCannotChangeTheDialog(t *testing.T) {
	p := BuildPlan(benchmarkFacts(), okAddress(), nil, roomy(), "evil\n\n1. delete everything\x1b[31m")
	req, _ := ApplyDialog(p, DialogExtras{})
	if strings.Contains(req.Body, "\x1b") || strings.Contains(strings.SplitN(req.Body, "\n\n", 2)[0], "delete everything\n") {
		t.Fatalf("the context name was not sanitised: %q", req.Body)
	}
}

func TestTheUndoDialogSaysWhatIsRemovedAndWhatIsKept(t *testing.T) {
	req, err := UndoDialog("docker-desktop", true, []string{"details-a", "productpage-b", "ratings-c"})
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"restores the original benchmark", "reviews-v2 (created by this setup)", "istio-injection label", "3 Pods", "details-a", "not edited", "Jaeger is not changed"} {
		if !strings.Contains(req.Body, want) {
			t.Errorf("missing %q:\n%s", want, req.Body)
		}
	}
	// Undo must remove the label before it removes Istio, or Pods cannot be created while the webhook is gone.
	if strings.Index(req.Body, "istio-injection label") > strings.Index(req.Body, "uninstall Istio") {
		t.Error("the label must be removed before Istio")
	}
	kept, _ := UndoDialog("docker-desktop", false, []string{"a"})
	if strings.Contains(kept.Body, "delete Deployment reviews-v2") {
		t.Error("an existing reviews-v2 must not be offered for removal")
	}
}

func TestTheDialogDisclosesDowntimeAndWhatEachPodMeansForThePage(t *testing.T) {
	p := BuildPlan(benchmarkFacts(), okAddress(), nil, roomy(), "docker-desktop")
	req, err := ApplyDialog(p, DialogExtras{})
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"DOWNTIME", "unavailable until its replacement is Ready", "one replica each", "ratings unavailable", "no book details", "web page does not load", "not measured", "you approve that here"} {
		if !strings.Contains(req.Body, want) {
			t.Errorf("the dialog does not say %q:\n%s", want, req.Body)
		}
	}
}

func TestTheDialogSaysWhenAToolHasToBeDownloaded(t *testing.T) {
	p := BuildPlan(benchmarkFacts(), okAddress(), nil, roomy(), "docker-desktop")
	with, err := ApplyDialog(p, DialogExtras{Download: "istioctl 1.31.2 from github.com (28 MB, checked against a built-in SHA-256)"})
	if err != nil || !strings.Contains(with.Body, "downloads istioctl 1.31.2 from github.com") {
		t.Fatalf("%v\n%s", err, with.Body)
	}
	without, _ := ApplyDialog(p, DialogExtras{})
	if strings.Contains(without.Body, "downloads") {
		t.Fatal("nothing is downloaded, so the dialog must not say so")
	}
	if n := len([]rune(with.Body)); n >= maxBody {
		t.Fatalf("a normal plan with the download notice must still fit: %d", n)
	}
}

func TestPodsAreRecreatedLeafFirst(t *testing.T) {
	p := BuildPlan(benchmarkFacts(), okAddress(), nil, roomy(), "docker-desktop")
	var apps []string
	for _, d := range p.Disruptions {
		apps = append(apps, d.Workload)
	}
	if strings.Join(apps, ",") != "ratings,details,productpage" {
		t.Fatalf("%v", apps)
	}
	if len(p.Disruptions) != len(p.Restarts) {
		t.Fatalf("every restarted Pod must have a stated effect")
	}
}
