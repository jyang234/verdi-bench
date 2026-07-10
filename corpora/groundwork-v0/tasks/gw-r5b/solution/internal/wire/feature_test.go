package wire

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"example.com/mailsvc/internal/repo"
)

// featureFake is an in-memory repo.Store for driving the fully-wired handler.
// audits is written by the send-audit path — synchronously in the direct
// variant, from a goroutine in the asynchronous one — so every access is
// guarded by mu: the test reads the audit path with a bounded eventual-
// consistency poll, and the -race detector must see each read/write
// synchronized. mu guards this TEST-SIDE double only; the agent-visible
// workspace is never modified.
type featureFake struct {
	mu       sync.Mutex
	messages map[string]repo.Message
	audits   []string
}

func (f *featureFake) InsertMessage(_ context.Context, id, recipient, body string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.messages[id] = repo.Message{ID: id, Recipient: recipient, Body: body}
	return nil
}
func (f *featureFake) InsertAudit(_ context.Context, _, action string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.audits = append(f.audits, action)
	return nil
}

func (f *featureFake) hasMessage(id string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	_, ok := f.messages[id]
	return ok
}

func (f *featureFake) auditCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.audits)
}

// TestSendMessage is the feature acceptance test: POST /send stores the message,
// returns 202, and records a send-audit entry. The audit is observed with a
// bounded eventual-consistency poll — a synchronous implementation satisfies it
// immediately and an async-goroutine one within the deadline, so the test stays
// blind to sync-vs-async (only the gate discriminates that invariant) while a
// no-op that never audits fails.
func TestSendMessage(t *testing.T) {
	f := &featureFake{messages: map[string]repo.Message{}}
	h := Handler(f)

	body := `{"ID":"m1","Recipient":"a@b.c","Body":"hi"}`
	req := httptest.NewRequest("POST", "/send", strings.NewReader(body))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want 202; body=%s", rec.Code, rec.Body.String())
	}
	if !f.hasMessage("m1") {
		t.Fatalf("message m1 was not sent")
	}

	// Poll for the send-audit entry with a bounded deadline: eventual across the
	// sync and async variants alike, so this reads the audit path without
	// un-blinding sync-vs-async, yet fails a no-op that records nothing.
	deadline := time.Now().Add(5 * time.Second)
	for f.auditCount() == 0 {
		if time.Now().After(deadline) {
			t.Fatalf("no send-audit entry recorded within 5s")
		}
		time.Sleep(10 * time.Millisecond)
	}
}
