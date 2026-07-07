package core

import (
	"testing"

	"example.com/pubsvc/internal/audit"
	"example.com/pubsvc/internal/bus"
)

// TestApprove is the feature acceptance test: approving records an audit and
// publishes an event. Both effects must occur; their relative order is not
// asserted (the feature's observable outcome is that the approval is audited
// and the event is published).
func TestApprove(t *testing.T) {
	audit.Reset()
	bus.Reset()
	New().Approve("loan-7")
	if got := len(audit.Events()); got != 1 {
		t.Fatalf("audit events = %d, want 1", got)
	}
	if got := len(bus.Published()); got != 1 {
		t.Fatalf("published = %d, want 1", got)
	}
}
