// Package wire is deskflow's composition root.
package wire

import (
	"net/http"

	"example.com/deskflow/internal/api"
	"example.com/deskflow/internal/core"
	"example.com/deskflow/internal/repo"
)

// Handler builds the service over store and returns the routed HTTP handler.
func Handler(store repo.Store) http.Handler {
	svc := core.New(store)
	srv := api.New(svc, store)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /tickets/{id}", srv.GetTicket)
	mux.HandleFunc("PUT /tickets/{id}", srv.UpdateTicket)
	mux.HandleFunc("GET /tickets/{id}/history", srv.History)
	return mux
}
