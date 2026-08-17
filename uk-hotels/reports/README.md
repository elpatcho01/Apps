# Monthly digests

`YYYY-MM.md` files here are written and committed by the
`hotels-monthly-digest` workflow on the 2nd of each month. Read the most recent
one and you have caught up: what was collected, how healthy it looked, what
churned out of the property sample, what was reconstructed, and what needs
attention.

`data/analytics.json` is the analytics export, written by the same workflow.
It exists because BigQuery is unreachable from outside a workflow run — see the
export module for why — and it carries aggregates only, never raw observation
rows.

These commits are also what keeps the pipeline alive. GitHub disables scheduled
workflows after 60 days of repository inactivity and workflow *runs* do not
count as activity, only commits do. The report that tells you collection is
healthy is the same thing keeping it running.
