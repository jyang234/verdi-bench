# make the log level configurable

Ops wants to be able to turn on debug logging in `gateway` without a redeploy.
Right now the level is hard-coded to info in `main.go`. Can you read it from a
`LOG_LEVEL` env var (defaulting to info, accepting debug/info/warn/error) and
fall back to info with a warning if it's set to something we don't recognize?
