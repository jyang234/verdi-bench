// Package wire is inboxsvc's composition root.
package wire

import (
	"net/http"

	"example.com/inboxsvc/internal/api"
	"example.com/inboxsvc/internal/core"
	"example.com/inboxsvc/internal/delivery"
	"example.com/inboxsvc/internal/repo"
)

// Handler builds the service and returns the routed HTTP handler. deliverer is
// the recorder behind the delivery-receipt route.
func Handler(store repo.Store, deliverer delivery.Deliverer) http.Handler {
	svc := core.New(store, deliverer)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /messages/{id}", srv.GetMessage)
	mux.HandleFunc("POST /messages/{id}/receipt", srv.Deliver)
	return mux
}
