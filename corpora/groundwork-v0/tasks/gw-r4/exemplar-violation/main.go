// Command docsvc is a minimal, stdlib-only document service. It is strictly
// layered api -> core -> repo; the wire package composes it and main only binds
// it to a listener.
package main

import (
	"database/sql"
	"log"
	"net/http"

	"example.com/docsvc/internal/repo"
	"example.com/docsvc/internal/wire"
)

func main() {
	log.Fatal(run())
}

// run builds and serves the service. A nil *sql.DB stands in for the real
// driver; the static pipeline never executes this code.
func run() error {
	var db *sql.DB
	handler := wire.Handler(repo.New(db))
	httpSrv := &http.Server{Addr: ":8080", Handler: handler}
	return httpSrv.ListenAndServe()
}
