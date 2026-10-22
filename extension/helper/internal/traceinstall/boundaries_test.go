package traceinstall

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// These tests read this package's own source. They turn the safety claims in doc.go into checks that fail the build.
func productionSources(t *testing.T) map[string]string {
	t.Helper()
	files, _ := filepath.Glob("*.go")
	out := map[string]string{}
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") {
			continue
		}
		raw, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		out[f] = regexp.MustCompile(`(?m)^\s*//.*$`).ReplaceAllString(string(raw), "")
	}
	if len(out) < 10 {
		t.Fatalf("expected the package's files, found %d", len(out))
	}
	return out
}

func TestOnlyTheGuardWritesToTheCluster(t *testing.T) {
	for name, code := range productionSources(t) {
		if name == "guard.go" {
			continue
		}
		// calls on the Guard (r.guard.Create ...) are the allowed path; any other write call is not
		code = regexp.MustCompile(`guard\.(Create|Delete|DeletePod|DeleteSidecarPod|SetInjection)\(`).ReplaceAllString(code, "guard.ok(")
		for _, call := range []string{".CreateRaw(", ".DeleteRaw(", ".PatchLabels(", ".Create(", ".Update(", ".Delete(", ".DeletePod(", ".SetInjection("} {
			if strings.Contains(code, call) {
				t.Errorf("%s writes to the cluster directly (%s); every write must go through Guard", name, call)
			}
		}
	}
}

func TestThePackageNeverHandlesACredential(t *testing.T) {
	for name, code := range productionSources(t) {
		// no way to build a client or read a kubeconfig, no tokens, and the credential package is not imported
		for _, forbidden := range []string{"CreateToken", "BearerToken", "kube.Config{", "kube.NewClient(", "internal/kubeconfig", "internal/clusters", "internal/token", "internal/flow", "os.Getenv(\"KUBECONFIG"} {
			if strings.Contains(code, forbidden) {
				t.Errorf("%s mentions %q", name, forbidden)
			}
		}
		// the one place KUBECONFIG appears is the render environment, where it points at a file that does not exist
		if strings.Contains(code, "KUBECONFIG=") && name != "istioctl.go" {
			t.Errorf("%s sets KUBECONFIG", name)
		}
	}
}

func TestProcessesAreOnlyStartedByTheAuditedExecutor(t *testing.T) {
	for name, code := range productionSources(t) {
		if name == "fetch.go" {
			continue
		}
		for _, forbidden := range []string{"os/exec", "exec.Command", "syscall"} {
			if strings.Contains(code, forbidden) {
				t.Errorf("%s uses %q", name, forbidden)
			}
		}
	}
	// the one executor takes an explicit environment and never inherits the helper's (which may hold credentials)
	if !strings.Contains(productionSources(t)["fetch.go"], "cmd.Env = env") {
		t.Error("the executor must set the environment explicitly")
	}
}

func TestThePlanningPackageDoesNotImportTheInstaller(t *testing.T) {
	files, _ := filepath.Glob("../bookinfotrace/*.go")
	for _, f := range files {
		raw, _ := os.ReadFile(f)
		if strings.Contains(string(raw), "traceinstall") && !strings.HasSuffix(f, "_test.go") {
			t.Errorf("%s reaches the installer", f)
		}
	}
}

func TestNoMemberOrSharedCodeIsReferenced(t *testing.T) {
	for name, code := range productionSources(t) {
		for _, forbidden := range []string{"app/engine", "app/shared", "workflow_dependency", "deployment_topology", "failure_impact", "recommendation"} {
			if strings.Contains(code, forbidden) {
				t.Errorf("%s mentions %q", name, forbidden)
			}
		}
	}
}

func TestEverythingTheHelperWritesIsOwnerOnly(t *testing.T) {
	for name, code := range productionSources(t) {
		for _, m := range regexp.MustCompile(`0o([0-7]{3,4})`).FindAllStringSubmatch(code, -1) {
			if m[1] == "077" { // a mask that detects group and world access, not a mode that is written
				continue
			}
			if strings.HasSuffix(m[1], "77") || strings.HasSuffix(m[1], "55") || strings.HasSuffix(m[1], "44") || strings.HasSuffix(m[1], "64") {
				t.Errorf("%s uses file mode 0o%s", name, m[1])
			}
		}
	}
}
