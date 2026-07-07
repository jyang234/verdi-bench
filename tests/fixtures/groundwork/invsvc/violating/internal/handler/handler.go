// Package handler holds invsvc's HTTP entry points. Every method calls into the
// catalog layer and never touches store directly — the strict
// handler -> catalog -> store spine that makes ShowItem a clean read route:
// adding a single write reachable from ShowItem on a branch is a brand-new
// must_not_reach violation, not one the base already contains.
package handler

import (
	"context"
	"encoding/json"
	"net/http"

	"example.com/invsvc/internal/catalog"
)

// Server serves the item endpoints.
type Server struct {
	cat *catalog.Catalog
}

// New returns a Server over cat.
func New(cat *catalog.Catalog) *Server { return &Server{cat: cat} }

// ShowItem handles GET /items/{id} — a read route.
func (s *Server) ShowItem(w http.ResponseWriter, r *http.Request) {
	it, err := s.cat.Describe(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	writeJSON(r.Context(), w, it)
}

// RenameItem handles PUT /items/{id} — a write route.
func (s *Server) RenameItem(w http.ResponseWriter, r *http.Request) {
	var body struct{ Name string }
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.cat.Rename(r.Context(), r.PathValue("id"), body.Name); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func writeJSON(_ context.Context, w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
