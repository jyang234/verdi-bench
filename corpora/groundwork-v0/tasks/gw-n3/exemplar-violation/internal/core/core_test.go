package core

import (
	"testing"

	"example.com/banksvc/internal/store"
)

func TestDeposit(t *testing.T) {
	st := store.New()
	svc := New(st)
	if err := svc.Deposit("bob", 250); err != nil {
		t.Fatal(err)
	}
	if got := st.Balance("bob"); got != 750 {
		t.Fatalf("bob = %d, want 750", got)
	}
}
