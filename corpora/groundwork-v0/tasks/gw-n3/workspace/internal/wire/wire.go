// Package wire is banksvc's composition root.
package wire

import (
	"net/http"

	"example.com/banksvc/internal/api"
	"example.com/banksvc/internal/core"
	"example.com/banksvc/internal/store"
)

// Handler builds the service over st and returns the routed HTTP handler.
func Handler(st *store.Store) http.Handler {
	svc := core.New(st)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("POST /deposit", srv.Deposit)
	return mux
}
