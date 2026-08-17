# Monthly digests

One file per month, `YYYY-MM.md`, written and committed by the
`airfares-monthly-digest` workflow on the 2nd. Each one summarises what the
pipeline collected the previous month, how healthy collection looked, what it
reconstructed, and anything that needs attention.

These commits are also load-bearing. GitHub disables scheduled workflows after
60 days of repository inactivity, and workflow runs do not count — only commits
do. A monthly commit here keeps the daily collection schedule from being
switched off, silently, precisely because nothing was going wrong. See the
"60-day trap" section in the top-level README.

So: don't delete this directory, and don't stop the digest workflow to reduce
noise. Its noise is the point.
