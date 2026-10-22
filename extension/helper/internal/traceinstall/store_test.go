package traceinstall

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"resilience-engine/helper/internal/bookinfotrace"
)

var epoch = time.Date(2026, 10, 22, 12, 0, 0, 0, time.UTC)

func identity() ClusterIdentity {
	return ClusterIdentity{Context: "docker-desktop", Server: "127.0.0.1:6443", CASha256: strings.Repeat("a", 64), KubeSystemUID: "uid-kube-system"}
}

func someRecord() *Record {
	plan := bookinfotrace.PlanResult{Restarts: []string{"ratings-v1-x"}, Jaeger: &bookinfotrace.Address{IPv4: "192.168.65.254"}}
	inv := []Entry{{Ref: ObjectRef{APIVersion: "v1", Kind: "Namespace", Name: "istio-system"}, Stage: "istio", State: EntryPlanned}}
	return NewRecord("id-1", identity(), plan, bookinfotrace.Snapshot{Context: "docker-desktop"}, "fp", inv, epoch)
}

func TestARecordRoundTripsAndKeepsTheBindingAndDigest(t *testing.T) {
	s := Store{Dir: filepath.Join(t.TempDir(), "rec")}
	rec := someRecord()
	rec.Log("istio", "started", "", epoch)
	if err := s.Save(rec, epoch); err != nil {
		t.Fatal(err)
	}
	got, err := s.Load(identity())
	if err != nil || got == nil || got.InstallationID != "id-1" || got.PlanFingerprint != "fp" || len(got.Steps) != 1 {
		t.Fatalf("%+v %v", got, err)
	}
	if nothing, err := s.Load(ClusterIdentity{Context: "docker-desktop", KubeSystemUID: "other"}); nothing != nil || err != nil {
		t.Fatalf("another cluster instance must find no record: %v %v", nothing, err)
	}
}

func TestNoRecordIsNilNotAnError(t *testing.T) {
	if rec, err := (Store{Dir: filepath.Join(t.TempDir(), "absent")}).Load(identity()); rec != nil || err != nil {
		t.Fatalf("%v %v", rec, err)
	}
}

func TestFilesAreOwnerOnlyOnUnix(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows has no Unix mode bits; the per-user profile ACL protects the directory")
	}
	s := Store{Dir: filepath.Join(t.TempDir(), "rec")}
	if err := s.Save(someRecord(), epoch); err != nil {
		t.Fatal(err)
	}
	dir, _ := os.Stat(s.Dir)
	file, _ := os.Stat(s.path(identity()))
	if dir.Mode().Perm() != 0o700 || file.Mode().Perm() != 0o600 {
		t.Fatalf("dir %v file %v", dir.Mode().Perm(), file.Mode().Perm())
	}
}

func TestARecordOtherUsersCanReadIsRefused(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("no Unix mode bits")
	}
	s := Store{Dir: filepath.Join(t.TempDir(), "rec")}
	if err := s.Save(someRecord(), epoch); err != nil {
		t.Fatal(err)
	}
	os.Chmod(s.path(identity()), 0o644)
	if _, err := s.Load(identity()); !errors.Is(err, ErrRecordPermissions) {
		t.Fatalf("file: %v", err)
	}
	os.Chmod(s.path(identity()), 0o600)
	os.Chmod(s.Dir, 0o755)
	if _, err := s.Load(identity()); !errors.Is(err, ErrRecordPermissions) {
		t.Fatalf("directory: %v", err)
	}
}

func TestATamperedRecordIsRefused(t *testing.T) {
	edits := map[string]func(map[string]any){
		"inventory entry": func(m map[string]any) {
			m["inventory"].([]any)[0].(map[string]any)["ref"].(map[string]any)["name"] = "kube-system"
		},
		"installation id":  func(m map[string]any) { m["installationId"] = "id-2" },
		"baseline":         func(m map[string]any) { m["baseline"].(map[string]any)["vmUsedMiB"] = 1.0 },
		"snapshot":         func(m map[string]any) { m["snapshot"].(map[string]any)["context"] = "prod" },
		"phase":            func(m map[string]any) { m["phase"] = "applied" },
		"digest removed":   func(m map[string]any) { m["digest"] = "" },
		"cluster identity": func(m map[string]any) { m["cluster"].(map[string]any)["kubeSystemUid"] = "other" },
	}
	for name, edit := range edits {
		s := Store{Dir: filepath.Join(t.TempDir(), "rec")}
		if err := s.Save(someRecord(), epoch); err != nil {
			t.Fatal(err)
		}
		raw, _ := os.ReadFile(s.path(identity()))
		var m map[string]any
		_ = json.Unmarshal(raw, &m)
		edit(m)
		out, _ := json.Marshal(m)
		_ = os.WriteFile(s.path(identity()), out, 0o600)
		if _, err := s.Load(identity()); !errors.Is(err, ErrRecordCorrupt) {
			t.Errorf("%s: %v", name, err)
		}
	}
	s := Store{Dir: filepath.Join(t.TempDir(), "rec")}
	_ = s.Save(someRecord(), epoch)
	_ = os.WriteFile(s.path(identity()), []byte("{not json"), 0o600)
	if _, err := s.Load(identity()); !errors.Is(err, ErrRecordCorrupt) {
		t.Fatalf("%v", err)
	}
}

func TestASaveNeverLeavesAHalfWrittenFileUnderTheRealName(t *testing.T) {
	s := Store{Dir: filepath.Join(t.TempDir(), "rec")}
	rec := someRecord()
	if err := s.Save(rec, epoch); err != nil {
		t.Fatal(err)
	}
	before, _ := os.ReadFile(s.path(identity()))
	// make the directory read-only so the temporary file cannot be created: the old record must survive
	if runtime.GOOS != "windows" && os.Getuid() != 0 {
		os.Chmod(s.Dir, 0o500)
		rec.Phase = PhaseApplied
		if err := s.Save(rec, epoch.Add(time.Minute)); err == nil {
			t.Fatal("expected a failure")
		}
		os.Chmod(s.Dir, 0o700)
		after, _ := os.ReadFile(s.path(identity()))
		if string(before) != string(after) {
			t.Fatal("a failed save changed the record")
		}
	}
	entries, _ := os.ReadDir(s.Dir)
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".tmp-") {
			t.Errorf("temporary file left behind: %s", e.Name())
		}
	}
}

func TestTheRecordHoldsNoCredentialOrClusterText(t *testing.T) {
	raw, _ := json.Marshal(someRecord())
	for _, forbidden := range []string{"token", "Bearer", "client-key", "certificate-authority-data", "password"} {
		if strings.Contains(strings.ToLower(string(raw)), strings.ToLower(forbidden)) {
			t.Errorf("the record mentions %q", forbidden)
		}
	}
}

func TestOnlyAppliedAndRolledBackAreTerminal(t *testing.T) {
	for phase, want := range map[Phase]bool{PhaseApplying: false, PhaseApplied: true, PhaseRollingBack: false, PhaseRolledBack: true, PhaseRollbackFailed: false} {
		if (&Record{Phase: phase}).Terminal() != want {
			t.Errorf("%s", phase)
		}
	}
}

func TestForContextFindsRecordsOfOtherInstancesOfTheSameName(t *testing.T) {
	s := Store{Dir: filepath.Join(t.TempDir(), "rec")}
	_ = s.Save(someRecord(), epoch)
	found := s.ForContext("docker-desktop")
	if len(found) != 1 || found[0] != identity() || len(s.ForContext("kind-other")) != 0 {
		t.Fatalf("%v", found)
	}
}

func TestInstallationIDsAreRandomAndLongEnough(t *testing.T) {
	a, _ := NewInstallationID()
	b, _ := NewInstallationID()
	if len(a) != 32 || a == b {
		t.Fatalf("%q %q", a, b)
	}
}

func TestRecordWritesAreSyncedByDefault(t *testing.T) {
	syncWrites = true
	defer func() { syncWrites = false }()
	s := Store{Dir: filepath.Join(t.TempDir(), "rec")}
	if err := s.Save(someRecord(), epoch); err != nil {
		t.Fatal(err)
	}
	if got, err := s.Load(identity()); err != nil || got == nil {
		t.Fatalf("%v %v", got, err)
	}
}
