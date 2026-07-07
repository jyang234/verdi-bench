// Command alertsvc is a minimal, dependency-free blind-spot fixture: it makes the
// static graph's frontier concrete with a dynamic publish and a reflect call. It
// registers two HTTP routes through dynamic dispatch so root discovery has
// entrypoints to anchor reachability on.
package main

import (
	"log"
	"net/http"

	"example.com/alertsvc/internal/channel"
	"example.com/alertsvc/internal/handler"
	"example.com/alertsvc/internal/relay"
)

func main() {
	log.Fatal(run())
}

func run() error {
	ch := channel.New()
	rl := relay.New(ch)
	srv := handler.New(rl)

	mux := http.NewServeMux()
	mux.HandleFunc("POST /emit/{id}", srv.Emit)
	mux.HandleFunc("POST /notify/{id}", srv.Notify)

	httpSrv := &http.Server{Addr: ":8080", Handler: mux}
	return httpSrv.ListenAndServe()
}
