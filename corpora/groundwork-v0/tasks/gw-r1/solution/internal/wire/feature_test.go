package wire

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"example.com/deskflow/internal/repo"
)

// featureFake is an in-memory repo.Store for driving the fully-wired handler.
type featureFake struct {
	ticket repo.Ticket
	count  int
}

func (f *featureFake) SelectTicket(_ context.Context, _ string, out *repo.Ticket) error {
	*out = f.ticket
	return nil
}
func (f *featureFake) UpdateTicket(_ context.Context, _, subject string) error {
	f.ticket.Subject = subject
	return nil
}
func (f *featureFake) InsertAudit(_ context.Context, _, _ string) error { return nil }
func (f *featureFake) CountAudit(_ context.Context, _ string) (int, error) {
	return f.count, nil
}

// TestHistoryEndpoint is the feature acceptance test: GET /tickets/{id}/history
// returns the ticket and its audit-entry count as JSON.
func TestHistoryEndpoint(t *testing.T) {
	f := &featureFake{ticket: repo.Ticket{ID: "t1", Subject: "hello", Status: "open"}, count: 3}
	h := Handler(f)

	req := httptest.NewRequest("GET", "/tickets/t1/history", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", rec.Code, rec.Body.String())
	}
	var got struct {
		Ticket     repo.Ticket `json:"ticket"`
		AuditCount int         `json:"audit_count"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if got.AuditCount != 3 {
		t.Fatalf("audit_count = %d, want 3", got.AuditCount)
	}
	if got.Ticket.Subject != "hello" {
		t.Fatalf("ticket.subject = %q, want hello", got.Ticket.Subject)
	}
}
