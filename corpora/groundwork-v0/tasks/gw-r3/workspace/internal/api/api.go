// Package api holds billingsvc's HTTP entry points. Every method calls into the
// core layer and never touches repo directly.
package api

import (
	"context"
	"encoding/json"
	"net/http"

	"example.com/billingsvc/internal/core"
	"example.com/billingsvc/internal/repo"
)

// Server serves the invoice endpoints.
type Server struct {
	svc *core.Service
}

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// GetInvoice handles GET /invoices/{id}.
func (s *Server) GetInvoice(w http.ResponseWriter, r *http.Request) {
	inv, err := s.svc.GetInvoice(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	writeJSON(r.Context(), w, inv)
}

// CreateInvoice handles POST /invoices.
func (s *Server) CreateInvoice(w http.ResponseWriter, r *http.Request) {
	var inv repo.Invoice
	if err := json.NewDecoder(r.Body).Decode(&inv); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.svc.CreateInvoice(r.Context(), inv); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusCreated)
}

func writeJSON(_ context.Context, w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
