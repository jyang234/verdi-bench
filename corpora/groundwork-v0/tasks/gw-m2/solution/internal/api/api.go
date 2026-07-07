// Package api holds inboxsvc's HTTP entry points.
package api

import (
	"context"
	"encoding/json"
	"net/http"

	"example.com/inboxsvc/internal/core"
)

// Server serves the message endpoints.
type Server struct{ svc *core.Service }

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// GetMessage handles GET /messages/{id}.
func (s *Server) GetMessage(w http.ResponseWriter, r *http.Request) {
	m, err := s.svc.GetMessage(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	writeJSON(r.Context(), w, m)
}

// Deliver handles POST /messages/{id}/receipt.
func (s *Server) Deliver(w http.ResponseWriter, r *http.Request) {
	if err := s.svc.Deliver(r.Context(), r.PathValue("id")); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func writeJSON(_ context.Context, w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
