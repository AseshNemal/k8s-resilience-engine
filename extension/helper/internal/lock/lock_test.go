package lock

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func newFile(t *testing.T) *File {
	t.Helper()
	return &File{Path: filepath.Join(t.TempDir(), "sub", "connect.lock"), Stale: 10 * time.Minute}
}

func TestOnlyOneHolderAtATime(t *testing.T) {
	l := newFile(t)
	release, err := l.Acquire()
	if err != nil {
		t.Fatal(err)
	}

	if _, err := l.Acquire(); !errors.Is(err, ErrBusy) {
		t.Fatalf("a second acquire while held = %v, want ErrBusy", err)
	}
	release()
	again, err := l.Acquire()
	if err != nil {
		t.Fatalf("after release: %v", err)
	}
	again()
}

func TestAnotherProcessSeesTheSameLockBecauseItIsAFile(t *testing.T) {
	l := newFile(t)
	other := &File{Path: l.Path, Stale: l.Stale} // what a second helper process would build
	release, _ := l.Acquire()
	defer release()

	if _, err := other.Acquire(); !errors.Is(err, ErrBusy) {
		t.Fatalf("a second File on the same path = %v, want ErrBusy", err)
	}
}

func TestReleaseIsSafeToCallTwiceAndNeverFreesSomeoneElsesLock(t *testing.T) {
	l := newFile(t)
	first, _ := l.Acquire()
	first()
	second, err := l.Acquire()
	if err != nil {
		t.Fatal(err)
	}
	defer second()

	first() // a late, repeated release of the first holder

	if _, err := l.Acquire(); !errors.Is(err, ErrBusy) {
		t.Fatal("a repeated release must not free the lock the second holder owns")
	}
}

func TestALockLeftByACrashedHelperExpires(t *testing.T) {
	l := newFile(t)
	if _, err := l.Acquire(); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-11 * time.Minute)
	if err := os.Chtimes(l.Path, old, old); err != nil {
		t.Fatal(err)
	}

	release, err := l.Acquire()
	if err != nil {
		t.Fatalf("a stale lock must be taken over: %v", err)
	}
	release()
}

func TestAFreshLockIsNeverTakenOver(t *testing.T) {
	l := newFile(t)
	l.Acquire()
	recent := time.Now().Add(-9 * time.Minute)
	os.Chtimes(l.Path, recent, recent)

	if _, err := l.Acquire(); !errors.Is(err, ErrBusy) {
		t.Fatalf("a lock younger than Stale = %v, want ErrBusy", err)
	}
}

func TestTheLockFileIsPrivateToTheUser(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permission bits")
	}
	l := newFile(t)
	release, _ := l.Acquire()
	defer release()
	info, err := os.Stat(l.Path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("mode = %v", info.Mode().Perm())
	}
	dir, _ := os.Stat(filepath.Dir(l.Path))
	if dir.Mode().Perm()&0o077 != 0 {
		t.Fatalf("directory mode = %v", dir.Mode().Perm())
	}
}

func TestUnderContentionExactlyOneGoroutineHoldsTheLock(t *testing.T) {
	l := newFile(t)
	var holders, maxHolders, wins int32
	var wg sync.WaitGroup
	for i := 0; i < 24; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			release, err := (&File{Path: l.Path, Stale: l.Stale}).Acquire()
			if err != nil {
				return
			}
			atomic.AddInt32(&wins, 1)
			n := atomic.AddInt32(&holders, 1)
			for {
				m := atomic.LoadInt32(&maxHolders)
				if n <= m || atomic.CompareAndSwapInt32(&maxHolders, m, n) {
					break
				}
			}
			time.Sleep(5 * time.Millisecond)
			atomic.AddInt32(&holders, -1)
			release()
		}()
	}
	wg.Wait()
	if maxHolders != 1 || wins == 0 {
		t.Fatalf("max simultaneous holders = %d, successful acquires = %d", maxHolders, wins)
	}
}

func TestAnUnwritableLocationIsAnErrorNotABusyLock(t *testing.T) {
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "file"), []byte("a file where a directory is needed"), 0o600)
	l := &File{Path: filepath.Join(root, "file", "x", "connect.lock"), Stale: time.Minute}

	_, err := l.Acquire()

	if err == nil || errors.Is(err, ErrBusy) {
		t.Fatalf("err = %v, want a real error so the caller does not wait for a lock that can never be freed", err)
	}
}

func TestAFileThatCannotBeCreatedInAnExistingDirectoryIsARealErrorToo(t *testing.T) {
	if runtime.GOOS == "windows" || os.Geteuid() == 0 {
		t.Skip("needs POSIX permissions and a non-root user")
	}
	dir := t.TempDir()
	if err := os.Chmod(dir, 0o500); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(dir, 0o700) })

	_, err := (&File{Path: filepath.Join(dir, "connect.lock"), Stale: time.Minute}).Acquire()

	if err == nil || errors.Is(err, ErrBusy) {
		t.Fatalf("err = %v, want a real error, not a busy lock", err)
	}
}
