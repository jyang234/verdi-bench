// Package api holds ordersvc's HTTP entry points. Every method calls into the
// core layer and never touches repo directly.
package api

import (
	"context"
	"encoding/json"
	"net/http"

	"example.com/ordersvc/internal/core"
)

// Server serves the order endpoints.
type Server struct {
	svc *core.Service
}

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// GetOrder handles GET /orders/{id}.
func (s *Server) GetOrder(w http.ResponseWriter, r *http.Request) {
	o, err := s.svc.GetOrder(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	writeJSON(r.Context(), w, o)
}

// RenameOrder handles PUT /orders/{id}.
func (s *Server) RenameOrder(w http.ResponseWriter, r *http.Request) {
	var body struct{ Label string }
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.svc.RenameOrder(r.Context(), r.PathValue("id"), body.Label); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func writeJSON(_ context.Context, w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
