package core

import (
	"testing"

	"example.com/limitsvc/internal/store"
)

func TestRun(t *testing.T) {
	svc := New(store.New())
	if err := svc.Run("job-1"); err != nil {
		t.Fatal(err)
	}
	if got := svc.Done(); got != 1 {
		t.Fatalf("done = %d, want 1", got)
	}
}
