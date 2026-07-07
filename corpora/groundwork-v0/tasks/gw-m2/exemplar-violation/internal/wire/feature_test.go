package wire

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"example.com/inboxsvc/internal/delivery"
	"example.com/inboxsvc/internal/repo"
)

type featureFake struct{ msg repo.Message }

func (f *featureFake) SelectMessage(_ context.Context, _ string, out *repo.Message) error {
	*out = f.msg
	return nil
}
func (f *featureFake) InsertReceipt(_ context.Context, _ string) error { return nil }

// spyDeliverer records the message ids it is asked to Record.
type spyDeliverer struct{ ids []string }

func (s *spyDeliverer) Record(_ context.Context, id string) error {
	s.ids = append(s.ids, id)
	return nil
}

// TestReadReceipt is the feature acceptance test: GET returns the message (200)
// and records a read receipt for it through the wired read-receipt recorder.
func TestReadReceipt(t *testing.T) {
	f := &featureFake{msg: repo.Message{ID: "m1", Subject: "Hi", Body: "there"}}
	spy := &spyDeliverer{}
	h := Handler(f, delivery.NewMem(), spy)

	req := httptest.NewRequest("GET", "/messages/m1", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body=%s", rec.Code, rec.Body.String())
	}
	if len(spy.ids) != 1 || spy.ids[0] != "m1" {
		t.Fatalf("recorded ids = %v, want [m1]", spy.ids)
	}
}
