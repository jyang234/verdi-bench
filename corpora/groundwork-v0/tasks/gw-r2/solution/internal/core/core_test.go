package core

import (
	"context"
	"testing"

	"example.com/ordersvc/internal/repo"
)

// fakeStore is an in-memory repo.Store double for exercising the domain layer
// without a live database (the SQLStore methods dereference a nil *sql.DB).
type fakeStore struct {
	order repo.Order
	views int
}

func (f *fakeStore) SelectOrder(_ context.Context, _ string, out *repo.Order) error {
	*out = f.order
	return nil
}

func (f *fakeStore) UpdateOrder(_ context.Context, _, label string) error {
	f.order.Label = label
	return nil
}

func (f *fakeStore) IncrViews(_ context.Context, _ string) (int, error) {
	f.views++
	return f.views, nil
}

func TestGetOrder(t *testing.T) {
	f := &fakeStore{order: repo.Order{ID: "o1", Label: "hello", Status: "open"}}
	svc := New(f)
	got, err := svc.GetOrder(context.Background(), "o1")
	if err != nil {
		t.Fatal(err)
	}
	if got.Label != "hello" {
		t.Fatalf("label = %q, want hello", got.Label)
	}
}

func TestRenameOrder(t *testing.T) {
	f := &fakeStore{}
	svc := New(f)
	if err := svc.RenameOrder(context.Background(), "o1", "new"); err != nil {
		t.Fatal(err)
	}
	if f.order.Label != "new" {
		t.Fatalf("label = %q, want new", f.order.Label)
	}
}
