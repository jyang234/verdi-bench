// Package api holds pubsvc's HTTP entry points.
package api

import (
	"encoding/json"
	"net/http"

	"example.com/pubsvc/internal/core"
)

// Server serves the approvals endpoints.
type Server struct {
	svc *core.Service
}

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// Notify handles POST /notify.
func (s *Server) Notify(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Event string `json:"event"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	s.svc.Notify(body.Event)
	w.WriteHeader(http.StatusNoContent)
}

// Approve handles POST /approve.
func (s *Server) Approve(w http.ResponseWriter, r *http.Request) {
	var body struct {
		ID string `json:"id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	s.svc.Approve(body.ID)
	w.WriteHeader(http.StatusNoContent)
}
