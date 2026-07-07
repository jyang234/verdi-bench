// Command feedsvc is a minimal, stdlib-only activity-feed service. Its activity
// emitters dispatch through emit.Emitter; main wires the live one.
package main

import (
	"database/sql"
	"log"
	"net/http"

	"example.com/feedsvc/internal/bus"
	"example.com/feedsvc/internal/emit"
	"example.com/feedsvc/internal/repo"
	"example.com/feedsvc/internal/wire"
)

func main() {
	log.Fatal(run())
}

func run() error {
	var db *sql.DB
	store := repo.New(db)
	b := bus.New()
	// Reactions are published to the event bus; per-read activity is written to
	// the process log only.
	handler := wire.Handler(store, emit.NewBus(b), emit.NewLog())
	httpSrv := &http.Server{Addr: ":8080", Handler: handler}
	return httpSrv.ListenAndServe()
}
