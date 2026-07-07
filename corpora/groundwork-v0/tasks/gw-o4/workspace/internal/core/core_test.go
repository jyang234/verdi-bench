package core

import (
	"testing"

	"example.com/ingestsvc/internal/store"
)

func TestSeed(t *testing.T) {
	svc := New(store.New())
	if err := svc.Seed("row-1"); err != nil {
		t.Fatal(err)
	}
	if got := svc.Count(); got != 1 {
		t.Fatalf("count = %d, want 1", got)
	}
}
