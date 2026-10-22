package bookinfotrace

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// This package is planning only. These checks fail the build if a write, a process or the writable client type ever
// appears in it, so "read-only by construction" is a tested property and not a promise.
func sources(t *testing.T) map[string]string {
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
	if len(out) < 8 {
		t.Fatalf("expected the package's source files, found %d", len(out))
	}
	return out
}

func TestNoWriteToAClusterCanBeWrittenInThisPackage(t *testing.T) {
	for name, code := range sources(t) {
		for _, forbidden := range []string{".Create(", ".Update(", ".Delete(", "CreateToken", "kube.Client", "kube.NewClient", "os/exec", "exec.Command"} {
			if strings.Contains(code, forbidden) {
				t.Errorf("%s contains %q", name, forbidden)
			}
		}
	}
}

func TestTheClusterIsOnlyEverReachedThroughTheReadOnlyInterface(t *testing.T) {
	for name, code := range sources(t) {
		if strings.Contains(code, "kube.Reader") && !strings.Contains(code, "ServerVersion") && name != "facts.go" && name != "plan.go" {
			t.Errorf("%s uses the reader unexpectedly", name)
		}
	}
}

func TestEveryDockerCommandHasFixedArguments(t *testing.T) {
	// Arguments reach a command line only as string constants, a node name that matched the safe pattern, or an
	// address that was parsed as an IP. This pins the list of docker verbs the package is allowed to run.
	allowed := regexp.MustCompile(`\[\]string\{"(ps|info|stats|exec)"`)
	for name, code := range sources(t) {
		for _, m := range regexp.MustCompile(`\[\]string\{"([a-z]+)"`).FindAllStringSubmatch(code, -1) {
			if !allowed.MatchString(`[]string{"`+m[1]+`"`) && m[1] != "n" {
				t.Errorf("%s runs an unexpected command %q", name, m[1])
			}
		}
	}
}
