package wire

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"example.com/eventsvc/internal/bus"
	"example.com/eventsvc/internal/store"
)

// TestPublishEvent is the feature acceptance test: POST /publish/{id} emits an
// event whose name comes from the request.
func TestPublishEvent(t *testing.T) {
	b := bus.New()
	h := Handler(b, store.New())
	req := httptest.NewRequest("POST", "/publish/s1?event=signup", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("status = %d, body=%s", rec.Code, rec.Body.String())
	}
	if len(b.Published) != 1 {
		t.Fatalf("published %d event(s), want 1", len(b.Published))
	}
	if !strings.Contains(b.Published[0].Name, "signup") {
		t.Fatalf("event name = %q, want it to carry \"signup\"", b.Published[0].Name)
	}
}
