// Package api holds deskflow's HTTP entry points.
package api

import (
	"context"
	"encoding/json"
	"net/http"

	"example.com/deskflow/internal/core"
	"example.com/deskflow/internal/repo"
)

// Server serves the ticket endpoints.
type Server struct {
	svc   *core.Service
	store repo.Store
}

// New returns a Server over svc and store.
func New(svc *core.Service, store repo.Store) *Server { return &Server{svc: svc, store: store} }

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

// historyResponse is the GET /tickets/{id}/history body.
type historyResponse struct {
	Ticket     any `json:"ticket"`
	AuditCount int `json:"audit_count"`
}

// History handles GET /tickets/{id}/history.
func (s *Server) History(w http.ResponseWriter, r *http.Request) {
	var t repo.Ticket
	if err := s.store.SelectTicket(r.Context(), r.PathValue("id"), &t); err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	n, err := s.store.CountAudit(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	writeJSON(r.Context(), w, historyResponse{Ticket: t, AuditCount: n})
}

func writeJSON(_ context.Context, w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
