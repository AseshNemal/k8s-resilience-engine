package traceinstall

import (
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"go.yaml.in/yaml/v3"
)

// IstioVersion is the one release this package installs. It matches bookinfotrace.Contract.IstioVersion (tested).
const IstioVersion = "1.31.2"

// Artifact is a pinned istioctl download. The checksum is compiled in: a download is never trusted because of where
// it came from, only because its SHA-256 is this one.
type Artifact struct {
	Asset  string // file name of the release asset
	SHA256 string
	Kind   string // "tar.gz" or "zip"
	Binary string // the single file inside the archive that is kept
}

const releaseBase = "https://github.com/istio/istio/releases/download/" + IstioVersion + "/"

// URL is where the asset is downloaded from.
func (a Artifact) URL() string { return releaseBase + a.Asset }

// artifacts is keyed by "<GOOS>/<GOARCH>". Checksums are the ones Istio publishes next to each asset (the
// .sha256 files of release 1.31.2, read on 2026-10-22). Istio publishes no istioctl for windows/arm64.
var artifacts = map[string]Artifact{
	"darwin/arm64":  {"istioctl-1.31.2-osx-arm64.tar.gz", "b5f57049a6fe4b8ac39f13a9f28fcdd2a8cbfac9a086be56d9f09f490765960f", "tar.gz", "istioctl"},
	"darwin/amd64":  {"istioctl-1.31.2-osx.tar.gz", "17be5cc9031d4a44a574e3170914a5ea705148b9e8af1c6ee38f065fdb8790ba", "tar.gz", "istioctl"},
	"linux/amd64":   {"istioctl-1.31.2-linux-amd64.tar.gz", "6ce061447857b54cc90314fd8a72f0f8ed234f67cb0d6e3fb3ef3739caaa5edb", "tar.gz", "istioctl"},
	"linux/arm64":   {"istioctl-1.31.2-linux-arm64.tar.gz", "62a493e287f2547397a6a7fc7d0fa2df91e041081800c9ca8b6bbfa3a4e23378", "tar.gz", "istioctl"},
	"windows/amd64": {"istioctl-1.31.2-win.zip", "784791b0f8127ef6e3ab514da41821d0e0d69849e7847118864e4c44d2c45961", "zip", "istioctl.exe"},
}

// ErrUnsupportedPlatform means there is no pinned istioctl for this operating system and processor.
type ErrUnsupportedPlatform struct{ GOOS, GOARCH string }

func (e ErrUnsupportedPlatform) Error() string {
	return fmt.Sprintf("there is no verified istioctl %s for %s/%s, so the tracing setup cannot run on this computer", IstioVersion, e.GOOS, e.GOARCH)
}

// ArtifactFor returns the pinned istioctl for a platform, or ErrUnsupportedPlatform.
func ArtifactFor(goos, goarch string) (Artifact, error) {
	if a, ok := artifacts[goos+"/"+goarch]; ok {
		return a, nil
	}
	return Artifact{}, ErrUnsupportedPlatform{goos, goarch}
}

// ---- embedded manifests ------------------------------------------------------------------------------

// The manifests are copies of the files in the k8s-bookinfo repository (tracing/ and, for reviews-v2, the unmodified
// Deployment in bookinfo.yaml), so the extension is self-contained. A Python test fails if a copy differs from the
// file it was taken from.
//
//go:embed manifests/*.yaml data/istio-objects.json
var assets embed.FS

// Manifest names. The file names are constants, so nothing a caller passes can select another file.
const (
	fileInstall      = "manifests/install-minimal.yaml"
	fileServiceEntry = "manifests/serviceentry-jaeger-otlp.tmpl.yaml"
	fileTelemetry    = "manifests/telemetry-bookinfo.yaml"
	fileRoute        = "manifests/reviews-v2-route.yaml"
	fileReviewsV2    = "manifests/reviews-v2.yaml"
	fileClient       = "manifests/trace-demo-client.yaml"
)

const addressPlaceholder = "__JAEGER_OTLP_IPV4__"

func readAsset(name string) []byte {
	raw, err := assets.ReadFile(name)
	if err != nil {
		panic("traceinstall: missing embedded file " + name) // a build error, caught by the tests
	}
	return raw
}

// InstallConfig is the IstioOperator file handed to istioctl for rendering.
func InstallConfig() []byte { return readAsset(fileInstall) }

// decodeAll parses a multi-document YAML file into generic maps.
func decodeAll(raw []byte) ([]map[string]any, error) {
	dec := yaml.NewDecoder(strings.NewReader(string(raw)))
	var out []map[string]any
	for {
		var doc map[string]any
		err := dec.Decode(&doc)
		if err != nil {
			if errors.Is(err, io.EOF) {
				return out, nil
			}
			return nil, err
		}
		if doc != nil {
			out = append(out, doc)
		}
	}
}

// ObjectRef names one object: what the expected-inventory file and the record hold.
type ObjectRef struct {
	APIVersion string `json:"apiVersion"`
	Kind       string `json:"kind"`
	Namespace  string `json:"namespace"`
	Name       string `json:"name"`
}

// String is "Kind namespace/name" (or "Kind name" for cluster-scoped objects).
func (o ObjectRef) String() string {
	if o.Namespace == "" {
		return o.Kind + " " + o.Name
	}
	return o.Kind + " " + o.Namespace + "/" + o.Name
}

// ExpectedIstioObjects is the exact list istioctl 1.31.2 renders for install-minimal.yaml, recorded on 2026-10-22.
// A render that differs from it is refused, so a different istioctl or a changed config cannot add an object unseen.
func ExpectedIstioObjects() []ObjectRef {
	var file struct {
		Objects []ObjectRef `json:"objects"`
	}
	if err := json.Unmarshal(readAsset("data/istio-objects.json"), &file); err != nil {
		panic("traceinstall: istio-objects.json is malformed: " + err.Error())
	}
	return file.Objects
}
