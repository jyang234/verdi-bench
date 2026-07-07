// Package wire is ordersvc's composition root: it assembles the layers and
// registers the HTTP routes. Extracting it from main lets tests drive the
// fully-wired handler with an in-memory repo.
package wire

import (
	"net/http"

	"example.com/ordersvc/internal/api"
	"example.com/ordersvc/internal/core"
	"example.com/ordersvc/internal/repo"
)

// Handler builds the service over store and returns the routed HTTP handler.
func Handler(store repo.Store) http.Handler {
	svc := core.New(store)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /orders/{id}", srv.GetOrder)
	mux.HandleFunc("PUT /orders/{id}", srv.RenameOrder)
	return mux
}
