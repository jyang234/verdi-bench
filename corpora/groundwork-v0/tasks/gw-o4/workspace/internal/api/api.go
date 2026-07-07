// Package api holds ingestsvc's HTTP entry points.
package api

import (
	"encoding/json"
	"net/http"

	"example.com/ingestsvc/internal/core"
)

// Server serves the ingest endpoints.
type Server struct {
	svc *core.Service
}

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// Seed handles POST /seed.
func (s *Server) Seed(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Row string `json:"row"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.svc.Seed(body.Row); err != nil {
		http.Error(w, err.Error(), http.StatusUnprocessableEntity)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
