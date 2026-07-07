// Package wire is catalogsvc's composition root.
package wire

import (
	"net/http"

	"example.com/catalogsvc/internal/api"
	"example.com/catalogsvc/internal/core"
	"example.com/catalogsvc/internal/repo"
)

// Handler builds the service over store and returns the routed HTTP handler.
func Handler(store repo.Store) http.Handler {
	svc := core.New(store)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /products/{id}", srv.GetProduct)
	mux.HandleFunc("PUT /products/{id}", srv.UpdateProduct)
	return mux
}
