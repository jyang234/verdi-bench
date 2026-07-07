package core

import (
	"errors"
	"testing"

	"example.com/ingestsvc/internal/store"
)

// TestImportHappy is the feature acceptance test: every row is committed.
func TestImportHappy(t *testing.T) {
	svc := New(store.New())
	if err := svc.Import([]string{"a", "b", "c"}); err != nil {
		t.Fatal(err)
	}
	if got := svc.Count(); got != 3 {
		t.Fatalf("count = %d, want 3", got)
	}
}

// TestImportRejected: a rejected row aborts the import and commits nothing.
func TestImportRejected(t *testing.T) {
	svc := New(store.New())
	err := svc.Import([]string{"a", "", "c"})
	if !errors.Is(err, store.ErrRejected) {
		t.Fatalf("err = %v, want ErrRejected", err)
	}
	if got := svc.Count(); got != 0 {
		t.Fatalf("count = %d, want 0 (nothing committed)", got)
	}
}
