// Package wire is mailsvc's composition root: it assembles the layers and
// registers the HTTP routes. Extracting it from main lets tests drive the
// fully-wired handler with an in-memory repo.
package wire

import (
	"net/http"

	"example.com/mailsvc/internal/api"
	"example.com/mailsvc/internal/core"
	"example.com/mailsvc/internal/repo"
)

// Handler builds the service over store and returns the routed HTTP handler.
func Handler(store repo.Store) http.Handler {
	svc := core.New(store)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("POST /send", srv.Send)
	return mux
}
