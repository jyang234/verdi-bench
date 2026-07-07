// Package api holds feedsvc's HTTP entry points.
package api

import (
	"context"
	"encoding/json"
	"net/http"

	"example.com/feedsvc/internal/core"
)

// Server serves the feed endpoints.
type Server struct{ svc *core.Service }

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// GetItem handles GET /feed/{id}.
func (s *Server) GetItem(w http.ResponseWriter, r *http.Request) {
	it, err := s.svc.GetItem(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	writeJSON(r.Context(), w, it)
}

// React handles POST /feed/{id}/react.
func (s *Server) React(w http.ResponseWriter, r *http.Request) {
	if err := s.svc.React(r.Context(), r.PathValue("id")); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func writeJSON(_ context.Context, w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
