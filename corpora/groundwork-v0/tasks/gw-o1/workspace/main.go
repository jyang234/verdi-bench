// Command walletsvc is a minimal, stdlib-only wallet service whose transactions
// must be committed or rolled back on every path.
package main

import (
	"log"
	"net/http"

	"example.com/walletsvc/internal/store"
	"example.com/walletsvc/internal/wire"
)

func main() {
	log.Fatal(run())
}

func run() error {
	handler := wire.Handler(store.New())
	httpSrv := &http.Server{Addr: ":8080", Handler: handler}
	return httpSrv.ListenAndServe()
}
