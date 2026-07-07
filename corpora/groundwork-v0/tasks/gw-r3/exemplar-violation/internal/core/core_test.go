package core

import (
	"context"
	"testing"

	"example.com/billingsvc/internal/repo"
)

// fakeStore is an in-memory repo.Store double for exercising the domain layer
// without a live database (the SQLStore methods dereference a nil *sql.DB).
type fakeStore struct {
	inv      repo.Invoice
	audits   []string
	receipts int
}

func (f *fakeStore) SelectInvoice(_ context.Context, _ string, out *repo.Invoice) error {
	*out = f.inv
	return nil
}

func (f *fakeStore) InsertInvoice(_ context.Context, id, customer string, amount int) error {
	f.inv = repo.Invoice{ID: id, Customer: customer, Amount: amount}
	return nil
}

func (f *fakeStore) InsertAuditLog(_ context.Context, _, action string) error {
	f.audits = append(f.audits, action)
	return nil
}

func (f *fakeStore) UpdateInvoiceFinalized(_ context.Context, _, _ string) error {
	f.inv.Finalized = true
	return nil
}

func (f *fakeStore) InsertReceipt(_ context.Context, _ string) error {
	f.receipts++
	return nil
}

func TestGetInvoice(t *testing.T) {
	f := &fakeStore{inv: repo.Invoice{ID: "inv1", Customer: "acme", Amount: 100}}
	svc := New(f)
	got, err := svc.GetInvoice(context.Background(), "inv1")
	if err != nil {
		t.Fatal(err)
	}
	if got.Customer != "acme" {
		t.Fatalf("customer = %q, want acme", got.Customer)
	}
}

func TestCreateInvoice(t *testing.T) {
	f := &fakeStore{}
	svc := New(f)
	if err := svc.CreateInvoice(context.Background(), repo.Invoice{ID: "inv1", Customer: "acme", Amount: 100}); err != nil {
		t.Fatal(err)
	}
	if f.inv.ID != "inv1" {
		t.Fatalf("invoice id = %q, want inv1", f.inv.ID)
	}
	if len(f.audits) != 1 || f.audits[0] != "create" {
		t.Fatalf("audits = %v, want [create]", f.audits)
	}
}
