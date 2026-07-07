// Command userdirsvc is a minimal, stdlib-only user-directory service, strictly
// layered api -> core -> repo.
package main

import (
	"database/sql"
	"log"
	"net/http"

	"example.com/userdirsvc/internal/repo"
	"example.com/userdirsvc/internal/wire"
)

func main() {
	log.Fatal(run())
}

func run() error {
	var db *sql.DB
	httpSrv := &http.Server{Addr: ":8080", Handler: wire.Handler(repo.New(db))}
	return httpSrv.ListenAndServe()
}
