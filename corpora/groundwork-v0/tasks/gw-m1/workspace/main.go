// Command accountsvc is a minimal, stdlib-only account service. Its counters
// dispatch through views.Counter; main wires the live implementation.
package main

import (
	"database/sql"
	"log"
	"net/http"

	"example.com/accountsvc/internal/repo"
	"example.com/accountsvc/internal/views"
	"example.com/accountsvc/internal/wire"
)

func main() {
	log.Fatal(run())
}

func run() error {
	var db *sql.DB
	store := repo.New(db)
	// Signups are persisted through the ledger counter.
	handler := wire.Handler(store, views.NewLedger(store))
	httpSrv := &http.Server{Addr: ":8080", Handler: handler}
	return httpSrv.ListenAndServe()
}
