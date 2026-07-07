package wire

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"example.com/ordersvc/internal/repo"
)

// featureFake is an in-memory repo.Store for driving the fully-wired handler.
type featureFake struct {
	order repo.Order
	views int
}

func (f *featureFake) SelectOrder(_ context.Context, _ string, out *repo.Order) error {
	*out = f.order
	return nil
}
func (f *featureFake) UpdateOrder(_ context.Context, _, label string) error {
	f.order.Label = label
	return nil
}
func (f *featureFake) IncrViews(_ context.Context, _ string) (int, error) {
	f.views++
	return f.views, nil
}

// TestOrderViewCount is the feature acceptance test: GET /orders/{id} returns
// the order together with a per-order view count that increments on each GET.
func TestOrderViewCount(t *testing.T) {
	f := &featureFake{order: repo.Order{ID: "o1", Label: "hello", Status: "open"}}
	h := Handler(f)

	get := func() int {
		req := httptest.NewRequest("GET", "/orders/o1", nil)
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200; body=%s", rec.Code, rec.Body.String())
		}
		var got struct {
			Order repo.Order `json:"order"`
			Views int        `json:"views"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
			t.Fatalf("decode: %v", err)
		}
		if got.Order.Label != "hello" {
			t.Fatalf("order.label = %q, want hello", got.Order.Label)
		}
		return got.Views
	}

	if v := get(); v != 1 {
		t.Fatalf("first GET views = %d, want 1", v)
	}
	if v := get(); v != 2 {
		t.Fatalf("second GET views = %d, want 2", v)
	}
}
