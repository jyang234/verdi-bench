// Package api holds limitsvc's HTTP entry points.
package api

import (
	"encoding/json"
	"net/http"

	"example.com/limitsvc/internal/core"
)

// Server serves the limiter endpoints.
type Server struct {
	svc *core.Service
}

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// Run handles POST /run.
func (s *Server) Run(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Job string `json:"job"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.svc.Run(body.Job); err != nil {
		http.Error(w, err.Error(), http.StatusServiceUnavailable)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// Process handles POST /process.
func (s *Server) Process(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Payload string `json:"payload"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.svc.Process(body.Payload); err != nil {
		http.Error(w, err.Error(), http.StatusUnprocessableEntity)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
