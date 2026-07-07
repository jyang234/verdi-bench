package wire

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"example.com/billingsvc/internal/repo"
)

// featureFake is an in-memory repo.Store for driving the fully-wired handler.
type featureFake struct {
	inv      repo.Invoice
	audits   []string
	receipts int
}

func (f *featureFake) SelectInvoice(_ context.Context, _ string, out *repo.Invoice) error {
	*out = f.inv
	return nil
}
func (f *featureFake) InsertInvoice(_ context.Context, id, customer string, amount int) error {
	f.inv = repo.Invoice{ID: id, Customer: customer, Amount: amount}
	return nil
}
func (f *featureFake) InsertAuditLog(_ context.Context, _, action string) error {
	f.audits = append(f.audits, action)
	return nil
}
func (f *featureFake) UpdateInvoiceFinalized(_ context.Context, _, _ string) error {
	f.inv.Finalized = true
	return nil
}
func (f *featureFake) InsertReceipt(_ context.Context, _ string) error {
	f.receipts++
	return nil
}

// TestFinalizeInvoice is the feature acceptance test: POST
// /invoices/{id}/finalize marks the invoice finalized and issues one receipt.
func TestFinalizeInvoice(t *testing.T) {
	f := &featureFake{inv: repo.Invoice{ID: "inv1", Customer: "acme", Amount: 100}}
	h := Handler(f)

	req := httptest.NewRequest("POST", "/invoices/inv1/finalize", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204; body=%s", rec.Code, rec.Body.String())
	}
	if !f.inv.Finalized {
		t.Fatalf("invoice not finalized")
	}
	if f.receipts != 1 {
		t.Fatalf("receipts = %d, want 1", f.receipts)
	}
}
