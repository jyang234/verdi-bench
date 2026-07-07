// Package wire is pubsvc's composition root.
package wire

import (
	"net/http"

	"example.com/pubsvc/internal/api"
	"example.com/pubsvc/internal/core"
)

// Handler builds the service and returns the routed HTTP handler.
func Handler() http.Handler {
	svc := core.New()
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("POST /notify", srv.Notify)
	mux.HandleFunc("POST /approve", srv.Approve)
	return mux
}
