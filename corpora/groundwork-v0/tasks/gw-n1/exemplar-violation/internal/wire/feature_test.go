package wire

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"example.com/catalogsvc/internal/repo"
)

type featureFake struct{ created []repo.Product }

func (f *featureFake) SelectProduct(_ context.Context, _ string, _ *repo.Product) error { return nil }
func (f *featureFake) InsertProduct(_ context.Context, p repo.Product) error {
	f.created = append(f.created, p)
	return nil
}
func (f *featureFake) UpdateProduct(_ context.Context, _, _ string) error { return nil }
func (f *featureFake) InsertAudit(_ context.Context, _, _ string) error   { return nil }

// TestCreateProduct is the feature acceptance test: POST /products creates one.
func TestCreateProduct(t *testing.T) {
	f := &featureFake{}
	h := Handler(f)
	req := httptest.NewRequest("POST", "/products", strings.NewReader(`{"ID":"p9","Name":"Gadget","Price":250}`))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d, body=%s", rec.Code, rec.Body.String())
	}
	if len(f.created) != 1 || f.created[0].ID != "p9" {
		t.Fatalf("created = %v, want one product p9", f.created)
	}
}
