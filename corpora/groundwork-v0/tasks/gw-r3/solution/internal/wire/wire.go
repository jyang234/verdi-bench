// Package wire is billingsvc's composition root: it assembles the layers and
// registers the HTTP routes. Extracting it from main lets tests drive the
// fully-wired handler with an in-memory repo.
package wire

import (
	"net/http"

	"example.com/billingsvc/internal/api"
	"example.com/billingsvc/internal/core"
	"example.com/billingsvc/internal/repo"
)

// Handler builds the service over store and returns the routed HTTP handler.
func Handler(store repo.Store) http.Handler {
	svc := core.New(store)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /invoices/{id}", srv.GetInvoice)
	mux.HandleFunc("POST /invoices", srv.CreateInvoice)
	mux.HandleFunc("POST /invoices/{id}/finalize", srv.FinalizeInvoice)
	return mux
}
