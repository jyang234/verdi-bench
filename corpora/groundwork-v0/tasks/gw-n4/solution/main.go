// Command eventsvc is a minimal, stdlib-only event-notification service. Its
// Create route publishes a statically-named event; the Publish route (added by
// the feature) emits an event whose name comes from the request — a dynamic
// boundary the static graph cannot name.
package main

import (
	"log"
	"net/http"

	"example.com/eventsvc/internal/bus"
	"example.com/eventsvc/internal/store"
	"example.com/eventsvc/internal/wire"
)

func main() {
	log.Fatal(run())
}

func run() error {
	b := bus.New()
	st := store.New()
	httpSrv := &http.Server{Addr: ":8080", Handler: wire.Handler(b, st)}
	return httpSrv.ListenAndServe()
}
