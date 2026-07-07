package wire

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"example.com/accountsvc/internal/repo"
	"example.com/accountsvc/internal/views"
)

type featureFake struct{ acct repo.Account }

func (f *featureFake) SelectAccount(_ context.Context, _ string, out *repo.Account) error {
	*out = f.acct
	return nil
}
func (f *featureFake) IncrCounter(_ context.Context, _, _ string) error { return nil }

// TestViewCount is the feature acceptance test: GET returns the account and a
// per-account view count that increments on each read.
func TestViewCount(t *testing.T) {
	f := &featureFake{acct: repo.Account{ID: "a1", Name: "Ada"}}
	h := Handler(f, views.NewMem(), views.NewMem())
	var last int
	for want := 1; want <= 2; want++ {
		req := httptest.NewRequest("GET", "/accounts/a1", nil)
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, body=%s", rec.Code, rec.Body.String())
		}
		var got struct {
			Views int `json:"views"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
			t.Fatal(err)
		}
		last = got.Views
		if last != want {
			t.Fatalf("views = %d, want %d", last, want)
		}
	}
}
