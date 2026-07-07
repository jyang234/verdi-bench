package core

import (
	"context"
	"testing"

	"example.com/accountsvc/internal/repo"
	"example.com/accountsvc/internal/views"
)

type fakeStore struct{ acct repo.Account }

func (f *fakeStore) SelectAccount(_ context.Context, _ string, out *repo.Account) error {
	*out = f.acct
	return nil
}
func (f *fakeStore) IncrCounter(_ context.Context, _, _ string) error { return nil }

func TestGetAccount(t *testing.T) {
	f := &fakeStore{acct: repo.Account{ID: "a1", Name: "Ada"}}
	svc := New(f, views.NewMem(), views.NewMem())
	got, n, err := svc.GetAccount(context.Background(), "a1")
	if err != nil {
		t.Fatal(err)
	}
	if got.Name != "Ada" {
		t.Fatalf("name = %q, want Ada", got.Name)
	}
	if n != 1 {
		t.Fatalf("views = %d, want 1", n)
	}
}
