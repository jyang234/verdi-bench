package wire

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"example.com/docsvc/internal/repo"
)

// featureFake is an in-memory repo.Store for driving the fully-wired handler.
type featureFake struct {
	docs map[string]repo.Doc
}

func (f *featureFake) SelectDoc(_ context.Context, id string, out *repo.Doc) error {
	*out = f.docs[id]
	return nil
}
func (f *featureFake) InsertDoc(_ context.Context, id, title, body string) error {
	f.docs[id] = repo.Doc{ID: id, Title: title, Body: body}
	return nil
}
func (f *featureFake) UpdateDoc(_ context.Context, id, title string) error {
	d := f.docs[id]
	d.Title = title
	f.docs[id] = d
	return nil
}
func (f *featureFake) DeleteDoc(_ context.Context, id string) error {
	delete(f.docs, id)
	return nil
}

// TestDeleteDoc is the feature acceptance test: DELETE /docs/{id} removes the
// document.
func TestDeleteDoc(t *testing.T) {
	f := &featureFake{docs: map[string]repo.Doc{"d1": {ID: "d1", Title: "hello", Body: "body"}}}
	h := Handler(f)

	req := httptest.NewRequest("DELETE", "/docs/d1", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204; body=%s", rec.Code, rec.Body.String())
	}
	if _, ok := f.docs["d1"]; ok {
		t.Fatalf("document d1 was not deleted")
	}
}
