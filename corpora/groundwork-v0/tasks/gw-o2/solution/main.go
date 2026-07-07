// Command limitsvc is a minimal, stdlib-only rate-limited work service whose
// limiter slots must be released on every path.
package main

import (
	"log"
	"net/http"

	"example.com/limitsvc/internal/store"
	"example.com/limitsvc/internal/wire"
)

func main() {
	log.Fatal(run())
}

func run() error {
	handler := wire.Handler(store.New())
	httpSrv := &http.Server{Addr: ":8080", Handler: handler}
	return httpSrv.ListenAndServe()
}
