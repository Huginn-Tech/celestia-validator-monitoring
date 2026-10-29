#!/usr/bin/env bash
# The dashboard settings of every CI scenario, rendered by tools/ci/render.yml from the example
# inventories into BUILD/<scenario>/dashboard-config.json (each render's rules under
# BUILD/<scenario>/rules and BUILD/<scenario>/tests):
#   single, split, mainnet  the example inventories; mainnet with tools/ci/mainnet-extras.yml
#   mocha                   the mainnet example on Mocha (chain_id mocha-5), as the rules job renders it
#   five, nogov, nopage     mainnet's settings with one switch changed (below)
#   table                   split's settings with one more node (below)
# Usage, from the repo root (the dashboard and dashboard-report jobs): bash tools/ci/dashboard_configs.sh BUILD
set -e
mkdir -p "$1"
build=$(cd "$1" && pwd)
# mainnet gets the same outside check, upgrade signalling and announcements as in the rules
# job, and a validator name, so every optional panel and query is generated and checked.
for s in single split mainnet; do
  extra=()
  if [ "$s" = mainnet ]; then
    extra=(-e @tools/ci/mainnet-extras.yml)
  fi
  ansible-playbook -i "ansible/inventory/example-$s/hosts.yml" tools/ci/render.yml -e "celestia_mon_ci_scenario=$s" \
    -e "celestia_mon_ci_build=$build/$s" -e "celestia_mon_ci_tests=$build/$s/tests" "${extra[@]}"
done
# On Mocha GovVoteMissing sends its message only, by default, so the vote check never pages.
ansible-playbook -i ansible/inventory/example-mainnet/hosts.yml tools/ci/render.yml -e celestia_mon_ci_scenario=mainnet \
  -e "celestia_mon_ci_build=$build/mocha" -e "celestia_mon_ci_tests=$build/mocha/tests" \
  -e '{"celestia_mon_signal": {"chain_id": "mocha-5", "rpc": ["http://127.0.0.1:26657"], "validator": "celestiavaloper1ci"}}'
# mainnet without upgrade signalling has four answers, which the Servers and Monitoring tiles make six.
mkdir -p "$build/five"
jq '.signal_enabled = false' "$build/mainnet/dashboard-config.json" > "$build/five/dashboard-config.json"
# mainnet with gov: false (the Network upgrade tile, no vote words), and with the vote check but no
# page (as on Mocha: Vote missing never red); check_surfaces and check_words run in each.
for v in nogov nopage; do
  mkdir -p "$build/$v"
  jq "if \"$v\" == \"nogov\" then .gov_enabled = false | .gov_page = false else .gov_page = false end" \
    "$build/mainnet/dashboard-config.json" > "$build/$v/dashboard-config.json"
done
# split with a backup node: five servers, so the Servers now table (26) instead of the cards, and three
# answers, so no Monitoring tile; check_surfaces must still find every alert.
mkdir -p "$build/table"
jq '.nodes += ["val-2"] | .links += [{"host": "val-2", "mbps": null}]
    | .data_mounts += [{"host": "val-2", "mount": "/var/lib/celestia"}]' \
  "$build/split/dashboard-config.json" > "$build/table/dashboard-config.json"
