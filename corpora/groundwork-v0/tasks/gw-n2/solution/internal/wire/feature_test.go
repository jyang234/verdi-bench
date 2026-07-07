package wire

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"example.com/userdirsvc/internal/repo"
)

type featureFake struct {
	user   repo.User
	groups int
}

func (f *featureFake) SelectUser(_ context.Context, _ string, out *repo.User) error {
	*out = f.user
	return nil
}
func (f *featureFake) CountGroups(_ context.Context, _ string) (int, error) { return f.groups, nil }
func (f *featureFake) UpdateUser(_ context.Context, _, _ string) error      { return nil }
func (f *featureFake) InsertAudit(_ context.Context, _, _ string) error     { return nil }

// TestGetSummary is the feature acceptance test: GET /users/{id}/summary returns
// the user plus their group count.
func TestGetSummary(t *testing.T) {
	f := &featureFake{user: repo.User{ID: "u1", Name: "Ann", Email: "ann@example.com"}, groups: 3}
	h := Handler(f)
	req := httptest.NewRequest("GET", "/users/u1/summary", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body=%s", rec.Code, rec.Body.String())
	}
	var body struct {
		User       repo.User
		GroupCount int
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.User.ID != "u1" || body.GroupCount != 3 {
		t.Fatalf("summary = %+v, want u1 with 3 groups", body)
	}
}
