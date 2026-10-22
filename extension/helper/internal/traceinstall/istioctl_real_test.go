package traceinstall

import (
	"os"
	"runtime"
	"testing"
)

// Opt-in: renders with a real, already verified istioctl 1.31.2 (set RESILIENCE_TEST_ISTIOCTL to its path). It checks
// that the real output is exactly the reviewed list of 32 objects. It never contacts a cluster.
func TestTheRealIstioctlRendersExactlyTheReviewedObjects(t *testing.T) {
	bin := os.Getenv("RESILIENCE_TEST_ISTIOCTL")
	if bin == "" {
		t.Skip("RESILIENCE_TEST_ISTIOCTL is not set")
	}
	env := ToolEnv{GOOS: runtime.GOOS, GOARCH: runtime.GOARCH, Dir: t.TempDir(), Exec: RealExec{}}
	manifests, err := env.Render(bg, bin)
	if err != nil || len(manifests) != 32 {
		t.Fatalf("%d %v", len(manifests), err) // includes a render whose content differs from the reviewed digest
	}
	if digest, _ := RenderDigest(manifests); digest != ReviewedRenderDigest {
		t.Fatalf("the real render has digest %s, not the reviewed %s", digest, ReviewedRenderDigest)
	}
}
