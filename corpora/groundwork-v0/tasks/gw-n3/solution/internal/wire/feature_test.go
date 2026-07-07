package wire

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"example.com/banksvc/internal/store"
)

// TestBalanceRoute is the feature acceptance test: GET /accounts/{id}/balance
// returns the account's current balance.
func TestBalanceRoute(t *testing.T) {
	st := store.New()
	h := Handler(st)
	req := httptest.NewRequest("GET", "/accounts/bob/balance", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body=%s", rec.Code, rec.Body.String())
	}
	var body struct{ Balance int64 }
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Balance != 500 {
		t.Fatalf("balance = %d, want 500", body.Balance)
	}
}
