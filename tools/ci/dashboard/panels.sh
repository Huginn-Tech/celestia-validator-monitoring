#!/usr/bin/env bash
# The "Panels match the switches" check of the dashboard job in .github/workflows/ci.yml, run from the
# repo root after the job generated the dashboards.
set -e
count() { jq '[.panels[] | ., (.panels // [])[] | select(.type != "row")] | length' "build/$1/dashboard.json"; }
tiles() { jq -c '[.panels[] | select(.gridPos.y == 3 and .type == "stat") | .id]' "build/$1/dashboard.json"; }
columns() { jq '[.panels[] | select(.id >= 30 and .id < 34)] | length' "build/$1/dashboard.json"; }
test "$(count single)" = 32
test "$(count split)" = 70
test "$(count mainnet)" = 73
test "$(count five)" = 67
test "$(tiles single)" = '[11,12]'
test "$(tiles split)" = '[11,12,14]'
test "$(tiles mainnet)" = '[11,12,17,14,15,16]'
test "$(tiles five)" = '[11,12,17,14,15,18]'
# The upgrade tile is Governance with the vote check on: Vote missing amber (6) and, where
# GovVoteMissing pages, red (16); Network upgrade, without a vote word, with gov: false.
for v in nogov nopage; do
  test "$(count "$v")" = 73
  test "$(tiles "$v")" = '[11,12,17,14,15,16]'
done
tile16() {
  jq -c '.panels[] | select(.id == 16) | [.fieldConfig.defaults.displayName,
         ([.fieldConfig.defaults.mappings[].options | to_entries[] | select(.value.text == "Vote missing") | .key] | sort)]' \
    "build/$1/dashboard.json"
}
test "$(tile16 mainnet)" = '["Governance",["16","6"]]'
test "$(tile16 nopage)" = '["Governance",["6"]]'
test "$(tile16 nogov)" = '["Network upgrade",[]]'
charts() { jq -c '[.panels[] | select(.id > 100 and .id < 200 and .type == "timeseries") | .id]' "build/$1/dashboard.json"; }
test "$(charts single)" = '[111,116,115]'
test "$(charts split)" = '[111,112,116,115]'
test "$(charts mainnet)" = '[111,112,116,115]'
test "$(columns single)" = 1
test "$(columns split)" = 4
test "$(columns mainnet)" = 4
test "$(columns five)" = 4
for s in single split mainnet five; do
  jq -e '.uid == "celestia-mon-validator" and .title == "Celestia validator" and .templating.list == []' "build/$s/dashboard.json"
  jq -e '(.panels[] | select(.id == 300) | .gridPos.y) as $y
         | ([.panels[] | select(.type != "row" and .id < 100) | .gridPos.y + .gridPos.h] | max) == $y
         and (.panels[] | select(.id == 300) | .collapsed)
         and (.panels[] | select(.id == 100) | .gridPos.y) == $y + 1' "build/$s/dashboard.json"
  jq -e '[.panels[] | select(.gridPos.y == 0) | [.gridPos.x, .gridPos.w, .gridPos.h]]
         == [[0,4,3],[4,4,3],[8,4,3],[12,4,3],[16,4,3],[20,4,3]]' "build/$s/dashboard.json"
  jq -e '[.panels[] | select(.gridPos.y == 3 and .type == "stat") | .gridPos] as $t
         | ($t | map(.w) | unique | length) == 1 and ($t | map(.h) | unique) == [3]
         and ($t | map(.w) | add) == 24' "build/$s/dashboard.json"
  jq -e '[.panels[] | ., (.panels // [])[] | select(.type == "stat" and .gridPos.h == 3 and (.id < 30 or .id > 33))]
         | all(.title == "" and .options.text.titleSize == 13)
           and (map(.options.text.valueSize) | unique | length == 1 and (.[0] | IN(22, 20, 18)))' \
    "build/$s/dashboard.json"
  jq -e --slurpfile cfg "build/$s/dashboard-config.json" '
         ($cfg[0].nodes + ([$cfg[0].links[].host] - $cfg[0].nodes)) as $servers | ($servers | length) as $n
         | [.panels[] | ., (.panels // [])[] | select(.type != "row" and .id >= 410 and .id < 500)] as $all
         | ($all | map(select(.id < 480))) as $p
         | ($p | length) == 6 * $n
         and ($all | map(select(.id >= 480) | [.id, .gridPos.x, .gridPos.w]) | sort) == [[480, 0, 24], [490, 0, 24]]
         and ([.panels[] | select(.id == 400 or .id == 450) | .collapsed] == [false, true])
         and ([.panels[] | select(.id == 450) | .panels[] | .id | select(. >= 410 and . < 440)] | length) == 0
         and ([.panels[] | select(.id == 450) | .panels[] | .id | select(. >= 440)] | length) == 3 * $n + 2
         and ($p | group_by(.gridPos.y) | all((map(.gridPos.h) | unique | length) == 1
                                              and (map(.gridPos.w) | unique | length) == 1
                                              and (map(.gridPos.w) | add) == 24))
         and ($n == 1 or ($p | group_by(.id % 10) | all(map(.gridPos.x) | unique | length == 1)))
         and ($p | all(. as $c | $c.title | startswith($servers[$c.id % 10] + " · ")))
         and ([.annotations.list[] | select(.name | startswith("Out of memory on ")) | .filter.ids]
              == [range(0; $n) | [420 + .]])' "build/$s/dashboard.json"
  # The Validator row: six cells on the top strip's column edges and of its height (3 rows),
  # then charts of one height, of one width on each line and filling it.
  jq -e '(.panels[] | select(.id == 100) | .gridPos.y) as $y
         | [.panels[] | select(.id > 100 and .id < 200)] as $v
         | ($v | map(select(.type == "stat"))) as $f | ($v | map(select(.type == "timeseries"))) as $c
         | ($f | map(.id)) == [109,117,118,101,102,103]
         and ($f | map([.gridPos.x, .gridPos.y, .gridPos.w, .gridPos.h]))
             == [[0,$y+1,4,3],[4,$y+1,4,3],[8,$y+1,4,3],[12,$y+1,4,3],[16,$y+1,4,3],[20,$y+1,4,3]]
         and ($c | map(.gridPos.h) | unique) == [7]
         and ($c | group_by(.gridPos.y) | all(map(.gridPos.w) | (unique | length) == 1 and add == 24))
         and ($v | length) == ($f | length) + ($c | length)' "build/$s/dashboard.json"
  jq -e '[.panels[] | select(.id >= 30 and .id < 34)] as $c | [.panels[] | select(.id >= 34 and .id < 38)] as $l
         | ($c | length) as $n | ($c | map(.gridPos)) as $cg | ($l | map(.gridPos)) as $lg
         | $n >= 1 and ($l | length) == $n and ([.panels[] | select(.id == 26)] | length) == 0
         and ($cg | map(.y) | unique) == [6] and ($cg | map(.h) | unique | length) == 1
         and ($lg | map(.h) | unique | length) == 1
         and ($c | map(.targets | length) | unique | length) == 1 and ($l | map(.targets | length) | unique) == [4]
         and (if $n == 1 then
                [$cg[0].x, $cg[0].w, $lg[0].x, $lg[0].w, $lg[0].y, $lg[0].h] == [0, 12, 12, 12, 6, $cg[0].h]
              else
                ($cg | map(.w) | unique | length) == 1 and ($cg | map(.w) | add) == 24
                and ($cg | map(.x)) == [range(0; $n) * $cg[0].w] and ($lg | map(.x)) == ($cg | map(.x))
                and ($lg | map(.w) | unique) == [$cg[0].w] and ($lg | map(.y) | unique) == [6 + $cg[0].h]
                and ([$l[] | [.gridPos.h, .options.namePlacement]] | unique | . == [[4, "left"]] or . == [[5, "top"]])
              end)' "build/$s/dashboard.json"
  jq -e '[.panels[] | select(.id >= 20 and .id < 30)] as $b | ($b | map(.id) | sort) == [20, 21, 24, 25]
         and (INDEX($b[]; .id | tostring) | map_values(.gridPos)) as $g
         | ([$g["20"].y, $g["20"].h] == [$g["21"].y, $g["21"].h]) and $g["21"].x == $g["20"].x + $g["20"].w
         and $g["21"].x + $g["21"].w == 24
         and ([$g["20"], $g["25"], $g["24"]] | map([.x, .w]) | unique | length) == 1
         and $g["25"].y == $g["20"].y + $g["20"].h and $g["24"].y == $g["25"].y + $g["25"].h
         and $g["24"].h == 1' "build/$s/dashboard.json"
done
jq -e '.tensile_enabled and .signal_enabled and .announcements_enabled and .kms_hosts == []
       and .kms_names == ["kms-1"] and .monitor_host == "mon-1" and .monitor_only and .name == "CI validator"' \
  build/mainnet/dashboard-config.json
# The vote check is on with upgrade signalling, and pages with chain_id celestia.
jq -e '.gov_enabled == true and .gov_page == true' build/mainnet/dashboard-config.json
jq -e '.kms_hosts == ["kms-1"] and .kms_names == ["kms-1"] and (.tensile_enabled | not) and (.signal_enabled | not)
       and .gov_enabled == false and .gov_page == false
       and .monitor_host == "mon-1" and .monitor_only and .name == ""' build/split/dashboard-config.json
jq -e '.kms_names == [] and .monitor_host == "node-1" and (.monitor_only | not)' build/single/dashboard-config.json
# The monitor is a server of its own: its node_exporter is a link, and its Prometheus disk a data mount.
jq -e '([.links[].host] | index("mon-1")) != null and ([.data_mounts[] | select(.host == "mon-1") | .mount] == ["/"])' \
  build/mainnet/dashboard-config.json
jq -e '.nodes == ["val-1", "fibre-1"]' build/mainnet/dashboard-config.json
jq -e '[.fibre_mounts[].host] | sort == ["fibre-1", "val-1"]' build/mainnet/dashboard-config.json
want='[["fibre-1", "fibre.example.org:7980"], ["val-1", "val.example.org:7980"]]'
jq -e --argjson want "$want" '[.fibre_endpoints[] | [.host, .endpoint]] | sort == $want' build/mainnet/dashboard-config.json
