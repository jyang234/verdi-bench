package core

import (
	"context"
	"testing"

	"example.com/docsvc/internal/repo"
)

// fakeStore is an in-memory repo.Store double for exercising the domain layer
// without a live database (the SQLStore methods dereference a nil *sql.DB).
type fakeStore struct {
	docs map[string]repo.Doc
}

func newFake() *fakeStore { return &fakeStore{docs: map[string]repo.Doc{}} }

func (f *fakeStore) SelectDoc(_ context.Context, id string, out *repo.Doc) error {
	*out = f.docs[id]
	return nil
}

func (f *fakeStore) InsertDoc(_ context.Context, id, title, body string) error {
	f.docs[id] = repo.Doc{ID: id, Title: title, Body: body}
	return nil
}

func (f *fakeStore) UpdateDoc(_ context.Context, id, title string) error {
	d := f.docs[id]
	d.Title = title
	f.docs[id] = d
	return nil
}

func (f *fakeStore) DeleteDoc(_ context.Context, id string) error {
	delete(f.docs, id)
	return nil
}

func TestCreateAndGet(t *testing.T) {
	f := newFake()
	svc := New(f)
	if err := svc.Create(context.Background(), "d1", "hello", "body"); err != nil {
		t.Fatal(err)
	}
	got, err := svc.Get(context.Background(), "d1")
	if err != nil {
		t.Fatal(err)
	}
	if got.Title != "hello" {
		t.Fatalf("title = %q, want hello", got.Title)
	}
}

func TestRename(t *testing.T) {
	f := newFake()
	svc := New(f)
	if err := svc.Create(context.Background(), "d1", "hello", "body"); err != nil {
		t.Fatal(err)
	}
	if err := svc.Rename(context.Background(), "d1", "new"); err != nil {
		t.Fatal(err)
	}
	if f.docs["d1"].Title != "new" {
		t.Fatalf("title = %q, want new", f.docs["d1"].Title)
	}
}

func TestAuthorizeBlocksEmptyID(t *testing.T) {
	f := newFake()
	svc := New(f)
	if err := svc.Create(context.Background(), "", "hello", "body"); err == nil {
		t.Fatal("expected create with empty id to be rejected by Authorize")
	}
}
