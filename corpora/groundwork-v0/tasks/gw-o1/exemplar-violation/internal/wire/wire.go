// Package wire is walletsvc's composition root.
package wire

import (
	"net/http"

	"example.com/walletsvc/internal/api"
	"example.com/walletsvc/internal/core"
	"example.com/walletsvc/internal/store"
)

// Handler builds the service over st and returns the routed HTTP handler.
func Handler(st *store.Store) http.Handler {
	svc := core.New(st)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("POST /deposit", srv.Deposit)
	mux.HandleFunc("POST /transfer", srv.Transfer)
	return mux
}
