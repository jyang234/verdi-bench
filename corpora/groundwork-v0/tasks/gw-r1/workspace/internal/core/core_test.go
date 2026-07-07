package core

import (
	"context"
	"testing"

	"example.com/deskflow/internal/repo"
)

// fakeStore is an in-memory repo.Store double for exercising the domain layer
// without a live database (the SQLStore methods dereference a nil *sql.DB).
type fakeStore struct {
	ticket repo.Ticket
	audits []string
	count  int
}

func (f *fakeStore) SelectTicket(_ context.Context, _ string, out *repo.Ticket) error {
	*out = f.ticket
	return nil
}

func (f *fakeStore) UpdateTicket(_ context.Context, _, subject string) error {
	f.ticket.Subject = subject
	return nil
}

func (f *fakeStore) InsertAudit(_ context.Context, _, action string) error {
	f.audits = append(f.audits, action)
	return nil
}

func (f *fakeStore) CountAudit(_ context.Context, _ string) (int, error) {
	return f.count, nil
}

func TestGetTicket(t *testing.T) {
	f := &fakeStore{ticket: repo.Ticket{ID: "t1", Subject: "hello", Status: "open"}}
	svc := New(f)
	got, err := svc.GetTicket(context.Background(), "t1")
	if err != nil {
		t.Fatal(err)
	}
	if got.Subject != "hello" {
		t.Fatalf("subject = %q, want hello", got.Subject)
	}
}

func TestRenameTicket(t *testing.T) {
	f := &fakeStore{}
	svc := New(f)
	if err := svc.RenameTicket(context.Background(), "t1", "new"); err != nil {
		t.Fatal(err)
	}
	if f.ticket.Subject != "new" {
		t.Fatalf("subject = %q, want new", f.ticket.Subject)
	}
	if len(f.audits) != 1 || f.audits[0] != "rename" {
		t.Fatalf("audits = %v, want [rename]", f.audits)
	}
}
