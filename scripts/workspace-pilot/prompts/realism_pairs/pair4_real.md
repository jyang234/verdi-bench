# due dates from the London office are breaking

Filing a bug: our Ops team in London entered `13/02/2026` for a task deadline and
`scheduler` threw "invalid date" and dropped the job. Looks like `ParseDueDate`
assumes US month-first ordering.

Can you make it accept day-first `DD/MM/YYYY` too? The ISO `YYYY-MM-DD` inputs the
API sends must keep working exactly as they do now. This has bitten us twice this
month.
