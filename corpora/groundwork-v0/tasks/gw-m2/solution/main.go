// Command inboxsvc is a minimal, stdlib-only message service. Its receipt
// recorders dispatch through delivery.Deliverer; main wires the live one.
package main

import (
	"database/sql"
	"log"
	"net/http"

	"example.com/inboxsvc/internal/delivery"
	"example.com/inboxsvc/internal/repo"
	"example.com/inboxsvc/internal/wire"
)

func main() {
	log.Fatal(run())
}

func run() error {
	var db *sql.DB
	store := repo.New(db)
	// Delivery receipts are persisted through the DB recorder; per-request read
	// receipts stay in process memory.
	handler := wire.Handler(store, delivery.NewDb(store), delivery.NewMem())
	httpSrv := &http.Server{Addr: ":8080", Handler: handler}
	return httpSrv.ListenAndServe()
}
