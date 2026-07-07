package core

import (
	"context"
	"testing"

	"example.com/catalogsvc/internal/repo"
)

type fakeStore struct {
	product repo.Product
	created []repo.Product
	audits  []string
}

func (f *fakeStore) SelectProduct(_ context.Context, _ string, out *repo.Product) error {
	*out = f.product
	return nil
}
func (f *fakeStore) InsertProduct(_ context.Context, p repo.Product) error {
	f.created = append(f.created, p)
	return nil
}
func (f *fakeStore) UpdateProduct(_ context.Context, _, name string) error {
	f.product.Name = name
	return nil
}
func (f *fakeStore) InsertAudit(_ context.Context, _, action string) error {
	f.audits = append(f.audits, action)
	return nil
}

func TestGetProduct(t *testing.T) {
	f := &fakeStore{product: repo.Product{ID: "p1", Name: "Widget", Price: 100}}
	svc := New(f)
	got, err := svc.GetProduct(context.Background(), "p1")
	if err != nil {
		t.Fatal(err)
	}
	if got.Name != "Widget" {
		t.Fatalf("name = %q, want Widget", got.Name)
	}
}
