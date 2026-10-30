#!/usr/bin/env bash
# The signal job in .github/workflows/ci.yml, run from the repo root.
set -euo pipefail
d="$RUNNER_TEMP/signal"
mkdir -p "$d"
op=1111111111111111111111111111111111111111
addr=$(python3 tools/ci/rpc_stub.py address "$op")
# The validator account: the operator address's bytes under the account prefix.
acc=$(python3 tools/ci/rpc_stub.py address "$op" celestia)
echo "operator address: $addr, account: $acc"
# file, network, GetUpgrade answer, VersionTally answer for v10, the validator's version (null:
# none), lowest block[, more fields]. Written aside and moved, so the stand-in never reads half a file.
state() {
  cat >"$1.new" <<EOF
{"network": "$2", "version": "9.0.4", "app_version": 9, "height": 15000000, "lowest": $6,
 "tally": {"10": "$4"}, "upgrade": "$3", "signals": {"$op": $5}${7:+, $7}}
EOF
  mv "$1.new" "$1"
}
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1
state "$d/other.json" mocha-5 "" EMj0pLgBGNbYkt0B null 1
python3 tools/ci/rpc_stub.py 8767 "$d/rpc.json" &
python3 tools/ci/rpc_stub.py 8768 "$d/other.json" &
for p in 8767 8768; do curl -fsS --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null "http://127.0.0.1:$p/health"; done
run() { # validator, RPC URLs...; $cfg adds config fields
  local v=$1
  shift
  printf '{"chain_id": "celestia", "rpc": %s, "validator": "%s", "listen": "127.0.0.1:9918", "interval_seconds": 60%s}' \
    "$(printf '%s\n' "$@" | jq -R . | jq -sc .)" "$v" "${cfg:+, $cfg}" >"$d/config.json"
  # A read that never ends (a loop on a broken answer) fails in a minute instead of holding the job.
  timeout 60 python3 -I tools/celestia-mon-signal --config "$d/config.json" --once 2>"$d/err"
}

# The upgrade to v10 is pending and the validator signalled it. The RPC URL carries a key.
# The stand-in answers queries at its last height only, so the reading is of one height.
m=$(run "$addr" http://127.0.0.1:8767/ci-secret-key)
printf '%s\n' "$m"
for want in 'celestia_mon_app_version 9' 'celestia_mon_node_version_info{version="9.0.4"} 1' \
            'celestia_mon_chain_height 15000000' 'celestia_mon_signal_next_version 10' \
            'celestia_mon_signal_tally_power 396393243' 'celestia_mon_signal_threshold_power 386480712' \
            'celestia_mon_signal_total_power 463776854' 'celestia_mon_signal_validator_version 10' \
            'celestia_mon_upgrade_pending 1' 'celestia_mon_upgrade_version 10' 'celestia_mon_upgrade_height 15116658' \
            'celestia_mon_chain_block_time_seconds 2.87' 'celestia_mon_upgrade_eta_seconds 334808' \
            'celestia_mon_signal_rpc_info{rpc="http://127.0.0.1:8767"} 1'; do
  grep -qxF "$want" <<<"$m" || { echo "missing: $want"; exit 1; }
done
grep -Eq '^celestia_mon_signal_last_success_timestamp_seconds [1-9]' <<<"$m"
if grep -q ci-secret-key - "$d/err" <<<"$m"; then echo "the key in the RPC URL shows"; exit 1; fi

# The round for v10 is open, nothing is scheduled, and the validator has not signalled.
state "$d/rpc.json" celestia "" CJv2gb0BEMj0pLgBGNbYkt0B null 1
m=$(run "$addr" http://127.0.0.1:8767)
grep -qxF 'celestia_mon_signal_tally_power 396393243' <<<"$m"
grep -qxF 'celestia_mon_signal_validator_version 0' <<<"$m"
grep -qxF 'celestia_mon_upgrade_pending 0' <<<"$m"
if grep -q '^celestia_mon_upgrade_\(version\|height\|eta_seconds\)' <<<"$m"; then echo "upgrade details without an upgrade"; exit 1; fi

# Nobody signalled v10: the answer has no voting_power field.
state "$d/rpc.json" celestia "" EMj0pLgBGNbYkt0B null 1
m=$(run "$addr" http://127.0.0.1:8767)
grep -qxF 'celestia_mon_signal_tally_power 0' <<<"$m"
grep -qxF 'celestia_mon_signal_threshold_power 386480712' <<<"$m"

# A Mocha RPC and an unreachable one come first: both are skipped, the third is read.
m=$(run "$addr" http://127.0.0.1:8768 http://127.0.0.1:9 http://127.0.0.1:8767)
cat "$d/err"
grep -qxF 'celestia_mon_signal_errors_total{reason="wrong_network"} 1' <<<"$m"
grep -qxF 'celestia_mon_signal_errors_total{reason="network"} 1' <<<"$m"
grep -qxF 'celestia_mon_signal_rpc_info{rpc="http://127.0.0.1:8767"} 1' <<<"$m"
grep -qF "http://127.0.0.1:8768 serves 'mocha-5', not 'celestia'" "$d/err"
m=$(run "$addr" http://127.0.0.1:8768)
grep -qx 'celestia_mon_signal_last_success_timestamp_seconds 0.0' <<<"$m"
if grep -q '^celestia_mon_app_version' <<<"$m"; then echo "a reading from another network"; exit 1; fi

# A node that is catching up, then one whose last block is 10 minutes old, comes first: it
# serves an old state, so it is skipped as behind and the next RPC is read.
state "$d/other.json" celestia "" CJv2gb0BEMj0pLgBGNbYkt0B null 1 '"catching_up": true'
m=$(run "$addr" http://127.0.0.1:8768 http://127.0.0.1:8767)
grep -qxF 'celestia_mon_signal_errors_total{reason="behind"} 1' <<<"$m"
grep -qxF 'celestia_mon_signal_rpc_info{rpc="http://127.0.0.1:8767"} 1' <<<"$m"
grep -qF 'http://127.0.0.1:8768 is catching up' "$d/err"
state "$d/other.json" celestia "" CJv2gb0BEMj0pLgBGNbYkt0B null 1 '"age": 600'
m=$(run "$addr" http://127.0.0.1:8768 http://127.0.0.1:8767)
grep -qxF 'celestia_mon_signal_errors_total{reason="behind"} 1' <<<"$m"
grep -qxF 'celestia_mon_signal_rpc_info{rpc="http://127.0.0.1:8767"} 1' <<<"$m"
grep -qE "http://127\.0\.0\.1:8768's last block is [0-9]+ s old" "$d/err"
# Behind a provider's URL, the calls of one read can reach different nodes: /abci_info from
# one 100 blocks behind the one that answered /status serves an old state, so it is skipped.
state "$d/other.json" celestia "" CJv2gb0BEMj0pLgBGNbYkt0B null 1 '"abci_behind": 100'
m=$(run "$addr" http://127.0.0.1:8768 http://127.0.0.1:8767)
grep -qxF 'celestia_mon_signal_errors_total{reason="behind"} 1' <<<"$m"
grep -qxF 'celestia_mon_signal_rpc_info{rpc="http://127.0.0.1:8767"} 1' <<<"$m"
grep -qF 'http://127.0.0.1:8768 answered /abci_info at height 14999900, 15000000 in /status' "$d/err"
# A signal store that does not hold the height /abci_info gave (on a node one block behind,
# or one that pruned it) answers with code 0, no value and a log: a failed read, never a
# validator that has not signalled.
state "$d/other.json" celestia "" CJv2gb0BEMj0pLgBGNbYkt0B 10 1 '"no_version": true'
m=$(run "$addr" http://127.0.0.1:8768)
cat "$d/err"
grep -qxF 'celestia_mon_signal_errors_total{reason="query"} 1' <<<"$m"
grep -qx 'celestia_mon_signal_last_success_timestamp_seconds 0.0' <<<"$m"
grep -qF 'version does not exist' "$d/err"
if grep -q '^celestia_mon_signal_validator_version' <<<"$m"; then echo "a store without the height read as no signal"; exit 1; fi

# A node that keeps only its last 500 blocks: the block time is measured over those.
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 14999500
m=$(run "$addr" http://127.0.0.1:8767)
grep -qxF 'celestia_mon_chain_block_time_seconds 2.87' <<<"$m"
grep -qxF 'celestia_mon_upgrade_eta_seconds 334808' <<<"$m"
# A node that refuses /blockchain: the reading stands, without a block time, and the ETA uses 2.6 s per block.
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1 '"refuse": ["blockchain"]'
m=$(run "$addr" http://127.0.0.1:8767)
grep -Eq '^celestia_mon_signal_last_success_timestamp_seconds [1-9]' <<<"$m"
grep -qxF 'celestia_mon_upgrade_height 15116658' <<<"$m"
grep -qxF 'celestia_mon_signal_errors_total{reason="block_time"} 1' <<<"$m"
grep -qxF 'celestia_mon_upgrade_eta_seconds 303311' <<<"$m"
if grep -q '^celestia_mon_chain_block_time_seconds' <<<"$m"; then echo "a block time from a node that refused it"; exit 1; fi

# Only unreachable RPCs, one with a key in its URL: no reading, and the key does not show.
m=$(run "$addr" http://127.0.0.1:9/ci-secret-key http://127.0.0.1:10)
grep -qxF 'celestia_mon_signal_errors_total{reason="network"} 2' <<<"$m"
grep -qx 'celestia_mon_signal_last_success_timestamp_seconds 0.0' <<<"$m"
if grep -q ci-secret-key - "$d/err" <<<"$m"; then echo "the key in the RPC URL shows"; exit 1; fi

# A redirect is not followed, not even to a good RPC, and an answer over 1 MiB is not read.
state "$d/other.json" celestia "" CJv2gb0BEMj0pLgBGNbYkt0B 10 1 '"redirect": "http://127.0.0.1:8767/status"'
m=$(run "$addr" http://127.0.0.1:8768)
grep -qxF 'celestia_mon_signal_errors_total{reason="http"} 1' <<<"$m"
grep -qx 'celestia_mon_signal_last_success_timestamp_seconds 0.0' <<<"$m"
grep -qF 'http://127.0.0.1:8768 answered 302 for /status, a redirect, which is not followed' "$d/err"
state "$d/other.json" celestia "" CJv2gb0BEMj0pLgBGNbYkt0B 10 1 '"oversize": true'
m=$(run "$addr" http://127.0.0.1:8768)
grep -qxF 'celestia_mon_signal_errors_total{reason="format"} 1' <<<"$m"
grep -qF 'http://127.0.0.1:8768 answered /status with more than 1048576 bytes' "$d/err"
# JSON nested deeper than Python's parser goes cannot be read either: in /status the next RPC
# is read, and in a failed /blockchain the reading stands without a block time.
state "$d/other.json" celestia "" CJv2gb0BEMj0pLgBGNbYkt0B 10 1 '"nested": {"status": 200}'
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1 '"nested": {"blockchain": 500}'
m=$(run "$addr" http://127.0.0.1:8768 http://127.0.0.1:8767)
cat "$d/err"
grep -qxF 'celestia_mon_signal_errors_total{reason="format"} 1' <<<"$m"
grep -qxF 'celestia_mon_signal_errors_total{reason="block_time"} 1' <<<"$m"
grep -qxF 'celestia_mon_signal_rpc_info{rpc="http://127.0.0.1:8767"} 1' <<<"$m"
grep -qxF 'celestia_mon_upgrade_height 15116658' <<<"$m"
grep -qF 'http://127.0.0.1:8768 answered /status with JSON nested too deeply to read' "$d/err"
grep -qF 'http://127.0.0.1:8767 answered 500 for /blockchain; the block time measured last is kept' "$d/err"
if grep -q '^celestia_mon_chain_block_time_seconds' <<<"$m"; then echo "a block time from a nested answer"; exit 1; fi
# An app version below 0 (the later of the two app_version fields counts) is a broken answer,
# which must not hold the read: the next RPC is read.
state "$d/other.json" celestia "" CJv2gb0BEMj0pLgBGNbYkt0B 10 1 '"app_version": -2'
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1
m=$(run "$addr" http://127.0.0.1:8768 http://127.0.0.1:8767)
grep -qxF 'celestia_mon_signal_errors_total{reason="format"} 1' <<<"$m"
grep -qxF 'celestia_mon_signal_rpc_info{rpc="http://127.0.0.1:8767"} 1' <<<"$m"
grep -qF 'http://127.0.0.1:8768 answered /abci_info with app version -2 at height 15000000' "$d/err"

# Governance: two proposals in their voting period, the validator account voted on one and
# another account on the other, and one that has left it. A title is free text: a line
# break, a zero-width space and a direction override do not reach the label, nor more than
# 80 characters.
t100=$(printf 'x%.0s' $(seq 100))
gov='"gov": [{"id": 101, "start": 1792000000, "end": 1792604800, "nanos": 500000000, "voters": ["'"$acc"'"],
              "title": "Raise the\nblock size\u200b\u202e"},
             {"id": 102, "start": 1792300000, "end": 1792386400, "expedited": true, "voters": ["celestia1other"],
              "title": "'"$t100"'"},
             {"id": 99, "status": 3, "start": 1791000000, "end": 1791604800, "voters": ["'"$acc"'"], "title": "Passed"}]'
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1 "$gov"
m=$(run "$addr" http://127.0.0.1:8767)
printf '%s\n' "$m"
for want in "celestia_mon_gov_voting_proposals{voter=\"$acc\"} 2" \
            'celestia_mon_gov_voting_start_timestamp_seconds{proposal_id="101"} 1792000000.0' \
            'celestia_mon_gov_voting_end_timestamp_seconds{proposal_id="101"} 1792604800.5' \
            'celestia_mon_gov_voting_start_timestamp_seconds{proposal_id="102"} 1792300000.0' \
            'celestia_mon_gov_voting_end_timestamp_seconds{proposal_id="102"} 1792386400.0' \
            'celestia_mon_gov_validator_voted{proposal_id="101"} 1' 'celestia_mon_gov_validator_voted{proposal_id="102"} 0' \
            'celestia_mon_gov_proposal_info{proposal_id="101",title="Raise the block size"} 1' \
            "celestia_mon_gov_proposal_info{proposal_id=\"102\",title=\"${t100:0:79}…\"} 1" \
            'celestia_mon_upgrade_height 15116658'; do
  grep -qxF "$want" <<<"$m" || { echo "missing: $want"; exit 1; }
done
grep -Eq '^celestia_mon_gov_last_success_timestamp_seconds [1-9]' <<<"$m"
if grep -q 'proposal_id="99"' <<<"$m"; then echo "a proposal out of its voting period was read"; exit 1; fi
if grep -q 'reason="gov"' <<<"$m"; then echo "a good read of the proposals counted an error"; exit 1; fi
# None open: the count is 0 and no proposal line is served.
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1
m=$(run "$addr" http://127.0.0.1:8767)
grep -qxF "celestia_mon_gov_voting_proposals{voter=\"$acc\"} 0" <<<"$m"
if grep -q 'proposal_id=' <<<"$m"; then echo "a proposal line without a proposal"; exit 1; fi
# The first RPC fails the Proposals query: x/signal is read there, the proposals through the next.
state "$d/other.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1 '"gov_error": true'
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1 "$gov"
m=$(run "$addr" http://127.0.0.1:8768 http://127.0.0.1:8767)
cat "$d/err"
grep -qxF 'celestia_mon_signal_rpc_info{rpc="http://127.0.0.1:8768"} 1' <<<"$m"
grep -qxF 'celestia_mon_signal_errors_total{reason="gov"} 1' <<<"$m"
grep -qxF 'celestia_mon_gov_validator_voted{proposal_id="101"} 1' <<<"$m"
grep -qF 'http://127.0.0.1:8768 answered /cosmos.gov.v1.Query/Proposals with code 6' "$d/err"
# Every RPC fails it: the x/signal reading stands, and no proposal is served.
m=$(run "$addr" http://127.0.0.1:8768)
grep -Eq '^celestia_mon_signal_last_success_timestamp_seconds [1-9]' <<<"$m"
grep -qxF 'celestia_mon_upgrade_height 15116658' <<<"$m"
grep -qxF 'celestia_mon_gov_last_success_timestamp_seconds 0.0' <<<"$m"
if grep -q '^celestia_mon_gov_voting_proposals' <<<"$m"; then echo "proposals served without a read"; exit 1; fi
grep -qF 'no RPC gave the proposals' "$d/err"
# The first RPC answers the Proposals query in a form that does not read: a proposal without its voting end
# time, then an answer with the voter that lists a proposal out of its voting period. x/signal is read there,
# the proposals through the next.
for broken in end voted; do
  state "$d/other.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1 "$gov"', "gov_broken": "'"$broken"'"'
  m=$(run "$addr" http://127.0.0.1:8768 http://127.0.0.1:8767)
  cat "$d/err"
  grep -qxF 'celestia_mon_signal_rpc_info{rpc="http://127.0.0.1:8768"} 1' <<<"$m"
  grep -qxF 'celestia_mon_signal_errors_total{reason="gov"} 1' <<<"$m"
  grep -qxF 'celestia_mon_gov_validator_voted{proposal_id="101"} 1' <<<"$m"
  grep -qF 'cannot read the proposals in the answer of http://127.0.0.1:8768: ValueError(' "$d/err"
  if [ "$broken" = end ]; then want="proposal 101's voting end time is missing"; else want='proposal 99 has a vote'; fi
  grep -qF "$want" "$d/err" || { echo "the log does not say: $want"; exit 1; }
done
# A title in Markdown: its square brackets become round ones, so Discord shows no link under other words.
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1 \
  '"gov": [{"id": 103, "start": 1792000000, "end": 1792604800, "title": "[Vote here](https://phish.example)"}]'
m=$(run "$addr" http://127.0.0.1:8767)
grep -qxF 'celestia_mon_gov_proposal_info{proposal_id="103",title="(Vote here)(https://phish.example)"} 1' <<<"$m"
# gov false: no proposal is read and no gov metric is served; gov must be true or false.
m=$(cfg='"gov": false' run "$addr" http://127.0.0.1:8767)
grep -qxF 'celestia_mon_upgrade_height 15116658' <<<"$m"
if grep -q '^celestia_mon_gov_' <<<"$m"; then echo "gov metrics with gov false"; exit 1; fi
if cfg='"gov": "yes"' run "$addr" http://127.0.0.1:8767 >/dev/null; then echo "gov \"yes\" was accepted"; exit 1; fi
grep -qF "gov is 'yes', not true or false" "$d/err"

# URLs the service refuses at start, without printing them: a login, a query string, no host.
for u in http://ci:ci-secret-key@127.0.0.1:8767 'http://127.0.0.1:8767/?key=ci-secret-key' http://:26657; do
  if run "$addr" "$u" >/dev/null; then echo "rpc $u was accepted"; exit 1; fi
  grep -q 'rpc 1 of 1 is not an http or https URL' "$d/err"
  if grep -q ci-secret-key "$d/err"; then echo "a refused URL shows its key"; exit 1; fi
done
# http to a host that is neither this one nor an address of the private network: https is
# needed there. localhost is this host.
for u in http://rpc.example.org:26657/ci-secret-key http://203.0.113.7:26657 'http://[2001:db8::1]:26657'; do
  if run "$addr" "$u" >/dev/null; then echo "rpc $u was accepted"; exit 1; fi
  grep -q 'rpc 1 of 1 is an http URL of a host that is not this one or an address of the private network' "$d/err"
  if grep -q ci-secret-key "$d/err"; then echo "a refused URL shows its key"; exit 1; fi
done
m=$(run "$addr" http://localhost:8767)
grep -qxF 'celestia_mon_signal_rpc_info{rpc="http://localhost:8767"} 1' <<<"$m"

# Addresses that do not decode: the service and the install's check refuse them.
last=${addr: -1}
# shellcheck disable=SC2015 # echo cannot fail
bad=${addr%?}$([ "$last" = q ] && echo p || echo q)
if run "$bad" http://127.0.0.1:8767 >/dev/null; then echo "an address with a wrong checksum was accepted"; exit 1; fi
grep -q 'wrong checksum' "$d/err"
python3 -I tools/celestia-mon-signal --check-address "$addr"
for a in "$bad" "${addr/celestiavaloper/celestiavalcons}" celestiavaloper1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq; do
  if python3 -I tools/celestia-mon-signal --check-address "$a" 2>>"$d/err"; then echo "--check-address accepted $a"; exit 1; fi
done
cat "$d/err"
# A checksum that holds over 32 bytes: the length is checked too.
long=$(python3 tools/ci/rpc_stub.py address "$(printf '22%.0s' $(seq 32))")
if python3 -I tools/celestia-mon-signal --check-address "$long" 2>"$d/err"; then echo "a 32-byte address was accepted"; exit 1; fi
grep -q 'holds 32 bytes' "$d/err"
# The decoder against BIP 173's own vectors, so that a mistake the stand-in's encoder shares
# cannot pass: valid and broken checksums, and a segwit address whose program BIP 173 gives.
python3 -I - tools/celestia-mon-signal <<'EOF'
import importlib.machinery
import importlib.util
import sys

loader = importlib.machinery.SourceFileLoader("collector", sys.argv[1])
collector = importlib.util.module_from_spec(importlib.util.spec_from_loader("collector", loader))
loader.exec_module(collector)
for s in ["A12UEL5L", "a12uel5l", "abcdef1qpzry9x8gf2tvdw0s3jn54khce6mua7lmqqqxw",
          "split1checkupstagehandshakeupstreamerranterredcaperred2y9e3w", "?1ezyfcl",
          "an83characterlonghumanreadablepartthatcontainsthenumber1andtheexcludedcharactersbio1tt5tgs"]:
    collector.bech32_decode(s)
for s in ["A1G7SGD8", "a12UEL5L", "x1b4n0q5v", "li1dgmt3"]:
    try:
        collector.bech32_decode(s)
    except ValueError:
        continue
    sys.exit(f"{s} was accepted")
prefix, values = collector.bech32_decode("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4")
assert prefix == "bc" and values[0] == 0, (prefix, values)
assert collector.from5(values[1:]) == bytes.fromhex("751e76e8199196d454941c45d1b3a323f1433bd6")
# The encoder (of the validator account) against the same vector.
program = collector.to5(bytes.fromhex("751e76e8199196d454941c45d1b3a323f1433bd6"))
assert collector.bech32_encode("bc", [0] + program).upper() == "BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4"
EOF

# As a service: while the block time cannot be measured, the one measured last stays; while
# the proposals cannot be read, the ones read last stay and x/signal goes on being read;
# while no RPC can be read, the whole last reading stays and only its time ages.
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1 "$gov"
printf '{"chain_id": "celestia", "rpc": ["http://127.0.0.1:8767"], "validator": "%s", "listen": "127.0.0.1:9918", "interval_seconds": 1}' \
  "$addr" >"$d/service.json"
python3 -I tools/celestia-mon-signal --config "$d/service.json" 2>"$d/service.err" &
service=$!
metrics() { curl -fsS http://127.0.0.1:9918/metrics; }
reading() { metrics | grep -Ev '^(#|celestia_mon_(signal|gov)_last_success_timestamp_seconds|celestia_mon_signal_errors_total)'; }
wait_for() { # an extended regular expression that a whole metrics line must match
  for _ in $(seq 30); do
    if grep -Eqx "$1" <<<"$(metrics 2>/dev/null)"; then return 0; fi
    sleep 1
  done
  echo "no metrics line matches $1"; cat "$d/service.err"; exit 1
}
wait_for 'celestia_mon_upgrade_eta_seconds 334808'
# The proposals are read after x/signal, in the same read.
wait_for 'celestia_mon_gov_validator_voted\{proposal_id="101"\} 1'
before=$(reading)
# One thing changes at a time, so a read that began before the change gives the same reading.
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1 "$gov"', "refuse": ["blockchain"]'
wait_for 'celestia_mon_signal_errors_total\{reason="block_time"\} ([2-9]|[1-9][0-9]+)'
test "$(reading)" = "$before" || { echo "the block time measured last did not stay"; reading; exit 1; }
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1 "$gov"', "refuse": ["blockchain"], "gov_error": true'
wait_for 'celestia_mon_signal_errors_total\{reason="gov"\} [1-9][0-9]*'
gov_at=$(metrics | grep '^celestia_mon_gov_last_success_timestamp_seconds ')
signal_at=$(metrics | grep '^celestia_mon_signal_last_success_timestamp_seconds ')
wait_for 'celestia_mon_signal_errors_total\{reason="gov"\} ([3-9]|[1-9][0-9]+)'
test "$(reading)" = "$before" || { echo "a failed read of the proposals changed the reading"; reading; exit 1; }
test "$(metrics | grep '^celestia_mon_gov_last_success_timestamp_seconds ')" = "$gov_at" ||
  { echo "the proposals' read time moved without a read"; exit 1; }
test "$(metrics | grep '^celestia_mon_signal_last_success_timestamp_seconds ')" != "$signal_at" ||
  { echo "a failed read of the proposals held back x/signal"; exit 1; }
# A Proposals answer that does not read keeps them the same way.
state "$d/rpc.json" celestia CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1 "$gov"', "refuse": ["blockchain"], "gov_broken": "end"'
for _ in $(seq 30); do
  if grep -qF "proposal 101's voting end time is missing" "$d/service.err"; then break; fi
  sleep 1
done
grep -qF 'cannot read the proposals in the answer of http://127.0.0.1:8767: ValueError(' "$d/service.err"
test "$(reading)" = "$before" || { echo "an unreadable answer of the proposals changed the reading"; reading; exit 1; }
test "$(metrics | grep '^celestia_mon_gov_last_success_timestamp_seconds ')" = "$gov_at" ||
  { echo "the proposals' read time moved after an unreadable answer"; exit 1; }
state "$d/rpc.json" mocha-5 CgcIChDy0poH CJv2gb0BEMj0pLgBGNbYkt0B 10 1 '"refuse": ["blockchain"]'
wait_for 'celestia_mon_signal_errors_total\{reason="wrong_network"\} [1-9][0-9]*'
at=$(metrics | grep '^celestia_mon_signal_last_success_timestamp_seconds ')
wait_for 'celestia_mon_signal_errors_total\{reason="wrong_network"\} ([3-9]|[1-9][0-9]+)'
test "$(reading)" = "$before" || { echo "a failed read changed the reading"; reading; exit 1; }
test "$(metrics | grep '^celestia_mon_signal_last_success_timestamp_seconds ')" = "$at" ||
  { echo "the last read time moved without a read"; exit 1; }
kill "$service"
