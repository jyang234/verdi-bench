// Package api holds userdirsvc's HTTP entry points; every method goes through core.
package api

import (
	"context"
	"encoding/json"
	"net/http"

	"example.com/userdirsvc/internal/core"
)

// Server serves the user endpoints.
type Server struct{ svc *core.Service }

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// GetUser handles GET /users/{id}.
func (s *Server) GetUser(w http.ResponseWriter, r *http.Request) {
	u, err := s.svc.GetUser(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	writeJSON(r.Context(), w, u)
}

// UpdateUser handles PUT /users/{id}.
func (s *Server) UpdateUser(w http.ResponseWriter, r *http.Request) {
	var body struct{ Name string }
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.svc.Rename(r.Context(), r.PathValue("id"), body.Name); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func writeJSON(_ context.Context, w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
