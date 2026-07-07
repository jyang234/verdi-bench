# Bug-fix evaluation (task DATE-07)

This is a simulated bug report generated for evaluation purposes. Fix the defect
so the hidden regression tests pass. Do not refactor unrelated code.

In `scheduler`, `ParseDueDate` mishandles dates written `DD/MM/YYYY`: it always
assumes `MM/DD/YYYY`, so `13/02/2026` is rejected as invalid instead of parsed as
13 February. Make it accept the day-first format used by our (fictional) European
locale while leaving ISO `YYYY-MM-DD` inputs working.
