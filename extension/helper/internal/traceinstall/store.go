package traceinstall

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"

	"resilience-engine/helper/internal/bookinfotrace"
)

// ClusterIdentity is what a cluster is, not what it is called. A cluster recreated under the same context name has
// another kube-system namespace UID (and usually another CA), so it is another identity and its record is not used.
type ClusterIdentity struct {
	Context       string `json:"context"`
	Server        string `json:"server"` // host:port only
	CASha256      string `json:"caSha256"`
	KubeSystemUID string `json:"kubeSystemUid"`
}

// Key names the record file.
func (c ClusterIdentity) Key() string {
	raw, _ := json.Marshal(c)
	s := sha256.Sum256(raw)
	return hex.EncodeToString(s[:])[:16]
}

// Phase is where the setup stands. Only rolled-back and applied are terminal; every other phase means a run was
// interrupted or failed to clean up, and the next step must be Undo.
type Phase string

const (
	PhaseApplying       Phase = "applying"
	PhaseApplied        Phase = "applied"
	PhaseRollingBack    Phase = "rolling-back"
	PhaseRolledBack     Phase = "rolled-back"
	PhaseRollbackFailed Phase = "rollback-failed"
)

// EntryState is what the run knows about one object it may own.
type EntryState string

const (
	EntryPlanned EntryState = "planned"  // in the inventory, not touched
	EntryIntent  EntryState = "intended" // about to be created: written first, so a crash is recoverable
	EntryCreated EntryState = "created"  // created by this run; UID known
	EntryDeleted EntryState = "deleted"
	EntryForeign EntryState = "foreign" // the name was already taken by something this run did not create: never touched
	EntryKept    EntryState = "kept"    // owned, but deliberately left in place (for example CRDs other Istio resources use)
)

// Entry is one object of the managed resource inventory.
type Entry struct {
	Ref   ObjectRef  `json:"ref"`
	Stage string     `json:"stage"`
	State EntryState `json:"state"`
	UID   string     `json:"uid,omitempty"`
}

// StepLog is one line of the progress journal the page shows.
type StepLog struct {
	At     string `json:"at"`
	Step   string `json:"step"`
	Status string `json:"status"` // started, done, failed, skipped
	Detail string `json:"detail,omitempty"`
}

// FailureInfo says why a run stopped.
type FailureInfo struct {
	Stage  string `json:"stage"`
	Reason string `json:"reason"`
	At     string `json:"at"`
}

// Record is the durable state of one installation. It holds facts and names, never a credential, a kubeconfig
// fragment, a token or text a cluster returned.
type Record struct {
	Version         int             `json:"version"`
	InstallationID  string          `json:"installationId"`
	Cluster         ClusterIdentity `json:"cluster"`
	IstioVersion    string          `json:"istioVersion"`
	StartedAt       string          `json:"startedAt"`
	UpdatedAt       string          `json:"updatedAt"`
	Phase           Phase           `json:"phase"`
	PlanFingerprint string          `json:"planFingerprint"`
	// RenderDigest is the content digest of the Istio objects the person approved (see ReviewedRenderDigest).
	RenderDigest string                     `json:"renderDigest"`
	Baseline     bookinfotrace.Measurements `json:"baseline"`
	Snapshot     bookinfotrace.Snapshot     `json:"snapshot"`
	PlannedPods  []string                   `json:"plannedPods"`
	JaegerIPv4   string                     `json:"jaegerIpv4"`
	Inventory    []Entry                    `json:"inventory"`
	LabelApplied bool                       `json:"labelApplied"`
	Recreated    []string                   `json:"recreated"` // app names whose Pod this run deleted
	Steps        []StepLog                  `json:"steps"`
	Failure      *FailureInfo               `json:"failure,omitempty"`
	Remaining    []string                   `json:"remaining,omitempty"`
	// Binding ties the facts that must never change after the run starts (identity, installation ID, baseline,
	// snapshot, the planned inventory) together. Digest covers the whole file and changes on every save.
	Binding string `json:"binding"`
	Digest  string `json:"digest"`
}

const recordVersion = 1

func hashJSON(v any) string {
	raw, _ := json.Marshal(v)
	s := sha256.Sum256(raw)
	return hex.EncodeToString(s[:])
}

func (r *Record) bindingDigest() string {
	refs := make([]ObjectRef, 0, len(r.Inventory))
	for _, e := range r.Inventory {
		refs = append(refs, e.Ref)
	}
	return hashJSON(struct {
		ID       string
		Cluster  ClusterIdentity
		Baseline bookinfotrace.Measurements
		Snapshot bookinfotrace.Snapshot
		Refs     []ObjectRef
		Pods     []string
		Render   string
	}{r.InstallationID, r.Cluster, r.Baseline, r.Snapshot, refs, r.PlannedPods, r.RenderDigest})
}

func (r *Record) integrityDigest() string {
	copyOf := *r
	copyOf.Digest = ""
	return hashJSON(copyOf)
}

// NewRecord starts a record. The inventory is the complete list of objects the run may own, all "planned".
func NewRecord(id string, cluster ClusterIdentity, plan bookinfotrace.PlanResult, snapshot bookinfotrace.Snapshot, fingerprint, renderDigest string, inventory []Entry, now time.Time) *Record {
	r := &Record{Version: recordVersion, InstallationID: id, Cluster: cluster, IstioVersion: IstioVersion, StartedAt: now.UTC().Format(time.RFC3339),
		Phase: PhaseApplying, PlanFingerprint: fingerprint, RenderDigest: renderDigest, Baseline: plan.Baseline, Snapshot: snapshot, PlannedPods: append([]string(nil), plan.Restarts...),
		Inventory: inventory, Recreated: []string{}, Steps: []StepLog{}}
	if plan.Jaeger != nil {
		r.JaegerIPv4 = plan.Jaeger.IPv4
	}
	r.Binding = r.bindingDigest()
	return r
}

// NewInstallationID returns 128 random bits as hex.
func NewInstallationID() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// Entry returns the inventory entry for an object, or nil.
func (r *Record) Entry(ref ObjectRef) *Entry {
	for i := range r.Inventory {
		if r.Inventory[i].Ref == ref {
			return &r.Inventory[i]
		}
	}
	return nil
}

// Log appends to the journal. The journal is capped so a long recovery cannot grow the file without limit.
func (r *Record) Log(step, status, detail string, now time.Time) {
	r.Steps = append(r.Steps, StepLog{At: now.UTC().Format(time.RFC3339), Step: step, Status: status, Detail: detail})
	if len(r.Steps) > 400 {
		r.Steps = r.Steps[len(r.Steps)-400:]
	}
}

// Terminal reports whether nothing is left to finish or undo.
func (r *Record) Terminal() bool { return r.Phase == PhaseApplied || r.Phase == PhaseRolledBack }

// Storage errors. Each is fixed wording; none contains a path.
var (
	ErrRecordCorrupt     = errors.New("the saved setup record is damaged or was changed, so it is not used")
	ErrRecordPermissions = errors.New("the saved setup record can be read by other users, so it is not used")
	ErrStoreUnavailable  = errors.New("the setup record could not be saved")
)

// Store keeps records in one private directory per user.
type Store struct{ Dir string }

func (s Store) path(id ClusterIdentity) string {
	return filepath.Join(s.Dir, "bookinfo-tracing-"+id.Key()+".json")
}

func (s Store) ensureDir() error {
	if err := os.MkdirAll(s.Dir, 0o700); err != nil {
		return ErrStoreUnavailable
	}
	if err := checkPrivate(s.Dir, true); err != nil {
		return err
	}
	return nil
}

// checkPrivate refuses a record directory or file that other users can use. Windows has no Unix mode bits: there
// the per-user profile directory's access list is what protects the file, and nothing is claimed beyond that.
func checkPrivate(path string, dir bool) error {
	if runtime.GOOS == "windows" {
		return nil
	}
	info, err := os.Lstat(path)
	if err != nil {
		return ErrStoreUnavailable
	}
	if info.Mode()&os.ModeSymlink != 0 || (dir && !info.IsDir()) || (!dir && !info.Mode().IsRegular()) || info.Mode().Perm()&0o077 != 0 {
		return ErrRecordPermissions
	}
	return nil
}

// Load returns the record of this cluster, nil when there is none. A record that fails its checks is an error, not
// "no record": the caller must not start over a file it cannot trust.
func (s Store) Load(id ClusterIdentity) (*Record, error) {
	if _, err := os.Stat(s.Dir); err != nil {
		return nil, nil
	}
	if err := checkPrivate(s.Dir, true); err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(s.path(id))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, ErrStoreUnavailable
	}
	if err := checkPrivate(s.path(id), false); err != nil {
		return nil, err
	}
	var r Record
	if json.Unmarshal(raw, &r) != nil || r.Version != recordVersion || r.Cluster != id ||
		r.Binding != r.bindingDigest() || r.Digest != r.integrityDigest() || r.InstallationID == "" {
		return nil, ErrRecordCorrupt
	}
	return &r, nil
}

// Save writes the record atomically (a temporary file, then a rename), mode 0600.
func (s Store) Save(r *Record, now time.Time) error {
	if err := s.ensureDir(); err != nil {
		return err
	}
	r.UpdatedAt = now.UTC().Format(time.RFC3339)
	r.Digest = r.integrityDigest()
	raw, err := json.MarshalIndent(r, "", "  ")
	if err != nil {
		return ErrStoreUnavailable
	}
	if err := writeFileAtomic(s.path(r.Cluster), raw, 0o600); err != nil {
		return ErrStoreUnavailable
	}
	return nil
}

// ForContext lists the valid records saved for a context name, whatever cluster they belong to. The page uses it to say
// that the cluster behind a context is not the one a record was made for.
func (s Store) ForContext(contextName string) []ClusterIdentity {
	files, _ := filepath.Glob(filepath.Join(s.Dir, "bookinfo-tracing-*.json"))
	var out []ClusterIdentity
	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			continue
		}
		var r Record
		if json.Unmarshal(raw, &r) == nil && r.Cluster.Context == contextName && strings.HasSuffix(f, r.Cluster.Key()+".json") {
			out = append(out, r.Cluster)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Key() < out[j].Key() })
	return out
}

// LatestFor returns the most recently updated valid record saved for a context name, without contacting any cluster.
// The page uses it to show where a setup stands; Apply, Undo and Verify never use it, because they act only on the
// record of the cluster they actually reached.
func (s Store) LatestFor(contextName string) (*Record, error) {
	var newest *Record
	for _, id := range s.ForContext(contextName) {
		rec, err := s.Load(id)
		if err != nil {
			return nil, err
		}
		if rec != nil && (newest == nil || rec.UpdatedAt > newest.UpdatedAt) {
			newest = rec
		}
	}
	return newest, nil
}
