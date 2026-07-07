// Command ingestsvc is a minimal, stdlib-only bulk-import service whose batches
// must be flushed or discarded on every path.
package main

import (
	"log"
	"net/http"

	"example.com/ingestsvc/internal/store"
	"example.com/ingestsvc/internal/wire"
)

func main() {
	log.Fatal(run())
}

func run() error {
	handler := wire.Handler(store.New())
	httpSrv := &http.Server{Addr: ":8080", Handler: handler}
	return httpSrv.ListenAndServe()
}
