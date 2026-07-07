package wire

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"example.com/eventsvc/internal/bus"
	"example.com/eventsvc/internal/store"
)

// TestGetSubscriber exercises the existing read route.
func TestGetSubscriber(t *testing.T) {
	h := Handler(bus.New(), store.New())
	req := httptest.NewRequest("GET", "/subscribers/s1", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	var sub struct{ ID, Name string }
	if err := json.Unmarshal(rec.Body.Bytes(), &sub); err != nil {
		t.Fatal(err)
	}
	if sub.Name != "Alice" {
		t.Fatalf("name = %q, want Alice", sub.Name)
	}
}

// TestCreatePublishesStatic exercises the existing static-publish route.
func TestCreatePublishesStatic(t *testing.T) {
	b := bus.New()
	h := Handler(b, store.New())
	req := httptest.NewRequest("POST", "/subscribers/s3", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d", rec.Code)
	}
	if len(b.Published) != 1 || b.Published[0].Name != "subscriber.created" {
		t.Fatalf("published = %v, want one subscriber.created", b.Published)
	}
}
