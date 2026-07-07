// Command banksvc is a minimal, stdlib-only accounts service whose transactions
// must be committed or rolled back on every path.
package main

import (
	"log"
	"net/http"

	"example.com/banksvc/internal/store"
	"example.com/banksvc/internal/wire"
)

func main() {
	log.Fatal(run())
}

func run() error {
	handler := wire.Handler(store.New())
	httpSrv := &http.Server{Addr: ":8080", Handler: handler}
	return httpSrv.ListenAndServe()
}
