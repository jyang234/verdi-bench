// Package api holds walletsvc's HTTP entry points.
package api

import (
	"encoding/json"
	"net/http"

	"example.com/walletsvc/internal/core"
)

// Server serves the wallet endpoints.
type Server struct {
	svc *core.Service
}

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// Deposit handles POST /deposit.
func (s *Server) Deposit(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Account string `json:"account"`
		Amount  int64  `json:"amount"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.svc.Deposit(body.Account, body.Amount); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
