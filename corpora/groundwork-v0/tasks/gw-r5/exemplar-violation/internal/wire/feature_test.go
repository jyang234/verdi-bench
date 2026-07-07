package wire

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"example.com/mailsvc/internal/repo"
)

// featureFake is an in-memory repo.Store for driving the fully-wired handler.
// audits is written by the send-audit path but never read by the test: in the
// asynchronous variant it is populated from a goroutine, so reading it here
// would race. The test observes only the synchronous send effect.
type featureFake struct {
	messages map[string]repo.Message
	audits   []string
}

func (f *featureFake) InsertMessage(_ context.Context, id, recipient, body string) error {
	f.messages[id] = repo.Message{ID: id, Recipient: recipient, Body: body}
	return nil
}
func (f *featureFake) InsertAudit(_ context.Context, _, action string) error {
	f.audits = append(f.audits, action)
	return nil
}

// TestSendMessage is the feature acceptance test: POST /send stores the message
// and returns 202. It asserts only the synchronous send effect, never the audit.
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
	if _, ok := f.messages["m1"]; !ok {
		t.Fatalf("message m1 was not sent")
	}
}
