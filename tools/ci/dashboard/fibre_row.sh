#!/usr/bin/env bash
# The "The Fibre row is symmetric" check of the dashboard job in .github/workflows/ci.yml, run from the
# repo root after the job generated the dashboards.
set -e
for s in split mainnet five; do
  jq -e '[.panels[] | select(.id > 200 and .id < 250)] as $f
         | [$f[] | select(.type == "stat")] as $p | ($p | map(.gridPos)) as $s
         | [$f[] | select(.type == "timeseries") | .gridPos] as $c
         | [$f[] | select(.type == "status-history")] as $b | [$f[] | select(.type == "state-timeline")] as $u
         | ($s | map(.y) | unique | length) == 1 and ($s | map(.h) | unique) == [3]
           and ($s | map(.w) | unique) == [4] and ($s | map(.w) | add) == 24
           and ($p | all(.title == "" and .options.graphMode == "none" and .options.justifyMode == "auto"
                         and .options.textMode == "value_and_name" and .options.text.titleSize == 13))
           and ($p | map(.options.text.valueSize) | unique | length) == 1
           and ($b | map([.id, .title, (.targets | length), .gridPos.x, .gridPos.w, .gridPos.h]))
               == [[215, "", 3, 0, 21, 2]]
           and ($u | map([.id, .title, (.targets | length), .gridPos.x, .gridPos.w, .gridPos.h, .gridPos.y, .options.rowHeight]))
               == [[216, "", 3, 21, 3, 2, $b[0].gridPos.y, $b[0].options.rowHeight]]
           and $b[0].gridPos.y == ($s | map(.y + .h) | max)
           and ($c | map(.h) | unique | length) == 1 and ($c | map(.y) | min) == $b[0].gridPos.y + 2
           and ($c | sort_by(.y, .x) | map([.x, .w])) == [[0, 12], [12, 12], [0, 12], [12, 12]]' \
    "build/$s/dashboard.json"
done
