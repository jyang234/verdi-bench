// Command pubsvc is a minimal, stdlib-only approvals service that must audit an
// approval before it publishes the event.
package main

import (
	"log"
	"net/http"

	"example.com/pubsvc/internal/wire"
)

func main() {
	log.Fatal(run())
}

func run() error {
	handler := wire.Handler()
	httpSrv := &http.Server{Addr: ":8080", Handler: handler}
	return httpSrv.ListenAndServe()
}
