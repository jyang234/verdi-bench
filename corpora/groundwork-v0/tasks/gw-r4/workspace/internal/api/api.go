// Package api holds docsvc's HTTP entry points. Every method calls into the
// core layer and never touches repo directly.
package api

import (
	"context"
	"encoding/json"
	"net/http"

	"example.com/docsvc/internal/core"
)

// Server serves the document endpoints.
type Server struct {
	svc *core.Service
}

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// GetDoc handles GET /docs/{id}.
func (s *Server) GetDoc(w http.ResponseWriter, r *http.Request) {
	d, err := s.svc.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	writeJSON(r.Context(), w, d)
}

// CreateDoc handles POST /docs.
func (s *Server) CreateDoc(w http.ResponseWriter, r *http.Request) {
	var body struct {
		ID    string
		Title string
		Body  string
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.svc.Create(r.Context(), body.ID, body.Title, body.Body); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusCreated)
}

// UpdateDoc handles PUT /docs/{id}.
func (s *Server) UpdateDoc(w http.ResponseWriter, r *http.Request) {
	var body struct{ Title string }
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.svc.Rename(r.Context(), r.PathValue("id"), body.Title); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func writeJSON(_ context.Context, w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
