**The project budget's period start is a calendar date** —
`get_project_budget()` / `getProjectBudget()` refuse a `period_start_date`
that is not a real calendar date written `YYYY-MM-DD` with `BadRequestError`
naming the field, in both SDKs (TypeScript accepted any string; Python also
read a date-time as its date).
