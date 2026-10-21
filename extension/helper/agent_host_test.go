package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestQuietSiblingIsUsedOnlyWhenItIsNextToTheHelper(t *testing.T) {
	dir := t.TempDir()
	exe := filepath.Join(dir, "resilience-helper.exe")
	if got := quietSibling(exe, os.Stat); got != "" {
		t.Fatalf("no sibling exists, got %q", got)
	}
	want := filepath.Join(dir, quietName)
	if err := os.WriteFile(want, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := quietSibling(exe, os.Stat); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
	// a folder with that name is not a program
	if err := os.Remove(want); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(want, 0o755); err != nil {
		t.Fatal(err)
	}
	if got := quietSibling(exe, os.Stat); got != "" {
		t.Fatalf("a directory must not count, got %q", got)
	}
}
