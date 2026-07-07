// Package wire is deskflow's composition root: it assembles the layers and
// registers the HTTP routes. It is exempt from the layering invariant (listed
// in the policy's layering roots) because wiring dependencies together is the
// one place that legitimately references every layer. Extracting it from main
// lets tests drive the fully-wired handler with an in-memory repo.
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
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /tickets/{id}", srv.GetTicket)
	mux.HandleFunc("PUT /tickets/{id}", srv.UpdateTicket)
	return mux
}
