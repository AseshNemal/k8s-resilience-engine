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
		os.Remove(f.Path) // left behind by a helper that never finished
	}
	return nil, ErrBusy
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
