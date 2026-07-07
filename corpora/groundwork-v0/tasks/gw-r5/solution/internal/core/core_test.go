package core

import (
	"context"
	"testing"

	"example.com/mailsvc/internal/repo"
)

// fakeStore is an in-memory repo.Store double for exercising the domain layer
// without a live database (the SQLStore methods dereference a nil *sql.DB).
// audits is written by the send-audit path but never asserted here: in the
// asynchronous variant it is populated from a goroutine, so reading it from the
// test would race. Tests observe only the synchronous send effect.
type fakeStore struct {
	messages map[string]repo.Message
	audits   []string
}

func newFake() *fakeStore { return &fakeStore{messages: map[string]repo.Message{}} }

func (f *fakeStore) InsertMessage(_ context.Context, id, recipient, body string) error {
	f.messages[id] = repo.Message{ID: id, Recipient: recipient, Body: body}
	return nil
}

func (f *fakeStore) InsertAudit(_ context.Context, _, action string) error {
	f.audits = append(f.audits, action)
	return nil
}

func TestSend(t *testing.T) {
	f := newFake()
	svc := New(f)
	if err := svc.Send(context.Background(), repo.Message{ID: "m1", Recipient: "a@b.c", Body: "hi"}); err != nil {
		t.Fatal(err)
	}
	if _, ok := f.messages["m1"]; !ok {
		t.Fatalf("message m1 was not sent")
	}
}
