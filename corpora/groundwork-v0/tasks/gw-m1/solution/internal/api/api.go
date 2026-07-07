// Package api holds accountsvc's HTTP entry points.
package api

import (
	"context"
	"encoding/json"
	"net/http"

	"example.com/accountsvc/internal/core"
)

// Server serves the account endpoints.
type Server struct{ svc *core.Service }

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// accountView is the GET /accounts/{id} body.
type accountView struct {
	Account any `json:"account"`
	Views   int `json:"views"`
}

// GetAccount handles GET /accounts/{id}.
func (s *Server) GetAccount(w http.ResponseWriter, r *http.Request) {
	a, n, err := s.svc.GetAccount(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	writeJSON(r.Context(), w, accountView{Account: a, Views: n})
}

// Signup handles POST /accounts/{id}/signup.
func (s *Server) Signup(w http.ResponseWriter, r *http.Request) {
	if err := s.svc.Signup(r.Context(), r.PathValue("id")); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func writeJSON(_ context.Context, w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
