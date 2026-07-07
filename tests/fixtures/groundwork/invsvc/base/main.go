// Command invsvc is a minimal, dependency-free (stdlib-only) fixture service for
// the verdi-bench groundwork grader plugin. It is strictly layered
// handler -> catalog -> store and registers its HTTP handlers through dynamic
// dispatch (the registration root discovery must see through). main never calls
// a handler directly.
package main

import (
	"database/sql"
	"log"
	"net/http"

	"example.com/invsvc/internal/catalog"
	"example.com/invsvc/internal/handler"
	"example.com/invsvc/internal/store"
)

func main() {
	log.Fatal(run())
}

// run builds and serves the service. A nil *sql.DB stands in for the real
// driver; the static pipeline never executes this code.
func run() error {
	var db *sql.DB
	st := store.New(db)
	cat := catalog.New(st)
	srv := handler.New(cat)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /items/{id}", srv.ShowItem)
	mux.HandleFunc("PUT /items/{id}", srv.RenameItem)

	httpSrv := &http.Server{Addr: ":8080", Handler: mux}
	return httpSrv.ListenAndServe()
}
