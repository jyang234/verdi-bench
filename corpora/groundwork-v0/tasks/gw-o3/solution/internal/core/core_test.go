package core

import (
	"testing"

	"example.com/pubsvc/internal/audit"
	"example.com/pubsvc/internal/bus"
)

func TestNotify(t *testing.T) {
	audit.Reset()
	bus.Reset()
	New().Notify("evt-1")
	if got := len(audit.Events()); got != 1 {
		t.Fatalf("audit events = %d, want 1", got)
	}
	if got := len(bus.Published()); got != 1 {
		t.Fatalf("published = %d, want 1", got)
	}
}
