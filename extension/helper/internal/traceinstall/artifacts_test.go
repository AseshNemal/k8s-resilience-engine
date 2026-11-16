package traceinstall

import (
	"errors"
	"strings"
	"testing"

	"resilience-engine/helper/internal/bookinfotrace"
)

func TestEveryPinnedPlatformHasAFullChecksumAndTheRightArchiveKind(t *testing.T) {
	for _, platform := range []string{"darwin/arm64", "darwin/amd64", "linux/amd64", "linux/arm64", "windows/amd64"} {
		parts := strings.Split(platform, "/")
		a, err := ArtifactFor(parts[0], parts[1])
		if err != nil {
			t.Fatalf("%s: %v", platform, err)
		}
		if len(a.SHA256) != 64 || strings.Trim(a.SHA256, "0123456789abcdef") != "" {
			t.Errorf("%s: %q is not a SHA-256", platform, a.SHA256)
		}
		if !strings.Contains(a.Asset, IstioVersion) || !strings.HasPrefix(a.URL(), "https://github.com/istio/istio/releases/download/"+IstioVersion+"/") {
			t.Errorf("%s: %s", platform, a.URL())
		}
		wantKind, wantBinary := "tar.gz", "istioctl"
		if parts[0] == "windows" {
			wantKind, wantBinary = "zip", "istioctl.exe"
		}
		if a.Kind != wantKind || a.Binary != wantBinary || !strings.HasSuffix(a.Asset, wantKind) {
			t.Errorf("%s: %+v", platform, a)
		}
	}
}

func TestPlatformsWithoutAPinnedIstioctlAreRefused(t *testing.T) {
	for _, p := range [][2]string{{"windows", "arm64"}, {"freebsd", "amd64"}, {"linux", "386"}, {"", ""}} {
		_, err := ArtifactFor(p[0], p[1])
		var unsupported ErrUnsupportedPlatform
		if !errors.As(err, &unsupported) {
			t.Errorf("%v: %v", p, err)
		}
	}
}

func TestTheVersionsAgreeWithThePlanningContract(t *testing.T) {
	if IstioVersion != bookinfotrace.Contract.IstioVersion {
		t.Fatalf("%s vs %s", IstioVersion, bookinfotrace.Contract.IstioVersion)
	}
}

func TestTheExpectedIstioInventoryIsTheRenderedThirtyTwo(t *testing.T) {
	objects := ExpectedIstioObjects()
	c := bookinfotrace.Contract.Install
	if len(objects) != c.RenderedObjects {
		t.Fatalf("%d objects, contract says %d", len(objects), c.RenderedObjects)
	}
	crds, webhooks := 0, 0
	for _, o := range objects {
		switch o.Kind {
		case "CustomResourceDefinition":
			crds++
		case "MutatingWebhookConfiguration", "ValidatingWebhookConfiguration":
			webhooks++
		}
		if o.Kind == "Deployment" && (o.Namespace != "istio-system" || o.Name != "istiod") {
			t.Errorf("unexpected workload %s", o)
		}
	}
	if crds != c.CustomResourceDefinitions || webhooks != c.Webhooks {
		t.Fatalf("crds %d webhooks %d", crds, webhooks)
	}
}

func TestTheEmbeddedManifestsParse(t *testing.T) {
	for _, name := range []string{fileInstall, fileServiceEntry, fileTelemetry, fileRoute, fileReviewsV2, fileClient} {
		docs, err := decodeAll(readAsset(name))
		if err != nil || len(docs) == 0 {
			t.Errorf("%s: %v (%d documents)", name, err, len(docs))
		}
	}
}
