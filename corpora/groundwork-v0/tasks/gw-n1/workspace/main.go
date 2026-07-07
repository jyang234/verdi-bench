// Command catalogsvc is a minimal, stdlib-only product-catalog service, strictly
// layered api -> core -> repo.
package main

import (
	"database/sql"
	"log"
	"net/http"

	"example.com/catalogsvc/internal/repo"
	"example.com/catalogsvc/internal/wire"
)

func main() {
	log.Fatal(run())
}

func run() error {
	var db *sql.DB
	httpSrv := &http.Server{Addr: ":8080", Handler: wire.Handler(repo.New(db))}
	return httpSrv.ListenAndServe()
}
