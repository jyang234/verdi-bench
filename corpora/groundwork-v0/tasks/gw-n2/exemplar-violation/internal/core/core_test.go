package core

import (
	"context"
	"testing"

	"example.com/userdirsvc/internal/repo"
)

type fakeStore struct {
	user   repo.User
	groups int
	audits []string
}

func (f *fakeStore) SelectUser(_ context.Context, _ string, out *repo.User) error {
	*out = f.user
	return nil
}
func (f *fakeStore) CountGroups(_ context.Context, _ string) (int, error) { return f.groups, nil }
func (f *fakeStore) UpdateUser(_ context.Context, _, name string) error {
	f.user.Name = name
	return nil
}
func (f *fakeStore) InsertAudit(_ context.Context, _, action string) error {
	f.audits = append(f.audits, action)
	return nil
}

func TestGetUser(t *testing.T) {
	f := &fakeStore{user: repo.User{ID: "u1", Name: "Ann", Email: "ann@example.com"}}
	svc := New(f)
	got, err := svc.GetUser(context.Background(), "u1")
	if err != nil {
		t.Fatal(err)
	}
	if got.Name != "Ann" {
		t.Fatalf("name = %q, want Ann", got.Name)
	}
}
