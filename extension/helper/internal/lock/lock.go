// Package lock keeps two connection attempts from running at once.
//
// The helper is a separate process each time the page, the background renewal job or a person runs it, so
// an in-memory lock cannot stop two of them from issuing tokens or installing credentials together. The lock
// is a file created exclusively in the user's configuration directory: the operating system decides who
// created it first, on every platform, with no dependency.
package lock

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// ErrBusy means another connection or renewal holds the lock.
var ErrBusy = errors.New("another connection or renewal is already running")

// Lock is taken for the whole of a command that issues a token, installs a credential or disconnects.
type Lock interface {
	// Acquire returns at once. The release function is safe to call more than once and frees the lock
	// only if this holder still owns it.
	Acquire() (release func(), err error)
}

// afterStaleCheck lets a test pause a waiter between "this lock is stale" and "take it over". It is nil in production.
var afterStaleCheck func()

// File is a Lock backed by an exclusively created file.
type File struct {
	Path string
	// Stale is how old an unreleased lock must be before it is taken over. It must exceed the longest
	// command (five minutes), so a crash cannot block renewal for long and a live command never loses it.
	Stale time.Duration
}

func (f *File) Acquire() (func(), error) {
	if err := os.MkdirAll(filepath.Dir(f.Path), 0o700); err != nil {
		return nil, fmt.Errorf("the lock directory cannot be created: %w", err)
	}
	for attempt := 0; attempt < 2; attempt++ {
		handle, err := os.OpenFile(f.Path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
		if err == nil {
			fmt.Fprintf(handle, "%d\n", os.Getpid())
			handle.Close()
			return f.releaser(), nil
		}
		if !errors.Is(err, os.ErrExist) {
			return nil, fmt.Errorf("the lock cannot be created: %w", err)
		}
		info, statErr := os.Stat(f.Path)
		if statErr != nil {
			continue // it was released between the two calls: try to create it again
		}
		if time.Since(info.ModTime()) < f.Stale {
			return nil, ErrBusy
		}
		if afterStaleCheck != nil {
			afterStaleCheck()
		}
		if !f.takeOver(info) {
			return nil, ErrBusy
		}
	}
	return nil, ErrBusy
}

// claimStale is how old a takeover claim must be before it is treated as left behind by a process that died while
// taking over. A takeover takes milliseconds.
const claimStale = time.Minute

// takeOver removes a lock that was left behind by a helper that never finished. Deciding that a lock is stale and
// removing it are two steps, and between them another waiter may have removed the same file and created a new, live
// lock; deleting that one would leave two holders. So the removal happens under a claim file that only one waiter can
// create at a time, and inside the claim the file is looked at again: it is removed only if it is still the very file
// that was judged stale and is still old. It reports whether the caller may now try to create the lock.
func (f *File) takeOver(seen os.FileInfo) bool {
	claim := f.Path + ".takeover"
	handle, err := os.OpenFile(claim, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		// Another waiter is taking over right now. A claim that is itself ancient was left by a crash: clear it so the
		// next attempt can proceed.
		if info, statErr := os.Stat(claim); statErr == nil && time.Since(info.ModTime()) > claimStale {
			os.Remove(claim)
		}
		return false
	}
	handle.Close()
	defer os.Remove(claim)

	now, err := os.Stat(f.Path)
	if err != nil {
		return true // already gone: the caller creates it
	}
	if !os.SameFile(seen, now) || time.Since(now.ModTime()) < f.Stale {
		return false // replaced by a live holder in the meantime
	}
	return os.Remove(f.Path) == nil
}

// releaser frees the lock once. A holder that was taken over (stale) and releases late must not delete
// the new holder's file, so it only removes the file it created.
func (f *File) releaser() func() {
	info, _ := os.Stat(f.Path)
	var once sync.Once
	return func() {
		once.Do(func() {
			now, err := os.Stat(f.Path)
			if err == nil && info != nil && os.SameFile(info, now) {
				os.Remove(f.Path)
			}
		})
	}
}
