// Package wire is feedsvc's composition root.
package wire

import (
	"net/http"

	"example.com/feedsvc/internal/api"
	"example.com/feedsvc/internal/core"
	"example.com/feedsvc/internal/emit"
	"example.com/feedsvc/internal/repo"
)

// Handler builds the service and returns the routed HTTP handler. emitter is
// the emitter behind the reaction route; activity emits on reads.
func Handler(store repo.Store, emitter, activity emit.Emitter) http.Handler {
	svc := core.New(store, emitter, activity)
	srv := api.New(svc)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /feed/{id}", srv.GetItem)
	mux.HandleFunc("POST /feed/{id}/react", srv.React)
	return mux
}
