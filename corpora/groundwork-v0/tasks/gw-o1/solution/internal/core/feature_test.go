package core

import (
	"errors"
	"testing"

	"example.com/walletsvc/internal/store"
)

// TestTransferHappy is the feature acceptance test: a transfer moves funds.
func TestTransferHappy(t *testing.T) {
	st := store.New()
	svc := New(st)
	if err := svc.Transfer("alice", "bob", 100); err != nil {
		t.Fatal(err)
	}
	if got := svc.Balance("alice"); got != 900 {
		t.Fatalf("alice = %d, want 900", got)
	}
	if got := svc.Balance("bob"); got != 600 {
		t.Fatalf("bob = %d, want 600", got)
	}
}

// TestTransferInsufficient: a transfer that overdraws fails and moves nothing.
func TestTransferInsufficient(t *testing.T) {
	st := store.New()
	svc := New(st)
	err := svc.Transfer("bob", "alice", 100000)
	if !errors.Is(err, store.ErrInsufficient) {
		t.Fatalf("err = %v, want ErrInsufficient", err)
	}
	if got := svc.Balance("bob"); got != 500 {
		t.Fatalf("bob = %d, want 500 (unchanged)", got)
	}
}
