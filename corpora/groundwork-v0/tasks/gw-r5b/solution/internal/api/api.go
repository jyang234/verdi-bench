// Package api holds mailsvc's HTTP entry points. Every method calls into the
// core layer and never touches repo directly.
package api

import (
	"encoding/json"
	"net/http"

	"example.com/mailsvc/internal/core"
	"example.com/mailsvc/internal/repo"
)

// Server serves the message endpoints.
type Server struct {
	svc *core.Service
}

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// Send handles POST /send.
func (s *Server) Send(w http.ResponseWriter, r *http.Request) {
	var msg repo.Message
	if err := json.NewDecoder(r.Body).Decode(&msg); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.svc.Send(r.Context(), msg); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusAccepted)
}
