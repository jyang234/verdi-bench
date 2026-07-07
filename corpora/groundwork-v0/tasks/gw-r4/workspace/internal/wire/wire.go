// Package wire is docsvc's composition root: it assembles the layers and
// registers the HTTP routes. Extracting it from main lets tests drive the
// fully-wired handler with an in-memory repo.
package wire

import (
	"net/http"

	"example.com/docsvc/internal/api"
	"example.com/docsvc/internal/core"
	"example.com/docsvc/internal/repo"
)

// Handler builds the service over store and returns the routed HTTP handler.
func Handler(store repo.Store) http.Handler {
	svc := core.New(store)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /docs/{id}", srv.GetDoc)
	mux.HandleFunc("POST /docs", srv.CreateDoc)
	mux.HandleFunc("PUT /docs/{id}", srv.UpdateDoc)
	return mux
}
