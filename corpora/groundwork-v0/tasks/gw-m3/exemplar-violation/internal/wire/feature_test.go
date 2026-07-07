package wire

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"example.com/feedsvc/internal/emit"
	"example.com/feedsvc/internal/repo"
)

type featureFake struct{ item repo.Item }

func (f *featureFake) SelectItem(_ context.Context, _ string, out *repo.Item) error {
	*out = f.item
	return nil
}

// spyEmitter records the feed item ids it is asked to Emit.
type spyEmitter struct{ ids []string }

func (s *spyEmitter) Emit(_ context.Context, id string) error {
	s.ids = append(s.ids, id)
	return nil
}

// TestReadActivity is the feature acceptance test: GET returns the feed item
// (200) and emits a read-activity event for it through the wired read-activity
// emitter.
func TestReadActivity(t *testing.T) {
	f := &featureFake{item: repo.Item{ID: "f1", Actor: "ada", Verb: "posted"}}
	spy := &spyEmitter{}
	h := Handler(f, emit.NewNop(), spy)

	req := httptest.NewRequest("GET", "/feed/f1", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body=%s", rec.Code, rec.Body.String())
	}
	if len(spy.ids) != 1 || spy.ids[0] != "f1" {
		t.Fatalf("emitted ids = %v, want [f1]", spy.ids)
	}
}
