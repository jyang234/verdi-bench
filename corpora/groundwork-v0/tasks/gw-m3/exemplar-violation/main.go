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
	// Reactions and per-read activity are both published to the event bus.
	handler := wire.Handler(store, emit.NewBus(b), emit.NewBus(b))
	httpSrv := &http.Server{Addr: ":8080", Handler: handler}
	return httpSrv.ListenAndServe()
}
