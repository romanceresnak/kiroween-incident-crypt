# submission — Tasks

Local tasks only: no AWS, Terraform or cluster change, no push. Execution notes and stop
conditions are those of `agents-core/tasks.md`. Commit after each task.

## TASK-01 — Kiro hooks and the dispatcher script
REQ-HK-01 to HK-06.
**Test:** hook files (schema, matchers, no overlap); the script against fake tools for every
area and every path form; hostile and out-of-scope paths; failure propagation; shellcheck.

## TASK-02 — LICENSE and README
REQ-LIC-01, LIC-02, REQ-RD-01 to RD-03; the document checks for them.
**Test:** licence text and `.kiro` tracking; README sections in order; Mermaid well formed;
cost figures equal the JSON; every path and command target cited exists.

## TASK-03 — Kiro write-up
REQ-WRU-01 to WRU-05. **Test:** every cited commit exists; spec counts match the files;
every "Kiro caught it" entry has a commit; the not-used features are named.

## TASK-04 — Demo script
REQ-DEM-01 to DEM-04. **Test:** timings add up to at most 170 s; every command's script or
path exists.

## TASK-05 — Blog draft and social post
REQ-BLG-01, REQ-SOC-01. **Test:** hashtag first, at most 280 characters, Kiro named; the blog
states the hackathon.

## TASK-06 — Devpost text
REQ-DVP-01 to DVP-04. **Test:** sections, category, all five criteria, the no-public-URL
decision is stated.

## TASK-07 — Status
Record the task table, the operator's checklist and what was not verified.
