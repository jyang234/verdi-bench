// Package wire is ingestsvc's composition root.
package wire

import (
	"net/http"

	"example.com/ingestsvc/internal/api"
	"example.com/ingestsvc/internal/core"
	"example.com/ingestsvc/internal/store"
)

// Handler builds the service over st and returns the routed HTTP handler.
func Handler(st *store.Store) http.Handler {
	svc := core.New(st)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("POST /seed", srv.Seed)
	mux.HandleFunc("POST /import", srv.Import)
	return mux
}
