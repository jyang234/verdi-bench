// Package api holds deskflow's HTTP entry points. Every method calls into the
// core layer and never touches repo directly.
package api

import (
	"context"
	"encoding/json"
	"net/http"

	"example.com/deskflow/internal/core"
)

// Server serves the ticket endpoints.
type Server struct {
	svc *core.Service
}

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// GetTicket handles GET /tickets/{id}.
func (s *Server) GetTicket(w http.ResponseWriter, r *http.Request) {
	t, err := s.svc.GetTicket(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	writeJSON(r.Context(), w, t)
}

// UpdateTicket handles PUT /tickets/{id}.
func (s *Server) UpdateTicket(w http.ResponseWriter, r *http.Request) {
	var body struct{ Subject string }
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.svc.RenameTicket(r.Context(), r.PathValue("id"), body.Subject); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func writeJSON(_ context.Context, w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

// historyResponse is the GET /tickets/{id}/history body.
type historyResponse struct {
	Ticket     any `json:"ticket"`
	AuditCount int `json:"audit_count"`
}

// History handles GET /tickets/{id}/history.
func (s *Server) History(w http.ResponseWriter, r *http.Request) {
	t, n, err := s.svc.GetHistory(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	writeJSON(r.Context(), w, historyResponse{Ticket: t, AuditCount: n})
}
