// Package api holds catalogsvc's HTTP entry points; every method goes through core.
package api

import (
	"context"
	"encoding/json"
	"net/http"

	"example.com/catalogsvc/internal/core"
	"example.com/catalogsvc/internal/repo"
)

// Server serves the product endpoints.
type Server struct{ svc *core.Service }

// New returns a Server over svc.
func New(svc *core.Service) *Server { return &Server{svc: svc} }

// GetProduct handles GET /products/{id}.
func (s *Server) GetProduct(w http.ResponseWriter, r *http.Request) {
	p, err := s.svc.GetProduct(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	writeJSON(r.Context(), w, p)
}

// UpdateProduct handles PUT /products/{id}.
func (s *Server) UpdateProduct(w http.ResponseWriter, r *http.Request) {
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

// Create handles POST /products.
func (s *Server) Create(w http.ResponseWriter, r *http.Request) {
	var body struct {
		ID    string
		Name  string
		Price int64
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.svc.Create(r.Context(), toProduct(body.ID, body.Name, body.Price)); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusCreated)
}

func toProduct(id, name string, price int64) repo.Product {
	return repo.Product{ID: id, Name: name, Price: price}
}
