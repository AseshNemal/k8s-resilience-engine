package traceinstall

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
)

const (
	maxArchiveBytes = 100 << 20
	maxBinaryBytes  = 250 << 20
)

// Fetcher downloads one URL. The real one (fetch.go) follows redirects only within GitHub's release hosts.
type Fetcher interface {
	Get(ctx context.Context, url string, maxBytes int64) ([]byte, error)
}

// Exec runs a program with an explicit environment. No shell is involved and the environment is never inherited.
type Exec interface {
	Run(ctx context.Context, name string, args, env []string) (stdout []byte, exitCode int, err error)
}

// ToolEnv is everything the istioctl manager touches.
type ToolEnv struct {
	GOOS, GOARCH string
	Dir          string // private directory for the verified tool; created 0700
	Fetcher      Fetcher
	Exec         Exec
	Lookup       func(goos, goarch string) (Artifact, error) // ArtifactFor unless a test pins another
	Getenv       func(string) string                         // for the few variables Windows needs
}

func (e ToolEnv) artifact() (Artifact, error) {
	if e.Lookup != nil {
		return e.Lookup(e.GOOS, e.GOARCH)
	}
	return ArtifactFor(e.GOOS, e.GOARCH)
}

// Errors the page can turn into fixed wording. None contains a path or text from the download.
var (
	ErrDownloadUnavailable = errors.New("istioctl could not be downloaded")
	ErrChecksumMismatch    = errors.New("the downloaded istioctl does not match its pinned checksum, so it was discarded")
	ErrBadArchive          = errors.New("the istioctl archive does not contain exactly the expected file")
	ErrToolTampered        = errors.New("the stored istioctl no longer matches what was verified")
	ErrStorage             = errors.New("the private tool directory is not usable")
	ErrRenderFailed        = errors.New("istioctl could not render the Istio objects")
	ErrRenderUnexpected    = errors.New("the rendered Istio objects are not exactly the reviewed list")
)

type verifiedFile struct {
	Version       string `json:"version"`
	Asset         string `json:"asset"`
	ArchiveSHA256 string `json:"archiveSha256"`
	BinarySHA256  string `json:"binarySha256"`
}

func sha(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }

// checkURL accepts only the pinned release asset on GitHub over https.
func checkURL(a Artifact) error {
	u, err := url.Parse(a.URL())
	if err != nil || u.Scheme != "https" || u.Host != "github.com" ||
		!strings.HasPrefix(u.Path, "/istio/istio/releases/download/"+IstioVersion+"/") || path.Base(u.Path) != a.Asset {
		return ErrDownloadUnavailable
	}
	return nil
}

// NeedsDownload reports whether Ensure would have to download istioctl, so the approval dialog can say so.
func (e ToolEnv) NeedsDownload() (bool, Artifact, error) {
	a, err := e.artifact()
	if err != nil {
		return false, Artifact{}, err
	}
	_, ok := e.reusable(a)
	return !ok, a, nil
}

func (e ToolEnv) toolDir(a Artifact) string {
	return filepath.Join(e.Dir, IstioVersion+"-"+e.GOOS+"-"+e.GOARCH)
}

// reusable returns the stored binary when it is exactly what was verified against the pinned checksum.
func (e ToolEnv) reusable(a Artifact) (string, bool) {
	dir := e.toolDir(a)
	raw, err := os.ReadFile(filepath.Join(dir, "verified.json"))
	if err != nil {
		return "", false
	}
	var v verifiedFile
	if json.Unmarshal(raw, &v) != nil || v.Version != IstioVersion || v.Asset != a.Asset || v.ArchiveSHA256 != a.SHA256 {
		return "", false
	}
	bin := filepath.Join(dir, a.Binary)
	body, err := os.ReadFile(bin)
	if err != nil || sha(body) != v.BinarySHA256 {
		return "", false
	}
	return bin, true
}

// Ensure returns the path of the verified istioctl, downloading it first when it is not stored. Nothing is kept
// unless its SHA-256 equals the pinned one. A stored binary that was changed afterwards is not trusted: it is
// replaced by a fresh verified download.
func (e ToolEnv) Ensure(ctx context.Context) (string, error) {
	a, err := e.artifact()
	if err != nil {
		return "", err
	}
	if bin, ok := e.reusable(a); ok {
		return bin, nil
	}
	if err := checkURL(a); err != nil {
		return "", err
	}
	if e.Fetcher == nil {
		return "", ErrDownloadUnavailable
	}
	archive, err := e.Fetcher.Get(ctx, a.URL(), maxArchiveBytes)
	if err != nil {
		return "", ErrDownloadUnavailable
	}
	if sha(archive) != a.SHA256 {
		return "", ErrChecksumMismatch
	}
	binary, err := extractOne(archive, a)
	if err != nil {
		return "", err
	}
	dir := e.toolDir(a)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", ErrStorage
	}
	bin := filepath.Join(dir, a.Binary)
	if err := writeFileAtomic(bin, binary, 0o700); err != nil {
		return "", ErrStorage
	}
	meta, _ := json.Marshal(verifiedFile{Version: IstioVersion, Asset: a.Asset, ArchiveSHA256: a.SHA256, BinarySHA256: sha(binary)})
	if err := writeFileAtomic(filepath.Join(dir, "verified.json"), meta, 0o600); err != nil {
		return "", ErrStorage
	}
	return bin, nil
}

// syncWrites makes every write wait until the data is on disk. It is always true in the helper; the test suite turns
// it off for the hundreds of simulated runs, and one test turns it on to cover the real path.
var syncWrites = true

// writeFileAtomic writes next to the target and renames, so a crash never leaves half a file under the real name.
func writeFileAtomic(target string, data []byte, mode os.FileMode) error {
	tmp, err := os.CreateTemp(filepath.Dir(target), ".tmp-*")
	if err != nil {
		return err
	}
	name := tmp.Name()
	cleanup := func() { _ = os.Remove(name) }
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		cleanup()
		return err
	}
	if syncWrites {
		if err := tmp.Sync(); err != nil {
			tmp.Close()
			cleanup()
			return err
		}
	}
	if err := tmp.Close(); err != nil {
		cleanup()
		return err
	}
	if err := os.Chmod(name, mode); err != nil {
		cleanup()
		return err
	}
	if err := os.Rename(name, target); err != nil {
		cleanup()
		return err
	}
	return nil
}

// extractOne returns the content of the one expected file. The archive must contain that file and nothing else
// (directories excepted); links, absolute paths, ".." and oversized files are refused.
func extractOne(archive []byte, a Artifact) ([]byte, error) {
	switch a.Kind {
	case "tar.gz":
		return extractTar(archive, a.Binary)
	case "zip":
		return extractZip(archive, a.Binary)
	}
	return nil, ErrBadArchive
}

func safeName(name string) (string, bool) {
	clean := path.Clean(strings.ReplaceAll(name, `\`, "/"))
	if clean == "." || strings.HasPrefix(clean, "/") || clean == ".." || strings.HasPrefix(clean, "../") || strings.Contains(clean, ":") {
		return "", false
	}
	return clean, true
}

func extractTar(archive []byte, want string) ([]byte, error) {
	gz, err := gzip.NewReader(bytes.NewReader(archive))
	if err != nil {
		return nil, ErrBadArchive
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	var found []byte
	haveIt := false
	for {
		h, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, ErrBadArchive
		}
		name, ok := safeName(h.Name)
		if !ok {
			return nil, ErrBadArchive
		}
		if h.Typeflag == tar.TypeDir {
			continue
		}
		if h.Typeflag != tar.TypeReg || name != want || haveIt || h.Size > maxBinaryBytes {
			return nil, ErrBadArchive
		}
		body, err := io.ReadAll(io.LimitReader(tr, maxBinaryBytes+1))
		if err != nil || int64(len(body)) > maxBinaryBytes {
			return nil, ErrBadArchive
		}
		found, haveIt = body, true
	}
	if !haveIt || len(found) == 0 {
		return nil, ErrBadArchive
	}
	return found, nil
}

func extractZip(archive []byte, want string) ([]byte, error) {
	zr, err := zip.NewReader(bytes.NewReader(archive), int64(len(archive)))
	if err != nil {
		return nil, ErrBadArchive
	}
	var found []byte
	haveIt := false
	for _, f := range zr.File {
		name, ok := safeName(f.Name)
		if !ok {
			return nil, ErrBadArchive
		}
		if f.FileInfo().IsDir() {
			continue
		}
		if !f.Mode().IsRegular() || name != want || haveIt || f.UncompressedSize64 > maxBinaryBytes {
			return nil, ErrBadArchive
		}
		rc, err := f.Open()
		if err != nil {
			return nil, ErrBadArchive
		}
		body, err := io.ReadAll(io.LimitReader(rc, maxBinaryBytes+1))
		rc.Close()
		if err != nil || int64(len(body)) > maxBinaryBytes {
			return nil, ErrBadArchive
		}
		found, haveIt = body, true
	}
	if !haveIt || len(found) == 0 {
		return nil, ErrBadArchive
	}
	return found, nil
}

// ---- rendering ---------------------------------------------------------------------------------------

// Manifest is one rendered object.
type Manifest struct {
	Ref  ObjectRef
	Body map[string]any
}

// renderEnv is the environment istioctl runs with. It holds no credential and cannot find a kubeconfig: HOME points
// at the private directory and KUBECONFIG at a file that does not exist, so the render cannot reach any cluster.
func (e ToolEnv) renderEnv(home string) []string {
	missing := filepath.Join(home, "no-kubeconfig")
	if e.GOOS == "windows" {
		get := e.Getenv
		if get == nil {
			get = os.Getenv
		}
		root := get("SystemRoot")
		return []string{"USERPROFILE=" + home, "HOME=" + home, "TEMP=" + home, "TMP=" + home, "SystemRoot=" + root,
			"PATH=" + filepath.Join(root, "System32"), "KUBECONFIG=" + missing}
	}
	return []string{"HOME=" + home, "PATH=/usr/bin:/bin", "KUBECONFIG=" + missing, "LANG=C"}
}

// kindOrder is the order objects are created in: namespaces and CRDs first, then what istiod needs, then the
// workload, then the webhooks that point at it. Undo deletes in the reverse order.
var kindOrder = map[string]int{
	"Namespace": 0, "CustomResourceDefinition": 1, "ServiceAccount": 2, "ConfigMap": 3, "ClusterRole": 4, "ClusterRoleBinding": 5,
	"Role": 6, "RoleBinding": 7, "Service": 8, "Deployment": 9, "ValidatingWebhookConfiguration": 10, "MutatingWebhookConfiguration": 11,
}

// creationBefore orders objects the way they are created. The inventory uses the same order, so Undo, which walks it
// backwards, removes the webhooks first and the namespace last.
func creationBefore(a, b ObjectRef) bool {
	ka, kb := kindOrder[a.Kind], kindOrder[b.Kind]
	if ka != kb {
		return ka < kb
	}
	return a.Name < b.Name
}

func refOf(doc map[string]any) ObjectRef {
	meta, _ := doc["metadata"].(map[string]any)
	str := func(m map[string]any, k string) string { s, _ := m[k].(string); return s }
	return ObjectRef{APIVersion: str(doc, "apiVersion"), Kind: str(doc, "kind"), Namespace: str(meta, "namespace"), Name: str(meta, "name")}
}

// Render runs `istioctl manifest generate` on the embedded install file, offline, and returns the objects in
// creation order. The result must equal the reviewed list exactly (ExpectedIstioObjects): one object more or less,
// or a different name, and nothing is applied.
func (e ToolEnv) Render(ctx context.Context, bin string) ([]Manifest, error) {
	if e.Exec == nil {
		return nil, ErrRenderFailed
	}
	if err := os.MkdirAll(e.Dir, 0o700); err != nil {
		return nil, ErrStorage
	}
	scratch, err := os.MkdirTemp(e.Dir, "render-")
	if err != nil {
		return nil, ErrStorage
	}
	defer os.RemoveAll(scratch)
	config := filepath.Join(scratch, "install.yaml")
	if err := os.WriteFile(config, InstallConfig(), 0o600); err != nil {
		return nil, ErrStorage
	}
	out, code, err := e.Exec.Run(ctx, bin, []string{"manifest", "generate", "-f", config}, e.renderEnv(scratch))
	if err != nil || code != 0 {
		return nil, ErrRenderFailed
	}
	return parseRendered(out)
}

func parseRendered(out []byte) ([]Manifest, error) {
	docs, err := decodeAll(out)
	if err != nil {
		return nil, ErrRenderFailed
	}
	var got []Manifest
	for _, d := range docs {
		got = append(got, Manifest{Ref: refOf(d), Body: d})
	}
	want := map[ObjectRef]int{}
	for _, o := range ExpectedIstioObjects() {
		want[o]++
	}
	for _, m := range got {
		if want[m.Ref] == 0 {
			return nil, ErrRenderUnexpected
		}
		want[m.Ref]--
	}
	for _, left := range want {
		if left != 0 {
			return nil, ErrRenderUnexpected
		}
	}
	sort.SliceStable(got, func(i, j int) bool { return creationBefore(got[i].Ref, got[j].Ref) })
	for _, m := range got {
		if _, ok := kindOrder[m.Ref.Kind]; !ok {
			return nil, fmt.Errorf("%w: %s", ErrRenderUnexpected, m.Ref.Kind)
		}
	}
	return got, nil
}
