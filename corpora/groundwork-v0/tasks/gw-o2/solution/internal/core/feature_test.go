package core

import (
	"errors"
	"testing"

	"example.com/limitsvc/internal/store"
)

// TestProcessHappy is the feature acceptance test: a valid payload is processed.
func TestProcessHappy(t *testing.T) {
	svc := New(store.New())
	if err := svc.Process("payload-1"); err != nil {
		t.Fatal(err)
	}
	if got := svc.Done(); got != 1 {
		t.Fatalf("done = %d, want 1", got)
	}
}

// TestProcessInvalid: an invalid payload is rejected and processes nothing.
func TestProcessInvalid(t *testing.T) {
	svc := New(store.New())
	err := svc.Process("")
	if !errors.Is(err, ErrInvalid) {
		t.Fatalf("err = %v, want ErrInvalid", err)
	}
	if got := svc.Done(); got != 0 {
		t.Fatalf("done = %d, want 0 (unchanged)", got)
	}
}
