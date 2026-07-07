package core

import (
	"context"
	"testing"

	"example.com/inboxsvc/internal/delivery"
	"example.com/inboxsvc/internal/repo"
)

type fakeStore struct{ msg repo.Message }

func (f *fakeStore) SelectMessage(_ context.Context, _ string, out *repo.Message) error {
	*out = f.msg
	return nil
}
func (f *fakeStore) InsertReceipt(_ context.Context, _ string) error { return nil }

func TestGetMessage(t *testing.T) {
	f := &fakeStore{msg: repo.Message{ID: "m1", Subject: "Hi", Body: "there"}}
	svc := New(f, delivery.NewMem())
	got, err := svc.GetMessage(context.Background(), "m1")
	if err != nil {
		t.Fatal(err)
	}
	if got.Subject != "Hi" {
		t.Fatalf("subject = %q, want Hi", got.Subject)
	}
}
