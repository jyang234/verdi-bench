package core

import (
	"context"
	"testing"

	"example.com/feedsvc/internal/emit"
	"example.com/feedsvc/internal/repo"
)

type fakeStore struct{ item repo.Item }

func (f *fakeStore) SelectItem(_ context.Context, _ string, out *repo.Item) error {
	*out = f.item
	return nil
}

func TestGetItem(t *testing.T) {
	f := &fakeStore{item: repo.Item{ID: "f1", Actor: "ada", Verb: "posted"}}
	svc := New(f, emit.NewNop(), emit.NewNop())
	got, err := svc.GetItem(context.Background(), "f1")
	if err != nil {
		t.Fatal(err)
	}
	if got.Actor != "ada" {
		t.Fatalf("actor = %q, want ada", got.Actor)
	}
}
