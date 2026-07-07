// Package wire is userdirsvc's composition root.
package wire

import (
	"net/http"

	"example.com/userdirsvc/internal/api"
	"example.com/userdirsvc/internal/core"
	"example.com/userdirsvc/internal/repo"
)

// Handler builds the service over store and returns the routed HTTP handler.
func Handler(store repo.Store) http.Handler {
	svc := core.New(store)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /users/{id}", srv.GetUser)
	mux.HandleFunc("PUT /users/{id}", srv.UpdateUser)
	return mux
}
