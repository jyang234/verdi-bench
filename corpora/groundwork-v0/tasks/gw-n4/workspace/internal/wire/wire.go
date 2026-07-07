// Package wire is eventsvc's composition root.
package wire

import (
	"net/http"

	"example.com/eventsvc/internal/bus"
	"example.com/eventsvc/internal/handler"
	"example.com/eventsvc/internal/notify"
	"example.com/eventsvc/internal/store"
)

// Handler builds the service over b and st and returns the routed HTTP handler.
func Handler(b *bus.Bus, st *store.Store) http.Handler {
	n := notify.New(b)
	srv := handler.New(n, st)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /subscribers/{id}", srv.Get)
	mux.HandleFunc("POST /subscribers/{id}", srv.Create)
	return mux
}
