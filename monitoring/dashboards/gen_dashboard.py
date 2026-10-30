#!/usr/bin/env python3
"""Generate celestia-mon's Grafana dashboard from the inventory settings.

    gen_dashboard.py --config <settings.json> --out <dashboard.json>

The settings file is rendered by the celestia_mon_grafana Ansible role from
ansible/roles/celestia_mon_grafana/templates/dashboard-config.json.j2:

    {
      "datasource_uid": "celestia-mon-prometheus",
      "name": "...",                               # celestia_mon_validator_name; "" when not set
      "signer": "local" | "remote",
      "signer_multi_node": true | false,           # celestia_mon_signer_multi_node: tmkms signs through several nodes
      "fibre_enabled": true | false,
      "tensile_enabled": true | false,             # the outside check (celestia_mon_tensile), with Fibre on
      "signal_enabled": true | false,              # upgrade signalling (celestia_mon_signal)
      "gov_enabled": true | false,                 # its governance votes (celestia_mon_gov_on)
      "gov_page": true | false,                    # GovVoteMissing also pages (celestia_mon_gov_page)
      "announcements_enabled": true | false,       # celestia_mon_announcements
      "units": {"appd": "...", "fibre": "...", "tmkms": "..."},
      "host_units": [{"host": "...", "appd": "...", "fibre": "...", "tmkms": "..."}],  # only the hosts
                                                   # whose own celestia_mon_units differ from units; optional
      "thresholds": {...},                         # celestia_mon_thresholds
      "nodes": ["..."],                            # celestia-appd hosts: validator, then backup_node
      "kms_hosts": ["..."],                        # kms hosts with celestia_mon_kms_scrape
      "kms_names": ["..."],                        # every kms host, with a remote signer: names the signer's column
      "monitor_host": "...",                       # celestia_mon_monitor_host
      "monitor_only": true | false,                # the monitor host runs no Celestia service
      "prometheus_retention": "30d",               # celestia_mon_prometheus_retention
      "fibre_mounts": [{"host": "...", "mount": "..."}],  # every fibre host, active or standby
      "fibre_endpoints": [{"host": "...", "endpoint": "..."}],  # every fibre host; celestia_mon_fibre_public_endpoint or ""
      "data_mounts": [{"host": "...", "mount": "..."}],
      "links": [{"host": "...", "mbps": 1000}]     # every node_exporter host; mbps: celestia_mon_link_mbps or null
    }

Layout (docs/panels.md): a first screen without a row header that reads like a status page, top
to bottom: one framed strip with the validator's name and network, the Status (the worst alert in a
few words) and four facts; up to six one-word answers; the Servers band, one column per server
(and one for a remote signer whose host is not scraped) with what runs there now and its hardware
now; History, one line of bars per check and, after a gap, per server (the monitor's line also
carries the monitoring's own alerts), with each line's uptime;
the role band, which server held each role; and the key line (the range's ends and the colours).
Then All firing alerts (collapsed), the rows Validator, Fibre (its facts, the Upload readiness line
and its charts) and Servers (expanded; Servers has a column of hardware charts per server) and
Servers: disks, restarts and clocks and Network upgrade (collapsed). Panels that do not apply to
the install are left out.

Colour has three layers. Each server has one identity colour (HOST_COLORS) on every line, fill,
bar and sparkline it owns, so a section reads in the colour of the server that holds its role and a
role move shows as a colour change; what belongs to the whole network is the text colour. Amber
and red mean state only and appear only where an alert rule watches the condition, at the rule's
own threshold: amber while the problem is true or its alert is pending, red once a page alert
fires. The Status, the answers and the status bars read Prometheus's own ALERTS series, so
they agree with the alerts that were sent.

Roles can move between hosts (a backup node takes over signing after a tmkms repoint, a standby
Fibre is started on another host), so no query takes the host that holds a role from the
settings: the signing node is the one the signatures go through, and the active Fibre is the one
whose unit systemd reports active. Host names from the settings appear in queries only to name,
order and colour the servers (every configured server gets a status line and a column of the
Servers band, even when nothing is read from it), to select a server's own disks and link, and to
pair each Fibre host with its public endpoint.

Python 3 standard library only. The same settings always give the same bytes. Next to the
settings, the generator reads two files beside the alert rules: the rules template, whose every
alert must have a plain sentence and a short name in TITLES, and monitoring/rules/holds.json, the
hold times the descriptions print (CI checks them against the rules' for:). It stops with a message
rather than write a dashboard whose words would miss an alert or not fit their panel at 1440 px.

There are no dashboard variables. Only Grafana's built-in $__interval and $__range appear: both in
the status board, the role band and the uptime columns, and $__interval in the charts and
sparklines that count per time block; tools/ci/check_queries.py replaces them before it sends each
query to Prometheus.
"""
import argparse
import functools
import json
import math
import os
import re

UID = "celestia-mon-validator"
TITLE = "Celestia validator"
DOCS = "https://github.com/Huginn-Tech/celestia-validator-monitoring/blob/main/docs/alerts.md"
QUICKSTART = "https://github.com/Huginn-Tech/celestia-validator-monitoring/blob/main/docs/quickstart.md"
# Pages the descriptions link to, as (text, url). Panels carry no links of their own: one (i) per title.
NODE_EXPORTER_FULL = ("Node Exporter Full", "https://grafana.com/grafana/dashboards/1860-node-exporter-full/")
FIBRE_DASHBOARD = ("Fibre dashboard (celestia-app)",
                   "https://github.com/celestiaorg/celestia-app/blob/v10.5.0/observability/docker/grafana/dashboards/fibre.json")
TENSILE = ("Tensile", "https://tensile.huginn.tech")
MAINNET_LAYOUT = ("mainnet layout", QUICKSTART + "#mainnet-layout")
PANEL_GUIDE = ("Panel guide", "https://github.com/Huginn-Tech/celestia-validator-monitoring/blob/main/docs/panels.md")

# The alert rules template and the hold times of its rules that the descriptions print.
RULES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "rules")
RULES_TEMPLATE = os.path.join(RULES_DIR, "celestia-mon.rules.yml.j2")
HOLDS_FILE = os.path.join(RULES_DIR, "holds.json")

# Dashboard links in the controls menu: (title, url, only with Fibre).
LINKS = [
    ("Alert reference", DOCS, False),
    ("Server detail: Node Exporter Full", NODE_EXPORTER_FULL[1], False),
    ("Fibre dashboards (celestia-app)", FIBRE_DASHBOARD[1], True),
    ("Tensile: Fibre seen from outside", TENSILE[1], True),
]

# Interfaces left out of NIC traffic: loopback, containers, bridges, VPNs and tunnels,
# bond and team masters (their member NICs count) and VLAN sub-interfaces (their parent counts).
VIRT = (r"lo|docker.*|veth.*|br.*|vmbr.*|virbr.*|cni.*|flannel.*|cali.*|cilium.*|vxlan.*|kube.*"
        r"|tailscale.*|wg.*|tun.*|tap.*|bond.*|team.*|.+\..+")

# Block devices left out of disk latency: LVM/LUKS (dm), software RAID (md) and ZFS volumes (zd)
# count the I/O of their physical disks again; CD drives (sr) and network block devices (nbd)
# are noise. node_exporter already leaves out partitions, loop, ram and zram devices.
DISK = r"dm-.*|md.*|zd.*|sr.*|nbd.*"

# One identity colour per server, in servers order: every line, fill, bar and sparkline of the
# server, its name and the role band's tints (the hex at 35% alpha). Never green, yellow, orange,
# red or grey, which mean state; neighbours differ in hue and lightness, and each keeps at least
# 5:1 against the panel background. Servers past the list are drawn in the text colour.
HOST_COLORS = [("#5794F2", "#5794F2"), ("#CA95E5", "#CA95E5"), ("#6ED0E0", "#6ED0E0"), ("#8AB8FF", "#8AB8FF")]
# What belongs to the whole network rather than one server (block time, the network's missed
# share, expected counts, capacity lines): the text colour, dashed where it is a reference.
NETWORK = "#CCCCDC"
REFERENCE = "#CCCCDC99"

# Bar fills and key swatches: fine (a muted green, so a healthy board is calm), look soon, urgent
# (Grafana's dark-theme red, so red words and red bars match), and no data or not in use (at 45%, plainly
# grey on the dark page and a step under the fine green).
OK, WARN, ALERT, NODATA = "#56A64B99", "#FF9830", "#F2495C", "#8E8E8E73"
# Words use named colours; grey words (no data, not checked, standby) this fixed grey.
GREY = "#8E8E8E"
# The steady healthy word of the Status cell and of each answer tile, and no other word: Grafana's
# semi-dark-green, #56A64B, the hue of the fine bars. Healthy words elsewhere keep the text colour.
FINE = "semi-dark-green"
# A zero-width space: the uptime columns' lane names, distinct and invisible.
ZERO_WIDTH = chr(0x200B)

# Grafana 12.4's panel chrome: a titled panel loses 58 px of its height (header, padding, border),
# an untitled one 18 px. A grid row is 30 px, plus 8 px between rows.
CHROME_TITLED = 58
CHROME_UNTITLED = 18
LANE_PX = 24          # the least height of a History line or role lane
BAR_PX = 20           # the thickness of every bar of the History board and of the role band
# A state timeline or status history with its lane names on the left and no time axis insets its plot
# 8 px at the top and 8 px at the bottom (Grafana's paddingSide), so its lanes share the rest.
PLOT_INSET = 16
TABLE_ROW_PX = 42     # cellHeight "md": each table row
# The table's header row. At most 40 px, as the render showed: two rows then fit h5 with no
# scrollbar. Set it to the measured height to keep the band under the last row as small as can be.
TABLE_HEADER_PX = 40

# Text checks, at 1440 px wide with Grafana's menu docked (Grafana 12 docks it from 1200 px), with
# widths measured in the rendered dashboard (Grafana's Inter at weight 500): a letter 0.52 em, a
# digit 0.60 em, a separator (. ,) 0.30 em. Each strip pins one text size; a pinned size is never
# shrunk by Grafana, so the generator makes sure the longest text fits.
CHAR_EM, DIGIT_EM, SEP_EM = 0.52, 0.60, 0.30
# The values of the top strip and the answer tiles are checked letter by letter: each character's width
# in Inter at weight 500, in hundredths of an em, measured in Grafana (canvas measureText at 100 px;
# within 1 px of the rendered words). Other characters count CHAR_EM. A value has the cell's width less
# 2 * VALUE_PAD: such a cell (over 100 px high) pads its text 12 px, not the 8 px inner_px allows for.
INTER_500 = dict(zip(" abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,-·()%:/", (
    27, 57, 62, 58, 62, 59, 38, 62, 60, 25, 25, 56, 25, 89, 60, 60, 62, 62, 39, 54, 34, 60, 57, 83, 56, 58, 56,
    71, 66, 73, 72, 60, 59, 75, 74, 27, 58, 69, 57, 91, 76, 77, 64, 77, 65, 65, 65, 74, 71, 100, 70, 70, 64,
    65, 42, 62, 63, 66, 62, 63, 57, 63, 63, 30, 30, 46, 30, 37, 37, 99, 30, 37)))
VALUE_PAD = 4
LINE_EM = 1.2                            # a line of text is 1.2 times its size high
STACK_GAP_PX = 7                         # between label and value in a centred value_and_name stack
# The intro strip at the top: six framed, untitled cells of one height and width (identity, Status,
# Latest block, Block time, Voting power and Signed), each a label over a value, all in one size
# pair, so every label sits on one line and every value on another, and the column edges line up
# with the answer tiles below. Every six-cell row (this strip, the answers and the facts of the
# Validator, Fibre and Network upgrade rows) takes one value size, the first of INTRO_VALUE_SIZES at
# which every word and number of every one of them fits (one_value_size).
INTRO_ROWS, INTRO_LABEL_PX, INTRO_VALUE_SIZES = 3, 13, (22, 20, 18)
INTRO_WIDTHS = (4, 4, 4, 4, 4, 4)
NAME_CHARS = 17                          # the validator's name: 13 characters at 22 px, 15 at 20, 17 at 18
DEFAULT_NAME = "Validator"               # the name when celestia_mon_validator_name is empty
INTRO_NUMBER = "99.999.999"              # the widest number a cell shows (a block height)
KEY_ROWS, KEY_PX = 1, 12                 # the colour key and the range ends: one 12 px line in an untitled h1
FACT_PX = 18                             # a stat's value size where it sets none, never larger than the answers
# The table's 14 px text. Its columns are sized by short words ("no data", "Peers"), which run up to
# 0.56 em, so it keeps 0.55 em.
TABLE_CHAR = 0.55 * 14
TABLE_PAD = 16                           # a cell's padding
TABLE_ROOM = 1060                        # what the Servers now table may fill without a horizontal scrollbar
GAUGE_MIN, GAUGE_FLOOR = 90, 60          # the gauge columns' minimum width, and the least it may drop to
# The Servers band: per column a card (a stat, one row per part, the name left and the word right,
# both in one size so they share a baseline) over a bar gauge of four bars. Every card of an install
# has the same number of rows: one for each part its server runs, then blank rows down to the deepest
# card's. Each row is a BigValue item of at most 100 px, padded 8 px on each side, and needs a gap
# between its name and its word: three rows fit an h4 at about 28 px apart.
CARD_H = {1: 3, 2: 3, 3: 4}
CARD_SIZES = (14, 13)
CARD_PAD, CARD_GAP = 8, 16
# A titled panel's header keeps 96 px of the panel's width for its padding, the (i) and the menu button
# (hidden until hover, but its room is kept): the title has inner_px(w) - TITLE_CHROME.
TITLE_PX, TITLE_CHROME = 14, 79
# The bar gauge under each card, untitled, LOWER_ROWS high: four bars 24 px thick, 34 px apart, each
# with its name on its left, right-aligned in one column, so every bar of a panel starts on one x, and
# its value after a faint track. Grafana makes that column as wide as the name with the most characters
# measured at BAR_MEASURE_PX (for bars this thick) and BAR_NAME_GAP, but at most BAR_NAME_SHARE of the
# panel, and cuts a longer name: the names take the largest of BAR_NAME_SIZES that fits every panel of
# the band. Where none fits, the names go above the bars, in a panel one row higher (with the names
# above them, 4 rows leave the bars 6 px and clip their values).
LOWER_ROWS = 4
BAR_NAME_SIZES, BAR_MEASURE_PX, BAR_NAME_SHARE = (12, 11), 12, 0.4
BAR_NAME_GAP, BAR_VALUE_GAP, BAR_MIN_PX = 15, 20, 60
# Each row name of a card starts with a four-per-em space (3.5 px at 14 px): a BigValue item of at most
# 100 px is padded 8 px and the panel title 12 px, so the names line up with the title above them.
ROW_INDENT = "\u2005"
BAR_NAME_PX, BAR_VALUE_PX = 12, 14
# A card row whose words carry a number (peers, blocks behind) is a text: its colour rides on an
# invisible last character, which a regex mapping turns into amber, red or grey.
MARKS = {"orange": "\u200b", "red": "\u200c", GREY: "\u200d"}
MAX_COLUMNS = 4                          # more servers than this: the Servers now table instead

# Block height of every celestia-appd node: the validator and the backup nodes.
H = 'cometbft_consensus_height{job="celestia-app"}'
LSH = 'cometbft_consensus_validator_last_signed_height{job="celestia-app"}'
MISSED = 'cometbft_consensus_validator_missed_blocks{job="celestia-app"}'
# The blocks each node counted as missed in the last hour, the text SignaturesMissed reads (missed_1h
# in the rules template; CI checks that they are equal). The count is a counter without a _total
# suffix, so increase() over it carries a PromQL notice; this sums each minute's rise instead: the
# count now less its last value a minute before, or after a node restart, when the count starts
# again, its new value.
MISSED_BEFORE = "last_over_time(%s[1h] offset 1m)" % MISSED
MISSED_1H = "sum_over_time((%s - %s >= 0 or %s * 1 < %s)[1h:1m])" % (MISSED, MISSED_BEFORE, MISSED, MISSED_BEFORE)
POWER = 'cometbft_consensus_validator_power{job="celestia-app"}'
TOTAL = 'cometbft_consensus_validators_power{job="celestia-app"}'
# Signatures the node's privval client made: votes and proposals (PV), and Fibre's raw bytes (RAW).
# A node that only forwards Fibre's requests is never taken for the node the validator signs through.
PV = 'cometbft_privval_signing_latency_seconds_count{job="celestia-app",message_type=~"prevote|precommit|proposal"}'
RAW = 'cometbft_privval_signing_latency_seconds_count{job="celestia-app",message_type="raw_bytes"}'
# Each node's median time over its last 50 signatures, as RemoteSignerProblem reads it.
MEDIAN = 'max by (host) (cometbft_privval_signing_latency_median_seconds{job="celestia-app"})'

# True while the validator's key is in the active set: a node recorded a block for it in the
# last 2 minutes, as a signature (the last signed height changes) or a miss (the missed-block
# count grows). celestia-core sets the validator's voting power, last signed height and missed
# blocks only while its key is in the set (consensus/state.go recordMetrics) and never writes a
# power of 0, so after a jail or an unbond the power keeps its last value; these two show it.
# The misses are counted as in missed_in() below.
IN_SET = ("(max(changes(%s[2m])) > 0 or max(%s - min_over_time(%s[2m])) > 0)"
          % (LSH, MISSED, MISSED))
# IN_SET, or too new to tell: the last signed height has fewer than two samples in the 2 minutes
# (right after Prometheus or the node starts). Readings that would say "Out of set" use this, so a
# start never reads as out of the set.
IN_SET_OR_NEW = "(%s or max(count_over_time(%s[2m])) < 2)" % (IN_SET, LSH)
# NodeNotAdvancing's own pending condition: no node committed a block in 2 minutes. A window with
# a single height (right after Prometheus starts) shows no change either way, so it needs two.
STALL_SEEN = "(count_over_time(max(%s)[2m:]) > 1)" % H
STALL = "((changes(max(%s)[2m:]) == 0) and on () %s)" % (H, STALL_SEEN)
# Blocks since the validator's last signature on any node, uncapped, as ValidatorNotSigning.
BLOCKS_SINCE = "(max(%s) - (max(%s) or max(%s) * 0))" % (H, LSH, H)
# Prometheus scraped itself through this minute (three of its four 15 s scrapes): minutes without
# monitoring leave every share, and so does the minute in which Prometheus starts, whose rules have
# not run yet.
PROM = '(max(count_over_time(up{job="prometheus"}[1m])) >= 3)'
# Prometheus handing alerts to Alertmanager. In the pinned Prometheus (notifier/sendloop.go), sent
# counts alerts only when a send succeeds, errors only when it fails.
NOTIFY_ERRORS = 'prometheus_notifications_errors_total{job="prometheus"}'
NOTIFY_DROPPED = 'prometheus_notifications_dropped_total{job="prometheus"}'
NOTIFY_SENT = 'prometheus_notifications_sent_total{job="prometheus"}'
AM_DISCOVERED = 'prometheus_notifications_alertmanagers_discovered{job="prometheus"}'
# When each rule group last ran: the alert rules run while this moves.
RULES_RUN = 'prometheus_rule_group_last_evaluation_timestamp_seconds{job="prometheus"}'
# NodePeersLow's page: the node the validator signs through has no peers.
PEERS_PAGE = 'alertname="NodePeersLow",severity="page"'
# 10 while the page alert fires, else 0: added to a code, it turns the amber word red.
VNS10 = '(max(ALERTS{alertname="ValidatorNotSigning",alertstate="firing"}) * 10 or vector(0))'
NNA10 = '(max(ALERTS{alertname="NodeNotAdvancing",alertstate="firing"}) * 10 or vector(0))'
NE_UP = '(max by (host) (up{job="node"}) == 1)'
UP_LOST = 'or on () (max(up{job="prometheus"}) * 0 - 1)'
# ServiceDown's crash-loop rule for Fibre leaves out a host that booted in the last 40 minutes: Fibre exits
# until its node answers, and those restarts stay in the rule's 30-minute window (CI checks the rule's text).
BOOTED = 'time() - node_boot_time_seconds{job="node"} < 2400'

# A status bar covers the time block that starts at its timestamp, so the newest bar is the block
# that runs now: W reads that block, WS reads it as a subquery of 1-minute steps.
W = "[$__interval] offset -$__interval"
WS = "[$__interval:1m] offset -$__interval"
# The newest block has only just begun when a bar is drawn: its window runs ahead of the data, so
# for its first seconds it holds no sample, or too few for increase(), and every line would read
# grey (no data) or amber (no heartbeat). in_block() therefore falls back to the 2 minutes before
# the block until the block holds three of Prometheus's scrapes of itself, 30 s at the 15 s scrape
# and evaluation interval: by then every target has been scraped twice and the alert rules have run
# twice. A completed block holds them whenever monitoring ran, so there the fallback changes
# nothing. It reads the 2 minutes before a completed block only when monitoring had less than 30 s
# of data in it, and those 2 minutes hold data only if monitoring stopped within them: then the bar
# shows the state just before the outage instead of grey.
STARTED = '(count_over_time(up{job="prometheus"}%s) >= 3)' % W
# A subquery step of the newest block can lie in the future, where every selector repeats its last
# sample for up to 5 minutes (and a node would look stalled): in_block() leaves out the steps more
# than a minute after Prometheus's last scrape of itself, which also leaves out the steps of a
# monitoring outage.
LIVE = '(time() - max(timestamp(up{job="prometheus"})) < 60)'
BOARD_PANELS = {20, 21, 25, 216}

# FibreNotServing (uploads_failing) reads a 5-minute window before its hold.
UPLOAD_WINDOW = 300

# Plain sentences for the alerts, for the alert list and the red regions on the charts; a notification
# carries the rule's own summary instead, and the two share the alert name and the host. Each entry also
# has a short name for the Status cell: (key, sentence, short, part, needs). The key is a regex over
# alertname:severity:unit:reason:check:host of a series grouped by by_alert(); Prometheus anchors it,
# and $1 is the host at the end of the key. short: the Status cell's words, without a host, the same
# words the tiles, cards and lines use for the state, at most what the cell holds (the intro strip's
# fit check). part: what the item is about, which the Status cell's label line names ("Status ·
# Fibre"), one of PARTS. needs: the switch the alert needs (Setup.has).
P_VAL, P_SIGNING, P_SIGNER, P_FIBRE, P_SERVERS, P_MON, P_UPGRADE, P_GOV, P_CELESTIA = (
    "Validator", "Signing", "Signer", "Fibre", "Servers", "Monitoring", "Upgrade", "Governance", "Celestia")
PARTS = (P_VAL, P_SIGNING, P_SIGNER, P_FIBRE, P_SERVERS, P_MON, P_UPGRADE, P_GOV, P_CELESTIA)
TITLES = [
    ("NodeNotAdvancing:.*", "No new blocks on any node", "Nodes stalled", P_VAL, None),
    ("ValidatorNotSigning:.*", "Validator not signing", "Not signing", P_VAL, None),
    ("ValidatorSigningTwice:.*", "Validator signs through two nodes", "Two signers", P_SIGNING, "two_signers"),
    ("SignaturesMissed:.*", "Validator missed blocks", "Missed blocks", P_VAL, None),
    ("NodeBehind:.*:([^:]*)", "Node behind on $1", "Node behind", P_SERVERS, None),
    ("NodePeersLow:page::::([^:]*)", "Node without peers on $1", "No peers", P_SERVERS, None),
    ("NodePeersLow:message::::([^:]*)", "Node with few peers on $1", "Few peers", P_SERVERS, None),
    ("RemoteSignerProblem:message::::.*", "Remote signer slow", "Signer slow", P_SIGNER, "remote"),
    ("RemoteSignerProblem:message::[^:]+::.*", "Remote signer fails proposals", "Bad proposals", P_SIGNER, "remote"),
    ("FibreDown:page::::", "No Fibre server running", "Not running", P_FIBRE, "fibre"),
    ("FibreDown:page:::reachability:.*", "Fibre unreachable from outside", "Unreachable", P_FIBRE, "tensile"),
    ("FibreDown:page::not_registered:registration:.*", "No Fibre host registered on chain", "Not registered",
     P_FIBRE, "tensile"),
    ("FibreDown:page::wrong_host:registration:.*", "Chain points to another Fibre host", "Wrong host", P_FIBRE,
     "tensile"),
    ("FibreBothActive:.*", "Fibre running on two servers", "Two Fibres", P_FIBRE, "fibre"),
    ("FibreNotServing:message::missed_uploads::([^:]*)", "Fibre missed uploads on $1", "Uploads missed", P_FIBRE,
     "tensile"),
    ("FibreNotServing:message::not_served::([^:]*)", "Fibre did not serve rows on $1", "Rows not served", P_FIBRE,
     "tensile"),
    ("FibreNotServing:message::uploads_failing::([^:]*)", "Fibre uploads fail on $1", "Uploads failing", P_FIBRE,
     "fibre"),
    ("FibreNotServing:message::no_telemetry::([^:]*)", "No Fibre metrics on $1", "No Fibre data", P_FIBRE, "fibre"),
    # Fibre's own health check: a message after 3 minutes, a page after 10, one sentence for both.
    ("FibreNotServing:[a-z]+::not_ready::([^:]*)", "Fibre not ready on $1", "Not ready", P_FIBRE, "fibre"),
    ("ServiceDown:message:node_exporter:::([^:]*)", "$1 not reporting", "Not reporting", P_SERVERS, None),
    ("ServiceDown:message:celestia_app:::([^:]*)", "Node metrics lost on $1", "No node data", P_SERVERS, None),
    ("ServiceDown:message:otelcol:::([^:]*)", "Fibre metrics lost on $1", "No Fibre data", P_FIBRE, "fibre"),
    ("ServiceDown:message:appd:::([^:]*)", "Celestia node stopped on $1", "Node stopped", P_SERVERS, None),
    ("ServiceDown:message:appd:crashloop::([^:]*)", "Node keeps restarting on $1", "Node crashing", P_SERVERS, None),
    ("ServiceDown:message:appd:not_loaded::([^:]*)", "Node service missing on $1", "Node missing", P_SERVERS, None),
    ("ServiceDown:message:tmkms:::([^:]*)", "Remote signer stopped on $1", "Signer down", P_SIGNER, "tmkms"),
    ("ServiceDown:message:tmkms:crashloop::([^:]*)", "Signer keeps restarting on $1", "Signer crash", P_SIGNER,
     "tmkms"),
    ("ServiceDown:message:tmkms:not_loaded::([^:]*)", "Signer service missing on $1", "No signer", P_SIGNER, "tmkms"),
    ("ServiceDown:message:fibre:crashloop::([^:]*)", "Fibre keeps restarting on $1", "Fibre crash", P_FIBRE, "fibre"),
    ("ServiceDown:message:blackbox:::([^:]*)", "Fibre health check not read on $1", "Probe stopped", P_FIBRE, "fibre"),
    ("DiskLow:page::::([^:]*)", "Disk full or read-only on $1", "Disk critical", P_SERVERS, None),
    ("DiskLow:message::::([^:]*)", "Disk low or read-only on $1", "Disk low", P_SERVERS, None),
    ("RaidDegraded:.*:([^:]*)", "RAID degraded on $1", "RAID degraded", P_SERVERS, None),
    ("ClockNotSynced:.*:([^:]*)", "Clock not synchronised on $1", "No time sync", P_SERVERS, None),
    ("MonitorCannotReach:message::::([^:]*)", "Monitor cannot reach $1", "Not reached", P_SERVERS, "reached"),
    ("MonitorCannotReach:page::::", "Monitor cannot reach any server", "Not reached", P_SERVERS, "monitor_only"),
    ("MonitoringBroken:.*", "Alert rules failing", "Rules failing", P_MON, None),
    ("NotificationsFailing:.*", "Alerts not delivered", "Alerts unsent", P_MON, None),
    ("DeadmanFailing:.*", "Dead-man service not reached", "No heartbeat", P_MON, None),
    ("TensileUnreadable:.*", "Outside check stopped", "Check stopped", P_FIBRE, "tensile"),
    ("AnnouncementsUnreadable:.*", "Announcements not read", "Feed stopped", P_MON, "announcements"),
    ("UpgradeSignalMissing:.*", "Upgrade signal needed", "Not signalled", P_UPGRADE, "signal"),
    ("UpgradeScheduled:.*", "Network upgrade scheduled", "Upgrade set", P_UPGRADE, "signal"),
    ("UpgradeSoon:.*", "Network upgrade within 24 hours", "Upgrade soon", P_UPGRADE, "signal"),
    ("SignalUnreadable:.*", "Upgrade check stopped", "Signal stale", P_UPGRADE, "signal"),
    # The vote check (gov): the page only where it is on (gov_page). With the check on, SignalUnreadable
    # also covers the proposals, and the later key wins.
    ("GovVoteMissing:page:.*", "Validator has not voted on a proposal", "Vote missing", P_GOV, "gov_page"),
    ("GovVoteMissing:message:.*", "Validator has not voted on a proposal", "Vote missing", P_GOV, "gov"),
    ("SignalUnreadable:.*", "Upgrade and vote check stopped", "Signal stale", P_GOV, "gov"),
]
# The Status cell's words for an alert whose labels match no key of TITLES (its label line then
# reads "Status" alone).
SHORT_FALLBACK = "See alerts"
# Message alerts with a host label that colour a line of their own (or, the monitoring's, the
# monitor's line in their own words), not the generic warning of a server's line (and of the Servers
# tile).
SERVER_OTHERS = ("RemoteSignerProblem|FibreNotServing|MonitoringBroken|NotificationsFailing|DeadmanFailing|"
                 "TensileUnreadable|AnnouncementsUnreadable|SignalUnreadable|UpgradeSignalMissing|UpgradeScheduled|"
                 "UpgradeSoon|GovVoteMissing")
# The heartbeat goes to the dead-man service only and is never shown as an alert.
UNTITLED = {"Watchdog"}
# The Status cell's panel id.
STATUS_ID = 10
# The alerts that no tile, History line, role band lane or server card names (check_surfaces), each
# with the reason. Every other alert of the rules must have such a place, so the first screen shows
# its state with the alert's own words, and a query change cannot cut the link unnoticed.
NO_SURFACE = {
    "UpgradeScheduled": "a scheduled upgrade is no fault: the Network upgrade tile shows it, its version, height "
                        "and time to go, from the same signalling metrics the rule reads",
}
# Worst first between alerts of one level, so the order stays the same between refreshes.
PRIORITY = {"ValidatorSigningTwice": 0.095, "NodePeersLow": 0.092, "NodeNotAdvancing": 0.09,
            "ValidatorNotSigning": 0.08, "FibreDown": 0.07,
            "DiskLow": 0.06, "MonitorCannotReach": 0.055, "UpgradeSignalMissing": 0.05, "GovVoteMissing": 0.045,
            "ServiceDown": 0.04, "SignaturesMissed": 0.035, "RemoteSignerProblem": 0.03, "FibreNotServing": 0.025,
            "NodeBehind": 0.02, "RaidDegraded": 0.018, "FibreBothActive": 0.015, "UpgradeSoon": 0.012,
            "ClockNotSynced": 0.01}
# A message alert's rank where it differs from its page's. NodePeersLow's message: few peers usually follow
# a node it peers with that stopped, so it ranks below that ServiceDown and below missed blocks, and above
# the lag the few peers cause (NodeBehind). GovVoteMissing's message: a vote with up to 2 days left, below
# every fault of the servers, above a clock out of sync.
MESSAGE_PRIORITY = {"NodePeersLow": 0.022, "GovVoteMissing": 0.011}

# The Role column: a server's parts add up to one code. (code, words, short words).
NODE_PARTS = [(1, "Signing node", "Signing"), (2, "Backup node", "Backup"), (3, "Node, not signing", "Not signing")]
FIBRE_PARTS = [(4, "Fibre active", "Fibre"), (8, "Fibre standby", "Fibre standby")]
KMS_PARTS = [(16, "Remote signer", "Remote signer"), (32, "Signer down", "Signer down"), (64, "No signer", "No signer")]


def q(value):
    """A PromQL string literal."""
    return json.dumps(value)


def num(value):
    """A number as PromQL and Grafana expect it."""
    return json.dumps(value)


def js_regex(text):
    """text with JavaScript regex metacharacters escaped."""
    return re.sub(r"[\\^$.*+?()\[\]{}|/]", lambda m: "\\" + m.group(0), text)


def re2(text):
    """text with the metacharacters of Prometheus's regular expressions (RE2) escaped."""
    return re.sub(r"[\\^$.*+?()\[\]{}|]", lambda m: "\\" + m.group(0), text)


def devices(host, left_out):
    """The selector of host's devices, less those whose name matches the regex left_out (VIRT, DISK)."""
    return "{host=%s,device!~%s}" % (q(host), q(left_out))


def label(expr, name, value):
    """expr with the label name set to the fixed text value ('$' kept as it is)."""
    return 'label_replace(%s, %s, %s, "", "")' % (expr, q(name), q(value.replace("$", "$$")))


def chain(*branches):
    """The branches joined with 'or', leaving out None: the first branch with a sample wins."""
    return " or ".join(b for b in branches if b)


def target(ref, expr, legend, instant=False, interval=None, fmt=None):
    """A Prometheus query. interval: Grafana's minimum step for it, e.g. "5m".
    fmt: "table" for a table panel's query."""
    t = {"refId": ref, "expr": expr, "legendFormat": legend, "instant": instant, "range": not instant}
    if interval:
        t["interval"] = interval
    if fmt:
        t["format"] = fmt
    return t


def refs(exprs, instant=False):
    """Targets with refIds A, B, C, ... from (expr, legend) pairs."""
    return [target(chr(65 + i) if i < 26 else "R%d" % i, expr, legend, instant)
            for i, (expr, legend) in enumerate(exprs)]


def steps(*pairs):
    """Threshold steps from (color, value) pairs; the first value is the base (None)."""
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in pairs]}


def mapping(*items):
    """Value mappings from (value, text, color) triples; color None keeps the field's colour."""
    options = {}
    for i, (v, t, c) in enumerate(items):
        options[str(v)] = {"index": i, "text": t}
        if c:
            options[str(v)]["color"] = c
    return [{"type": "value", "options": options}]


def range_map(low, high, text, color=None):
    """A value mapping for every value from low to high."""
    result = {"index": 0, "text": text}
    if color:
        result["color"] = color
    return [{"type": "range", "options": {"from": low, "to": high, "result": result}}]


def special_map(match, text, color=None):
    """A value mapping for a special value: "null" (no value) or "nan"."""
    result = {"index": 0, "text": text}
    if color:
        result["color"] = color
    return [{"type": "special", "options": {"match": match, "result": result}}]


def regex_map(pattern, color):
    """A value mapping that colours text matching pattern and keeps the text."""
    return [{"type": "regex", "options": {"pattern": pattern, "result": {"color": color, "index": 0}}}]


def lane_map(words):
    """A status line's or role lane's mappings: its words, and grey "No data" for an empty time block.
    An override's mappings replace the panel's, so every lane repeats the "No data" entry."""
    return mapping(*words) + special_map("null", "No data", NODATA)


def override(matcher, option, **props):
    """A field override. Property names use '__' for '.', e.g. custom__axisPlacement."""
    return {"matcher": {"id": matcher, "options": option},
            "properties": [{"id": k.replace("__", "."), "value": v} for k, v in props.items()]}


def fixed(color):
    """A fixed series colour."""
    return {"fixedColor": color, "mode": "fixed"}


def dash(pattern):
    """A dashed line style."""
    return {"dash": pattern, "fill": "dash"}


def more(alerts=(), pages=()):
    """The end of a description: markdown links to each alert's entry in the alert reference and to
    other pages, as (text, url). Grafana shows them in the panel's (i) tooltip."""
    parts = ["[%s](%s#%s)" % (a, DOCS, a.lower()) for a in alerts] + ["[%s](%s)" % p for p in pages]
    return " More: %s." % ", ".join(parts) if parts else ""


def panel(pid, kind, title, desc, targets, defaults, overrides, options):
    return {"id": pid, "type": kind, "title": title, "description": desc, "targets": targets,
            "fieldConfig": {"defaults": defaults, "overrides": list(overrides)}, "options": options}


def stat(pid, title, desc, targets, unit="none", decimals=0, thresholds=None, mappings=(), no_value=None,
         overrides=(), size=FACT_PX, text_mode="value", color_mode="value", graph="none", justify="center",
         orientation="auto", wide=True, title_size=None):
    """Current values in one pinned text size (size px), coloured by thresholds (by default a single
    step in the text colour) or by a value mapping. graph "area" draws a sparkline over the dashboard
    range (needs range queries). text_mode "value_and_name" stacks the series name (title_size px)
    over the value; in the other modes the panel's own title is the label. decimals None lets
    Grafana choose."""
    defaults = {"color": {"mode": "thresholds"}, "thresholds": thresholds or steps(("text", None)), "unit": unit}
    if decimals is not None:
        defaults["decimals"] = decimals
    if no_value is not None:
        defaults["noValue"] = no_value
    if mappings:
        defaults["mappings"] = list(mappings)
    text = {"valueSize": size}
    if title_size:
        text["titleSize"] = title_size
    options = {"colorMode": color_mode, "graphMode": graph, "justifyMode": justify, "orientation": orientation,
               "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
               "showPercentChange": False, "text": text, "textMode": text_mode, "wideLayout": wide}
    return panel(pid, "stat", title, desc, targets, defaults, overrides, options)


def timeseries(pid, title, desc, targets, unit, threshold=None, scheme=False, soft_max=None, soft_min=None,
               minimum=0, maximum=None, decimals=None, step=False, smooth=False, span_nulls=False, centered=False,
               overrides=(), mirror=(), legend=True, bars=False, stack=False, fill=None, low_bad=False,
               color=NETWORK):
    """Filled lines over time: 2 px lines over a fill that fades downwards, in color unless an
    override (host_colors, a fixed part colour) gives a series its own. threshold: (value, colour)
    of an alert rule's threshold, drawn as a dashed line at the value and nothing more, so a healthy
    chart carries no block of warning colour (low_bad: the alert holds below the value; the line is
    the same); scheme: a single series that takes the threshold's colour where it crosses it. soft_max,
    soft_min: the range the axis always shows (a soft max stops Grafana's 0..100 axis on flat data);
    minimum, maximum: hard limits, None for none. step: counts drawn as steps; smooth: smooth curves.
    span_nulls: False, or the longest gap in ms a line bridges. centered: zero in the middle of the
    axis. mirror: refIds drawn below zero (receive above, transmit below). bars: events per time
    bucket as bars, so a quiet chart is empty and a single event shows. stack: the series are parts
    of a whole, stacked in flat fills (a total drawn over them needs an override with
    custom__stacking={"mode": "none", "group": "A"}). fill: the fill opacity, when the default does
    not suit. legend False: one series, without a legend."""
    custom = {"axisBorderShow": False, "axisWidth": VALUE_AXIS_PX, "drawStyle": "bars" if bars else "line",
              "fillOpacity": 20, "gradientMode": "opacity",
              "lineInterpolation": "stepAfter" if step else "smooth" if smooth else "linear",
              "lineWidth": 2, "showPoints": "never", "spanNulls": span_nulls,
              "thresholdsStyle": {"mode": "dashed" if threshold else "off"}}
    if bars:
        custom.update({"barAlignment": 0, "barWidthFactor": 0.7, "fillOpacity": 80, "gradientMode": "none",
                       "lineWidth": 1})
    if stack:
        custom.update({"fillOpacity": 45, "gradientMode": "none", "lineWidth": 1,
                       "stacking": {"group": "A", "mode": "normal"}})
    if fill is not None:
        custom["fillOpacity"] = fill
    if soft_max is not None:
        custom["axisSoftMax"] = soft_max
    if soft_min is not None:
        custom["axisSoftMin"] = soft_min
    if centered or mirror:
        custom["axisCenteredZero"] = True
    defaults = {"color": fixed(color), "custom": custom, "thresholds": steps(("text", None)), "unit": unit}
    if threshold:
        value, tone = threshold
        if scheme:
            defaults["color"] = {"mode": "thresholds"}
            custom["gradientMode"] = "scheme"
            defaults["thresholds"] = steps((color, None), (tone, value))
        else:
            # Grafana draws a line at every step but the base one, in that step's colour.
            defaults["thresholds"] = steps(("transparent", None), (tone, value))
    if minimum is not None and not mirror and not centered:
        defaults["min"] = minimum
    if maximum is not None:
        defaults["max"] = maximum
    if decimals is not None:
        defaults["decimals"] = decimals
    if mirror:
        overrides = [override("byFrameRefID", r, custom__transform="negative-Y") for r in mirror] + list(overrides)
    options = {"legend": {"calcs": [], "displayMode": "list", "placement": "bottom", "showLegend": legend},
               "tooltip": {"mode": "multi", "sort": "desc"}}
    return panel(pid, "timeseries", title, desc, targets, defaults, overrides, options)


def grid_px(height):
    """Pixel height of a panel height grid rows tall."""
    return 38 * height - 8


def grid_rows(content, chrome):
    """Grid rows a panel needs to show content px under chrome px of panel chrome."""
    return int(math.ceil((content + chrome + 8) / 38.0))


def inner_px(width):
    """Content width in px of a panel width grid columns wide, at 1440 px with the menu docked."""
    return 46.25 * width - 25


def text_px(text, size, letter=CHAR_EM):
    """Estimated width in px of text at size px: digits DIGIT_EM, separators SEP_EM, the rest letter."""
    return size * sum(DIGIT_EM if c.isdigit() else SEP_EM if c in ".," else letter for c in text)


def value_px(text, size):
    """Width in px of a strip or tile value at size px, letter by letter (INTER_500)."""
    return size * sum(INTER_500.get(c, CHAR_EM * 100) for c in text) / 100.0


def value_room(width):
    """Room in px for a value in a strip cell or tile width columns wide."""
    return inner_px(width) - 2 * VALUE_PAD


def stack_px(label_size, value_size, centred):
    """Height in px of a label over a value (a stat's value_and_name stack, or a bar gauge's name over
    its bar); a centred stack adds a gap between them."""
    return LINE_EM * (label_size + value_size) + (STACK_GAP_PX if centred else 0)


def check_fit(what, texts, size, width):
    """Stops unless every text fits width px of content at size px."""
    for t in texts:
        if text_px(t, size) > inner_px(width):
            raise SystemExit("%s: %r needs %d px at %d px, and its panel has %d" % (what, t, text_px(t, size), size,
                                                                                      inner_px(width)))


def seconds(text):
    """Seconds in a duration such as 10m, 1h30m or 90s."""
    parts = re.findall(r"(\d+)([hms])", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        raise SystemExit("%s: %r is not a duration such as 10m" % (HOLDS_FILE, text))
    return sum(int(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in parts)


def minutes_text(secs):
    """A duration in seconds as text: "1 minute", "10 minutes"."""
    m = secs / 60.0
    return "%g minute%s" % (m, "" if m == 1 else "s")


def retention_days(text):
    """Whole days in a Prometheus duration such as 30d, 720h or 4w (at least 1)."""
    text = str(text).strip()
    units = {"y": 365 * 86400, "w": 7 * 86400, "d": 86400, "h": 3600, "m": 60, "s": 1, "ms": 0.001}
    parts = re.findall(r"(\d+)(ms|[ywdhms])", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        raise SystemExit("celestia_mon_prometheus_retention %r is not a Prometheus duration such as 30d" % text)
    return max(1, int(sum(int(n) * units[u] for n, u in parts) // 86400))


@functools.lru_cache(maxsize=None)
def rules_text():
    """The text of the rules template, read once."""
    with open(RULES_TEMPLATE, encoding="utf-8") as f:
        return f.read()


def load_rules():
    """(holds, alert names): the hold times in monitoring/rules/holds.json by name, and the name of
    every alert in the rules template."""
    with open(HOLDS_FILE, encoding="utf-8") as f:
        holds = json.load(f)["holds"]
    names = sorted(set(re.findall(r"^\s*- alert: (\w+)", rules_text(), re.M)))
    return holds, names


class Setup:
    """The settings, with what the panels derive from them.

    remote, multi (tmkms signs through several nodes on purpose), twice (ValidatorSigningTwice is
    on: remote, not multi), fibre, tensile, signal, gov (the vote check, with signal), gov_page (its
    page), announcements and mon_only (the monitor on a host of its own) are the switches. nodes are the
    celestia-appd hosts, the validator first; fibre_hosts every Fibre host; kms_hosts the KMS hosts
    whose node_exporter runs, with a remote signer; servers the nodes, then the other node_exporter
    hosts (the monitor's among them), in inventory order: the order of the status board, the table
    and the host colours. data_mounts and stores are the disks DiskLow watches as data mounts and as
    Fibre stores, as the rules template picks them. A host's systemd unit names are its entry in
    host_units, else units (unit()); a query matches a unit under every name the hosts of its role use
    (is_unit()), as the alert rules do."""

    def __init__(self, cfg):
        t = cfg["thresholds"]
        self.ds = {"type": "prometheus", "uid": cfg["datasource_uid"]}
        self.name = (cfg.get("name") or "").strip()
        self.remote = cfg["signer"] == "remote"
        self.multi = self.remote and bool(cfg.get("signer_multi_node"))
        # A remote signer through one node: two signers is a fault there (ValidatorSigningTwice).
        self.twice = self.remote and not self.multi
        self.fibre = bool(cfg["fibre_enabled"])
        self.tensile = self.fibre and bool(cfg.get("tensile_enabled"))
        self.signal = bool(cfg.get("signal_enabled"))
        # The vote check comes with upgrade signalling; its page only where it is on.
        self.gov = self.signal and bool(cfg.get("gov_enabled"))
        self.gov_page = self.gov and bool(cfg.get("gov_page"))
        self.announcements = bool(cfg.get("announcements_enabled"))
        self.monitor = cfg.get("monitor_host") or ""
        self.retention = str(cfg.get("prometheus_retention") or "30d")
        self.days = min(30, retention_days(self.retention))
        self.nodes = list(cfg.get("nodes", []))
        if not self.nodes:
            raise SystemExit("the settings list no celestia-appd node (nodes)")
        self.links = list(cfg["links"])
        hosts = [link["host"] for link in self.links]
        self.servers = self.nodes + [h for h in hosts if h not in self.nodes]
        # The monitor on a host of its own (no Celestia service): its Prometheus reaches every other
        # server over the network, and its own node_exporter answers even while it reaches none of them,
        # so node_up, "a server reports", leaves it out. reached: the servers MonitorCannotReach watches.
        self.mon_only = bool(cfg.get("monitor_only")) and self.monitor in self.servers
        self.reached = [h for h in self.servers if h != self.monitor]
        self.node_up = 'up{job="node",host!=%s}' % q(self.monitor) if self.mon_only else 'up{job="node"}'
        self.fibre_mounts = list(cfg.get("fibre_mounts", [])) if self.fibre else []
        self.fibre_hosts = [m["host"] for m in self.fibre_mounts]
        if self.fibre and not self.fibre_hosts:
            raise SystemExit("celestia_mon_fibre_enabled is true, but no host is in the fibre group")
        self.fibre_endpoints = [(e["host"], e["endpoint"]) for e in cfg.get("fibre_endpoints", [])
                                if self.fibre and e.get("endpoint")]
        scraped = [h for h in cfg.get("kms_hosts", []) if h in hosts]
        self.kms_hosts = scraped if self.remote else []
        # The KMS hosts no node_exporter runs on: they only name the remote signer's column.
        self.kms_names = [h for h in cfg.get("kms_names", []) if h not in scraped] if self.remote else []
        # DiskLow pages on the data mounts of node and KMS hosts; a Fibre store has rules of its own
        # unless it is also a data mount of such a host.
        stores = {m["host"]: m["mount"] for m in self.fibre_mounts if m["mount"]}
        paging = set(self.nodes) | set(scraped)
        self.data_mounts = [(d["host"], d["mount"]) for d in cfg.get("data_mounts", [])
                            if d["host"] in paging or d["mount"] != stores.get(d["host"])]
        self.stores = [(h, m) for h, m in stores.items() if not (h in paging and (h, m) in self.data_mounts)]
        self.units = {"appd": cfg["units"]["appd"] + ".service",
                      "fibre": cfg["units"].get("fibre", "") + ".service",
                      "tmkms": cfg["units"].get("tmkms", "") + ".service"}
        # The hosts whose unit names differ from units, with their own (unit()).
        self.host_units = {u["host"]: {k: u.get(k, "") + ".service" for k in self.units}
                           for u in cfg.get("host_units", [])}
        self.N = t["not_signing_blocks"]
        self.R = t.get("signatures_missed_ratio", 0.05)
        self.NB = t.get("node_behind_blocks", 20)
        self.PM = t.get("peers_message_below", 2)
        self.PP = t.get("peers_page_below", 1)
        self.S = t["signer_median_seconds"]
        self.DP = t["disk_page_free_ratio"]
        self.DM = t["disk_message_free_ratio"]
        self.CL = t["crashloop_restarts"]
        self.holds, self.alert_names = load_rules()

    def has(self, need):
        """Whether the alerts that need need exist in this install (None: always)."""
        return {None: True, "remote": self.remote, "two_signers": self.twice, "fibre": self.fibre,
                "tensile": self.tensile, "signal": self.signal, "gov": self.gov, "gov_page": self.gov_page,
                "announcements": self.announcements,
                "tmkms": bool(self.kms_hosts), "reached": bool(self.reached), "monitor_only": self.mon_only}[need]

    def color(self, host):
        """(named colour, hex) of a server's identity colour; the text colour past the list."""
        i = self.servers.index(host) if host in self.servers else len(HOST_COLORS)
        return HOST_COLORS[i] if i < len(HOST_COLORS) else ("text", None)

    def tint(self, host):
        """A server's identity colour at 35% alpha, for the role band: enough that the servers' segments
        tell apart at a glance, while the band's white words keep at least 7:1 on every HOST_COLORS tint
        (Grafana writes them white while they keep 3:1)."""
        hexa = self.color(host)[1]
        return hexa + "59" if hexa else GREY + "59"

    def hold(self, name, extra=0):
        """How long after its condition a rule fires, as text, from monitoring/rules/holds.json, plus
        extra seconds of the rule's own window."""
        if name not in self.holds:
            raise SystemExit("%s has no entry %r, which the dashboard prints" % (HOLDS_FILE, name))
        return minutes_text(seconds(self.holds[name]["for"]) + extra)

    def ms(self):
        """The signer's median limit in milliseconds, as text."""
        return "%g" % (self.S * 1000)

    def unit(self, host, key):
        """The unit of key (appd, fibre or tmkms) on host, with .service: its own name, else the shared one."""
        return self.host_units.get(host, self.units)[key]

    def names(self, key):
        """The unit names of key on the hosts of its role (the nodes, the Fibre hosts, the scraped KMS hosts),
        sorted; the shared name alone where the role has no host."""
        hosts = {"appd": self.nodes, "fibre": self.fibre_hosts, "tmkms": self.kms_hosts}[key]
        return sorted({self.unit(h, key) for h in hosts}) or [self.units[key]]

    def is_unit(self, key):
        """The matcher on the name label of key's unit: name="x" where the hosts of its role share one name,
        as on most installs, else name=~ over every name, as the alert rules match it."""
        names = self.names(key)
        return "name=%s" % q(names[0]) if len(names) == 1 else "name=~%s" % q("|".join(re2(n) for n in names))


def lookup(hosts):
    """Each host's place in hosts (1, 2, ...) as one series per host: a value mapping turns the
    place back into the name, and a table sorts its rows by it."""
    return "(%s)" % " or ".join(label("vector(%d)" % (i + 1), "host", h) for i, h in enumerate(hosts))


def link_capacity(link, sel):
    """Bytes/s the link carries: celestia_mon_link_mbps when set, else the summed NIC port speeds.
    node_exporter reports -125000 for a NIC of unknown speed, so only positive speeds count."""
    if link.get("mbps") is None:
        return "on (host) sum by (host) (node_network_speed_bytes%s > 0)" % sel
    try:
        rate = float(link["mbps"]) * 125000
    except (TypeError, ValueError):
        rate = 0
    if rate <= 0:
        raise SystemExit("celestia_mon_link_mbps of %s must be a positive number (Mbit/s), not %r"
                         % (link["host"], link["mbps"]))
    return num(int(rate) if rate == int(rate) else rate)


def missed_in(window):
    """Blocks the validator missed in the last window, per series: the count now minus its lowest
    value in the window. The missed-block count is a counter without a _total suffix, and Prometheus
    3 notes increase() over such a name. After a node restart the count starts again from 0, so this
    counts the misses since the restart; a series new to Prometheus (a fresh install, a renamed host,
    a gap) counts from its first sample, never its whole value."""
    return "(%s - min_over_time(%s[%s]))" % (MISSED, MISSED, window)


def count_24h(expr, agg, job="celestia-app", alive=None):
    """How much a counter (expr) of job's targets grew over the last 24 hours, or since monitoring
    started when that is less, as a whole number. Each target is read once a minute, combined per host
    with agg (sum or max); a target that is up but has not counted yet reads 0, so a counter that
    appears with its first increment counts from 0, and a restart counts as a reset, never as a drop.
    A plain "now minus 24 hours ago" would count from the process start when Prometheus holds less
    than a day, and lose the count before a restart.
    alive: for a counter that a collector passes on (Fibre's, through the OpenTelemetry collector), one
    series per host that is there exactly when the counter can be, such as a gauge of the same process.
    The 0 is then keyed on it rather than on the collector's up: after a restart of the collector or of
    Fibre the collector is up while Fibre's series are missing, and they come back at their running
    totals, which a 0 in that gap would count as growth. With alive the count has no value while the
    counter never could (the panel's no-value text speaks), rather than 0."""
    key = alive or "max by (host) (up{job=%s} == 1)" % q(job)
    count = "round(%s(increase((%s by (host) (%s) or on (host) (%s * 0))[24h:1m])))" % (agg, agg, expr, key)
    return count if alive else count + " or on () vector(0)"


def missed_in_block():
    """missed_in() for a status bar's time block: its last sample minus its lowest one."""
    return "(%s - %s)" % (in_block("last_over_time", MISSED), in_block("min_over_time", MISSED))


def blocks_since_signature(cap):
    """Blocks since the validator's last signature on any node, as ValidatorNotSigning computes
    it, capped at cap: the highest height of all nodes minus the highest last signed height.
    Only the node the signer is connected to reports a last signed height, so the value follows
    the signer from node to node. Until the first signature after a start, it is the full height."""
    return "clamp_max(%s, %s)" % (BLOCKS_SINCE, num(cap))


def signing_nodes(s, window="[2m]"):
    """The node or nodes the validator signs through, one series per host. With a remote signer:
    the nodes whose privval client signed a vote or a proposal in the window. A node that tmkms left
    keeps the validator's key and reads its signatures from the chain's commits, so the last signed
    height cannot tell which node signs. With a local key: the node whose last signed height
    advanced in the window. window: a range, with an offset if needed, e.g. "[2m] offset 2m"."""
    if s.remote:
        return "(sum by (host) (increase(%s%s)) > 0)" % (PV, window)
    return "(max by (host) (changes(%s%s)) > 0)" % (LSH, window)


def fibre_via(s, window):
    """The node Fibre's signatures (raw bytes) went through in the window, such as 15m, as its place in
    s.nodes."""
    return "max(%s and on (host) (sum by (host) (increase(%s[%s])) > 0))" % (lookup(s.nodes), RAW, window)


def newer(s):
    """A node that began signing in the last 2 minutes (a tmkms repoint)."""
    return "(%s unless on (host) %s)" % (signing_nodes(s), signing_nodes(s, "[2m] offset 2m"))


def two_signers(s, state=""):
    """ValidatorSigningTwice now, pending or firing (state "firing": firing only), as one series without
    labels. Every surface that says "Two signers" reads the alert itself, so none can disagree with
    the page; a tmkms repoint never matches it (the rule needs signatures on both nodes in two windows)."""
    sel = ',alertstate="%s"' % state if state else ""
    return 'max(ALERTS{alertname="ValidatorSigningTwice"%s})' % sel


def twice10(s):
    """10 while ValidatorSigningTwice fires, else 0: added to a code, it turns the amber word red."""
    return "(%s * 10 or vector(0))" % two_signers(s, "firing")


def fibre_active(s):
    """One series per host whose Fibre unit systemd reports active, labelled host only."""
    return '(max by (host) (node_systemd_unit_state{%s,state="active"} == 1))' % s.is_unit("fibre")


def fibre_seen(s):
    """fibre_active, plus a host that sends Fibre metrics while its node_exporter does not answer:
    its Fibre runs, only its unit state cannot be read."""
    return ("(%s or ((max by (host) (fibre_server_upload_shard_budget_bytes) * 0 + 1) "
            'unless on (host) (max by (host) (up{job="node"}) == 1)))' % fibre_active(s))


def fibre_lost(s):
    """The Fibre hosts whose unit state cannot be read (node_exporter does not answer)."""
    return '(%s unless on (host) (max by (host) (up{job="node"}) == 1))' % lookup(s.fibre_hosts)


def wrong_host(s):
    """The registration names another endpoint than the active Fibre host's public endpoint: FibreDown's
    registration rule, one term per Fibre host with an endpoint, from a read of Tensile under 9 minutes
    old, and not while two Fibres run."""
    return "((%s) unless on () (count(%s) > 1))" % (" or ".join(
        "(max by (endpoint) (celestia_mon_fibre_registered{endpoint!=%s} == 1) and on () "
        '(max(node_systemd_unit_state{host=%s,name=%s,state="active"}) == 1) and on () '
        "(time() - celestia_mon_tensile_last_success_timestamp_seconds < 540))" % (q(e), q(h), q(s.unit(h, "fibre")))
        for h, e in s.fibre_endpoints), fibre_active(s))


def last(s, metric):
    """The last value of a celestia-mon-signal metric in the past 7 days, as the upgrade rules read it."""
    return "max(last_over_time(%s{job=\"signal\",host=%s}[7d]))" % (metric, q(s.monitor))


def gov(s, metric):
    """Each proposal's last value of a celestia-mon-signal vote metric in the past hour, as GovVoteMissing
    reads it."""
    return "max by (proposal_id) (last_over_time(%s{job=\"signal\",host=%s}[1h]))" % (metric, q(s.monitor))


def upgrade_title(s):
    """The name of the upgrade tile: Governance with the vote check on, which it then shows too."""
    return "Governance" if s.gov else "Network upgrade"


def disks(mounts, metric):
    """metric of each (host, mount point) of mounts, joined with 'or'."""
    return "(%s)" % " or ".join("%s{host=%s,mountpoint=%s}" % (metric, q(h), q(m)) for h, m in mounts)


def free_share(mounts):
    """The free share of each (host, mount point) of mounts, joined with 'or'."""
    return "(%s)" % " or ".join("(node_filesystem_avail_bytes{host=%s,mountpoint=%s} / node_filesystem_size_bytes"
                                "{host=%s,mountpoint=%s})" % (q(h), q(m), q(h), q(m)) for h, m in mounts)


# The status board reads Prometheus's own alert states per time block.

def in_block(fn, x, sub=False):
    """fn (an _over_time function, increase or changes) over x in the bar's time block, per series:
    x a series selector, or with sub an expression read in 1-minute steps (a subquery) without the
    steps past the data (LIVE). Until the block has started (STARTED), the 2 minutes before it count
    too, so the newest bar is never grey or amber only because its block has just begun."""
    if sub:
        x = "(%s and on () %s)" % (x, LIVE)
        return "(%s(%s%s) or (%s(%s[2m:1m]) unless on () %s))" % (fn, x, WS, fn, x, STARTED)
    return "(%s(%s%s) or (%s(%s[2m]) unless on () %s))" % (fn, x, W, fn, x, STARTED)


def firing(sel):
    """An alert matching sel fired at some moment of the bar's time block."""
    return "(max(%s))" % in_block("max_over_time", 'ALERTS{alertstate="firing",%s}' % sel)


def fired_apart(sel, other):
    """An alert matching sel fired at a minute of the bar's time block in which none matching other did.
    It reads 1-minute steps, so firing() still shows a page shorter than a step; this keeps a page apart
    from an alert that fired at another moment of a long block."""
    return "(max(%s))" % in_block(
        "max_over_time", '(ALERTS{alertstate="firing",%s} unless on () ALERTS{alertstate="firing",%s})' % (sel, other),
        sub=True)


def prom_started():
    """Prometheus started in the bar's time block."""
    return "(max(%s) > time())" % in_block("max_over_time", 'process_start_time_seconds{job="prometheus"}')


def active(sel):
    """An alert matching sel was pending or firing at some moment of the bar's time block, except the
    block in which Prometheus started: its first evaluations there can leave a short pending with no
    fault behind it (a firing alert still shows, through firing())."""
    return "(max(%s) unless on () %s)" % (in_block("max_over_time", "ALERTS{%s}" % sel), prom_started())


def active_apart(sel, other):
    """An alert matching sel was pending or firing at a minute of the bar's time block in which none
    matching other was, except the block in which Prometheus started, as active()."""
    return "(max(%s) unless on () %s)" % (
        in_block("max_over_time", "(ALERTS{%s} unless on () ALERTS{%s})" % (sel, other), sub=True), prom_started())


def seen(selector):
    """The highest value of a series in the bar's time block."""
    return "(max(%s))" % in_block("max_over_time", selector)


def nodata():
    """0 for every time block in which Prometheus ran: keeps each line present, and grey."""
    return "%s * 0" % seen('up{job="prometheus"}')


def evaluated():
    """Prometheus ran and evaluated the alert rules in the time block: monitoring had data."""
    return "(max(%s) > 0)" % in_block(
        "increase", 'prometheus_rule_group_iterations_total{job="prometheus",rule_group=~".*;celestia_mon"}')


def rules_ran():
    """The alert rules ran in the time block and none failed: a bar is green only then."""
    return "(%s unless on () %s)" % (evaluated(), firing('alertname="MonitoringBroken"'))


def stale(metric, secs):
    """A check's timestamp was older than secs at some minute of the time block."""
    return "(max(%s) > %s)" % (in_block("max_over_time", "(time() - max(%s))" % metric, sub=True), num(secs))


def down(job):
    """A scrape target of job did not answer at some moment of the time block."""
    return "(min(%s) == 0)" % in_block("min_over_time", "up{job=%s}" % q(job))


def uptime(inner):
    """The share of the selected time's minutes in which inner was 1, among the minutes it had a value."""
    return "avg_over_time((%s)[$__range:1m])" % inner


def unsent(window):
    """Prometheus failed to hand alerts to Alertmanager in the last window and handed none over, or
    knows no Alertmanager. A send that succeeds clears it at once, so it does not linger after
    Alertmanager recovers."""
    return ("((((sum(increase(%s[%s])) > 0) or (sum(increase(%s[%s])) > 0)) unless on () "
            "(sum(increase(%s[%s])) > 0)) or (max(%s) == 0))"
            % (NOTIFY_ERRORS, window, NOTIFY_DROPPED, window, NOTIFY_SENT, window, AM_DISCOVERED))


def signed_share(s, rng, step, cap):
    """The share of the blocks committed over rng that carry the validator's signature, from exact
    differences per step: a step in which the validator was not signing at all (more than
    not_signing_blocks blocks since its last signature) counts all its blocks as unsigned. A step
    with more than cap blocks is a node catching up after an outage, and counts as unsigned too,
    unless the step before had no fresh height while some node ran through the gap (the same
    process start time): then it was a scrape or Prometheus gap, and the node kept counting its own
    misses. Steps in which Prometheus did not scrape itself leave both sums."""
    blk = "clamp_min(max(%s) - max(%s offset %s), 0)" % (H, H, step)
    miss = "(max(clamp_min(%s - %s offset %s, 0)) or on () vector(0))" % (MISSED, MISSED, step)
    start = 'process_start_time_seconds{job="celestia-app"}'
    bridged = ("(absent_over_time(%s[%s] offset %s) and on () (count(%s == %s offset %s) > 0))"
               % (H, step, step, start, start, step))
    signed = ("((clamp_min(%s - %s, 0) unless on () (%s > %s) unless on () ((%s > %s) unless on () %s)) "
              "or on () (%s * 0))" % (blk, miss, BLOCKS_SINCE, num(s.N), blk, num(cap), bridged, blk))
    promok = '(count_over_time(up{job="prometheus"}[%s]) > 0)' % step
    return ("(sum_over_time((%s and on () %s)[%s:%s]) / sum_over_time((%s and on () %s)[%s:%s]))"
            % (signed, promok, rng, step, blk, promok, rng, step))


# The alert sentences: the Status cell and the alert list group ALERTS series the same way.

def by_alert(x):
    """x (ALERTS-shaped) grouped by what tells its alerts apart. ServiceDown's crash loop and
    not-loaded rules get a reason from their labels: the crash loop carries job but no state, a
    unit that is not loaded carries neither."""
    sdr = ('label_replace(label_replace(label_join(%s, "sdk", ":", "alertname", "unit", "state", "job"), '
           '"reason", "crashloop", "sdk", "ServiceDown:(appd|tmkms|fibre)::node"), '
           '"reason", "not_loaded", "sdk", "ServiceDown:(appd|tmkms)::")' % x)
    return "max by (alertname, severity, unit, reason, check, host) (%s)" % sdr


def titled(s, x, short=False):
    """x (grouped by by_alert) with a title label: a plain sentence, else the alert's name; with
    short, also a short label (the Status cell's words, else SHORT_FALLBACK) and a part label (what
    the item is about, none for SHORT_FALLBACK)."""
    expr = 'label_join(%s, "key", ":", "alertname", "severity", "unit", "reason", "check", "host")' % x
    expr = 'label_replace(%s, "title", "$1", "alertname", "(.+)")' % expr
    if short:
        expr = label(expr, "short", SHORT_FALLBACK)
    for key, sentence, words, part, need in TITLES:
        if s.has(need):
            expr = "label_replace(%s, %s, %s, %s, %s)" % (expr, q("title"), q(sentence), q("key"), q(key))
            if short:
                expr = "label_replace(%s, %s, %s, %s, %s)" % (expr, q("short"), q(words), q("key"), q(key))
                expr = "label_replace(%s, %s, %s, %s, %s)" % (expr, q("part"), q(part), q("key"), q(key))
    return expr


def check_titles(s):
    """Every alert of the rules template has a sentence, a short name and a part."""
    named = {key.split(":")[0] for key, sentence, words, part, _ in TITLES if sentence and words and part in PARTS}
    missing = [a for a in s.alert_names if a not in named and a not in UNTITLED]
    if missing:
        raise SystemExit("gen_dashboard.py has no sentence, short name and part in TITLES for the alert(s) %s of %s; "
                         "add them, so the Status cell and the alert list name it in plain words"
                         % (", ".join(missing), RULES_TEMPLATE))


# check_surfaces() and check_words() read the rules template and the first screen's queries as text.
# A label value of a rule's alerts that the template does not fix: ANY (any value, or a label the
# checks do not read), SOME (some value, never empty).
ANY, SOME = object(), object()
VARIANT_LABELS = ("alertname", "severity", "unit", "reason", "check", "job", "state")
REDS = {"red", ALERT}
# Not surfaces: the Status cell takes its words from TITLES itself, and the Uptime column (21) prints
# shares, not states.
NOT_SURFACES = {STATUS_ID, 21}
# Words a surface may show for an alert's state instead of its TITLES short word, each with the reason
# (check_words): {alert: {word: reason}}.
OTHER_WORDS = {}


def rule_variants():
    """Every rule of the rules template as the labels its alerts carry, one dict of VARIANT_LABELS per
    unit for a rule whose unit the template fills in from its ServiceDown list (units); a list per rule.
    ServiceDown's crash loop and not-loaded rules carry no reason label: by_alert() reads it from the
    job and state their expressions leave, and so does this. A rule whose expression keeps its metric's
    reason label (RemoteSignerProblem's proposal failures) has SOME reason."""
    text = rules_text()
    units = re.findall(r"\('(\w+)', ", re.search(r"\{% set units = (.*?)%\}", text, re.S).group(1))
    rules = []
    for block in re.split(r"^\s*- alert: ", text, flags=re.M)[1:]:
        m = re.search(r"^( *)labels:\n((?:\1 +\S.*\n)+)", block, re.M)
        head = block[:m.start()] if m else block
        labels = dict(re.findall(r"^\s+(severity|unit|reason|check): *(\S.*?)\s*$", m.group(2), re.M)) if m else {}
        v = {"alertname": re.match(r"\w+", block).group(0), "severity": labels.get("severity", ""),
             "reason": labels.get("reason", ""), "check": labels.get("check", ""), "job": ANY, "state": ANY}
        if v["alertname"] == "ServiceDown":
            if "absent(" in head:
                v.update(reason="not_loaded", job="", state="")
            elif "node_systemd_service_restart_total" in head:
                v.update(reason="crashloop", job=SOME, state="")
            else:
                v.update(job=SOME, state=SOME if "node_systemd_unit_state" in head else "")
        elif not v["reason"] and re.search(r"\{[^}]*\breason\s*!?[=~]", head):
            v["reason"] = SOME
        unit = labels.get("unit", "")
        rules.append([dict(v, unit=u) for u in (units if "{{" in unit else [unit])])
    return rules


def variant_key(v, host="h"):
    """The key titled() matches TITLES against, for alerts with v's labels on host ("" for none)."""
    return ":".join(["x" if v[k] is SOME else v[k] for k in VARIANT_LABELS[:5]] + [host])


def title_entries(v):
    """The TITLES entries whose key matches alerts with v's labels, with a host or without one (a rule
    that keeps no host label)."""
    return [e for e in TITLES if re.fullmatch(e[0], variant_key(v)) or re.fullmatch(e[0], variant_key(v, ""))]


def variants(s):
    """(labels, TITLES entry) of every rule this install holds, as rule_variants() reads them, with the
    entry titled() gives its alerts: like titled()'s chain, the last key that matches wins. Stops when a
    rule's alerts match no key, so a new reason or check cannot reach the Status cell as SHORT_FALLBACK."""
    out = []
    for rule in rule_variants():
        if rule[0]["alertname"] in UNTITLED:
            continue
        found = False
        for v in rule:
            entries = title_entries(v)
            found = found or bool(entries)
            live = [e for e in entries if s.has(e[4])]
            if live:
                out.append((v, live[-1]))
        if not found:
            raise SystemExit("No key of TITLES matches the alerts of a %s rule (%s): add a sentence, a short name and "
                             "a part for them" % (rule[0]["alertname"], variant_key(rule[0])))
    return out


def value_part(expr):
    """expr with the right side of every 'and' and 'unless' blanked: those series only filter the left
    side, so a selector there never gives the query its value. Strings and label matchers do not count
    as parentheses or keywords."""
    masked = re.sub(r'"(?:[^"\\]|\\.)*"', lambda m: " " * len(m.group(0)), expr)
    masked = re.sub(r"\{[^}]*\}", lambda m: " " * len(m.group(0)), masked)
    depth, depths = 0, []
    for ch in masked:
        depths.append(depth)
        depth += {"(": 1, ")": -1}.get(ch, 0)
    keep = list(expr)
    for m in re.finditer(r"\b(and|unless)\b", masked):
        d = depths[m.start()]
        modifier = re.compile(r"\s*(on|ignoring)\s*\([^)]*\)").match(masked, m.end())
        i = start = modifier.end() if modifier else m.end()
        # The right side ends where its group closes or at the next 'or', 'and' or 'unless' of its group.
        while i < len(masked):
            if depths[i] == d and (masked[i] == ")" or (re.match(r"(or|and|unless)\b", masked[i:])
                                                        and not re.match(r"\w", masked[i - 1]))):
                break
            i += 1
        keep[start:i] = " " * (i - start)
    return "".join(keep)


def value_selectors(expr):
    """The label matchers of every ALERTS and ALERTS_FOR_STATE selector that can give expr its value,
    each a list of (label, operator, value)."""
    return [re.findall(r'(\w+)\s*(=~|!~|!=|=)\s*"((?:[^"\\]|\\.)*)"', sel)
            for sel in re.findall(r"\bALERTS(?:_FOR_STATE)?\{([^}]*)\}", value_part(expr))]


def admits(op, pattern, value):
    """Whether a label matcher accepts value (a string, ANY or SOME)."""
    if value is ANY:
        return True
    if value is SOME:
        return not (op == "=" and pattern == "")
    hit = value == pattern if op in ("=", "!=") else bool(re.fullmatch(pattern, value))
    return hit if op in ("=", "=~") else not hit


def names(selector, v):
    """Whether a selector names the alerts with v's labels: it picks their alert name (= or =~; one that
    only excludes names none) and every matcher on VARIANT_LABELS accepts their value."""
    return (any(k == "alertname" and op in ("=", "=~") for k, op, _ in selector)
            and all(admits(op, p, v[k]) for k, op, p in selector if k in VARIANT_LABELS))


def target_words(p, t):
    """(text, colour) of every word query t of panel p can show: the mappings Grafana applies to its
    field (an override of its refId, else the panel's; a table's columns, all of them) and a card row's
    label texts (text_row), whose colour rides on their invisible last character."""
    fc = p["fieldConfig"]
    own = [pr["value"] for o in fc["overrides"] for pr in o["properties"] if pr["id"] == "mappings"
           and (p["type"] == "table" or o["matcher"] == {"id": "byFrameRefID", "options": t.get("refId")})]
    maps = [m for ms in (own if own and p["type"] != "table" else own + [fc["defaults"].get("mappings", [])])
            for m in ms]
    words = []
    for m in maps:
        for r in (m["options"].values() if m["type"] == "value" else [m["options"]["result"]]):
            if r.get("text"):
                words.append((r["text"], r.get("color")))
    marks = {mark: color for color, mark in MARKS.items()}
    for lit in re.findall(r'"text", ("(?:[^"\\]|\\.)*")', t["expr"]):
        text = json.loads(lit)
        color = marks.get(text[-1:]) if text else None
        words.append((re.sub(r"\$\{\d+\}", NUMBER_SAMPLE, text.rstrip("".join(MARKS.values()))), color or "text"))
    return words


def surfaces(panels):
    """(name, selectors, words) of every query on the first screen beside the Status cell: the tiles, the
    History lines, the role band's lanes, the server cards and the panels under them."""
    out = []
    for p in panels:
        if p["id"] >= 100 or p["id"] in NOT_SURFACES:
            continue
        for t in p.get("targets", []):
            name = " · ".join(n for n in (p.get("title") or p["fieldConfig"]["defaults"].get("displayName", ""),
                                          (t.get("legendFormat") or "").strip(ROW_INDENT + ZERO_WIDTH)) if n)
            out.append((name or "panel %d" % p["id"], value_selectors(t["expr"]), target_words(p, t)))
    return out


def check_surfaces(s, panels):
    """Every alert this install's rules hold reaches a place on the first screen beside the Status cell
    and the alert list: a tile, a line of the History board, the role band, a server card or a panel under
    it whose query names it, for each reason, check, unit and severity its rules carry (variants), so a
    new reason cannot fire while the surfaces list only the old ones. A selector names alerts only where
    it picks them by name and can give the query its value: not where it only excludes names, nor on the
    right of 'and' or 'unless'. A page alert must also have a red word on one of its surfaces. The alerts
    with no such place are in NO_SURFACE, each with its reason."""
    found = surfaces(panels)
    problems = []
    for v, entry in variants(s):
        if v["alertname"] in NO_SURFACE:
            continue
        mine = [(n, w) for n, sels, w in found if any(names(sel, v) for sel in sels)]
        if not mine:
            problems.append("no surface names %s (%s)" % (variant_key(v), entry[2]))
        elif v["severity"] == "page" and not any(c in REDS for _, w in mine for _, c in w):
            problems.append("%s pages, but no surface that names it (%s) has a red word"
                            % (variant_key(v), ", ".join(sorted({n for n, _ in mine}))))

    def named(alert):
        return any(names(sel, dict(dict.fromkeys(VARIANT_LABELS, ANY), alertname=alert))
                   for _, sels, _ in found for sel in sels)
    problems += ["no surface names %s" % a for a in sorted(UNTITLED) if a in s.alert_names and not named(a)]
    problems += ["NO_SURFACE lists %s, which the first screen now names: remove it from NO_SURFACE" % a
                 for a in sorted(NO_SURFACE) if named(a)]
    if problems:
        raise SystemExit("The first screen does not show every alert's state (check_surfaces); give each a state on "
                         "a tile, History line, role band lane or server card, or list the alert in NO_SURFACE with "
                         "the reason:\n  " + "\n  ".join(problems))


def check_words(s, panels):
    """Every surface that names an alert's state shows the alert's TITLES short word, as the Status cell
    does, so one state has one name on the whole first screen. A query's selectors are read in order, as
    its branches take precedence: a selector that names states no earlier one named needs their word
    among the query's words, or, where it takes in states of several words (a line's coarser code), one
    of them. OTHER_WORDS lists the words a surface may show instead, each with its reason."""
    found = surfaces(panels)
    live = [(v, e) for v, e in variants(s) if v["alertname"] not in NO_SURFACE]
    shown = {w for _, _, words in found for w, _ in words}
    problems = ["OTHER_WORDS lists %r for %s, which no surface shows: remove it" % (w, a)
                for a, words in sorted(OTHER_WORDS.items()) if any(v["alertname"] == a for v, _ in live)
                for w in sorted(words) if w not in shown]
    for name, sels, words in found:
        texts = {w for w, _ in words}
        seen = set()
        for sel in sels:
            new = [i for i, (v, _) in enumerate(live) if names(sel, v) and i not in seen]
            seen.update(new)
            shorts = {live[i][1][2] for i in new}
            ok = shorts | {w for i in new for w in OTHER_WORDS.get(live[i][0]["alertname"], {})}
            if new and not texts & ok:
                problems.append("%s names %s but shows none of %s (its words: %s)"
                                % (name, ", ".join(sorted({variant_key(live[i][0]) for i in new})),
                                   " / ".join(sorted(shorts)), ", ".join(sorted(texts))))
    if problems:
        raise SystemExit("A first-screen surface names an alert's state in other words than the Status cell "
                         "(check_words); use the alert's TITLES short word, or list the word in OTHER_WORDS with "
                         "the reason:\n  " + "\n  ".join(sorted(set(problems))))


def message(sel, quiet=None):
    """Message alerts matching sel: 2.2 firing, 2 pending (both amber). quiet: alert names (as
    series with an alertname label) whose pending alerts are left out."""
    pending = by_alert('ALERTS{alertstate="pending",%s}' % sel)
    if quiet:
        pending = "(%s unless on (alertname) %s)" % (pending, quiet)
    return "(%s * 0 + 2.2 or %s * 0 + 2)" % (by_alert('ALERTS{alertstate="firing",%s}' % sel), pending)


def restarting(s):
    """The collector alerts that are pending only because their collector did not answer a scrape in the
    last minute: install.sh restarts the collectors, and each would otherwise flash in the Status cell
    for about a minute. Once such an alert fires (its collector stayed down), it shows."""
    terms = [label("(min(min_over_time(up{job=%s}[1m])) == 0)" % q(job), "alertname", name)
             for name, job, on in [("TensileUnreadable", "tensile", s.tensile), ("SignalUnreadable", "signal", s.signal),
                                   ("AnnouncementsUnreadable", "announcements", s.announcements)] if on]
    return "(%s)" % " or ".join(terms) if terms else None


def alert_items(s):
    """What is wrong now, one series per sentence (labels title and short), valued by level: 3 a page
    fires (red), 2.5 a page is pending, 2 to 2.95 a message or monitoring trouble (amber); plus a
    priority between alerts of one level. Alertmanager's inhibitions are mirrored, and follow-on alerts
    are grouped into their cause. A scheduled upgrade is no item: the Network upgrade tile shows it."""
    # The node the validator signs through without peers (NodePeersLow's page): the signatures that stop
    # or go missing, no node advancing where no other node does, and that host's lag and its message
    # about few peers, are its follow-on alerts.
    peers = "ALERTS{%s}" % PEERS_PAGE
    # Alertmanager holds an alert back only while its cause fires, so a page that fires folds only into a
    # cause read as firing (fires): a pending cause holds nothing, and the page has gone out. A pending
    # page and a message fold into a cause in either state ("").
    fires = ',alertstate="firing"'

    def drop(state):
        """The alerts that fold into a cause in state, as series labelled alertname."""
        cause = "ALERTS{%s%s}" % (PEERS_PAGE, state)
        stalled = 'max(ALERTS{alertname="NodeNotAdvancing"%s})' % state
        return " or ".join("(%s and on () %s)" % (label("vector(1)", "alertname", a), cond) for a, cond in [
            ("ValidatorNotSigning", "(%s or max(%s))" % (stalled, cause)),
            ("NodeNotAdvancing", "max(%s)" % cause),
            ("SignaturesMissed", 'max(ALERTS{alertname=~"NodeNotAdvancing|ValidatorNotSigning"%s} or %s)' % (state, cause)),
            ("RemoteSignerProblem", stalled),
            ("NodeBehind", stalled),
            ("FibreNotServing", 'max(ALERTS{alertname=~"NodeNotAdvancing|FibreDown"%s})' % state)])

    def fibre_held(state):
        """FibreNotServing held back by a cause in state, as the inhibitions do: a collector that is down
        holds back that host's every reason but not_ready (Fibre's own health check, which the monitor asks
        Fibre directly) and not_served (the failed reads Tensile reports), and ValidatorNotSigning not_ready."""
        return ("unless on (alertname, host, reason) (max by (alertname, host, reason) "
                '(ALERTS{alertname="FibreNotServing",reason!~"not_ready|not_served"}) and on (host) max by (host) '
                '(ALERTS{alertname="ServiceDown",unit="otelcol"%s})) unless on (alertname, reason) (max by (alertname, '
                'reason) (ALERTS{alertname="FibreNotServing",reason="not_ready"}) and on () '
                'max(ALERTS{alertname="ValidatorNotSigning"%s}))' % (state, state))
    # Held back as messages only, as the inhibitions do: a crash-looping Fibre holds its host's every
    # FibreNotServing message and no page (the crash loop alert outlasts the loop, and a Fibre that has
    # answered NOT_SERVING for 10 minutes runs), and two Fibres hold missed_uploads: only the one the
    # chain names gets uploads.
    fibre_messages = ("unless on (alertname, host) %s unless on (alertname, reason) (max by (alertname, reason) "
                      '(ALERTS{alertname="FibreNotServing",reason="missed_uploads"}) and on () '
                      'max(ALERTS{alertname="FibreBothActive"}))'
                      % label('max by (host) (ALERTS{alertname="ServiceDown",unit="fibre"})', "alertname",
                              "FibreNotServing"))
    peers_held = "unless on (alertname, host) (%s)" % " or ".join(
        label("max by (host) (%s)" % peers, "alertname", a) for a in ("NodeBehind", "NodePeersLow"))
    pages = ("(%s * 0 + 3 unless on (alertname) (%s) %s) or (%s * 0 + 2.5 unless on (alertname) (%s) %s)"
             % (by_alert('ALERTS{alertstate="firing",severity="page"}'), drop(fires), fibre_held(fires),
                by_alert('ALERTS{alertstate="pending",severity="page"}'), drop(""), fibre_held("")))
    messages = ("%s unless on (alertname) (%s) %s %s %s"
                % (message('severity="message",alertname!~"UpgradeScheduled|ServiceDown|DiskLow"', restarting(s)),
                   drop(""), fibre_held(""), fibre_messages, peers_held))
    # A host whose node_exporter does not answer shows once; a stopped celestia-appd not also as lost metrics.
    reporting = message('alertname="ServiceDown",unit="node_exporter"')
    service = ("%s or ((%s unless on (host) %s) unless on (host, unit) %s)"
               % (reporting, message('alertname="ServiceDown",unit!="node_exporter"'), reporting,
                  'label_replace(%s, "unit", "celestia_app", "", "")' % message('alertname="ServiceDown",unit="appd"')))
    disk = ('%s unless on (host) ALERTS{alertname="DiskLow",severity="page"}'
            % message('alertname="DiskLow",severity="message"'))
    items = [pages, messages, service, disk]
    # Every alert of the rules has a priority as a page and as a message (0 when none is set; a message's
    # from MESSAGE_PRIORITY where it has one), and every item is a page or a message, so group_left keeps
    # every item. "group_left ()" names no labels to copy; a bare "group_left (" would read the operand as
    # that list.
    prio = "(%s)" % " or ".join(
        label(label("vector(%s)" % num(MESSAGE_PRIORITY.get(a, PRIORITY.get(a, 0)) if sev == "message"
                                       else PRIORITY.get(a, 0)), "alertname", a), "severity", sev)
        for a in s.alert_names for sev in ("page", "message"))
    alerts = ("(%s) + on (alertname, severity) group_left () %s"
              % (" or ".join("(%s)" % i for i in items), prio))
    return "(max by (title, short, part) (%s) or %s)" % (titled(s, alerts, short=True), synth(s))


def synth_items(s):
    """(level, sentence, short, part, condition) of the Status cell's items no alert rule carries."""
    items = [
        (2.95, "Alert rules are not running", "Rules stopped", P_MON, "(time() - max(%s) > 120)" % RULES_RUN),
        (2.95, "Alert rules are not running", "Rules stopped", P_MON, "max(absent(%s))" % RULES_RUN),
        (2.9, "Alertmanager down, alerts not sent", "Alerting down", P_MON, '(min(up{job="alertmanager"}) == 0)'),
        # Not while Alertmanager is down: "Alerting down" already says so, as one item.
        (2.85, "Alerts not reaching Alertmanager", "Alerts unsent", P_MON,
         '(%s unless on () (min(up{job="alertmanager"}) == 0))' % unsent("3m")),
        # A failed notification stops the Watchdog for an hour, also after NotificationsFailing cleared.
        (2.8, "A notification failed in the last hour", "No heartbeat", P_MON,
         '(((((count(ALERTS{alertname="Watchdog",alertstate="firing"}) or vector(0)) == 0) * 0 + 1) '
         "and on () (time() - max(%s) <= 120)) unless on () max(ALERTS{alertname=\"NotificationsFailing\"}))"
         % RULES_RUN),
    ]
    if s.announcements:
        # A Celestia announcement goes to Alertmanager directly, not through the rules. It is news, not a
        # fault: level 1.5 shows its words in the text colour.
        items.append((1.5, "New Celestia announcement, see your alerts", "Announcement", P_CELESTIA,
                      "(sum(increase(celestia_mon_announcements_forwarded_total[30m])) > 0)"))
    return items


def synth(s):
    """The Status cell's items no alert rule carries: the monitoring itself and an announcement."""
    return "(%s)" % " or ".join(label(label(label("%s * 0 + %s" % (cond, num(level)), "title", text), "short", words),
                                      "part", part)
                                for level, text, words, part, cond in synth_items(s))


def status_words(s):
    """(short name, sentence) of every item the Status cell can show in this install, each once, in
    TITLES order and then the items no rule carries. A scheduled upgrade is no item (alert_items)."""
    pairs = [(words, sentence) for key, sentence, words, _, need in TITLES
             if s.has(need) and not key.startswith("UpgradeScheduled:")]
    pairs += [(words, sentence) for _, sentence, words, _, _ in synth_items(s)]
    unique = []
    for p in pairs:
        if p not in unique:
            unique.append(p)
    return unique


def status_shorts(s):
    """The short names the Status cell can show, each once, in status_words order, then SHORT_FALLBACK.
    The n-th (from 1) is shown as code 10 n + 1 in amber and 10 n + 2 in red; "All online" is code 1."""
    shorts = []
    for words, _ in status_words(s):
        if words not in shorts:
            shorts.append(words)
    return shorts + [SHORT_FALLBACK]


def status_expr(s):
    """The Status cell: one series with label status whose value is a code for the worst item's short
    name and colour (status_shorts); with no item, "All online" (code 1). The worst item has the highest
    level, then the highest priority (PRIORITY, MESSAGE_PRIORITY). The label names the worst item's part
    when it has one (label part, from TITLES) and counts the items when there are several: "Status",
    "Status · Fibre", "Status · 3 alerts", "Fibre · 3 alerts" (both do not fit one line of the cell)."""
    t = alert_items(s)
    lookup = "(%s)" % " or ".join(label("vector(%d)" % (10 * i), "short", w) for i, w in enumerate(status_shorts(s), 1))
    # 0 text (level under 2), 1 amber, 2 red, plus ten times the short name's place in the lookup.
    code = "(clamp(floor(topk(1, %s)) - 1, 0, 2) + on (short) %s)" % (t, lookup)
    # count_values turns the number of items into a label; label_join puts it next to the part.
    counted = 'label_join(%s * on () group_left (n) count_values("n", count(%s)), "pn", ":", "part", "n")' % (code, t)
    many = "([2-9]|[1-9][0-9]+)"
    named = label(counted, "status", "Status")
    named = 'label_replace(%s, "status", "Status · $1", "part", "(.+)")' % named
    named = 'label_replace(%s, "status", "Status · $1 alerts", "pn", %s)' % (named, q(":" + many))
    named = 'label_replace(%s, "status", "$1 · $2 alerts", "pn", %s)' % (named, q("(.+):" + many))
    clear = label("vector(1) unless on () count(%s)" % t, "status", "Status")
    return "(%s or %s)" % (named, clear)


def panel_texts(p):
    """Every text a stat panel can show in place of a number: its mapped words and its no-value text."""
    fc = p["fieldConfig"]
    texts = [fc["defaults"].get("noValue", "")]
    maps = list(fc["defaults"].get("mappings", []))
    for o in fc["overrides"]:
        for prop in o["properties"]:
            if prop["id"] == "mappings":
                maps += prop["value"]
    for m in maps:
        results = m["options"].values() if m["type"] == "value" else [m["options"]["result"]]
        texts += [r.get("text", "") for r in results]
    return [t for t in texts if t]


# The key on a cell of a six-cell row under which six_cells() leaves its texts for one_value_size().
SIX_CELLS = "_six_cells"


def six_cells(items, samples=None):
    """Marks (panel, width) items as the cells of a six-cell row: every text each can show (its words, its
    no-value text and samples {panel id: [texts]}, the values no mapping names) waits for one_value_size(),
    which sizes every such row of the dashboard at once."""
    samples = samples or {}
    for p, w in items:
        p[SIX_CELLS] = [(t, w) for t in panel_texts(p) + samples.get(p["id"], [])]
    return items


def one_value_size(panels):
    """One value size for every six-cell row of the dashboard (the top strip, the answers and the facts of
    the Validator, Fibre and Network upgrade rows), so their proportions match: the largest of
    INTRO_VALUE_SIZES at which every text of every such cell fits its cell, measured letter by letter
    (value_px within value_room). Stops when a text does not fit even at the smallest. Returns the size."""
    cells = [p for p in panels if SIX_CELLS in p]
    texts = [(t, w, p["id"]) for p in cells for t, w in p.pop(SIX_CELLS)]
    fits = [v for v in INTRO_VALUE_SIZES if all(value_px(t, v) <= value_room(w) for t, w, _ in texts)]
    if not fits:
        t, w, pid = max(texts, key=lambda x: value_px(x[0], INTRO_VALUE_SIZES[-1]) - value_room(x[1]))
        raise SystemExit("Panel %d: %r needs %d px at %d px, and its cell has %d; the six-cell rows share one value "
                         "size (if it is a host or validator name, shorten it in the inventory)"
                         % (pid, t, value_px(t, INTRO_VALUE_SIZES[-1]), INTRO_VALUE_SIZES[-1], value_room(w)))
    for p in cells:
        p["options"]["text"]["valueSize"] = fits[0]
    return fits[0]


# The first screen.

# The Status cell's items no alert rule carries (synth_items), as its (i) names what sets them.
SYNTH_PLAIN = {"Rules stopped": "no alert rule ran for 2 minutes", "Alerting down": "Alertmanager does not answer",
               "Alerts unsent": "alerts failed to reach Alertmanager in the last 3 minutes",
               "No heartbeat": "a notification failed in the last hour",
               "Announcement": "a new Celestia announcement, for 30 minutes"}


def status_list(s):
    """The Status cell's words in this install for its (i), in status_shorts order, each once with what
    sets it: its alerts (the names of its TITLES keys), then an item no rule carries in plain words
    (SYNTH_PLAIN). Words set by the same alerts share one entry: "Disk critical, Disk low: DiskLow".
    SHORT_FALLBACK is left out: variants() stops on an alert without a key."""
    names, plain = {}, {}
    for key, _, words, _, need in TITLES:
        n = key.split(":")[0]
        if s.has(need) and n != "UpgradeScheduled" and n not in names.setdefault(words, []):
            names[words].append(n)
    for _, sentence, words, _, _ in synth_items(s):
        p = SYNTH_PLAIN.get(words, sentence)
        if p not in plain.setdefault(words, []):
            plain[words].append(p)
    entries = []
    for w in status_shorts(s):
        if w == SHORT_FALLBACK:
            continue
        what = ", or ".join(([", ".join(names[w])] if names.get(w) else []) + plain.get(w, []))
        for e in entries:
            if e[1] == what:
                e[0].append(w)
                break
        else:
            entries.append(([w], what))
    # No-break spaces keep each word on one line of the (i), and each " ·" at the end of a line.
    nb = " "
    return (nb + "· ").join("%s: %s" % (", ".join(w.replace(" ", nb) for w in ws), what) for ws, what in entries)


def status_cell(s):
    """(id, label, description, query, options) of the Status cell: "All online" in green, or the worst
    item's short name in amber, red once a page fires (an announcement in the text colour); the label
    names the part and counts the items. Its (i) lists the words of the install with what sets them
    (status_list); each short name's sentence is in docs/panels.md and in the All firing alerts row."""
    shorts = status_shorts(s)
    desc = ("What is wrong now, or about to be: the worst alert's short name; All online in green when nothing is. "
            "Red: an urgent alert fires. Amber: a warning fires, or an alert's problem is true and is sent if it "
            "lasts. The label names the part and counts the alerts. Follow-on alerts fold into their cause; "
            "silenced alerts still show.")
    if s.announcements:
        desc += " A new Celestia announcement shows for 30 minutes in the text colour."
    desc += (" Every alert in full, with its rule name and how long it has been active: the row All firing alerts, "
             "right under History.\n\nThe words your install can show, and what sets them. %s."
             % status_list(s) + more(pages=[("Alert reference", DOCS)]))
    words = [(1, "All online", FINE)]
    for i, w in enumerate(shorts, 1):
        words += [(10 * i, w, "text"), (10 * i + 1, w, "orange"), (10 * i + 2, w, "red")]
    return (10, "{{status}}", desc, status_expr(s),
            dict(color_mode="value", mappings=mapping(*words), no_value="No data"))


def intro(s):
    """The intro strip, one band at the top: the validator's name under its network, the Status and
    four neutral facts, in six framed, untitled cells of one height and width. The Status comes second,
    read right after whose validator this is, and far from the time picker, whose menus cover the
    strip's right end. Each cell is a label over a value, set from the left, in one size pair, so the
    labels share one line and the values another. The generator stops when a text does not fit its cell."""
    name = s.name or DEFAULT_NAME
    if len(name) > NAME_CHARS:
        raise SystemExit("celestia_mon_validator_name %r has %d characters; at most %d fit the dashboard's top strip"
                         % (name, len(name), NAME_CHARS))
    # A longer name makes every six-cell row's values smaller (one_value_size), so they stay one size.
    if value_px(name, INTRO_VALUE_SIZES[-1]) > value_room(INTRO_WIDTHS[0]):
        raise SystemExit("celestia_mon_validator_name %r needs %d px even at %d px; the top strip's identity cell has "
                         "%d. Use a shorter name." % (name, value_px(name, INTRO_VALUE_SIZES[-1]),
                                                      INTRO_VALUE_SIZES[-1], value_room(INTRO_WIDTHS[0])))
    size = INTRO_VALUE_SIZES[0]
    # The network over the name: Mocha with its chain id while that fits the cell, else without. A cell
    # over 100 px high pads its text 12 px, not 8, so the label has 8 px less than inner_px.
    mocha = "Mocha testnet (mocha-5)"
    with_id = text_px(mocha, INTRO_LABEL_PX) <= inner_px(INTRO_WIDTHS[0]) - 8
    networks = [mocha if with_id else "Mocha testnet", "Celestia mainnet"]
    mocha_label = "Mocha testnet ($1)" if with_id else "Mocha testnet"
    net = ('max by (net) (label_replace(label_replace(label_replace(max by (chain_id) (%s), '
           '"net", "$1", "chain_id", "(.*)"), "net", "Celestia mainnet", "chain_id", "celestia"), '
           '"net", %s, "chain_id", "(mocha-.*)")) * 0 + 1' % (H, q(mocha_label)))
    signed = "Blocks signed, %d d" % s.days
    cells = [
        (1, "{{net}}",
         "Celestia validator%s monitoring: which validator this is and which network its nodes are on, read from the "
         "nodes' own metrics (chain_id: celestia on mainnet, mocha-5 on the Mocha testnet), so a Mocha and a mainnet "
         "dashboard cannot be confused. The name is celestia_mon_validator_name in the inventory. No node data: no "
         "node reports its chain." % (" and Fibre" if s.fibre else ""),
         net, dict(mappings=mapping((1, name, "text")), no_value="No node data")),
        status_cell(s),
        (2, "Latest block",
         "The newest block committed by any node of this setup. It grows by about 20 a minute, so it changes on every "
         "refresh. Compare it with an explorer or [status.celestia.org](https://status.celestia.org/) to see that the "
         "nodes are at the network's head.",
         "max(%s) - 1" % H, dict(unit="locale", no_value="No data")),
        (3, "Block time",
         "Average time between blocks over the last 5 minutes, from the blocks' own timestamps. Celestia is designed "
         "for about 2.6 s; about 2.9 s is normal on mainnet and Mocha today. Slower blocks affect every validator, "
         "not only this one. Stalled: no block arrived in those 5 minutes.",
         'max(rate(cometbft_consensus_block_interval_seconds_sum{job="celestia-app"}[5m]) / '
         '(rate(cometbft_consensus_block_interval_seconds_count{job="celestia-app"}[5m]) > 0)) or (%s * 0 - 1)' % STALL,
         dict(unit="s", decimals=2, color_mode="value", no_value="No data",
              mappings=mapping((-1, "Stalled", "orange")))),
        (4, "Voting power",
         "This validator's share of all voting power in the active set. It sets how often the validator proposes "
         "blocks and how much Fibre data it stores. A drop means delegations left or the validator was slashed. "
         "'Out of set': the chain moves but no node records blocks for the validator's key (jailed, unbonded or "
         "outside the active set); never in the first 2 minutes after a start, which are too short to tell. While "
         "no node reports or no block arrives, the share cannot be read, and the words say so.",
         chain("(max(absent(%s)) * 0 - 1)" % H, "(%s * 0 - 2)" % STALL,
               "((max(%s) / max(%s)) and on () %s)" % (POWER, TOTAL, IN_SET_OR_NEW), "(max(%s) * 0 - 3)" % H),
         dict(unit="percentunit", decimals=2, no_value="No data",
              mappings=mapping((-1, "No data", GREY), (-2, "Stalled", GREY), (-3, "Out of set", GREY)))),
        (5, signed,
         "Share of the blocks committed over the last %d days that carry this validator's signature. Blocks committed "
         "while it was not signing at all (jailed, out of the set, signer or node down) count as unsigned; time without "
         "monitoring data is left out. On a young install, or when Prometheus's size limit holds less, it covers the "
         "days Prometheus has. Nil votes count as missed. Explorers show uptime over the chain's slashing window "
         "(about 10,000 blocks), so their number can differ." % s.days,
         signed_share(s, "%dd" % s.days, "5m", 300),
         dict(unit="percentunit", decimals=2, no_value="No data",
              mappings=range_map(0.99995, 1, "100%") + special_map("nan", "No data"))),
    ]
    # The labels each cell can show, where they are not its query's legend, and the values its words
    # do not name: the widest number of each fact.
    labels = {1: networks, 10: ["Status", "Status · 99 alerts"] + ["Status · %s" % p for p in PARTS]
              + ["%s · 99 alerts" % p for p in PARTS]}
    samples = {1: [name], 2: [INTRO_NUMBER], 3: ["9.99 s"], 4: ["99.99%"], 5: ["99.99%"]}
    if sum(INTRO_WIDTHS) != 24 or len(INTRO_WIDTHS) != len(cells):
        raise SystemExit("INTRO_WIDTHS must give the %d cells of the top strip 24 columns in all" % len(cells))
    content = grid_px(INTRO_ROWS) - CHROME_UNTITLED
    if stack_px(INTRO_LABEL_PX, INTRO_VALUE_SIZES[0], False) > content:
        raise SystemExit("The top strip: a label over a value needs %d px; an untitled h%d panel has %d"
                         % (stack_px(INTRO_LABEL_PX, INTRO_VALUE_SIZES[0], False), INTRO_ROWS, content))
    panels = []
    for (pid, legend, desc, expr, kw), w in zip(cells, INTRO_WIDTHS):
        kw.setdefault("color_mode", "none")
        p = stat(pid, "", desc, [target("A", expr, legend, instant=True)], size=size,
                 text_mode="value_and_name", justify="auto", wide=False, title_size=INTRO_LABEL_PX, **kw)
        what = "The top strip's %s cell" % {1: "identity", 10: "Status"}.get(pid, repr(legend))
        check_fit(what + " (label)", labels.get(pid, [legend]), INTRO_LABEL_PX, w)
        panels.append((p, w))
    # The value size: one_value_size(), from the name and every word of every cell.
    return six_cells(panels, samples)


def validator_tile(s):
    """(id, title, description, query, words) of the Validator tile. Missed blocks uses SignaturesMissed's
    own expression, so the word and the rule agree to the block."""
    n = num(s.N)
    expr = chain(
        "(max(absent(%s)) * 0 + 7) + on () %s" % (H, NNA10),
        "(%s * 0 + 5) + on () %s" % (STALL, NNA10),
        '((((%s > %s) * 0 + 6 and on () (min(up{job="celestia-app"}) == 0)) or ((max(%s) * 0 + 4) unless on () %s) '
        "or (%s > %s) * 0 + 3) + on () %s)" % (BLOCKS_SINCE, n, POWER, IN_SET_OR_NEW, BLOCKS_SINCE, n, VNS10),
        "(max(max by (host) (%s) > on (host) %s * max by (host) (delta(%s[1h])) "
        "unless on (host) (max(%s) - on () group_right %s > %s))) * 0 + 2"
        % (MISSED_1H, num(s.R), H, H, H, num(s.NB)),
        "max(%s) * 0 + 1" % H)
    words = [(1, "Signing", FINE), (2, "Missed blocks", "orange"),
             (3, "Not signing", "orange"), (13, "Not signing", "red"),
             (4, "Out of set", "orange"), (14, "Out of set", "red"),
             (5, "Nodes stalled", "orange"), (15, "Nodes stalled", "red"),
             (6, "Not seen", "orange"), (16, "Not seen", "red"),
             (7, "Nodes offline", "orange"), (17, "Nodes offline", "red")]
    desc = ("Is the validator signing, and in the active set? Signing: its signature is on one of the last %g blocks. "
            "Missed blocks: over %g%% of the last hour's blocks lack it (SignaturesMissed, after %s). Not signing: "
            "over %g blocks since its last signature; red once ValidatorNotSigning pages. Not seen: a node Prometheus "
            "cannot read may still sign; check an explorer before you move the key. Every word: the panel guide."
            % (s.N, s.R * 100, s.hold("SignaturesMissed"), s.N))
    return 11, "Validator", desc + more(["ValidatorNotSigning", "SignaturesMissed", "NodeNotAdvancing"],
                                        [PANEL_GUIDE]), expr, words


def signing_expr(s):
    """The node the validator signs through now, as its place in s.nodes (1, 2, ...), or a code for
    why there is none (signing_words). Two signers is ValidatorSigningTwice itself: amber while it is
    pending, red once it pages."""
    sig = signing_nodes(s)
    return chain(
        "%s * 0 + 45" % two_signers(s, "firing") if s.twice else None,
        "%s * 0 + 31" % STALL,
        "%s * 0 + 35" % two_signers(s) if s.twice else None,
        "(count(%s) > 1) * 0 + 30" % sig if s.remote else None,
        "max(%s and on (host) %s)" % (lookup(s.nodes), sig),
        '((min(up{job="celestia-app"}) == 0) * 0 + 32) + on () %s' % VNS10,
        "((max(%s) * 0 + 34) unless on () %s)" % (POWER, IN_SET_OR_NEW),
        "(max(%s) * 0 + 33) + on () %s" % (H, VNS10))


def signing_words(s):
    """The words of signing_expr: a node's server name in the server's colour, or the reason."""
    words = [(i + 1, h, s.color(h)[0]) for i, h in enumerate(s.nodes)]
    if s.remote:
        words.append((30, "Two nodes" if s.multi else "Switching", "text"))
    if s.twice:
        words += [(35, "Two signers", "orange"), (45, "Two signers", "red")]
    return words + [(31, "Nodes stalled", "orange"), (32, "Not visible", "orange"), (42, "Not visible", "red"),
                    (33, "Not signing", "orange"), (43, "Not signing", "red"), (34, "Out of set", GREY)]


def signing_tile(s):
    """(id, title, description, query, words) of the Signing through tile."""
    if s.remote:
        desc = ("Which server's node the validator's signatures go through now. tmkms, the remote signer, connects to "
                "that node. ")
        if s.multi:
            desc += "Two nodes: tmkms signs through both, as set up (celestia_mon_signer_multi_node). "
        else:
            desc += ("Switching: both nodes signed in the last 2 minutes, as during a repoint. Two signers: "
                     "ValidatorSigningTwice, amber while pending, red once it pages; stop one signer at once. ")
    else:
        desc = "Which server's node signs for the validator now: its last signed height advanced in 2 minutes. "
    desc += ("Not signing: in the set, but no node signed for 2 minutes; red once ValidatorNotSigning pages. "
             "Whether a backup could take over: its card below. Every word: the panel guide.")
    return 12, "Signing through", desc + more(["ValidatorNotSigning"] + (["ValidatorSigningTwice"] if s.twice else []),
                                              [PANEL_GUIDE]), \
        signing_expr(s), signing_words(s)


# FibreNotServing's reasons, the Fibre tile's and the cards' words for them (D2), and the switch each needs.
# The surfaces read them in this order: what failed first, then Fibre's own health check, then no metrics.
NOT_SERVING = [("missed_uploads", "Uploads missed", "tensile"), ("not_served", "Rows not served", "tensile"),
               ("uploads_failing", "Uploads failing", "fibre"), ("not_ready", "Not ready", "fibre"),
               ("no_telemetry", "No Fibre data", "fibre")]
# FibreNotServing (not_ready) once it pages: red on the Fibre tile, the History line and the cards.
NOT_READY_PAGE = 'alertname="FibreNotServing",reason="not_ready",severity="page"'
# Its place in NOT_SERVING: the amber code of each surface plus 10 is its red code.
NOT_READY_AT = [r for r, _, _ in NOT_SERVING].index("not_ready")


def fibre_tile(s):
    """(id, title, description, query, words) of the Fibre tile. Serving needs no recent upload: a quiet
    network sends none for hours, and Fibre is fine meanwhile. ServiceDown's crash loop reads Fibre crash,
    as the Status cell and the cards, ahead of the reasons it holds back; FibreNotServing shows by its
    reason while it is pending or firing, as the Status cell does; the outside check by FibreDown's own
    states."""
    seen_f = fibre_seen(s)
    lost = fibre_lost(s)
    outside = 'max(ALERTS{alertname="FibreDown",%s%s})'

    def check(state, base):
        """The outside check's branches, by FibreDown's reason or check: base + 3 not registered, + 2 wrong
        host, + 0 unreachable."""
        return ["(%s * 0 + %d)" % (outside % ('reason="not_registered"', state), base + 3),
                "(%s * 0 + %d)" % (outside % ('reason="wrong_host"', state), base + 2),
                "(%s * 0 + %d)" % (outside % ('check="reachability"', state), base)]
    expr = chain(*(
        ['(max(ALERTS{alertname="FibreDown",check="",alertstate="firing"}) * 0 + 15) + on () '
         "((count(%s) > 0) * 0 + 4 or vector(0))" % lost]
        + (check(',alertstate="firing"', 20) if s.tensile else [])
        + ['max(ALERTS{%s,alertstate="firing"}) * 0 + %d' % (NOT_READY_PAGE, 41 + NOT_READY_AT)]
        + ['((count(%s) > 0) and on () max(ALERTS{alertname="FibreDown",check=""})) * 0 + 9' % lost,
           '(((count(%s) or vector(0)) == 0) * 0 + 5 and on () (count(%s == 1) > 0))' % (seen_f, s.node_up),
           "(count(%s) > 1) * 0 + 3" % seen_f,
           'max(ALERTS{alertname="ServiceDown",unit="fibre",alertstate="firing"}) * 0 + 6']
        + ['max(ALERTS{alertname="FibreNotServing",reason=%s}) * 0 + %d' % (q(r), 31 + i)
           for i, (r, _, need) in enumerate(NOT_SERVING) if s.has(need)]
        + (check("", 10) if s.tensile else [])
        + ["count(%s) * 0 + 1" % seen_f]))
    words = [(1, "Serving", FINE), (3, "Two Fibres", "orange"), (5, "Not running", "orange"),
             (15, "Not running", "red"), (9, "Not seen", "orange"), (19, "Not seen", "red"),
             (6, "Fibre crash", "orange")]
    words += [(31 + i, w, "orange") for i, (_, w, need) in enumerate(NOT_SERVING) if s.has(need)]
    words += [(41 + NOT_READY_AT, "Not ready", "red")]
    if s.tensile:
        words += [(10, "Unreachable", "orange"), (20, "Unreachable", "red"), (12, "Wrong host", "orange"),
                  (22, "Wrong host", "red"), (13, "Not registered", "orange"), (23, "Not registered", "red")]
    desc = ("Is Fibre, the service that stores and serves this validator's share of blob data, running and taking "
            "uploads? Serving: one Fibre runs and no Fibre alert is due; a quiet network sends no blob for hours, "
            "and that is fine. Amber: FibreNotServing by its reason (Not ready: Fibre's own health check fails), Two "
            "Fibres (FibreBothActive), Fibre crash (ServiceDown's crash loop) or Not running; red once FibreDown or "
            "Not ready pages. Not seen: a Fibre host is not reporting, so its Fibre may still run. Which server runs "
            "it: the cards below. Every word: the panel guide.")
    return 14, "Fibre", desc + more(["FibreDown", "FibreNotServing", "FibreBothActive", "ServiceDown"],
                                    [PANEL_GUIDE, FIBRE_DASHBOARD]), expr, words


def outside_tile(s):
    """(id, title, description, query, words) of the Fibre from outside tile (Tensile on)."""
    registration = 'max(ALERTS{alertname="FibreDown",reason=%s,alertstate="firing"})'
    reach = "max(celestia_mon_fibre_reachable and on (endpoint) celestia_mon_fibre_registered == 1)"
    expr = chain(
        "%s * 0 + 14" % (registration % q("not_registered")),
        "%s * 0 + 13" % (registration % q("wrong_host")),
        'max(ALERTS{alertname="FibreDown",check="reachability",alertstate="firing"}) * 0 + 12',
        'max(ALERTS{alertname="TensileUnreadable",alertstate="firing"}) * 0 + 10',
        'max(absent(up{job="tensile"})) * 0',
        '(max(up{job="tensile"}) == 0) * 0',
        "(time() - max(celestia_mon_fibre_last_check_timestamp_seconds) > 900) * 0",
        "(max(celestia_mon_fibre_registered) == 0) * 0 + 4",
        "(count(%s) > 0) * 0 + 3" % wrong_host(s) if s.fibre_endpoints else None,
        "(%s == 0) * 0 + 2" % reach,
        "(%s == 1) * 0 + 1" % reach,
        "(max(celestia_mon_fibre_registered) == 1) * 0 + 9")
    words = [(1, "Reachable", FINE), (2, "Unreachable", "orange"), (12, "Unreachable", "red"),
             (3, "Wrong host", "orange"), (13, "Wrong host", "red"), (4, "Not registered", "orange"),
             (14, "Not registered", "red"), (10, "Check stopped", "orange"), (0, "Not checked", GREY),
             (9, "Check pending", GREY)]
    desc = ("Can the internet reach your Fibre at the address registered on chain? Tensile checks it about every 5 "
            "minutes; this reads the result every minute. Reachable: the last check, under 15 minutes old, completed "
            "TCP and TLS; a wrong validator identity still reads Reachable and shows as Uploads missed. "
            "Unreachable, Wrong host, Not registered: FibreDown's outside checks, red once it pages (after %s); after "
            "a failover, send set-host. Check stopped: TensileUnreadable. Every word: the panel guide."
            % s.hold("FibreDown"))
    return 15, "Fibre from outside", desc + more(["FibreDown", "TensileUnreadable"], [PANEL_GUIDE, TENSILE]), expr, words


def upgrade_tile(s):
    """(id, title, description, query, words) of the Network upgrade tile (signal collector on), named
    Governance with the vote check on (gov): then also Vote missing while GovVoteMissing is pending or
    fires (amber; red once it pages), and Vote open while a proposal the validator account has not voted
    on is in its voting period and no alert holds yet."""
    # A proposal in its voting period without the validator account's vote, as GovVoteMissing reads them.
    unvoted = ("(count((%s == 0) and on (proposal_id) (%s - time() > 0)) > 0) * 0 + 7"
               % (gov(s, "celestia_mon_gov_validator_voted"), gov(s, "celestia_mon_gov_voting_end_timestamp_seconds")))
    expr = chain(
        'max(ALERTS{alertname="UpgradeSignalMissing",severity="page",alertstate="firing"}) * 0 + 13',
        'max(ALERTS{alertname="GovVoteMissing",severity="page",alertstate="firing"}) * 0 + 16' if s.gov_page else None,
        'max(ALERTS{alertname="UpgradeSoon",alertstate="firing"}) * 0 + 5',
        'max(ALERTS{alertname="GovVoteMissing"}) * 0 + 6' if s.gov else None,
        'max(ALERTS{alertname="SignalUnreadable",alertstate="firing"}) * 0 + 10',
        'max(absent(up{job="signal"})) * 0',
        '(max(up{job="signal"}) == 0) * 0',
        "(time() - max(celestia_mon_signal_last_success_timestamp_seconds) > 900) * 0",
        "(time() - max(celestia_mon_gov_last_success_timestamp_seconds) > 900) * 0" if s.gov else None,
        "((max(celestia_mon_upgrade_pending) == 1) * 0 + 5 and on () (max(celestia_mon_upgrade_eta_seconds) < 86400))",
        "(max(celestia_mon_upgrade_pending) == 1) * 0 + 4",
        'max(ALERTS{alertname="UpgradeSignalMissing"}) * 0 + 3',
        "((max(celestia_mon_signal_tally_power) > 0) * 0 + 3 unless on () "
        "(max(celestia_mon_signal_validator_version) == on () max(celestia_mon_signal_next_version)))",
        unvoted if s.gov else None,
        "(max(celestia_mon_signal_tally_power) > 0) * 0 + 2",
        "max(celestia_mon_upgrade_pending) * 0 + 1")
    words = [(1, "None pending", FINE), (2, "Signal sent", "text"), (3, "Not signalled", "orange"),
             (13, "Not signalled", "red"), (4, "Upgrade set", "text"), (5, "Upgrade soon", "orange"),
             (10, "Signal stale", "orange"), (0, "Not checked", GREY)]
    if s.gov:
        words += [(6, "Vote missing", "orange"), (7, "Vote open", "text")]
    if s.gov_page:
        words += [(16, "Vote missing", "red")]
    desc = ("Is a network upgrade coming, and has this validator signalled for it? None pending and Signal sent are "
            "fine. Not signalled: a round is open and this validator has not signalled; red once UpgradeSignalMissing "
            "pages (more than 1/3 of the voting power signalled, for %s). Signal only once every node runs a release "
            "that supports the new version. " % s.hold("UpgradeSignalMissing"))
    if s.gov:
        desc += ("Vote open: a governance proposal is in its voting period and the validator account has not voted; "
                 "vote, abstain if you have no view. Vote missing: less than 2 days of the vote are left (less than "
                 "half of a vote shorter than 4 days; GovVoteMissing, after %s)%s. "
                 % (s.hold("GovVoteMissing message"), "; red once it pages, with less than a day left"
                    if s.gov_page else ""))
    desc += ("Upgrade set, Upgrade soon, Signal stale: UpgradeScheduled, UpgradeSoon, SignalUnreadable. Versions and "
             "the height: the Network upgrade row. Every word: the panel guide.")
    alerts = ["UpgradeSignalMissing"] + (["GovVoteMissing"] if s.gov else []) + ["UpgradeScheduled", "UpgradeSoon",
                                                                                  "SignalUnreadable"]
    return 16, upgrade_title(s), desc + more(alerts, [PANEL_GUIDE]), expr, words


# ServiceDown's rules for a metrics endpoint (up == 0); the others watch a systemd unit.
ENDPOINT_UNITS = ("node_exporter", "celestia_app", "otelcol", "blackbox")


def service_down(s):
    """(ALERTS matchers, words) of each ServiceDown this install's rules hold, in TITLES order, with its
    TITLES short words. A unit's rules differ by labels (by_alert): stopped carries state, the crash loop
    job but no state, not loaded neither."""
    reasons = {"": ',state!=""', "crashloop": ',job!="",state=""', "not_loaded": ',job="",state=""'}
    out = []
    for key, _, short, _, need in TITLES:
        f = key.split(":")
        if f[0] == "ServiceDown" and s.has(need):
            extra = "" if f[2] in ENDPOINT_UNITS else reasons[f[3]]
            out.append(('alertname="ServiceDown",unit="%s"%s' % (f[2], extra), short))
    return out


# The words of a server's line and of the Servers tile beyond ServiceDown's (codes 30 and up). Not
# reached: MonitorCannotReach, amber for one server, red once no server answers and it pages. No peers
# and Few peers: NodePeersLow's page and message. RAID degraded (10) and No time sync (20): RaidDegraded and
# ClockNotSynced, codes clear of the monitor's own words (11 to 19).
SERVER_WORDS = [(1, "OK", OK), (2, "Warning", WARN), (3, "Disk critical", ALERT), (4, "Not reached", WARN),
                (5, "Not reached", ALERT), (6, "Disk low", WARN), (7, "Node behind", WARN), (8, "No peers", ALERT),
                (9, "Few peers", WARN), (10, "RAID degraded", WARN), (20, "No time sync", WARN), (0, "No data", NODATA)]
DISK_PAGE = 'alertname="DiskLow",severity="page"'
UNREACHED_PAGE = 'alertname="MonitorCannotReach",severity="page"'


def servers_tile(s):
    """(id, title, description, query, words) of the Servers tile: the server lines of the History board
    now, the worst first, in their words and colours. It only makes four or five answers six (with
    Monitoring for four), so the tiles line up with the six cells of the top strip."""
    sel = "host=~%s" % q("|".join(re2(h) for h in s.servers))

    def alert(match):
        """An alert matching match fires now for one of the servers."""
        return 'max(ALERTS{alertstate="firing",%s,%s})' % (match, sel)

    # As a line's green: the alert rules ran in the last 2 minutes and none failed.
    rules = ('((time() - max(%s) <= 120) unless on () max(ALERTS{alertname="MonitoringBroken",alertstate="firing"}))'
             % RULES_RUN)
    down = service_down(s)
    # MonitorCannotReach's page names no server.
    unreached = (['max(ALERTS{alertstate="firing",%s}) * 0 + 5' % UNREACHED_PAGE] if s.mon_only else []) + (
        ['%s * 0 + 4' % alert('alertname="MonitorCannotReach"')] if s.reached else [])
    expr = chain(*(
        ["%s * 0 + 3" % alert(DISK_PAGE), "%s * 0 + 8" % alert(PEERS_PAGE)] + unreached
        + ["%s * 0 + %d" % (alert(m), 30 + i) for i, (m, _) in enumerate(down)]
        + ["%s * 0 + 6" % alert('alertname="DiskLow"'),
           "%s * 0 + 9" % alert('alertname="NodePeersLow"'),
           "%s * 0 + 7" % alert('alertname="NodeBehind"'),
           "%s * 0 + 10" % alert('alertname="RaidDegraded"'),
           "%s * 0 + 20" % alert('alertname="ClockNotSynced"'),
           "%s * 0 + 2" % alert('severity="message",alertname!~%s' % q(SERVER_OTHERS)),
           '((count(up{job="node",%s} == 1) == %d) and on () %s) * 0 + 1' % (sel, len(s.servers), rules),
           'max(up{job="prometheus"}) * 0']))
    colour = {OK: "text", WARN: "orange", ALERT: "red", NODATA: GREY}
    words = ([(1, "All up", FINE)] + [(c, w, colour[f]) for c, w, f in SERVER_WORDS if c != 1]
             + [(30 + i, w, "orange") for i, (_, w) in enumerate(down)])
    desc = ("Are all servers reporting, with their services up and disks not full? All up: every server's "
            "node_exporter answers, no server alert fires and the rules ran. Otherwise the worst server alert in the "
            "Status cell's words, such as Disk critical (DiskLow pages), No peers (NodePeersLow pages: the node the "
            "validator signs through has none), Few peers (a node has fewer than %d peers), RAID degraded (an md "
            "RAID array has been missing a disk for %s), No time sync (a clock not synchronised for %s)%s; "
            "Warning: another warning names a server. Which server: its card below and its line in History. Every "
            "word: the panel guide."
            % (s.PM, s.hold("RaidDegraded"), s.hold("ClockNotSynced"),
               (" or Not reached (MonitorCannotReach: the monitor reaches none of a server's metrics%s)"
                % (", red once it reaches no server" if s.mon_only else "")) if s.reached else ""))
    return 17, "Servers", desc + more(["ServiceDown", "DiskLow", "NodeBehind", "NodePeersLow", "RaidDegraded",
                                       "ClockNotSynced"]
                                      + (["MonitorCannotReach"] if s.reached else []), [PANEL_GUIDE]), expr, words


def monitoring_now(s):
    """The monitoring itself now, as a code of MONITOR_WORDS: the alert rules run and none fails,
    Alertmanager is up and reached, notifications and the heartbeat go out, and the announcements are
    read (when on). The same items as the Status cell's monitoring words, and the alerts that watch them."""
    return chain(
        "(time() - max(%s) > 120) * 0 + 5" % RULES_RUN,
        "max(absent(%s)) * 0 + 5" % RULES_RUN,
        'max(ALERTS{alertname="MonitoringBroken",alertstate="firing"}) * 0 + 2',
        '(min(up{job="alertmanager"}) == 0) * 0 + 3',
        "%s * 0 + 4" % unsent("3m"),
        'max(ALERTS{alertname="NotificationsFailing",alertstate="firing"}) * 0 + 4',
        # A failed notification stops the heartbeat for an hour (synth_items' "No heartbeat").
        'max(ALERTS{alertname="DeadmanFailing",alertstate="firing"}) * 0 + 6',
        '((count(ALERTS{alertname="Watchdog",alertstate="firing"}) or vector(0)) == 0) * 0 + 6',
        'max(ALERTS{alertname="AnnouncementsUnreadable",alertstate="firing"}) * 0 + 7' if s.announcements else None,
        'max(up{job="prometheus"}) * 0 + 1')


MONITOR_WORDS = [(1, "Working", "text"), (2, "Rules failing", "orange"), (3, "Alerting down", "orange"),
                 (4, "Alerts unsent", "orange"), (5, "Rules stopped", "orange"), (6, "No heartbeat", "orange"),
                 (7, "Feed stopped", "orange")]


def monitoring_tile(s):
    """(id, title, description, query, words) of the Monitoring tile: only where the answers would be
    four, so that with Servers they are six and line up with the strip."""
    desc = ("Is the monitoring itself working, so every other answer is current and alerts reach you? Working: the "
            "alert rules ran in the last 2 minutes and none failed, Alertmanager is up and reached, and the heartbeat "
            "goes to your dead-man service. Otherwise the word names what failed: Rules stopped, Rules failing "
            "(MonitoringBroken), Alerting down, Alerts unsent (NotificationsFailing) or No heartbeat "
            "(DeadmanFailing)%s. The history: the line of %s, the monitor's server, in History."
            % (", or Feed stopped: the Celestia announcements are not read (AnnouncementsUnreadable)"
               if s.announcements else "", s.monitor))
    # Working in green, as every answer's healthy word; the cards' Monitoring rows keep the text colour.
    words = [(c, w, FINE if c == 1 else colour) for c, w, colour in MONITOR_WORDS]
    return 18, "Monitoring", desc + more(["MonitoringBroken", "NotificationsFailing", "DeadmanFailing"]
                                         + (["AnnouncementsUnreadable"] if s.announcements else [])), \
        monitoring_now(s), words


def answer_tiles(s):
    """(panel, width) of the one-word answers, in the top strip's cell form: untitled, the name over the
    word from the left, one word size for the row and equal widths, so their edges, labels and words line
    up with the cells of the strip above. Their value size is the six-cell rows' (one_value_size()). Each
    tile's steady healthy word is green (FINE), as the Status cell's All online; informational words keep
    the text colour, and Signing through the server's."""
    tiles = [validator_tile(s), signing_tile(s)]
    if s.fibre:
        tiles.append(fibre_tile(s))
    if s.tensile:
        tiles.append(outside_tile(s))
    if s.signal:
        tiles.append(upgrade_tile(s))
    # Only two, three or six equal tiles share the column edges of the strip's six cells: four or five
    # answers are made six by Servers (after Signing through) and, for four, Monitoring (last).
    if len(tiles) in (4, 5):
        tiles.insert(2, servers_tile(s))
    if len(tiles) == 5:
        tiles.append(monitoring_tile(s))
    if len(tiles) not in (1, 2, 3, 6):
        raise SystemExit("%d answer tiles cannot share the strip's column edges" % len(tiles))
    widths = [24 // len(tiles)] * len(tiles)
    items = []
    for (pid, title, desc, expr, words), w in zip(tiles, widths):
        p = stat(pid, "", desc, [target("A", expr, title, instant=True)], mappings=mapping(*words), no_value="No data",
                 text_mode="value_and_name", justify="auto", wide=False, title_size=INTRO_LABEL_PX)
        # The name also when the query returns nothing.
        p["fieldConfig"]["defaults"]["displayName"] = title
        check_fit("The answer tile %r (label)" % title, [title], INTRO_LABEL_PX, w)
        items.append((p, w))
    # The value size: one_value_size(), with the strip and the rows' facts.
    return six_cells(items)


HISTORY_DESC = ("How each check and each server fared over the selected time, one bar per time block, worst moment "
                "first. Green: fine. Amber: a warning fired, or an urgent alert was pending and cleared before it "
                "paged. Red: an urgent alert fired. Grey: no data. The bars are the alert states Prometheus evaluated "
                "at the time. Uptime, on the right: the share of the time the line was fine; it counts the time an "
                "alert fired, not a pending one, so 100% can stand beside an amber bar. Hover a bar to see every line "
                "at that moment.")


def check_lanes(s):
    """The status board's check lines, in board order: (refId, label, bars, uptime, words). bars: the
    worst state of each time block as a code; uptime: the share of the selected time the line was
    fine (instant); words: (code, text, fill) per code. Every green branch needs the alert rules to
    have run in the block, so a bar is never green when no rule checked it."""
    gate = rules_ran()
    rsp = 'max(ALERTS{alertname=~"RemoteSignerProblem|ValidatorSigningTwice",alertstate="firing"})'
    behind = 'max(ALERTS{alertname="NodeBehind",alertstate="firing"})'
    # ValidatorSigningTwice colours Validator signing and Remote signer: red once it pages, amber while
    # it was pending only.
    twice_red = "%s * 0 + 6" % firing('alertname="ValidatorSigningTwice"') if s.twice else None
    twice_amber = "%s * 0 + 7" % active('alertname="ValidatorSigningTwice"') if s.twice else None
    twice_words = [(6, "Two signers", ALERT), (7, "Two signers", WARN)] if s.twice else []
    vns, nna = 'alertname="ValidatorNotSigning"', 'alertname="NodeNotAdvancing"'
    lanes = [
        # The nodes advance and none lags: NodeNotAdvancing, and NodeBehind for any node.
        ("A", "Node in sync",
         chain("%s * 0 + 3" % firing('alertname="NodeNotAdvancing"'),
               "%s * 0 + 2" % active('alertname="NodeNotAdvancing"'),
               "%s * 0 + 4" % firing('alertname="NodeBehind"'),
               "((%s == 1) and on () %s) * 0 + 1" % (seen('up{job="celestia-app"}'), gate),
               nodata()),
         uptime("(((max(changes(%s[1m])) > bool 0) unless on () %s) or on () (%s * 0)) and on () %s"
                % (H, behind, PROM, PROM)),
         [(1, "In sync", OK), (2, "Nodes stalled", WARN), (3, "Nodes stalled", ALERT), (4, "Node behind", WARN),
          (0, "No data", NODATA)]),
        # While the nodes are stalled, ValidatorNotSigning is inhibited: grey, not a second red line for one
        # cause. A long block can hold both at different moments: minute by minute it stays red.
        ("B", "Validator signing",
         chain(twice_red,
               "((%s unless on () %s) or %s) * 0 + 3" % (firing(vns), firing(nna), fired_apart(vns, nna)),
               twice_amber,
               "((%s unless on () %s) or %s) * 0 + 2" % (active(vns), active(nna), active_apart(vns, nna)),
               "%s * 0 + 5" % firing(nna),
               "%s * 0 + 4" % firing('alertname="SignaturesMissed"'),
               "(%s and on () %s) * 0 + 1" % (seen(H), gate),
               nodata()),
         signed_share(s, "$__range", "1m", 60),
         [(1, "Signing", OK), (2, "Not signing", WARN), (3, "Not signing", ALERT),
          (4, "Missed blocks", WARN), (5, "Nodes stalled", NODATA)] + twice_words + [(0, "No data", NODATA)]),
    ]
    if s.remote:
        # Green needs real signatures in the block, never the median gauge, which freezes when tmkms stops.
        lanes.append(
            ("C", "Remote signer",
             chain(twice_red,
                   twice_amber,
                   "%s * 0 + 3" % firing('alertname="RemoteSignerProblem",reason!=""'),
                   "%s * 0 + 2" % firing('alertname="RemoteSignerProblem"'),
                   # A minute of the block with misses and no signature: one signature elsewhere in
                   # the block does not make it green.
                   "max(%s) * 0 + 4" % in_block(
                       "max_over_time", "(((sum(increase(%s[1m])) or on () vector(0)) == 0) and on () (sum(%s) > 0))"
                       % (PV, missed_in("1m")), sub=True),
                   "((sum(%s) > 0) and on () %s) * 0 + 1" % (in_block("increase", PV), gate),
                   "(sum(%s) > 0) * 0 + 4" % missed_in_block(),
                   nodata()),
             uptime("((((sum(increase(%s[1m])) > bool 0) unless on () %s) or on () (%s * 0)) and on () "
                    "((sum(increase(%s[1m])) > 0) or (sum(%s) > 0))) and on () %s"
                    % (PV, rsp, rsp, PV, missed_in("1m"), PROM)),
             [(1, "Signing, fast enough", OK), (2, "Signer slow", WARN), (3, "Bad proposals", WARN),
              (4, "No signatures", WARN)] + twice_words + [(0, "No data, or not asked to sign", NODATA)]))
    if s.fibre:
        # Uptime leaves out every minute the line is amber or red: a Fibre alert firing, or no Fibre running.
        fibre_alert = 'max(ALERTS{alertname=~"FibreNotServing|FibreBothActive",alertstate="firing"})'
        reasons = [(r, w) for r, w, need in NOT_SERVING if s.has(need)]
        # A reason shows once its alert fires, never while pending only, so a Fibre restart, which answers
        # its health check NOT_SERVING for seconds, leaves the line green. Not ready is red once it pages.
        lanes.append(
            ("D", "Fibre serving",
             chain(*(["%s * 0 + 3" % firing('alertname="FibreDown",check=""'),
                      "%s * 0 + 6" % firing(NOT_READY_PAGE),
                      "%s * 0 + 2" % active('alertname="FibreDown",check=""')]
                     + ["%s * 0 + %d" % (firing('alertname="FibreNotServing",reason=%s' % q(r)), 21 + i)
                        for i, (r, _) in enumerate(reasons)]
                     + ["%s * 0 + 5" % firing('alertname="FibreBothActive"'),
                        '((%s == 1) and on () %s) * 0 + 1' % (seen('node_systemd_unit_state{%s,state="active"}'
                                                                   % s.is_unit("fibre")), gate),
                        nodata()])),
             uptime("((((count(%s) > bool 0) or on () (%s * 0)) unless on () %s) or on () (%s * 0)) and on () %s"
                    % (fibre_active(s), PROM, fibre_alert, PROM, PROM)),
             [(1, "Serving", OK), (2, "Not running", WARN), (3, "Not running", ALERT), (6, "Not ready", ALERT)]
             + [(21 + i, w, WARN) for i, (_, w) in enumerate(reasons)]
             + [(5, "Two Fibres", WARN), (0, "No data", NODATA)]))
    if s.tensile:
        # celestia-mon-tensile keeps exporting the last reading when its reads fail, so green also
        # needs a check under 15 minutes old at every minute of the block, as FibreDown does.
        lanes.append(
            ("E", "Fibre from outside",
             chain("%s * 0 + 3" % firing('alertname="FibreDown",check="reachability"'),
                   "%s * 0 + 9" % firing('alertname="FibreDown",reason="not_registered"'),
                   "%s * 0 + 4" % firing('alertname="FibreDown",reason="wrong_host"'),
                   "%s * 0 + 5" % firing('alertname="TensileUnreadable"'),
                   "%s * 0 + 2" % active('alertname="FibreDown",check="reachability"'),
                   "%s * 0 + 8" % active('alertname="FibreDown",reason="not_registered"'),
                   "%s * 0 + 10" % active('alertname="FibreDown",reason="wrong_host"'),
                   "(%s or %s) * 0 + 6" % (stale("celestia_mon_fibre_last_check_timestamp_seconds", 900),
                                           down("tensile")),
                   "((%s == 1) and on () %s) * 0 + 1" % (seen("celestia_mon_fibre_reachable"), gate),
                   nodata()),
             uptime('(((max(ALERTS{alertname="FibreDown",check="registration",alertstate="firing"}) * 0) '
                    "or (max(celestia_mon_fibre_registered) == 0) "
                    "or max(celestia_mon_fibre_reachable and on (endpoint) celestia_mon_fibre_registered == 1)) "
                    "unless on () (time() - max(celestia_mon_fibre_last_check_timestamp_seconds) > 900)) "
                    "and on () %s" % PROM),
             [(1, "Reachable", OK), (2, "Unreachable", WARN), (8, "Not registered", WARN), (10, "Wrong host", WARN),
              (3, "Unreachable", ALERT), (4, "Wrong host", ALERT), (9, "Not registered", ALERT),
              (5, "Check stopped", WARN), (6, "Not checked", NODATA), (0, "No data", NODATA)]))
    # The monitoring has no check line: its alerts colour the monitor's server line (server_lanes).
    return lanes


def server_lanes(s):
    """One status line per server, in servers order: (refId, label, bars, uptime, words). The alerts
    excluded from the generic warning colour lines Remote signer, Fibre serving and Fibre from outside
    instead, and the monitoring's own alerts and heartbeat the monitor's line in the Monitoring row's
    words; the upgrade and vote alerts colour no line (the Network upgrade tile, or Governance, shows
    them). NodeBehind colours its server's line and Node in sync; NodePeersLow, RaidDegraded and
    ClockNotSynced their server's line. Not
    reached: MonitorCannotReach on each server the monitor reaches over the network. Uptime leaves out the
    minutes in which a page that names the server fired (DiskLow, NodePeersLow) or one of its metrics
    endpoints did not answer."""
    others = SERVER_OTHERS
    services = service_down(s)
    words = SERVER_WORDS + [(30 + i, w, WARN) for i, (_, w) in enumerate(services)]
    # The monitor's line: the monitoring's alerts (MONITOR_WORDS), then Alertmanager down, which only
    # Prometheus's own metrics show (no rule watches it; the heartbeat keeps going meanwhile), then the
    # heartbeat (Watchdog) missing from a block in which the rules ran: a failed notification stops it
    # for an hour, also after NotificationsFailing cleared. The monitor is one of the servers in every
    # layout, so this line shows the heartbeat also where no Monitoring tile or card row does (the
    # Servers now table, a monitor on a KMS host); check_surfaces reads its selector left of 'and' only.
    watching = ([("MonitoringBroken", "Rules failing"), ("NotificationsFailing", "Alerts unsent"),
                 ("DeadmanFailing", "No heartbeat")]
                + ([("AnnouncementsUnreadable", "Feed stopped")] if s.announcements else []))
    heartbeat = seen('ALERTS{alertname="Watchdog",alertstate="firing"}')
    own = (["%s * 0 + %d" % (firing("alertname=%s" % q(a)), 11 + j) for j, (a, _) in enumerate(watching)]
           + ["%s * 0 + 19" % down("alertmanager"),
              "((((%s or vector(0)) == 0) * 0 + 18) and on () %s)" % (heartbeat, evaluated())])
    own_words = ([(11 + j, w, WARN) for j, (_, w) in enumerate(watching)]
                 + [(19, "Alerting down", WARN), (18, "No heartbeat", WARN)])
    lanes = []
    for i, h in enumerate(s.servers):
        sel = "host=%s" % q(h)
        up = 'up{%s,job=~"node|celestia-app|otelcol"}' % sel
        page = 'max(ALERTS{alertname=~"DiskLow|NodePeersLow",severity="page",%s,alertstate="firing"})' % sel
        unreached = ((["%s * 0 + 5" % firing(UNREACHED_PAGE)] if s.mon_only else [])
                     + ["%s * 0 + 4" % firing('%s,alertname="MonitorCannotReach"' % sel)]) if h in s.reached else []
        mine = h == s.monitor
        lanes.append((
            "S%d" % (i + 1), h,
            chain(*(["%s * 0 + 3" % firing("%s,%s" % (sel, DISK_PAGE)), "%s * 0 + 8" % firing("%s,%s" % (sel, PEERS_PAGE))]
                    + unreached
                    + ["%s * 0 + %d" % (firing("%s,%s" % (sel, m)), 30 + j) for j, (m, _) in enumerate(services)]
                    + ["%s * 0 + 6" % firing('%s,alertname="DiskLow"' % sel),
                       "%s * 0 + 9" % firing('%s,alertname="NodePeersLow"' % sel),
                       "%s * 0 + 7" % firing('%s,alertname="NodeBehind"' % sel),
                       "%s * 0 + 10" % firing('%s,alertname="RaidDegraded"' % sel),
                       "%s * 0 + 20" % firing('%s,alertname="ClockNotSynced"' % sel)]
                    + (own if mine else [])
                    + ["%s * 0 + 2" % firing('%s,severity="message",alertname!~"%s"' % (sel, others)),
                       "((%s == 1) and on () %s) * 0 + 1" % (seen('up{job="node",%s}' % sel), rules_ran()),
                       nodata()])),
            uptime("((min(%s) unless on () %s) or on () (min(%s) * 0)) and on () %s" % (up, page, up, PROM)),
            words + (own_words if mine else [])))
    return lanes


def axis_width(s, check, servers, roles):
    """The label column of the board and the band: wide enough for the longest line label."""
    labels = [lbl for _, lbl, _, _, _ in check + servers] + [lbl for _, lbl, _, _ in roles]
    return max(140, 7 * max(len(lbl) for lbl in labels) + 24)


def spacer_lane():
    """An empty lane: between the check lines and the server lines of History, and closing the role band
    where needed (first_screen). No bar, no name, no uptime."""
    return ("Z", ZERO_WIDTH, "vector(-2)", "vector(-2)", [(-2, ZERO_WIDTH, "transparent")])


def board_row_height(title, height, lanes):
    """The rowHeight (a share of each lane) that makes every bar BAR_PX thick in a panel height rows tall
    with lanes lanes, titled or not."""
    lane = (grid_px(height) - (CHROME_TITLED if title else CHROME_UNTITLED) - PLOT_INSET) / float(len(lanes))
    return round(min(1.0, BAR_PX / lane), 2)


def status_history(pid, title, desc, lanes, height, axis):
    """One line of bars per lane, every bar BAR_PX thick, without a time axis: the key line under the role
    band names the range's ends."""
    targets = [target(ref, bars, lbl, interval="1m") for ref, lbl, bars, _, _ in lanes]
    defaults = {"color": {"mode": "thresholds"}, "thresholds": steps((NODATA, None)),
                "custom": {"axisPlacement": "auto", "axisWidth": axis, "fillOpacity": 100, "lineWidth": 0},
                "mappings": special_map("null", "No data", NODATA)}
    overrides = [override("byType", "time", custom__axisPlacement="hidden")]
    overrides += [override("byFrameRefID", ref, mappings=lane_map(words)) for ref, _, _, _, words in lanes]
    options = {"colWidth": 0.72, "legend": {"displayMode": "list", "placement": "bottom", "showLegend": False},
               "rowHeight": board_row_height(title, height, lanes), "showValue": "never",
               "tooltip": {"mode": "multi", "sort": "none"}}
    p = panel(pid, "status-history", title, desc, targets, defaults, overrides, options)
    p.update({"interval": "1m", "maxDataPoints": 150, "transparent": True})
    return p


def uptime_column(pid, title, lanes, height):
    """The uptime of each lane, printed on the lane itself: a state timeline with the same lanes,
    height, chrome, step and rowHeight as the bars beside it and invisible fill. Its lane-name axis is
    1 px wide, so its plot is inset like the bars' and each value sits on its line. Each query is the
    uptime read once, at the end of the range, and held over it; every query returns a value, so no
    value slides into another line's place. The lane names are distinct zero-width spaces, so no label
    shows. No description: the History panel explains the values."""
    targets = [target(ref, "last_over_time((%s %s)[1m:1m] @ end())" % (up, UP_LOST), ZERO_WIDTH * (i + 1),
                      interval="1m")
               for i, (ref, _, _, up, _) in enumerate(lanes)]
    defaults = {"color": {"fixedColor": "transparent", "mode": "fixed"}, "decimals": 2, "unit": "percentunit",
                "thresholds": steps(("text", None)),
                "custom": {"axisPlacement": "left", "axisWidth": 1, "fillOpacity": 0, "lineWidth": 0},
                "mappings": range_map(0.99995, 1, "100%") + mapping((-1, "No data", GREY), (-2, ZERO_WIDTH, "transparent"))
                + special_map("nan", "No data")}
    options = {"alignValue": "left", "legend": {"displayMode": "list", "placement": "bottom", "showLegend": False},
               "mergeValues": True, "rowHeight": board_row_height(title, height, lanes), "showValue": "always",
               "tooltip": {"mode": "none", "sort": "none"}}
    p = panel(pid, "state-timeline", title, "", targets, defaults,
              [override("byType", "time", custom__axisPlacement="hidden")], options)
    p.update({"interval": "1m", "maxDataPoints": 150, "transparent": True})
    return p


def role_lanes(s):
    """The role band's lanes: (refId, label, query, words). Each step reads its whole time block, so
    at 7 or 30 days a repoint or an outage shorter than a step still shows: a step shows its worst
    moment, as the board does."""
    sig = signing_nodes(s)
    moved = ("sum by (host) (%s) > 0" % in_block("increase", PV) if s.remote else
             "max by (host) (%s) > 0" % in_block("changes", LSH))

    def gated(expr):
        """A lane reads grey "No data" (-99) wherever monitoring had no data, as the board's lines do:
        never a role or a problem from a step in which the alert rules did not run."""
        return "((%s) and on () %s) or %s * 0 - 99" % (expr, evaluated(), seen('up{job="prometheus"}'))

    no_data = [(-99, "No data", NODATA)]
    vns, nna = 'alertname="ValidatorNotSigning"', 'alertname="NodeNotAdvancing"'
    lanes = [("A", "Validator signs through", gated(chain(
        "%s * 0 - 6" % firing('alertname="ValidatorSigningTwice"') if s.twice else None,
        "((%s unless on () %s) or %s) * 0 - 4" % (firing(vns), firing(nna), fired_apart(vns, nna)),
        "(max(%s) == 1) * 0 - 3" % in_block("max_over_time", "((changes(max(%s)[2m:]) == bool 0) and on () %s)"
                                            % (H, STALL_SEEN), sub=True),
        # Out of the set at some step of the block, as the board reads every line: never from one reading.
        "(max(%s) == 1) * 0 - 2" % in_block("max_over_time", "((max(%s) * 0 + 1) unless on () %s)"
                                            % (POWER, IN_SET_OR_NEW), sub=True),
        "%s * 0 - 5" % active('alertname="ValidatorSigningTwice"') if s.twice else None,
        "(max(%s) == 1) * 0 - 1" % in_block("max_over_time", "((count(%s) or vector(0)) > bool 1)" % sig, sub=True)
        if s.remote else None,
        "(min(%s) == 0) * 0" % in_block("min_over_time", "(count(%s) or vector(0))" % sig, sub=True),
        "max(%s and on (host) (%s))" % (lookup(s.nodes), moved),
        "max(%s) * 0" % H)),
        [(i + 1, h, s.tint(h)) for i, h in enumerate(s.nodes)]
        + ([(-1, "Two nodes" if s.multi else "Switching", NODATA)] if s.remote else [])
        + ([(-5, "Two signers", WARN), (-6, "Two signers", ALERT)] if s.twice else [])
        + [(0, "Not signing", WARN), (-4, "Not signing", ALERT), (-2, "Out of set", NODATA),
           (-3, "Nodes stalled", NODATA)] + no_data)]
    if s.fibre:
        seen_f = fibre_seen(s)
        lanes.append(("B", "Fibre active on", gated(chain(
            "%s * 0 - 4" % firing('alertname="FibreDown",check=""'),
            "(max(%s) == 1) * 0 - 1" % in_block("max_over_time", "(count(%s) > bool 1)" % seen_f, sub=True),
            # No Fibre at some step of the block, so a short outage still shows on long ranges.
            '((min(%s) == 0) * 0 and on () (count(%s == 1) > 0))'
            % (in_block("min_over_time", "(count(%s) or vector(0))" % seen_f, sub=True), s.node_up),
            "max(%s and on (host) %s)" % (lookup(s.fibre_hosts), seen_f),
            "(count(%s) > 0) * 0 - 5" % fibre_lost(s),
            '(((count(%s) or vector(0)) == 0) * 0 and on () (count(%s == 1) > 0))' % (seen_f, s.node_up))),
            [(i + 1, h, s.tint(h)) for i, h in enumerate(s.fibre_hosts)]
            + [(-1, "Two Fibres", WARN), (0, "Not running", WARN), (-4, "Not running", ALERT),
               (-5, "Not seen", WARN)] + no_data))
    if s.fibre and s.remote:
        # The node Fibre sends its signing requests to: raw bytes in the last 15 minutes, else the last hour.
        fsv = "(%s or %s)" % (fibre_via(s, "15m"), fibre_via(s, "1h"))
        # Amber only while a node signs for the validator and Fibre's node is another: while no node
        # signs (tmkms down), that is the signer's outage, not Fibre's setup.
        lanes.append(("C", "Fibre signs through", gated(chain(
            "((%s + 100 unless on () (%s == on () max(%s and on (host) %s))) unless on () absent(%s))"
            % (fsv, fsv, lookup(s.nodes), sig, sig),
            fsv,
            "(count(%s) * 0 - 1)" % fibre_seen(s))),
            [(i + 1, h, s.tint(h)) for i, h in enumerate(s.nodes)]
            + [(101 + i, h, WARN) for i, h in enumerate(s.nodes)] + [(-1, "None seen, 1 h", NODATA)] + no_data))
    return lanes


def role_band(s, lanes, height, axis):
    """The role band: who held each role over the selected time, its bars BAR_PX thick and, like History,
    without a time axis, so both plots end on one x (a time axis adds 8 px of padding on the right)."""
    desc = ("Which server held each role over the selected time, read from the metrics, never from the inventory. "
            "Validator signs through: the server whose node made the validator's signatures; a tmkms repoint shows "
            "as the name changing.")
    if s.fibre:
        desc += " Fibre active on: the server whose Fibre unit runs."
    if s.fibre and s.remote:
        desc += " Fibre signs through: the node Fibre sends its signing requests to, amber when it is another."
    desc += (" Amber: nobody or two servers held the role; red: its page alert fired. Every word: the panel guide.")
    desc += more(["ValidatorNotSigning"] + (["ValidatorSigningTwice"] if s.twice else [])
                 + (["FibreDown"] if s.fibre else []), [PANEL_GUIDE, MAINNET_LAYOUT])
    targets = [target(ref, expr, lbl, interval="1m") for ref, lbl, expr, _ in lanes]
    defaults = {"color": {"fixedColor": "#8E8E8E26", "mode": "fixed"},
                "custom": {"axisWidth": axis, "fillOpacity": 100, "lineWidth": 0},
                "mappings": special_map("null", "No data", NODATA)}
    overrides = [override("byType", "time", custom__axisPlacement="hidden")]
    overrides += [override("byFrameRefID", ref, mappings=lane_map(words)) for ref, _, _, words in lanes]
    options = {"alignValue": "left", "legend": {"displayMode": "list", "placement": "bottom", "showLegend": False},
               "mergeValues": True, "rowHeight": board_row_height("", height, lanes), "showValue": "always",
               "tooltip": {"mode": "single", "sort": "none"}}
    p = panel(25, "state-timeline", "", desc, targets, defaults, overrides, options)
    p.update({"interval": "1m", "maxDataPoints": 1500, "transparent": True})
    return p


def key_line(s, axis, width):
    """The line under the role band: the selected range's first and last moment (in the dashboard's time
    zone, UTC) under the two ends of the plots, which have no time axis, and between them the one rule
    for every colour on the screen: each server's name in its colour, a divider, then the states, each
    with its 10 px square, so a square always means a state. It is one line in an untitled h1 (width
    columns) whose font sets the line height to KEY_PX, lifted 1 px because the font's glyph box is a
    little taller than its line, so Grafana never scrolls it (the text panel's sanitiser drops
    line-height, overflow, flex and column-gap, but keeps font, gap, flex-grow, flex-basis, flex-wrap,
    margin, padding and border). The servers and the states are two groups in a box laid out from the
    right that wraps upwards: where the line has no room for the servers they wrap above the panel's
    top, which its scroll box clips without scrolling, and the states stay. The divider is the servers
    group's right border, so it wraps away with them."""
    swatch = ('<span style="display:flex;align-items:center"><span style="display:block;width:10px;height:10px;'
              'border-radius:2px;background-color:%s;margin-right:4px"></span>%s</span>')
    states = [(OK, "Fine"), (WARN, "Warning"), (ALERT, "Urgent"), (NODATA, "No data")]
    hosts = [(c, name) for _, name, c in band_columns(s) if c]
    gap = 14
    apart = 3 * gap // 2                 # from the last server to the divider, and from the divider to the states
    ends = ["${__from:text}", "${__to:text} UTC"]
    room = (inner_px(width) - axis - text_px("2026-10-21 09:18:15", KEY_PX)
            - text_px("2026-10-21 09:18:15 UTC", KEY_PX) - 4 * gap)
    if sum(text_px(t, KEY_PX) + 14 for _, t in states) + gap * (len(states) - 1) > room:
        raise SystemExit("The key line under the role band does not fit %d columns" % width)
    group = '<span style="display:flex;align-items:center;gap:%dpx%s">%s</span>'
    boxes = group % (gap, "", "".join(swatch % state for state in states))
    if hosts:
        boxes += group % (gap, ";padding-right:%dpx;border-right:1px solid %s66" % (apart, GREY),
                          "".join('<span style="color:%s">%s</span>' % (c, name) for c, name in hosts))
    content = ('<div style="display:flex;align-items:center;height:%dpx;margin-top:-1px;font:%dpx/%dpx Inter, '
               'Helvetica, Arial, sans-serif;color:#8e8e8e;padding-left:%dpx">'
               '<span style="flex-shrink:0">%s</span>'
               '<span style="flex-grow:1;flex-basis:0;min-width:0;height:%dpx;display:flex;flex-direction:row-reverse;'
               'flex-wrap:wrap-reverse;justify-content:center;align-items:center;gap:0 %dpx;margin:0 %dpx">%s</span>'
               '<span style="flex-shrink:0">%s</span></div>'
               % (KEY_PX, KEY_PX, KEY_PX, axis, ends[0], KEY_PX, apart, 2 * gap, boxes, ends[1]))
    return {"id": 24, "type": "text", "title": "", "transparent": True,
            "options": {"content": content, "mode": "html"}}


def role_codes(s, short, hosts=None):
    """(code, words, colour) of every Role the servers (or hosts) can show. A server's parts add up to
    one code: node 1-3, Fibre 4 or 8, KMS 16-64; 128 the monitor on a host of its own. Amber while the
    node does not sign or tmkms is stopped or not loaded; grey for a standby Fibre alone."""
    hosts = s.servers if hosts is None else hosts
    pick = 2 if short else 1
    node = [(0, None)] + ([(p[0], p[pick]) for p in NODE_PARTS] if set(hosts) & set(s.nodes) else [])
    fibre = [(0, None)] + ([(p[0], p[pick]) for p in FIBRE_PARTS] if set(hosts) & set(s.fibre_hosts) else [])
    kms = [(0, None)] + ([(p[0], p[pick]) for p in KMS_PARTS] if set(hosts) & set(s.kms_hosts) else [])
    codes = [(-1, "Not reporting", "orange"), (0, "–", GREY)]
    for n, nw in node:
        for f, fw in fibre:
            for k, kw in kms:
                if n + f + k:
                    color = "orange" if n == 3 or k in (32, 64) else GREY if n + f + k == 8 else "text"
                    codes.append((n + f + k, ", ".join(w for w in (nw, fw, kw) if w), color))
    if s.mon_only and s.monitor in hosts:
        codes.append((128, "Monitoring", "text"))
    return codes


def table_width(words):
    """Fixed width in px of a text column for its longest text."""
    return int(math.ceil(max(len(w) for w in words) * TABLE_CHAR + TABLE_PAD))


def servers_table(s):
    """(panel, height) of Servers now, the Servers band's form for more than MAX_COLUMNS columns: one row
    per configured server, even when nothing is read from it.
    Every cell has a word for "no data" (the server should report it and does not) and "–" (does not
    apply), so no cell is blank and a silent server never looks fine."""
    order = lookup(s.servers)
    na = "or on (host) (%s * 0 - 9)" % order
    nodata_all = "or on (host) (%s * 0 - 8)" % order
    sig = signing_nodes(s)
    shared = [(-8, "no data", GREY), (-9, "–", GREY)]

    node_up = '(max by (host) (up{job="celestia-app"}) == 1)'
    first = "%s * 0 + 1" % sig
    if s.twice:
        # The node tmkms is leaving during a repoint is already the backup.
        fresh = newer(s)
        leaving = "((%s unless on (host) %s) and on () (count(%s) > 0) unless on () %s)" % (sig, fresh, fresh,
                                                                                         two_signers(s))
        first = "(%s unless on (host) %s) * 0 + 1" % (sig, leaving)
    parts = [label("%s or (%s * 0 + 2 and on () count(%s)) or %s * 0 + 3" % (first, node_up, sig, node_up),
                   "part", "node")]
    if s.fibre:
        parts.append(label('%s * 0 + 4 or (max by (host) (up{job="otelcol"}) == 1) * 0 + 8' % fibre_seen(s),
                           "part", "fibre"))
    if s.kms_hosts:
        tmkms = 'node_systemd_unit_state{%s,state="active",host=~%s}' % (
            s.is_unit("tmkms"), q("|".join(re2(h) for h in s.kms_hosts)))
        kms = "(max by (host) (%s) == 1) * 0 + 16 or (max by (host) (%s) == 0) * 0 + 32" % (tmkms, tmkms)
        # The ServiceDown rule for a unit that is not loaded, per KMS host.
        for k in s.kms_hosts:
            kms += (' or (absent(node_systemd_unit_state{host=%s,name=%s}) and on () (up{job="node",host=%s} == 1)) '
                    "* 0 + 64" % (q(k), q(s.unit(k, "tmkms")), q(k)))
        parts.append(label(kms, "part", "kms"))
    if s.mon_only:
        # The monitor on a host of its own runs no Celestia service: its role is the monitoring.
        parts.append(label("%s * 0 + 128" % lookup([s.monitor]), "part", "monitor"))
    # A server whose node_exporter does not answer never shows a role it may no longer hold.
    role = ("((%s unless on (host) %s) * 0 - 1) or on (host) sum by (host) (%s) or on (host) (%s * 0)"
            % (order, NE_UP, " or ".join(parts), order))

    node = "%s %s" % (chain(
        '(max by (host) (up{job="celestia-app"}) == 0) * 0 - 1',
        "(max by (host) (changes(%s[2m])) == 0) * 0 - 2" % H,
        '(max by (host) (cometbft_blocksync_syncing{job="celestia-app"}) == 1) * 0 - 3',
        "(scalar(max(%s)) - max by (host) (%s))" % (H, H)), na)
    # NodePeersLow's words: red once its page fires, amber while either rule is pending or fires.
    peers = "%s or on (host) (%s * 0 - 8) %s" % (chain(
        "max by (host) (ALERTS{%s,alertstate=\"firing\"}) * 0 - 11" % PEERS_PAGE,
        'max by (host) (ALERTS{alertname="NodePeersLow"}) * 0 - 12',
        'max by (host) (cometbft_p2p_peers{job="celestia-app"})'), lookup(s.nodes), na)
    # RemoteSignerProblem's proposal failures. With tmkms signing through several nodes every node asks for
    # each proposal and tmkms refuses all but the first: a fault only while no node got one signed, as the rule.
    bad = ('(max by (host) (increase(cometbft_privval_signing_failures_total{job="celestia-app",message_type="proposal",'
           'reason!~"timeout|no_connection"}[6h])) > 0)')
    if s.multi:
        bad = ('(%s unless on () (sum(increase(cometbft_privval_signing_latency_seconds_count{job="celestia-app",'
               'message_type="proposal"}[6h])) > 0))' % bad)
    signer = "%s %s" % (chain(
        "%s * 0 - 5" % bad,
        "(%s and on (host) %s)" % (MEDIAN, sig),
        "(%s > %s) * 0 - 6" % (MEDIAN, num(s.S)),
        "((%s * 0 - 7) and on () ((count(%s) or vector(0)) == 0) and on () %s)" % (lookup(s.nodes), sig, IN_SET)), na)
    cpu = ('(1 - sum by (host) (rate(node_cpu_seconds_total{mode=~"idle|iowait"}[5m])) '
           '/ count by (host) (node_cpu_seconds_total{mode="idle"})) %s' % nodata_all)
    memory = ("(1 - max by (host) (node_memory_MemAvailable_bytes) / max by (host) (node_memory_MemTotal_bytes)) %s"
              % nodata_all)
    peaks = []
    for link in s.links:
        sel = devices(link["host"], VIRT)
        cap = link_capacity(link, sel)
        # A 75 s window holds a full minute of 15 s scrapes; [1m] would hold 45 s of them and read
        # the busiest 45 seconds instead of the busiest minute.
        rates = ["max_over_time((sum by (host) (rate(node_network_%s_bytes_total%s[75s])))[1h:1m])" % (m, sel)
                 for m in ("receive", "transmit")]
        peaks.append('(max by (host) (%s or %s) or ((max by (host) (up{job="node",host=%s}) == 1) * 0 - 4))'
                     % (label("%s / %s" % (rates[0], cap), "dir", "in"), label("%s / %s" % (rates[1], cap), "dir", "out"),
                        q(link["host"])))
    network = "%s %s" % (" or ".join(peaks), nodata_all)
    # The units whose restarts count, with ServiceDown's crash-loop words (TITLES) for each, and their names.
    crashes = ([("appd", "Node crashing")] + ([("fibre", "Fibre crash")] if s.fibre else [])
               + ([("tmkms", "Signer crash")] if s.kms_hosts else []))
    units = [u for k, _ in crashes for u in s.names(k)]
    restart_sel = "{name=~%s}" % q("|".join(re2(u) for u in units))
    crash = " or ".join("(floor(max by (host) (increase(node_systemd_service_restart_total{%s}[30m]))) > %s%s) * 0 - %d"
                        % (s.is_unit(k), num(s.CL), " unless on (host) (%s)" % BOOTED if k == "fibre" else "", 10 + i)
                        for i, (k, _) in enumerate(crashes))
    restarts = ("(%s or floor(sum by (host) (increase(node_systemd_service_restart_total%s[24h])))) and on (host) %s %s"
                % (crash, restart_sel, NE_UP, nodata_all))

    gauge = {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}
    behind = min(5, s.NB)
    stale_word = "> %s ms, stale" % s.ms()
    # (refId, header, query, words a text column can show or None for a gauge column, other properties)
    columns = [
        ("A", "Role", role, None, {}),
        ("B", "Node", node, ["in sync", "stalled", "syncing", "No node data", "9999 behind"] + [w for _, w, _ in shared],
         dict(unit="suffix: behind", thresholds=steps(("text", None), ("orange", s.NB + 1)),
              mappings=range_map(0, behind, "in sync", "text") + mapping(
                  (-1, "No node data", "orange"), (-2, "stalled", "orange"), (-3, "syncing", "text"), *shared))),
        ("C", "Peers", peers, ["999", "No peers", "Few peers"] + [w for _, w, _ in shared],
         dict(custom__align="right", mappings=mapping((-11, "No peers", "red"), (-12, "Few peers", "orange"), *shared))),
    ]
    if s.remote:
        columns.append(
            ("D", "Signing time", signer, ["Bad proposals", stale_word, "Not signing", "9.99 ms"] + [w for _, w, _ in shared],
             dict(unit="s", decimals=2, custom__align="right", thresholds=steps(("text", None), ("orange", s.S)),
                  mappings=mapping((-5, "Bad proposals", "orange"), (-6, stale_word, GREY),
                                   (-7, "Not signing", "orange"), *shared))))
    columns += [
        ("E", "CPU", cpu, None, dict(thresholds=steps((GREY, None)), mappings=mapping(*shared))),
        ("F", "Memory", memory, None, dict(thresholds=steps((GREY, None)), mappings=mapping(*shared))),
    ]
    # Disk used: the fullest disk DiskLow watches, data mounts and Fibre stores alike, as a grey bar.
    # A Fibre store's share has no limit while its host reports a storage budget (the budget rule
    # watches it instead), so no threshold can colour the bar honestly for every server: each rule's
    # live condition is a word instead, and only that is amber or red.
    watched = s.data_mounts + s.stores
    if watched:
        disk_hosts = [h for h in s.servers if h in {m[0] for m in watched}]
        budget = "max by (host) (fibre_server_upload_shard_budget_bytes)"
        # DiskLow's free-space limit: on data mounts, and on Fibre stores while their host reports no budget.
        limited = [free_share([m]) for m in s.data_mounts]
        limited += ["(%s unless on (host) (%s > 0))" % (free_share([m]), budget) for m in s.stores]
        # DiskLow's budget rule, on every Fibre store (a data mount too): the room the budget still needs is
        # more than the store's free space.
        tight = ["(%s - max by (host) (fibre_server_upload_shard_occupancy_bytes) > on (host) max by (host) "
                 "(node_filesystem_avail_bytes{host=%s,mountpoint=%s}) and on (host) (%s > 0))"
                 % (budget, q(m["host"]), q(m["mount"]), budget) for m in s.fibre_mounts if m["mount"]]
        disk = "%s or on (host) (%s * 0 - 8) %s" % (chain(
            'max by (host) (ALERTS{alertname="DiskLow",severity="page",alertstate="firing"}) * 0 - 7',
            "(max by (host) (%s) == 1) * 0 - 6" % disks(watched, "node_filesystem_readonly"),
            "(min by (host) ((%s)) < %s) * 0 - 5" % (" or ".join(limited), num(s.DM)),
            "(max by (host) (%s)) * 0 - 4" % " or ".join(tight) if tight else None,
            "(1 - min by (host) (%s))" % free_share(watched)), lookup(disk_hosts), na)
        columns.append(("G", "Disk used", disk, None,
                        dict(thresholds=steps((GREY, None)),
                             mappings=mapping((-7, "Disk critical", "red"), (-6, "Disk low", "orange"),
                                              (-5, "Disk low", "orange"), (-4, "Disk low", "orange"), *shared))))
    columns += [
        ("H", "Network peak", network, None,
         dict(thresholds=steps((GREY, None)), mappings=mapping((-4, "speed unknown", GREY), *shared))),
        ("I", "Restarts", restarts, [w for _, w in crashes] + ["999"] + [w for _, w, _ in shared],
         dict(custom__align="right", mappings=mapping(*[(-10 - i, w, "orange") for i, (_, w) in enumerate(crashes)],
                                                      *shared))),
    ]

    # Text columns get a fixed width from their longest text; the gauge columns share the rest. Role
    # takes its short words, and then the gauges a smaller minimum, where the full ones would not fit.
    gauges = [c for c in columns if c[3] is None and c[0] != "A"]
    fixed_px = {"Server": table_width(["Server"] + s.servers)}
    for _, heading, _, words, _ in columns:
        if words is not None:
            fixed_px[heading] = table_width([heading] + words)

    def role_px(short):
        return table_width(["Role"] + [w for h in s.servers for _, w, _ in role_codes(s, short, [h])])

    short = sum(fixed_px.values()) + role_px(False) + len(gauges) * GAUGE_MIN > TABLE_ROOM
    fixed_px["Role"] = role_px(short)
    room = TABLE_ROOM - sum(fixed_px.values())
    gauge_min = min(GAUGE_MIN, room // len(gauges))
    if gauge_min < GAUGE_FLOOR:
        raise SystemExit("The Servers now table needs %d px for its text columns (%s) and cannot keep %d px for each "
                         "gauge column within %d px; shorten the host names in the inventory"
                         % (sum(fixed_px.values()), ", ".join("%s %d" % kv for kv in sorted(fixed_px.items())),
                            GAUGE_FLOOR, TABLE_ROOM))

    targets = [target("Z", order, "", instant=True, fmt="table")]
    rename = {"host": "Server", "Value #Z": "Order"}
    index = {"host": 0}
    overrides = [override("byName", "Server", custom__width=fixed_px["Server"],
                          mappings=[m for h in s.servers for m in regex_map("^%s$" % js_regex(h), s.color(h)[0])])]
    for i, (ref, heading, expr, words, props) in enumerate(columns):
        targets.append(target(ref, expr, "", instant=True, fmt="table"))
        rename["Value #" + ref] = heading
        index["Value #" + ref] = i + 1
        props = dict(props)
        if ref == "A":
            props.update(custom__width=fixed_px["Role"], mappings=mapping(*role_codes(s, short)))
        elif words is not None:
            props["custom__width"] = fixed_px[heading]
        else:
            props.update(custom__cellOptions=gauge, custom__minWidth=gauge_min, unit="percentunit", decimals=0,
                         min=0, max=1)
        overrides.append(override("byName", heading, **props))

    desc = ("One row per server, in inventory order; every server you configured has a row even when nothing is read "
            "from it. Role: what the server does now, read from the metrics. Node: blocks behind the highest node "
            "('in sync' within %g). Peers: connected peers; amber Few peers while a node has fewer than %d "
            "(NodePeersLow), red No peers once it pages for the node the validator signs through. %sCPU, Memory, Disk "
            "used and Network peak: share in use, in grey. Restarts: systemd restarts of %s in 24 hours. An amber or "
            "red word is the alert's own, as in the Status cell; 'no data': the server should report this and does "
            "not; '–': does not apply. Every word: the panel guide."
            % (behind, s.PM, "Signing time: the median on the node that signs, amber from %s ms. " % s.ms()
               if s.remote else "", ", ".join(u[:-len(".service")] for u in units)))
    desc += more(["NodeBehind", "NodePeersLow", "RemoteSignerProblem", "DiskLow", "ServiceDown"] if s.remote else
                 ["NodeBehind", "NodePeersLow", "DiskLow", "ServiceDown"], [PANEL_GUIDE, NODE_EXPORTER_FULL])
    defaults = {"color": {"mode": "thresholds"}, "decimals": 0, "noValue": "No data", "unit": "none",
                "thresholds": steps(("text", None)),
                "custom": {"align": "auto", "cellOptions": {"type": "color-text"}, "filterable": False,
                           "inspect": False, "minWidth": 60}}
    options = {"cellHeight": "md", "enablePagination": False,
               "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
               "frozenColumns": {"left": 1}, "showHeader": True}
    p = panel(26, "table", "Servers now", desc, targets, defaults, overrides, options)
    p["transformations"] = [
        {"id": "merge", "options": {}},
        {"id": "sortBy", "options": {"fields": {}, "sort": [{"desc": False, "field": "Value #Z"}]}},
        {"id": "organize", "options": {"excludeByName": {"Time": True, "Value #Z": True},
                                       "indexByName": index, "renameByName": rename}}]
    return p, grid_rows(TABLE_HEADER_PX + TABLE_ROW_PX * len(s.servers), CHROME_TITLED)


# The Servers band, under the answers: what runs where now. One column per server, in servers order,
# and one for a remote signer whose host is not scraped. Each column is a card that names the
# server and gives each part it runs a row with its role now (always in one order, so a row of one
# card sits level with the same part's row in another), over a bar gauge of the server's hardware
# now in the server's colour. Roles come from the metrics, as everywhere: a tmkms repoint or a Fibre
# failover moves the words between cards. Words are plain text while fine, amber or red only where
# an alert watches, grey for "No data".

NOTHING = [(-8, "No data", GREY)]


def band_columns(s):
    """The Servers band's columns: (kind, name, hex colour or None). kind "server" for a host
    node_exporter runs on, "kms" for a KMS host whose node_exporter runs (celestia_mon_kms_scrape),
    "signer" for a remote signer whose host is not scraped (named by kms_names), which takes the
    next colour after the servers'."""
    cols = [("kms" if h in s.kms_hosts else "server", h, s.color(h)[1]) for h in s.servers]
    if s.remote and not s.kms_hosts:
        i = len(s.servers)
        cols.append(("signer", ", ".join(s.kms_names) or "Remote signer",
                     HOST_COLORS[i][1] if i < len(HOST_COLORS) else None))
    return cols


def pick(expr, host):
    """The value of expr (one series per host, labelled host only) for one host."""
    return "max((%s) and on (host) %s)" % (expr, label("vector(1)", "host", host))


def node_part(s):
    """What each consensus node does now, one series per host, as a code of NODE_WORDS. The node a
    repoint leaves is a backup at once, as the signer card says Switching meanwhile. Two signers is
    ValidatorSigningTwice itself, amber while pending, red once it pages. No peers and Few peers are
    NodePeersLow itself: red once its page fires, amber while either of its rules is pending or fires."""
    sig = signing_nodes(s)
    signing = sig
    if s.twice:
        fresh = newer(s)
        leaving = ("((%s unless on (host) %s) and on () (count(%s) > 0) unless on () %s)"
                   % (sig, fresh, fresh, two_signers(s)))
        signing = "(%s unless on (host) %s)" % (sig, leaving)
    up = '(max by (host) (up{job="celestia-app"}) == 1)'
    down = '(max by (host) (up{job="celestia-app"}) == 0)'
    # The height did not change in 2 minutes that hold two samples (not right after a start).
    stalled = ("((max by (host) (changes(%s[2m])) == 0) and on (host) (max by (host) (count_over_time(%s[2m])) > 1))"
               % (H, H))
    behind = "((scalar(max(%s)) - max by (host) (%s)) > %s)" % (H, H, num(s.NB))
    syncing = '(max by (host) (cometbft_blocksync_syncing{job="celestia-app"}) == 1)'
    # ServiceDown's crash loop carries job and no state (by_alert).
    loop = 'max by (host) (ALERTS{alertname="ServiceDown",unit="appd",alertstate="firing",job!="",state=""})'
    return chain(
        "%s * 0 + 11" % loop,
        "(%s unless on (host) %s) * 0 + 10" % (down, NE_UP),
        "%s * 0 + 9" % down,
        "(((%s and on () %s) * 0 + 4) + on () group_left () %s)" % (signing, two_signers(s), twice10(s))
        if s.twice else None,
        'max by (host) (ALERTS{%s,alertstate="firing"}) * 0 + 15' % PEERS_PAGE,
        'max by (host) (ALERTS{alertname="NodePeersLow"}) * 0 + 5',
        "%s * 0 + 1" % signing,
        "(%s and on (host) %s) * 0 + 8" % (up, stalled),
        "(%s and on (host) %s and on () count(%s)) * 0 + 3" % (up, behind, sig),
        "(%s and on (host) %s) * 0 + 13" % (up, behind),
        "((((%s * 0 + 6) unless on () count(%s)) and on () %s) + on () group_left () %s)" % (up, sig, IN_SET, VNS10),
        "(%s and on (host) %s) * 0 + 7" % (up, syncing),
        "(%s and on () count(%s)) * 0 + 2" % (up, sig),
        "%s * 0 + 12" % up)


# The Consensus node row: (code, words, words with a number, colour). The number ({peers}: the node's
# peers; {lag}: blocks behind the highest node) shows while it is known and has at most three digits;
# otherwise the plain words.
NODE_WORDS = [(1, "Signing", "Signing · {peers} peers", "text"),
              (2, "Backup, in sync", "Backup, in sync · {peers} peers", "text"),
              (3, "Node behind", "Node behind · {lag} blocks", "orange"),
              (13, "Node behind", "Node behind · {lag} blocks", "orange"),
              (4, "Two signers", None, "orange"), (14, "Two signers", None, "red"),
              (5, "Few peers", "Few peers · {peers}", "orange"), (15, "No peers", None, "red"),
              (6, "Not signing", None, "orange"), (16, "Not signing", None, "red"),
              (7, "Syncing", "Syncing, {lag} behind", "text"), (8, "Stalled", None, "orange"),
              (9, "No node data", None, "orange"), (10, "Not reporting", None, "orange"),
              (11, "Node crashing", None, "orange"), (12, "In sync", "In sync · {peers} peers", "text")] + \
    [(c, w, None, color) for c, w, color in NOTHING]
NUMBER_SAMPLE = "999"


def text_row(code, words, numbers):
    """A card row whose words carry numbers, as one series with the label text: code is the row's code
    (one series), words (code, words, words with numbers, colour) as NODE_WORDS, numbers (name, query of
    one series) the numbers by name. count_values turns the code and each number into a label; a missing
    number leaves its label empty, and its code shows the plain words. The colour rides on the text's
    last, invisible character (MARKS)."""
    x = 'count_values("code", %s)' % code
    names = [n for n, _ in numbers]
    for name, expr in numbers:
        x = ("(%s * on () group_left (%s) (count_values(%s, %s) or (vector(1) unless on () count(%s))))"
             % (x, name, q(name), expr, expr))
    x = 'label_join(%s, "key", ":", %s)' % (x, ", ".join(q(n) for n in ["code"] + names))
    for c, plain, numbered, color in words:
        mark = MARKS.get(color, "")
        x = 'label_replace(%s, "text", %s, "key", %s)' % (x, q(plain + mark), q("%d(:.*)?" % c))
        if numbered:
            used = [n for n in names if "{%s}" % n in numbered]
            pattern = ":".join(["%d" % c] + ["([0-9]{1,3})" if n in used else "[^:]*" for n in names])
            text = numbered
            for i, n in enumerate(used, 1):
                text = text.replace("{%s}" % n, "${%d}" % i)
            x = 'label_replace(%s, "text", %s, "key", %s)' % (x, q(text + mark), q(pattern))
    return "max by (text) (%s)" % x


def text_words(words):
    """Every text a text_row can show, numbers as NUMBER_SAMPLE, as (code, text, colour), for the fit checks."""
    out = []
    for c, plain, numbered, color in words:
        out.append((c, plain, color))
        if numbered:
            out.append((c, re.sub(r"\{\w+\}", NUMBER_SAMPLE, numbered), color))
    return out


def text_maps():
    """The colours of a text_row: its invisible last character, as regex mappings."""
    return [m for color, mark in MARKS.items() for m in regex_map(".*" + mark, color)]


def node_row(s, host):
    """The Consensus node row of host's card: its code, with its peers and how far it lags."""
    sel = "{job=\"celestia-app\",host=%s}" % q(host)
    numbers = [("peers", "max(cometbft_p2p_peers%s)" % sel),
               ("lag", "(max(%s) - max(cometbft_consensus_height%s))" % (H, sel))]
    return text_row("(%s or vector(-8))" % pick(node_part(s), host), NODE_WORDS, numbers)


def fibre_part(s):
    """What each Fibre host does now, one series per host, as a code of fibre_words(s). A standby stopped
    by design is plain text; it turns amber only where it could not take over (its collector is down).
    FibreNotServing shows by its reason while pending or firing, as the Status cell and the Fibre tile; Not
    ready is red once it pages, ahead of a crash loop, which holds back no page."""
    seen_f = fibre_seen(s)
    loop = 'max by (host) (ALERTS{alertname="ServiceDown",unit="fibre",alertstate="firing"})'
    collector = '(max by (host) (up{job="otelcol"}) == 1)'
    down10 = '(max(ALERTS{alertname="FibreDown",check="",alertstate="firing"}) * 10 or vector(0))'
    reasons = ['max by (host) (ALERTS{alertname="FibreNotServing",reason=%s}) * 0 + %d' % (q(r), 21 + i)
               for i, (r, _, need) in enumerate(NOT_SERVING) if s.has(need)]
    not_ready = 'max by (host) (ALERTS{%s,alertstate="firing"}) * 0 + %d' % (NOT_READY_PAGE, 31 + NOT_READY_AT)
    return chain(*(
        [not_ready, "%s * 0 + 11" % loop, "(%s and on () (count(%s) > 1)) * 0 + 7" % (seen_f, seen_f)] + reasons
        + ["%s * 0 + 1" % seen_f,
           "(((%s * 0 + 8) unless on () count(%s)) + on () group_left () %s)" % (NE_UP, seen_f, down10),
           "(%s unless on (host) %s) * 0 + 9" % (NE_UP, collector),
           "%s * 0 + 6" % NE_UP,
           "%s * 0 + 10" % lookup(s.fibre_hosts)]))


def fibre_words(s):
    """The Fibre row's words: D2's names for FibreNotServing's reasons, FibreBothActive and FibreDown."""
    return ([(1, "Active, serving", "text"), (7, "Two Fibres", "orange"), (6, "Standby, stopped", "text"),
             (9, "No Fibre data", "orange"), (8, "Not running", "orange"), (18, "Not running", "red"),
             (10, "Not reporting", "orange"), (11, "Fibre crash", "orange")]
            + [(21 + i, w, "orange") for i, (_, w, need) in enumerate(NOT_SERVING) if s.has(need)]
            + [(31 + NOT_READY_AT, "Not ready", "red")] + NOTHING)


def fibre_signer(s):
    """The node Fibre signs through, as its place in s.nodes: from Fibre's signatures (raw bytes) in the
    last 15 minutes, else the last hour, else the last day, for a quiet network. 100 more (amber) while
    the last hour's node is not the one the validator signs through. -1: none in a day."""
    sig = signing_nodes(s)
    recent = "(%s or %s)" % (fibre_via(s, "15m"), fibre_via(s, "1h"))
    return chain(
        "((%s + 100 unless on () (%s == on () max(%s and on (host) %s))) unless on () absent(%s))"
        % (recent, recent, lookup(s.nodes), sig, sig),
        recent, fibre_via(s, "24h"), "vector(-1)")


def fibre_signer_words(s):
    return ([(i + 1, h, s.color(h)[0]) for i, h in enumerate(s.nodes)]
            + [(101 + i, h, "orange") for i, h in enumerate(s.nodes)] + [(-1, "None in 24 h", GREY)] + NOTHING)


def signer_service(s, host):
    """tmkms's unit on a scraped KMS host, as ServiceDown reads it."""
    sel = "host=%s,name=%s" % (q(host), q(s.unit(host, "tmkms")))
    return chain(
        'max(ALERTS{alertname="ServiceDown",unit="tmkms",alertstate="firing",host=%s,job!="",state=""}) * 0 + 11'
        % q(host),
        '(max(absent(node_systemd_unit_state{%s})) and on () (max(up{job="node",host=%s}) == 1)) * 0 + 4' % (sel, q(host)),
        '(max(node_systemd_unit_state{%s,state="active"}) == 1) * 0 + 1' % sel,
        '(max(node_systemd_unit_state{%s,state="active"}) == 0) * 0 + 3' % sel,
        '(max(up{job="node",host=%s}) == 0) * 0 + 10' % q(host),
        "vector(-8)")


def card_rows(s, kind, host, short):
    """(name, query, words, text) of each row of a card; text: the query gives the label text (text_row),
    else a code of words. A card has a row only for a part its server runs, always in this order. Server
    cards: Consensus node (a node host), Fibre (a Fibre host, Fibre on; a stopped standby too), Monitoring
    (the monitor's own server). The signer's card: where the validator signs, where Fibre signs (Fibre
    on), then the signer's own service on a scraped KMS host. short: the names that fit a quarter."""
    if kind == "server":
        rows = []
        if host in s.nodes:
            rows.append(("Node" if short else "Consensus node", node_row(s, host), text_words(NODE_WORDS), True))
        if s.fibre and host in s.fibre_hosts:
            rows.append(("Fibre", "%s or vector(-8)" % pick(fibre_part(s), host), fibre_words(s), False))
        if host == s.monitor:
            rows.append(("Monitoring", monitoring_now(s), MONITOR_WORDS + NOTHING, False))
        return rows
    # No data keeps the row (and the rows under it) in its place.
    rows = [("Signs through" if short else "Validator signs through", "(%s) or vector(-8)" % signing_expr(s),
             signing_words(s) + NOTHING, False)]
    if s.fibre:
        rows.append(("Fibre via" if short else "Fibre signs through", fibre_signer(s), fibre_signer_words(s), False))
    if kind == "kms":
        rows.append(("Signer service", signer_service(s, host),
                     [(1, "Running", "text"), (3, "Signer down", "orange"), (4, "No signer", "orange"),
                      (10, "Not reporting", "orange"), (11, "Signer crash", "orange")] + NOTHING, False))
    return rows


def blank_rows(rows, depth):
    """rows ended in blank rows up to depth, so a card with fewer parts keeps the band's row spacing and
    each row its line (a horizontal stat shares its height evenly among its values). Each blank row has a
    distinct invisible name and an invisible word, as spacer_lane()."""
    return rows + [(ZERO_WIDTH * (k + 1), "vector(-11)", [(-11, ZERO_WIDTH, "transparent")], False)
                   for k in range(len(rows), depth)]


def card_fits(rows, size, width):
    """Every row's name and its longest word fit one line of a card width columns wide at size px."""
    room = inner_px(width) - 2 * CARD_PAD - CARD_GAP
    return all(text_px(name, size) + 4 + max(text_px(w, size) for _, w, _ in words) <= room
               for name, _, words, _ in rows)


# Grafana sizes each bar's value box by its own text and gives the track the rest, so "2%" under "43%"
# would end its track a digit further right. A one-digit share starts with a figure space instead, as
# wide as a digit, and the tracks above one another end within a few px.
PAD_PERCENT = [m for k in range(10)
               for m in range_map(round((k - 0.5) / 100, 6), round((k + 0.5) / 100 - 1e-6, 6), "\u2007%d%%" % k)]


def bar_name_px(text, size):
    """Width in px of a bar's name at size px, its leading spaces (LEAD_EM) included."""
    bare = text.lstrip("".join(LEAD_EM))
    return value_px(bare, size) + size * sum(LEAD_EM[c] for c in text[:len(text) - len(bare)])


def name_column(names, width):
    """The width in px of the names' column of a bar gauge width columns wide, as Grafana sizes it: the
    name with the most characters at BAR_MEASURE_PX and BAR_NAME_GAP, at most BAR_NAME_SHARE of the panel."""
    return min(bar_name_px(max(names, key=len), BAR_MEASURE_PX) + BAR_NAME_GAP, BAR_NAME_SHARE * inner_px(width))


def bars_left(panels, width):
    """The largest of BAR_NAME_SIZES at which the names fit left of the bars in every panel width columns
    wide, else None: (names, numbers, words) per panel. The names' column is Grafana's (name_column);
    each bar's value takes its own width and BAR_VALUE_GAP after its track. A number needs BAR_MIN_PX of
    bar beside it, a word (a state, whose bar is empty) only room."""
    inner = inner_px(width)
    for size in BAR_NAME_SIZES:
        fits = True
        for names, numbers, words in panels:
            column = name_column(names, width)
            room = inner - column
            if (any(bar_name_px(n, size) + BAR_NAME_GAP > column for n in names)
                    or room - max(value_px(v, BAR_VALUE_PX) for v in numbers) - BAR_VALUE_GAP < BAR_MIN_PX
                    or room - max(value_px(v, BAR_VALUE_PX) for v in words) - BAR_VALUE_GAP < 0):
                fits = False
        if fits:
            return size
    return None


def band_bars(pid, title, desc, fields, hue, unit, maximum, decimals, overrides=(), name_px=None):
    """Four bars, one per (name, query) of fields, from 0 to maximum, in the column's colour (hue), the
    value after a faint track. name_px: each name left of its bar at that size, in one column, so the bars
    of a panel start on one x; None: above it at BAR_NAME_PX (a panel one row higher)."""
    defaults = {"color": {"mode": "thresholds"}, "decimals": decimals, "max": maximum, "min": 0, "unit": unit,
                "noValue": "No data", "thresholds": steps((hue, None)),
                "mappings": mapping(*NOTHING) + special_map("nan", "No data", GREY)
                + (PAD_PERCENT if unit == "percentunit" else [])}
    options = {"displayMode": "basic", "maxVizHeight": 40, "minVizHeight": 16,
               "namePlacement": "left" if name_px else "top",
               "orientation": "horizontal", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
               "showUnfilled": True, "sizing": "auto",
               "text": {"titleSize": name_px or BAR_NAME_PX, "valueSize": BAR_VALUE_PX}, "valueMode": "color"}
    targets = [target(chr(65 + i), expr, name, instant=True) for i, (name, expr) in enumerate(fields)]
    return panel(pid, "bargauge", title, desc, targets, defaults, overrides, options)


def disk_now(s, host):
    """The fullest disk of host that DiskLow watches by its free space (data mounts, and Fibre stores
    while Fibre reports no storage budget), as the share used; or DiskLow's live condition as a code:
    -7 full or read-only (paged), -6 read-only, -4 a Fibre store smaller than its budget needs."""
    data = [m for m in s.data_mounts if m[0] == host]
    stores = [m for m in s.stores if m[0] == host]
    if not data and not stores:
        return "vector(-9)"
    budget = "max by (host) (fibre_server_upload_shard_budget_bytes)"
    limited = [free_share([m]) for m in data] + ["(%s unless on (host) (%s > 0))" % (free_share([m]), budget)
                                                 for m in stores]
    tight = ["(%s - max by (host) (fibre_server_upload_shard_occupancy_bytes) > on (host) max by (host) "
             "(node_filesystem_avail_bytes{host=%s,mountpoint=%s}) and on (host) (%s > 0))"
             % (budget, q(m["host"]), q(m["mount"]), budget) for m in s.fibre_mounts if m["host"] == host and m["mount"]]
    return chain(
        'max(ALERTS{alertname="DiskLow",severity="page",alertstate="firing",host=%s}) * 0 - 7' % q(host),
        "(max(%s) == 1) * 0 - 6" % disks(data + stores, "node_filesystem_readonly"),
        "max(%s) * 0 - 4" % " or ".join(tight) if tight else None,
        "(1 - min(%s))" % " or ".join(limited),
        # A host whose only watched disk is a Fibre store with a budget: the Fibre row shows it.
        "vector(%d)" % (-8 if data else -5))


def network_now(s, host):
    """The busiest minute of host's link in the last hour, in or out, as a share of the link speed
    (celestia_mon_link_mbps, else the NIC speed); -4 while the speed is unknown."""
    link = next((x for x in s.links if x["host"] == host), None)
    if link is None:
        return "vector(-9)"
    sel = devices(host, VIRT)
    cap = link_capacity(link, sel)
    # A 75 s window holds a full minute of 15 s scrapes.
    rates = ["max_over_time((sum by (host) (rate(node_network_%s_bytes_total%s[75s])))[1h:1m])" % (m, sel)
             for m in ("receive", "transmit")]
    return chain("max(%s or %s)" % (label("%s / %s" % (rates[0], cap), "dir", "in"),
                                    label("%s / %s" % (rates[1], cap), "dir", "out")),
                 '(max(up{job="node",host=%s}) == 1) * 0 - 4' % q(host), "vector(-8)")


HARDWARE_NAMES = ["CPU", "Memory", "Disk used", "Network peak"]
# The longest hardware name beside a signer column leads with these spaces (level_names), each its
# width in em in Grafana's Inter: a figure space, as wide as a digit, then four-per-em spaces (ROW_INDENT's).
FIGURE, QUARTER = "\u2007", "\u2005"
LEAD_EM = {FIGURE: 0.648, QUARTER: 0.25}
# The words of the Disk used and Network peak bars: DiskLow's own words (TITLES) while its live condition
# holds (-7 paged: full or read-only; -6 read-only; -4 a Fibre store smaller than its budget needs), and
# the cases no bar can show.
DISK_WORDS = [(-7, "Disk critical", "red"), (-6, "Disk low", "orange"), (-4, "Disk low", "orange"),
              (-5, "Fibre store", GREY), (-9, "Not watched", GREY)]
LINK_WORDS = [(-4, "Speed unknown", GREY), (-9, "Not watched", GREY)]
# The signer's bars: the widest number they show, and their words.
SIGNING_SAMPLE = "999.99 ms"
SIGNING_WORDS = [(-5, "Bad proposals", "orange"), (-9, "None", GREY), (-8, "No data", GREY)]


def lower_texts(s, kind, names=HARDWARE_NAMES):
    """(names, numbers, words) of a column's lower panel, for bars_left; names: the hardware bars' own."""
    if kind == "signer":
        return signing_names(s), [SIGNING_SAMPLE], [w for _, w, _ in SIGNING_WORDS]
    return names, ["100%"], [w for _, w, _ in NOTHING + DISK_WORDS + LINK_WORDS]


def level_names(s, kinds, width, size):
    """The hardware bars' names, size px left of the bars in columns width wide (kinds: the band's). Beside
    a signer column, whose names are longer, the longest hardware name (the one Grafana sizes the names'
    column by) leads with spaces, invisible as the names are right-aligned, until it is as wide as the
    signer's longest: both columns then take one width, and the bars of the whole band start on one x, on
    any screen. Figure spaces first, then four-per-em spaces for the rest; only as many as keep every name
    whole at size px."""
    if "signer" not in kinds:
        return HARDWARE_NAMES
    longest = max(HARDWARE_NAMES, key=len)
    room = (bar_name_px(max(signing_names(s), key=len), BAR_MEASURE_PX)
            - bar_name_px(longest, BAR_MEASURE_PX)) / BAR_MEASURE_PX
    figures = int(max(room, 0) // LEAD_EM[FIGURE])
    lead = FIGURE * figures + QUARTER * int(round((max(room, 0) - figures * LEAD_EM[FIGURE]) / LEAD_EM[QUARTER]))

    def led(spaces):
        return [spaces + n if n == longest else n for n in HARDWARE_NAMES]
    while lead and bars_left([lower_texts(s, kind, led(lead)) for kind in kinds], width) != size:
        lead = lead[:-1]
    return led(lead)


def hardware(s, pid, host, hue, title, name_px=None, names=HARDWARE_NAMES):
    """Hardware now of one server: CPU, memory, its fullest watched disk and its network peak; names: the
    bars' names (level_names)."""
    sel = "host=%s" % q(host)
    fields = list(zip(names, [
        '1 - sum(rate(node_cpu_seconds_total{%s,mode=~"idle|iowait"}[5m])) / '
        'count(node_cpu_seconds_total{%s,mode="idle"}) or vector(-8)' % (sel, sel),
        "1 - max(node_memory_MemAvailable_bytes{%s}) / max(node_memory_MemTotal_bytes{%s}) or vector(-8)" % (sel, sel),
        disk_now(s, host),
        network_now(s, host)]))
    # Red only from DiskLow's page (disk_now's Disk critical), which only node and KMS hosts' data disks raise.
    red = ("it reads red Disk critical once DiskLow pages (below %g%% free, or read-only)" % (s.DP * 100)
           if host in s.nodes or host in s.kms_hosts else
           "DiskLow only sends messages for this server, so the bar never turns red")
    desc = ("How busy %s is now: CPU over 5 minutes, memory, its fullest watched disk, and the busiest minute of the "
            "last hour on its link. Disk used turns amber at %g%%, DiskLow's message level (sent after %s), and says "
            "Disk low while DiskLow's read-only or budget message holds; %s. The other bars have no alert and keep the "
            "server's colour. Their history: the Servers rows below (Disk used in the closed one)."
            % (host, (1 - s.DM) * 100, s.hold("DiskLow low space"), red)
            + more(["DiskLow"], [NODE_EXPORTER_FULL]))
    disk = override("byFrameRefID", "C", thresholds=steps((hue, None), ("orange", round(1 - s.DM, 6))),
                    mappings=mapping(*NOTHING, *DISK_WORDS) + special_map("nan", "No data", GREY) + PAD_PERCENT)
    link = override("byFrameRefID", "D", mappings=mapping(*NOTHING, *LINK_WORDS) + special_map("nan", "No data", GREY)
                    + PAD_PERCENT)
    return band_bars(pid, title, desc, fields, hue, "percentunit", 1, 0, [disk, link], name_px)


def signing_names(s):
    """The names of the signer's four bars: the median with the level its alert fires at, then the slowest
    1% of votes, of proposals and of Fibre's signatures (without Fibre: of every signature over the day)."""
    return ["Median (alert at %s ms)" % s.ms(), "Slowest 1%, votes", "Slowest 1%, proposals",
            "Slowest 1%, Fibre" if s.fibre else "Slowest 1%, 24 h"]


def signing_time(s, pid, hue, title, name_px=None):
    """The signer's lower panel: how fast it signs, in ms, against Celestia's limit for the median; the
    proposals bar says "Bad proposals" while RemoteSignerProblem's proposal failures fire."""
    sig = signing_nodes(s)
    bucket = 'cometbft_privval_signing_latency_seconds_bucket{job="celestia-app",message_type%s}'
    p99 = "max(histogram_quantile(0.99, sum by (host, le) (rate(%s[%s]))) and on (host) %s) * 1000 or vector(-9)"
    names = signing_names(s)
    exprs = [
        "max(%s and on (host) %s) * 1000 or vector(-9)" % (MEDIAN, sig),
        p99 % (bucket % '=~"prevote|precommit"', "5m", sig),
        'max(ALERTS{alertname="RemoteSignerProblem",reason!="",alertstate="firing"}) * 0 - 5 or '
        + p99 % (bucket % '="proposal"', "24h", sig),
        p99 % (bucket % '="raw_bytes"', "1h", "(sum by (host) (increase(%s[1h])) > 0)" % RAW) if s.fibre else
        p99 % (bucket % '=~"prevote|precommit|proposal"', "24h", sig)]
    desc = ("How fast the remote signer signs, on the node it signs through. Median: of the last 50 signatures; "
            "Celestia requires at most %s ms, so the bar turns amber there (RemoteSignerProblem after %s). Slowest "
            "1%%: of votes over 5 minutes, of proposals over 24 hours%s. Bad proposals: RemoteSignerProblem's "
            "proposal failures. None: no such signature in the window."
            % (s.ms(), s.hold("RemoteSignerProblem slow"),
               ", of Fibre's signatures over the last hour" if s.fibre else ", of every signature over 24 hours")
            + more(["RemoteSignerProblem"]))
    median = override("byFrameRefID", "A", thresholds=steps((hue, None), ("orange", s.S * 1000)))
    proposals = override("byFrameRefID", "C", mappings=mapping(*SIGNING_WORDS))
    p = band_bars(pid, title, desc, list(zip(names, exprs)), hue, "ms", s.S * 1000, 2, [median, proposals], name_px)
    p["fieldConfig"]["defaults"]["mappings"] = mapping((-9, "None", GREY), (-8, "No data", GREY))
    return p


def card_desc(s, kind, rows):
    """The (i) of a card that shows rows (as card_rows gives them): a sentence for each row it has, and a
    link to every alert whose word a row can show."""
    if kind == "server":
        parts = {name for name, _, _, _ in rows}
        node, fibre, watched = bool(parts & {"Consensus node", "Node"}), "Fibre" in parts, "Monitoring" in parts
        if not parts:
            # A Fibre host while Fibre is off, or a KMS host with a local key: still a server, scraped.
            return ("Nothing this dashboard reads runs on this server, so its card has no row; its hardware is below, "
                    "its line in History and its charts in the Servers rows." + more(pages=[PANEL_GUIDE]))
        desc = "What runs on this server now, one row for each part it runs, read from the metrics"
        desc += ", so it follows a tmkms repoint or a Fibre failover." if node or fibre else "."
        if node:
            desc += (" Consensus node: Signing or Backup, with its peers; amber 'Node behind' past %g blocks "
                     "(NodeBehind); amber 'Few peers' under %d peers, red 'No peers' once NodePeersLow pages for the "
                     "node the validator signs through; amber 'Not signing' while no node signs, red once "
                     "ValidatorNotSigning pages." % (s.NB, s.PM))
            if s.twice:
                desc += " Two signers: ValidatorSigningTwice; stop one signer."
        if fibre:
            desc += (" Fibre: Active or Standby, or a Fibre alert's words: amber 'Not running' while no Fibre runs "
                     "anywhere, red once FibreDown pages; amber 'Two Fibres' (FibreBothActive); FibreNotServing's "
                     "reasons.")
        if watched:
            failed = (["'Rules stopped'", "'Rules failing' (MonitoringBroken)", "'Alerting down'",
                       "'Alerts unsent' (NotificationsFailing)", "'No heartbeat' (DeadmanFailing)"]
                      + (["'Feed stopped' (AnnouncementsUnreadable)"] if s.announcements else []))
            desc += (" Monitoring, on %s, the monitor: Working while the alert rules run and alerts go out, else "
                     "what failed, in amber: %s or %s." % (s.monitor, ", ".join(failed[:-1]), failed[-1]))
        # The links: the node's and Fibre's alerts (each in the alert reference's order), ServiceDown, then
        # the monitoring's (monitoring_tile's).
        return desc + " Every word: the panel guide." + more(
            (["NodeBehind", "NodePeersLow", "ValidatorNotSigning"] + (["ValidatorSigningTwice"] if s.twice else [])
             if node else [])
            + (["FibreDown", "FibreBothActive", "FibreNotServing"] if fibre else [])
            + (["ServiceDown"] if node or fibre else [])
            + (["MonitoringBroken", "NotificationsFailing", "DeadmanFailing"]
               + (["AnnouncementsUnreadable"] if s.announcements else []) if watched else []), [PANEL_GUIDE])
    desc = ("Where the remote signer signs now: the node the validator's signatures go through")
    if s.fibre:
        desc += (", and the node Fibre's go through, amber when they differ, because then Fibre's uploads fail")
    desc += "."
    if s.twice:
        desc += " Two signers: ValidatorSigningTwice; stop one signer."
    if kind == "kms":
        desc += " Signer service: tmkms's unit on this server, as ServiceDown reads it."
    else:
        desc += " tmkms's own server is not scraped (celestia_mon_kms_scrape): only its signing is seen."
    return desc + more(["ValidatorNotSigning"] + (["ValidatorSigningTwice"] if s.twice else []) + ["RemoteSignerProblem"]
                       + (["ServiceDown"] if kind == "kms" else []), [MAINNET_LAYOUT])


def card(s, pid, title, desc, rows, size):
    """A card: a stat of rows, the name left and the words right. A card with a text row (text_row) reads
    every row as a table and keeps only the text and the other rows' values, in row order."""
    texts = any(t for _, _, _, t in rows)
    refs_ = [chr(65 + j) for j in range(len(rows))]
    targets = [target(r, e, "" if texts else ROW_INDENT + nm, instant=True, fmt="table" if texts else None)
               for r, (nm, e, _, _) in zip(refs_, rows)]
    overrides = []
    for r, (nm, _, words, text) in zip(refs_, rows):
        props = {"mappings": text_maps() if text else mapping(*words)}
        if texts:
            props["displayName"] = ROW_INDENT + nm
        overrides.append(override("byFrameRefID", r, **props))
    p = stat(pid, title, desc, targets, mappings=mapping(*NOTHING), no_value="No data", size=size, title_size=size,
             text_mode="value_and_name", justify="auto", orientation="horizontal", wide=True, overrides=overrides)
    if texts:
        # Grafana names each table query's value "Value #<refId>" when there are several, else "Value".
        keep = ["text"] + ["Value #%s" % r if len(rows) > 1 else "Value"
                           for r, (_, _, _, t) in zip(refs_, rows) if not t]
        p["transformations"] = [{"id": "filterFieldsByName",
                                 "options": {"include": {"pattern": "^(%s)$" % "|".join(keep)}}}]
        p["options"]["reduceOptions"]["fields"] = "/.*/"
    return p


def servers_band(s):
    """The lines of the Servers band: the cards on one line, the panels under them on the next, all of
    one width over the 24 columns; one server's card and hardware side by side in halves; past
    MAX_COLUMNS columns the Servers now table instead. The generator stops when a word would not fit."""
    cols = band_columns(s)
    n = len(cols)
    if n > MAX_COLUMNS:
        table, h_table = servers_table(s)
        return [(h_table, [(table, 24)])]
    w = 24 // n if n > 1 else 12
    # The full names where every card fits them, else the short ones.
    for short in (False, True):
        rows = [card_rows(s, kind, host, short) for kind, host, _ in cols]
        size = next((z for z in CARD_SIZES if all(card_fits(r, z, w) for r in rows)), None)
        if size:
            break
    if size is None:
        raise SystemExit("The Servers band: a card's row does not fit its %d columns even at %d px; shorten the host "
                         "names in the inventory" % (w, CARD_SIZES[-1]))
    # The bars' names left of them, else above them in a panel one row higher.
    lowers = [lower_texts(s, kind) for kind, _, _ in cols]
    name_px = bars_left(lowers, w)
    if not name_px and any(value_px(n, BAR_NAME_PX) > inner_px(w) - 2 * CARD_PAD
                           for names, _, _ in lowers for n in names):
        raise SystemExit("The Servers band: a bar's name does not fit %d columns" % w)
    lower_rows = LOWER_ROWS if name_px else LOWER_ROWS + 1
    bar_names = level_names(s, [kind for kind, _, _ in cols], w, name_px) if name_px else HARDWARE_NAMES
    # Every card as deep as the deepest: shorter ones end in blank rows, so each card keeps one height and
    # one row spacing.
    depth = max(len(r) for r in rows)
    height = CARD_H[depth]
    cards, lower = [], []
    for i, ((kind, name, hue), r) in enumerate(zip(cols, rows)):
        hue = hue or NETWORK
        title = name
        if kind != "server":
            title = next((t for t in ("%s · remote signer" % name, name)
                          if text_px(t, TITLE_PX) <= inner_px(w) - TITLE_CHROME), name)
        if text_px(title, TITLE_PX) > inner_px(w) - TITLE_CHROME:
            raise SystemExit("The Servers band: the title %r does not fit %d columns; shorten the host name" % (title, w))
        cards.append((card(s, 30 + i, title, card_desc(s, kind, r), blank_rows(r, depth), size), w))
        # One server: its hardware beside its card, titled, so both panels' content starts on one line.
        under = "Hardware now" if n == 1 else ""
        if kind == "signer":
            lower.append((signing_time(s, 34 + i, hue, under, name_px), w))
        else:
            lower.append((hardware(s, 34 + i, name, hue, under, name_px, bar_names), w))
    if {len(p["targets"]) for p, _ in cards} != {depth}:
        raise SystemExit("The Servers band: every card must have %d rows, blank ones included" % depth)
    if n == 1:
        h = max(height, lower_rows + 1)
        return [(h, cards + lower)]
    return [(height, cards), (lower_rows, lower)]


UPTIME_W = 3                             # the Uptime column: "Uptime" whole at 1440 px with the menu docked


def first_screen(s):
    """The lines of the first screen: the intro strip (with the Status), the answers, the Servers band,
    History (the check lines, a gap, the server lines) with its Uptime column, the role band and the
    key line."""
    check, servers, roles = check_lanes(s), server_lanes(s), role_lanes(s)
    lanes = check + [spacer_lane()] + servers
    axis = axis_width(s, check, servers, roles)
    h_board = grid_rows(LANE_PX * len(lanes) + PLOT_INSET, CHROME_TITLED)
    h_roles = grid_rows(LANE_PX * len(roles) + PLOT_INSET, CHROME_UNTITLED)
    # The role band's lanes keep History's pitch: where its whole rows leave room for one more lane
    # (two lanes in 3 rows), an empty one closes it at the bottom, rather than the lanes spreading apart.
    while (grid_px(h_roles) - CHROME_UNTITLED - PLOT_INSET) / float(len(roles) + 1) >= LANE_PX:
        ref, name, expr, _, words = spacer_lane()
        roles = roles + [(ref + str(len(roles)), name, expr, words)]
    desc = HISTORY_DESC + more(pages=[("Alert reference", DOCS), PANEL_GUIDE])
    w = 24 - UPTIME_W
    if text_px("Uptime", TITLE_PX) > inner_px(UPTIME_W) - (TITLE_CHROME - 40):
        raise SystemExit("The Uptime column's title does not fit %d columns" % UPTIME_W)
    return [
        (INTRO_ROWS, intro(s)),
        (INTRO_ROWS, answer_tiles(s)),
    ] + servers_band(s) + [
        (h_board, [(status_history(20, "History", desc, lanes, h_board, axis), w),
                   (uptime_column(21, "Uptime", lanes, h_board), UPTIME_W)]),
        (h_roles, [(role_band(s, roles, h_roles, axis), w)]),
        (KEY_ROWS, [(key_line(s, axis, w), w)]),
    ]


# The rows below the first screen.

def one(expr):
    """A panel's only query, instant."""
    return [target("A", expr, "", instant=True)]


# The Validator row. Its charts carry the host label of the node the validator signs through, so
# host_colors() draws them in that server's colour and a repoint shows as a colour change; what
# belongs to the whole network keeps the text colour.

# The charts: one height, with one legend line.
CHART_ROWS = 7


def cell(pid, label, desc, targets, **kw):
    """A cell of a six-cell row in the top strip's form: untitled, the label (INTRO_LABEL_PX) over the
    value, set from the left, in the text colour unless thresholds or words colour it. label None:
    the queries' legends name the cell, so its label can follow its state; one_value_size() sizes it."""
    kw.setdefault("color_mode", "none")
    p = stat(pid, "", desc, targets, size=INTRO_VALUE_SIZES[0], text_mode="value_and_name", justify="auto",
             wide=False, title_size=INTRO_LABEL_PX, **kw)
    if label:
        p["fieldConfig"]["defaults"]["displayName"] = label
    return p


def fit_cells(items, what, labels=None, samples=None):
    """Checks a six-cell row ((panel, width) items, INTRO_ROWS high) as the top strip is checked and marks
    its cells for one_value_size(), which gives every six-cell row of the dashboard one value size.
    labels: {panel id: [texts]} for a cell named by its legends; samples: {panel id: [texts]} it shows
    that no mapping names. Stops when a label does not fit at INTRO_LABEL_PX, or when a label over a
    value is taller than the cell."""
    labels = labels or {}
    content = grid_px(INTRO_ROWS) - CHROME_UNTITLED
    if stack_px(INTRO_LABEL_PX, INTRO_VALUE_SIZES[0], False) > content:
        raise SystemExit("%s: a label over a value needs %d px; an untitled h%d panel has %d"
                         % (what, stack_px(INTRO_LABEL_PX, INTRO_VALUE_SIZES[0], False), INTRO_ROWS, content))
    for p, w in items:
        check_fit(what + " (label)", labels.get(p["id"]) or [p["fieldConfig"]["defaults"]["displayName"]],
                  INTRO_LABEL_PX, w)
    return six_cells(items, samples)


def as_signer(expr, sig):
    """expr, one series without labels, with the host label of each node of sig (a signing_nodes()
    expression), so host_colors() draws it in that server's colour; empty while sig is."""
    return "(%s) * on () group_right () (%s * 0 + 1)" % (expr, sig)


def unsigned(expr, sig):
    """expr, without a host, while sig is empty: the same reading while no node signs."""
    return "(%s) unless on () %s" % (expr, sig)


def block_end(x):
    """x at the end of a bar's time block. A bar covers the block that starts at its time and lasts
    $__interval, as on the History board, so the newest bar is the block that runs now and grows
    while it runs: x's last value in 1-minute steps over the block, without the steps past the data
    (LIVE)."""
    return "last_over_time((%s and on () %s)%s)" % (x, LIVE, WS)


def grown(sel, agg, job="celestia-app"):
    """How much a counter (sel) grew per host in a bar's time block: its value at the block's end
    minus its value at its start; agg (sum or max) combines a host's series. A host that is up but has
    not counted yet reads 0, so a counter that appears with its first increment counts it, as in
    count_24h(); after a restart the block counts from the restart, never a negative number."""
    x = "(%s by (host) (%s) or on (host) (max by (host) (up{job=%s}) == 1) * 0)" % (agg, sel, q(job))
    end = block_end(x)
    return "(((%s - %s) >= 0) or (%s < %s))" % (end, x, end, x)


def shade(s, host, k):
    """darker() of a server's identity colour; the reference grey for a server past the palette."""
    hue = s.color(host)[0]
    return darker(hue, k) if hue.startswith("#") else REFERENCE


def grown_before(sel, agg, job="celestia-app"):
    """grown() for a bar that ends at its time (barAlignment -1): how much the counter grew in the
    $__interval before the bar's time, so the newest bar is the last whole block and none is half
    full. Both ends are read in 1-minute steps (sel may be an expression), so a node Prometheus could
    not read for part of the block keeps the minutes it could: the end is x's last value in the block,
    and the start is x one block earlier or, when the node was not read then, the block's lowest value,
    its first after the node came back (a counter only grows, and starts again at 0 with the process).
    A block without a sample of x has no bar."""
    x = "(%s by (host) (%s) or on (host) (max by (host) (up{job=%s}) == 1) * 0)" % (agg, sel, q(job))
    end = "last_over_time(%s[$__interval:1m])" % x
    start = ("(last_over_time(%s[1m:1m] offset $__interval) or on (host) min_over_time(%s[$__interval:1m]))"
             % (x, x))
    return "(((%s - %s) >= 0) or (%s < %s))" % (end, start, end, start)


# A chart legend's entry: its text at LEGEND_PX and LEGEND_ITEM_PX for the colour mark and the gap
# to the next, as Grafana 12.4 draws a list legend. LEGEND_HOST stands for {{host}}: the longest of
# the server names the docs use.
LEGEND_PX, LEGEND_ITEM_PX, LEGEND_HOST = 12, 30, "validator-server"


def check_legend(what, entries, width):
    """Stops unless a chart's legend entries fit one line of a panel width columns wide at 1440 px."""
    need = sum(LEGEND_ITEM_PX + text_px(e.replace("{{host}}", LEGEND_HOST), LEGEND_PX) for e in entries)
    if need > inner_px(width):
        raise SystemExit("%s: the legend needs %d px on one line, and its panel has %d" % (what, need, inner_px(width)))


def validator_row(s):
    """The Validator row: six cells (the three readings the signing alerts watch, then the proposals
    of the last 24 hours), then the charts behind the Validator and Signing through answers, all one
    height: two lines of two, or with a local key, which has no signer chart, Missed blocks across
    the row over two."""
    n = s.N
    sig_now = signing_nodes(s)
    if s.remote:
        prop = 'cometbft_privval_signing_latency_seconds_count{job="celestia-app",message_type="proposal"}'
        prop_label, prop_word = "Proposals signed, 24 h", "signed"
    else:
        prop = 'cometbft_consensus_proposal_create_count{job="celestia-app"}'
        prop_label, prop_word = "Proposals built, 24 h", "built"
    mpp = 'cometbft_consensus_proposer_missed_proposals{job="celestia-app"}'
    own = ('label_replace(max by (validator_address) (%s), "proposer_address", "$1", "validator_address", "(.+)")'
           % POWER)
    # SignaturesMissed's own expression: per node, the blocks of the last hour without the validator's
    # signature over the blocks of that hour; a node that has missed none since its start reads 0, and a
    # node more than node_behind_blocks behind the highest does not count.
    share = ("max(((max by (host) (%s) or max by (host) (%s) * 0) / (max by (host) (delta(%s[1h])) > 0)) "
             "unless on (host) (max(%s) - on () group_right %s > %s))" % (MISSED_1H, LSH, H, H, H, num(s.NB)))
    # The blocks of the last 24 hours: the height 24 hours ago, or the lowest one Prometheus has.
    day = "(max(%s) - (max(%s offset 24h) or max(min_over_time(%s[24h]))))" % (H, H, H)
    # A count of the last 24 hours reads "No data" when no node answered in that time, not 0.
    seen_24h = '(max(max_over_time(up{job="celestia-app"}[24h])) == 1)'
    if s.remote:
        third = cell(
            118, "Signing time",
            "How fast the remote signer answers on the node the validator signs through; well under %s ms is good. "
            "It is the median of the last 50 signatures. Above %s ms it turns amber, and after %s there "
            "RemoteSignerProblem sends a warning. No reading: no node signed in the last 2 minutes, or the node has "
            "made fewer than 50 signatures since it started."
            % (s.ms(), s.ms(), s.hold("RemoteSignerProblem slow")) + more(["RemoteSignerProblem"]),
            one("max(%s and on (host) %s)" % (MEDIAN, sig_now)),
            unit="s", decimals=2, color_mode="value", thresholds=steps(("text", None), ("orange", s.S)),
            no_value="No reading")
    else:
        third = cell(
            118, "Peers",
            "Peers of the node the validator signs through; 1 or more keeps it signing. With fewer than %d for %s "
            "NodePeersLow sends a warning, and with %s for %s an urgent alert: a node without peers receives no "
            "blocks or votes, and the validator misses every block. No node signs: no node has signed in the last "
            "2 minutes."
            % (s.PM, s.hold("NodePeersLow message"), "none" if s.PP == 1 else "fewer than %d" % s.PP,
               s.hold("NodePeersLow page")) + more(["NodePeersLow"]),
            one('max(max by (host) (cometbft_p2p_peers{job="celestia-app"}) and on (host) %s)' % sig_now),
            no_value="No node signs")
    cells = [
        # Red "Not signing" (-1) while ValidatorNotSigning pages, as every other surface says it: by then
        # the count shows only its cap. While no node advances, the Status reads Nodes stalled and folds
        # Not signing into it, so the cell keeps the count. Plain ALERTS selectors, as VNS10 and NNA10:
        # firing() reads a bar's time block, not the instant.
        cell(109, "Blocks since signature",
             "Blocks since the validator's last signature on any node; 0 or 1 is good. Above %g it turns amber; after "
             "%s ValidatorNotSigning sends an urgent alert, and the cell reads red Not signing while it pages, unless "
             "no node makes new blocks. The count carries on after a repoint, stops at %g, and right after a node "
             "start reads that until the first signature."
             % (n, s.hold("ValidatorNotSigning"), 10 * n) + more(["ValidatorNotSigning"]),
             one(chain('((max(ALERTS{alertname="ValidatorNotSigning",alertstate="firing"}) unless on () '
                       'max(ALERTS{alertname="NodeNotAdvancing",alertstate="firing"})) * 0 - 1)',
                       blocks_since_signature(10 * n))),
             color_mode="value", thresholds=steps(("text", None), ("orange", n + 1)),
             mappings=mapping((-1, "Not signing", "red")), no_value="No data"),
        cell(117, "Missed blocks, 1 h",
             "The share of the last hour's blocks without the validator's signature; 0.00%% or a few hundredths is "
             "good. SignaturesMissed reads the same share: above %g%% it turns amber, and after %s the alert sends a "
             "warning. The Missed blocks chart below shows when." % (s.R * 100, s.hold("SignaturesMissed"))
             + more(["SignaturesMissed"]),
             one(share), unit="percentunit", decimals=2, color_mode="value",
             thresholds=steps(("text", None), ("orange", s.R)), no_value="No data"),
        third,
        cell(101, prop_label,
             "Block proposals the validator %s in the last 24 hours; close to Proposals due, 24 h is good. It counts "
             "across node restarts, from the start of monitoring if that is more recent. A signer that signs votes but "
             "not proposals shows here as far fewer." % prop_word,
             one("(%s) and on () %s" % (count_24h(prop, "sum"), seen_24h)), no_value="No data"),
        # "Expected proposals, 24 h" measures 156 px at 13 px in Grafana's font, and the cell has 152 at
        # the 1440-docked width, so it would wrap.
        cell(102, "Proposals due, 24 h",
             "The proposals the validator's voting power should bring in 24 hours; good when %s is close. "
             "Proposers take turns by stake: it is the blocks of the last 24 hours times today's share of voting "
             "power, so a share that changed makes it less exact. Out of set: the validator is not in the active set."
             % prop_label,
             one(chain("(max(absent(%s)) * 0 - 1)" % H, "(%s * 0 - 2)" % STALL,
                       "(round(%s * max(%s) / max(%s)) and on () %s)" % (day, POWER, TOTAL, IN_SET_OR_NEW),
                       "(max(%s) * 0 - 3)" % H)),
             color_mode="value",
             mappings=mapping((-1, "No node", GREY), (-2, "No new blocks", GREY), (-3, "Out of set", GREY))),
        cell(103, "Missed proposals, 24 h",
             "The validator's turns in the last 24 hours whose proposal did not reach the nodes in time; 0 is good. "
             "One now and then is normal on a busy network. It counts from the start of monitoring if that is more "
             "recent. Causes: the signer did not sign the proposal, or the node built or sent the block too slowly.",
             one("(%s) and on () %s" % (count_24h("%s and on (proposer_address) %s" % (mpp, own), "max"), seen_24h)),
             no_value="No data"),
    ]
    strip = fit_cells(list(zip(cells, INTRO_WIDTHS)), "Validator cells")

    # Missed blocks: the share SignaturesMissed reads, the last hour's, at every point, in the colour of
    # the node the validator signs through, and the whole network's the same way, dashed. A miss
    # raises the line for an hour, so its rising edge marks when. There are no bars per time block:
    # one miss in a short block (a minute holds about 20 blocks) would reach the alert's dashed line,
    # which the alert never reads. A symlog axis, linear to 0.1%, puts the usual 0.1-1% mid-chart and
    # the alert level near the top. Grafana ranges a symlog axis by its data alone (a soft max does
    # nothing there), so the axis ends at twice the alert level, and a higher share runs along the top.
    net = 'max(avg_over_time(cometbft_consensus_missing_validators_power{job="celestia-app"}[1h])) / max(%s)' % TOTAL
    # The legend names the level the dashed line stands for, as Signing time's does.
    missed_level = "alert at %g%%" % (s.R * 100)
    # A log axis draws a grid line at every 2-9 between its labels, a band of stripes; the value
    # axes of Missed blocks and Signing time draw none (the time axis keeps its lines), and the labels
    # and the dashed alert line carry the scale.
    no_value_grid = override("byType", "number", custom__axisGridShow=False)
    # While no node signs, the share has no node to take its colour from: a grey dotted helper line, off
    # the legend (it would read as a second validator line) and named in the tooltip, under the red
    # ValidatorNotSigning band. The axis keeps Grafana's own decimals ("0.5%", "10%").
    missed_chart = timeseries(
        111, "Missed blocks",
        "The share of the last hour's blocks the validator did not sign, over time; a line near 0 is good. It is "
        "the share SignaturesMissed reads: above the dashed %g%% line for %s it sends a warning. A miss lifts the "
        "line for an hour, in the colour of the node the validator signs through. Dashed, in the text colour: the "
        "whole network's share of voting power missing; yours high and the network low points at this setup. "
        "Dotted grey, named in the tooltip only: the share the nodes count while no node signs. Red band: "
        "ValidatorNotSigning paged; no node signed, so the coloured line stops. The axis ends at %g%%; a higher "
        "share runs along the top."
        % (s.R * 100, s.hold("SignaturesMissed"), 2 * s.R * 100) + more(["SignaturesMissed", "ValidatorNotSigning"]),
        refs([(as_signer(share, sig_now), "{{host}} missed, 1 h (%s)" % missed_level),
              (unsigned(share, sig_now), "Missed, 1 h, while no node signs"),
              (net, "Network, 1 h")]),
        "percentunit", threshold=(s.R, "orange"), maximum=2 * s.R,
        overrides=[override("byFrameRefID", "B", color=fixed(GREY), custom__lineStyle=dash([2, 4]),
                            custom__lineWidth=1, custom__fillOpacity=0, custom__hideFrom=HIDDEN),
                   override("byFrameRefID", "C", color=fixed(NETWORK), custom__lineWidth=1, custom__fillOpacity=0,
                            custom__lineStyle=dash([6, 4])), no_value_grid])
    missed_chart["fieldConfig"]["defaults"]["custom"]["scaleDistribution"] = {"type": "symlog", "log": 10,
                                                                             "linearThreshold": 0.001}
    # uPlot always gives a symlog axis a tick at minus its linear threshold, here just under the plot,
    # where its label would sit beside the time axis; a share is never negative, so a negative value
    # reads as nothing (Grafana formats axis labels through the mappings).
    missed_chart["fieldConfig"]["defaults"]["mappings"] = range_map(-1, -1e-9, ZERO_WIDTH)

    # Proposals per whole hour, per node, missed ones on top in grey, against the expected number. Each
    # bar ends at its time, so the newest is the last whole hour: a running hour would show a short bar
    # and a short Expected, which read as "no proposals now". Expected steps before each point, so its
    # level spans the bar it belongs to.
    props = timeseries(
        116, "Proposals per hour",
        "Block proposals the validator %s per hour; bars that reach the dashed Expected line are good. Each node's "
        "bars take its colour, and missed proposals stack on top in grey. Expected is what the validator's share of "
        "voting power should bring; proposers take turns by stake, so hours differ by one or two. A signer that signs "
        "votes but not proposals leaves the bars short. A bar beside an outage counts only the minutes Prometheus "
        "could read its node. Each bar is a whole hour, so the hour that runs now shows once it ends. Red band: "
        "ValidatorNotSigning paged; no node signed, proposals included."
        % prop_word + more(["ValidatorNotSigning"]),
        refs([("%s > 0" % grown_before(prop, "sum"), "{{host}} proposals"),
              ("max(%s) > 0" % grown_before("(%s and on (proposer_address) %s)" % (mpp, own), "max"), "Missed"),
              ("(max(%s) - max(%s offset $__interval)) * max(%s) / max(%s) and on () %s" % (H, H, POWER, TOTAL, IN_SET),
               "Expected")]),
        "none", bars=True, stack=True, fill=60, decimals=0,
        overrides=[override("byFrameRefID", "B", color=fixed(GREY)),
                   override("byFrameRefID", "C", color=fixed(NETWORK), decimals=1, custom__drawStyle="line",
                            custom__lineInterpolation="stepBefore", custom__lineWidth=1, custom__fillOpacity=0,
                            custom__lineStyle=dash([6, 4]), custom__stacking={"group": "A", "mode": "none"})])

    # Block processing time per node over each point's own time ($__interval, at least 10 minutes):
    # the slowest 1% of checking a proposed block plus the slowest 1% of applying a committed one.
    # The signing node first and solid; the others, the backups, dashed. A log axis of three decades,
    # 10 ms to 10 s, as Signing time has: one slow block after a restart (half a second) would set a
    # linear axis and press the usual 40-60 ms lines onto the floor; here they sit a quarter up, so a
    # node that slowly gets slower shows.
    worst = ('sum by (host) (histogram_quantile(0.99, sum by (host, method, le) (rate('
             'cometbft_abci_connection_method_timing_seconds_bucket{job="celestia-app",'
             'method=~"process_proposal|finalize_block"}[$__interval]))))')
    sig_point = signing_nodes(s, "[$__interval]")
    processing = timeseries(
        115, "Block processing time (slowest 1%)",
        "Each node's slowest 1% of checking and applying a block; well under a second is good. It adds the slowest 1% "
        "of checking a proposed block and of applying a committed one; blocks come about every 3 seconds. Solid: the "
        "node the validator signs through; dashed: the backups. A slow node votes late, which shows as missed blocks. "
        "All nodes slow: a heavy block; one node slow: its CPU or disk. Red band: ValidatorNotSigning paged, as no "
        "node signed; a running node keeps its line. The axis is logarithmic, 10 ms to 10 s."
        + more(["ValidatorNotSigning"]),
        refs([("%s and on (host) %s" % (worst, sig_point), "{{host}}"),
              ("%s unless on (host) %s" % (worst, sig_point), "{{host}} backup")]),
        "s", minimum=0.01, maximum=10, fill=10, smooth=True,
        overrides=[override("byFrameRefID", "B", custom__lineStyle=dash([6, 4]), custom__fillOpacity=0), no_value_grid])
    processing["fieldConfig"]["defaults"]["custom"]["scaleDistribution"] = {"log": 10, "type": "log"}
    processing["interval"] = "10m"
    processing["maxDataPoints"] = 100

    missed_chart["maxDataPoints"] = 150
    missed_chart["interval"] = "1m"
    props["fieldConfig"]["defaults"]["custom"]["barAlignment"] = -1
    props["maxDataPoints"] = 1000
    props["interval"] = "1h"
    lines = [[missed_chart], [props, processing]]
    if s.remote:
        median = "%s median (alert at %s ms)" % ("{{host}}", s.ms())
        # The slowest 1% of vote signatures, one query per node so each draws in a darker shade of
        # its node's colour under the fixed legend "Slowest 1%, votes"; only the signing node's has data.
        slow = [("histogram_quantile(0.99, sum by (host, le) (rate(cometbft_privval_signing_latency_seconds_bucket"
                 '{job="celestia-app",host=%s,message_type=~"prevote|precommit"}[5m]))) and on (host) %s'
                 % (q(h), sig_now), chr(66 + i), h) for i, h in enumerate(s.nodes)]
        failed = chr(66 + len(slow))
        latency = timeseries(
            112, "Signing time",
            "How fast the remote signer answers on the node the validator signs through; a median under %s ms is good. "
            "Solid: the median of the last 50 signatures, the value RemoteSignerProblem watches; above the dashed %s ms "
            "line for %s it sends a warning. Dotted, darker: the slowest 1%% of vote signatures over 5 minutes, context "
            "only. Grey bars, right axis: failed signing requests (timeouts, lost connections, every retry). Red band: "
            "ValidatorNotSigning paged; no node signed, so the lines stop. The axis is logarithmic, 0.1 to 100 ms."
            % (s.ms(), s.ms(), s.hold("RemoteSignerProblem slow")) + more(["RemoteSignerProblem", "ValidatorNotSigning"]),
            [target("A", "%s and on (host) %s" % (MEDIAN, sig_now), median)]
            + [target(ref, expr, "Slowest 1%, votes") for expr, ref, _ in slow]
            + [target(failed, "sum(%s) > 0" % grown('cometbft_privval_signing_failures_total{job="celestia-app"}', "sum"),
                      "Failures")],
            "s",
            overrides=[
                # The alert's level belongs to the median alone. Grafana widens a log axis to whole
                # decades, so 0.5-20 ms becomes 0.1-100 ms: the median mid-chart, the 10 ms line above.
                override("byFrameRefID", "A", thresholds=steps(("transparent", None), ("orange", s.S)),
                         custom__thresholdsStyle={"mode": "dashed"}, min=0.0005, max=0.02)]
            + [override("byFrameRefID", ref, color=fixed(shade(s, h, 0.6)),
                        custom__lineStyle={"fill": "dot", "dash": [2, 2]}, custom__lineWidth=1, custom__fillOpacity=0,
                        min=0.0005, max=0.02) for _, ref, h in slow]
            + [override("byFrameRefID", failed, color=fixed(GREY), unit="none", decimals=0, min=0,
                        custom__axisSoftMax=4, custom__axisPlacement="right", custom__drawStyle="bars",
                        custom__fillOpacity=60, custom__gradientMode="none", custom__lineWidth=1,
                        custom__barAlignment=1, custom__scaleDistribution={"type": "linear"}),
               no_value_grid])
        # Medians near a millisecond and a slow one on one log axis, so a slow median stays in sight
        # and the 10 ms line sits in the upper part; the failures keep a linear axis of their own.
        latency["fieldConfig"]["defaults"]["custom"]["scaleDistribution"] = {"log": 10, "type": "log"}
        latency["maxDataPoints"] = 150
        latency["interval"] = "1m"
        lines = [[missed_chart, latency], [props, processing]]
    # The legend entries each chart can show at once (a miss adds "Missed" to the proposals, a
    # failure "Failures" to the signing time), with {{host}} for the node's name: one line each, so
    # the plots of a line stay level.
    shown = {111: ["{{host}} missed, 1 h (%s)" % missed_level, "Network, 1 h"],
             116: ["{{host}} proposals", "Missed", "Expected"],
             115: ["{{host}}"] + ["{{host}} backup"] * (len(s.nodes) - 1),
             112: ["{{host}} median (alert at %s ms)" % s.ms(), "Slowest 1%, votes", "Failures"]}
    for line in lines:
        for p in line:
            check_legend("Validator chart %r" % p["title"], shown[p["id"]], 24 // len(line))
    return [(INTRO_ROWS, strip)] + [(CHART_ROWS, [(p, 24 // len(line)) for p in line]) for line in lines]


# The Fibre row's facts take the top strip's cell style: untitled cells INTRO_ROWS high, the label over
# the value, set from the left, all six in one value size (cell, fit_cells). The widest "<x>/<y>" a
# fact must hold.
OF_SAMPLE = "99999/99999"
# One series per host whose Fibre sends its metrics: its storage budget gauge, which Fibre reports from
# its start. The 24-hour counts of Fibre's counters key their 0 on it (count_24h).
FIBRE_ALIVE = "max by (host) (fibre_server_upload_shard_budget_bytes)"
# A blob settles a few seconds after Fibre's upload of it, so Fibre behind the network draws a gap up to
# this as 0: on time.
SETTLE_SECONDS = 60
# A series kept off the legend and the tooltip, and drawn without a line or fill: it keeps an empty
# chart's axis and level line (a series hidden from the plot takes the axis with it), or makes room on it.
UNSEEN ={"custom__hideFrom": {"legend": True, "tooltip": True, "viz": False}, "custom__lineWidth": 0,
          "custom__fillOpacity": 0}


def rule_number(alert, reason, pattern):
    """The number that pattern (a regex with one group) finds in the expression of the alert rule with
    this name and reason label (None: any) in the rules template. Stops unless there is exactly one, so a
    chart that draws a rule's level cannot drift from the rule."""
    rules = re.split(r"^\s*- alert: ", rules_text(), flags=re.M)[1:]
    found = [m.group(1) for r in rules
             if r.startswith(alert + "\n") and (reason is None or re.search(r"^\s*reason: %s\s*$" % reason, r, re.M))
             for m in re.finditer(pattern, r.split("labels:")[0], re.M)]
    if len(found) != 1:
        raise SystemExit("%s: the %s rule%s must have one %r in its expression, not %d"
                         % (RULES_TEMPLATE, alert, "" if reason is None else " with reason %s" % reason, pattern,
                            len(found)))
    return int(found[0])


def of_fact(pid, name, desc, x, y, zero, missing, alarm=None):
    """A fact that reads "<x>/<y>" (x and y whole numbers, one series each without labels), the
    words zero while y is 0, or the words missing, in grey, while y has no value. count_values turns each
    number into a label, label_join writes the text into the label text, and labelsToFields turns that
    label into the only field the cell keeps (a text field, which the cell shows when its fields are
    chosen by name). There is always a series, so the label always shows. alarm: a condition (a query
    that has a value while it holds) under which the text is amber. Grafana colours a text from its own
    words only, so the text then ends in the invisible amber mark (MARKS), as the cards' rows do."""
    joined = ('label_join(count_values("x", %s) * on () group_left (y) count_values("y", %s > 0), '
              '"text", "/", "x", "y")' % (x, y))
    if alarm:
        joined = ('label_replace(%s and on () (%s), "text", "$1%s", "text", "(.*)") or on () %s'
                  % (joined, alarm, MARKS["orange"], joined))
    expr = " or on () ".join([joined, label("(%s == 0)" % y, "text", zero), label("vector(1)", "text", missing)])
    p = cell(pid, name, desc, one(expr), no_value=missing, color_mode="value",
             mappings=mapping((missing, missing, GREY)) + regex_map(".*" + MARKS["orange"], "orange"))
    p["transformations"] = [{"id": "labelsToFields", "options": {"keepLabels": ["text"], "mode": "columns"}},
                            {"id": "filterFieldsByName", "options": {"include": {"names": ["text"]}}}]
    p["options"]["reduceOptions"]["fields"] = "/.*/"
    return p


# Fibre's own health check, as the blackbox exporter's grpc prober reads it (job fibre_health, one target
# per Fibre host, service ""): per answer one serving_status series is 1 and the others 0, and the gRPC
# status code says whether Fibre answered at all (12, Unimplemented: a Fibre older than v10.5.0, which has
# no health service; 14 and the rest: no answer, as from a stopped standby, whose series are all 0).
READY = 'probe_grpc_healthcheck_response{job="fibre_health",serving_status="SERVING"}'
NOT_READY = 'probe_grpc_healthcheck_response{job="fibre_health",serving_status="NOT_SERVING"}'
PROBE_CODE = 'probe_grpc_status_code{job="fibre_health"}'
READINESS_WORDS = [(1, "Ready", OK), (2, "Not ready", WARN), (3, "Not ready", ALERT), (-1, "Not reported", NODATA),
                   (0, "Not checked", NODATA), (-99, "No data", NODATA)]


def readiness_band(s, axis):
    """Upload readiness, as one more line of History: bars of what the running Fibre answered its health
    check, per time block and worst moment first, as History reads its lines: red once FibreNotServing
    (not ready) paged, amber where Fibre answered NOT_SERVING (as the rule's condition; a restart shows
    for its seconds), Ready where it answered SERVING, grey Not reported (Unimplemented) or Not checked
    (no answer). The answers of every Fibre host count together: a stopped standby answers nothing, so it
    adds nothing beside the Fibre that runs. Grey No data wherever the alert rules did not run. Untitled,
    its lane named on the left in a column as wide as History's, without a time axis: the charts under
    it carry one. Beside it, in an untitled Uptime column, the share of the minutes in which Fibre
    answered SERVING or NOT_SERVING that it answered SERVING; No data while it answered neither.
    Returns the height and both panels."""
    expr = "((%s) and on () %s) or %s * 0 - 99" % (chain(
        "%s * 0 + 3" % firing(NOT_READY_PAGE),
        "(%s == 1) * 0 + 2" % seen(NOT_READY),
        "(%s == 1) * 0 + 1" % seen(READY),
        "(max(%s) == 1) * 0 - 1" % in_block("max_over_time", "(%s == bool 12)" % PROBE_CODE, sub=True),
        "%s * 0" % seen('up{job="prometheus"}')), evaluated(), seen('up{job="prometheus"}'))
    # A minute counts 0 while any Fibre host answered NOT_SERVING, as the bars' amber does, else 1 while
    # one answered SERVING.
    ready = uptime("(((max(%s) == 1) * 0) or on () ((max(%s) == 1) * 0 + 1)) and on () %s" % (NOT_READY, READY, PROM))
    desc = ("Whether the running Fibre reports itself ready for uploads, one bar per time block, worst moment first; "
            "Ready all along is good. Fibre is ready while six checks pass: app node, Fibre module, signer, validator "
            "in the active set, provider registration and store. Not ready: one fails, or Fibre is starting or "
            "stopping; FibreNotServing sends a message after %s, red once it pages after %s. Fibre's log names the "
            "check. Not reported: Fibre older than v10.5.0. Not checked: no answer. A stopped standby is left out. "
            "On the right: the share of the time Fibre answered Ready, of the time it answered at all."
            % (s.hold("FibreNotServing not_ready message"), s.hold("FibreNotServing not_ready page"))
            + more(["FibreNotServing"], [PANEL_GUIDE]))
    height = grid_rows(LANE_PX + PLOT_INSET, CHROME_UNTITLED)

    def spacer(i):
        """History's empty lane (spacer_lane), kept off the tooltip below. Grafana 12.4 stops with "An
        unexpected error happened" on a timeline lane hidden from the plot, so it stays drawn, transparent."""
        ref, name, bars, up, words = spacer_lane()
        return ref + str(i), name * i, bars, up, words
    # Grafana 12.4 draws a timeline of one lane across its whole plot and ignores its rowHeight, which
    # would make the bar 34 px thick here. The line therefore sits between two hidden lanes, and a
    # rowHeight above 1, which Grafana applies as set, lets the three overlap: each lane is rowHeight / 3
    # of the plot, so the middle one is BAR_PX thick and centred, and the share beside it sits on it.
    lanes = [spacer(1), ("A", "Upload readiness", expr, ready, READINESS_WORDS), spacer(2)]
    plot = grid_px(height) - CHROME_UNTITLED - PLOT_INSET
    band, share = status_history(215, "", desc, lanes, height, axis), uptime_column(216, "", lanes, height)
    for p in (band, share):
        p["options"]["rowHeight"] = round(BAR_PX * len(lanes) / float(plot), 2)
        p["fieldConfig"]["overrides"] += [override("byFrameRefID", lanes[i][0], custom__hideFrom=dict(
            legend=True, tooltip=True, viz=False)) for i in (0, 2)]
    return height, band, share


def fibre_row(s):
    """The Fibre row: six facts over the last 24 hours, so a network that sends no blobs for hours
    leaves them readable, then the Upload readiness line with its share (readiness_band) and four charts
    of one size in two lines: the two that the Fibre alerts read, then Uploads and Downloads. With the
    outside check the facts set Fibre's own count next to Tensile's record of the blobs sent to the
    validator, and the first chart draws what FibreNotServing (uploads missed) watches; without it, the
    facts are Fibre's own uploads and the first chart draws what FibreNotServing (uploads failing)
    watches. A chart of the upload's steps is left out: it was empty whenever no blob came, and its total
    is the Upload time fact."""
    ok = 'fibre_server_upload_shard_duration_seconds_count{success="true"}'
    count = "fibre_server_upload_shard_duration_seconds_count"
    rejected = "fibre_server_upload_shard_rejected_total"
    fibre_is = s.is_unit("fibre")
    failing = s.hold("FibreNotServing uploads_failing", UPLOAD_WINDOW)

    accepted = cell(
        202, "Uploads accepted, 24 h",
        "Uploads this Fibre accepted and signed in the last 24 hours; a low count on a quiet network is fine. "
        "It counts across Fibre restarts and failovers. A client may send an upload again, so it can exceed the "
        "blobs sent to the validator"
        + (" (Blobs endorsed counts those, from Tensile)." if s.tensile else
           ". Nothing on your servers tells a quiet network from a Fibre that receives nothing; the outside check "
           "(celestia_mon_tensile) does.")
        + more(pages=[FIBRE_DASHBOARD, TENSILE]),
        one(count_24h(ok, "sum", alive=FIBRE_ALIVE)), no_value="No Fibre data")
    upload_time = cell(
        205, "Upload time, 24 h",
        "The average time an accepted upload took in the last 24 hours; steady is good. It covers checking the "
        "payment promise and the rows, writing the shard to disk and signing it through the validator's signer; "
        "Fibre's own dashboard splits it by step. Failed uploads are left out, so quick rejections cannot hide "
        "slowness. No uploads: none in 24 hours." + more(pages=[FIBRE_DASHBOARD]),
        one(chain('sum(rate(fibre_server_upload_shard_duration_seconds_sum{success="true"}[24h])) '
                  "/ (sum(rate(%s[24h])) > 0)" % ok,
                  "(count(fibre_server_upload_shard_budget_bytes) * 0 - 1)")),
        unit="s", decimals=None, mappings=mapping((-1, "No uploads", None)), no_value="No Fibre data")
    # '> 0' leaves out an unlimited budget and one never derived because the validator was out of the
    # active set when Fibre started: -1 names them "No limit".
    budget = cell(
        204, "Storage budget used",
        "Share of this server's stake-based storage budget that its shards fill; below 100% is good. At 100% "
        "Fibre refuses uploads (the Fibre tile reads Uploads failing while FibreNotServing holds). The share "
        "follows the whole network's Fibre data over the 4-hour retention window, so a high one usually means "
        "network load, not a fault. No limit: unlimited, or the validator was out of the active set when Fibre "
        "started. Not running: no Fibre unit is active." + more(pages=[FIBRE_DASHBOARD]),
        one(chain("max((max by (host) (fibre_server_upload_shard_occupancy_bytes) / max by (host) "
                  "(fibre_server_upload_shard_budget_bytes > 0)) and on (host) %s)" % fibre_active(s),
                  "(max((%s == 0) and on (host) %s) - 1)" % (FIBRE_ALIVE, fibre_active(s)),
                  '(absent(node_systemd_unit_state{%s,state="active"} == 1) * -2)' % fibre_is)),
        unit="percentunit", decimals=0, no_value="No Fibre data",
        mappings=mapping((-1, "No limit", None), (-2, "Not running", None)))
    cleanups = cell(
        214, "Failed clean-ups, 24 h",
        "Failed runs of Fibre's removal of old shards in the last 24 hours; 0 is good. Fibre removes shards "
        "older than the retention window (4 hours by default) about once a minute. A failed run leaves old "
        "shards on disk, so the store fills and DiskLow fires later; Fibre's log names the error. The count "
        "goes across Fibre restarts." + more(["DiskLow"], [FIBRE_DASHBOARD]),
        one(count_24h('fibre_server_prune_duration_seconds_count{job="otelcol",success="false"}', "sum",
                      alive=FIBRE_ALIVE)),
        no_value="No Fibre data")

    if s.tensile:
        assigned = "max(celestia_mon_fibre_blobs_assigned_24h)"
        # The failed reads in Tensile's 24 hours from which FibreNotServing (rows not served) fires.
        unserved = rule_number("FibreNotServing", "not_served", r'outcome="broken"\}\)\s*>=\s*(\d+)')
        first = [
            accepted,
            of_fact(207, "Blobs endorsed, 24 h",
                    "Blobs that settled with the validator's signature, of those sent to it in 24 hours; near "
                    "equal is good. A blob settles once 2/3 of the voting power has signed and its publisher "
                    "stops waiting then, so a healthy validator that answers after that quorum is left out of "
                    "some: no colour, no alert. No blobs sent: none came in 24 hours, a quiet network. Not "
                    "checked: no Tensile reading." + more(pages=[TENSILE]),
                    "max(celestia_mon_fibre_blobs_endorsed_24h)", assigned, "No blobs sent", "Not checked"),
            of_fact(208, "Rows served, 24 h",
                    "Of Tensile's reads of rows the validator signed for, those that got the rows back; equal is "
                    "good. Tensile reads each blob once, late in the 4 hours Fibre must keep it, for blobs settled "
                    "in the last 24 hours. From %d failed reads in 24 hours the text is amber, the level at which "
                    "FibreNotServing (rows not served) fires, not in the 5 h 15 min after the registration moved; "
                    "Fibre's log names the error. No rows read: none in 24 hours, as on a quiet network. Not checked: "
                    "no Tensile reading." % unserved
                    + more(["FibreNotServing"], [TENSILE]),
                    'max(celestia_mon_fibre_obligations_24h{outcome="served"})',
                    'sum(max by (outcome) (celestia_mon_fibre_obligations_24h{outcome=~"served|broken"}))',
                    "No rows read", "Not checked",
                    alarm='max(celestia_mon_fibre_obligations_24h{outcome="broken"}) >= %d' % unserved),
        ]
        samples = {207: [OF_SAMPLE], 208: [OF_SAMPLE]}
    else:
        first = [
            accepted,
            cell(
                203, "Upload success, 24 h",
                "Share of upload requests Fibre accepted in the last 24 hours; near 100%% is good. Failures "
                "include clients' invalid payment promises, which are not this setup's fault, and uploads "
                "refused while the storage budget is full. No upload accepted for %s while signatures fail, the "
                "store fails or the budget is full is FibreNotServing (uploads failing). No uploads: none in 24 "
                "hours." % failing
                + more(["FibreNotServing"], [FIBRE_DASHBOARD]),
                one(chain("(sum(rate(%s[24h])) or on () vector(0)) / (sum(rate(%s[24h])) > 0)" % (ok, count),
                          "(count(fibre_server_upload_shard_budget_bytes) * 0 - 1)")),
                unit="percentunit", decimals=2, mappings=mapping((-1, "No uploads", None)),
                no_value="No Fibre data"),
            cell(
                201, "Last accepted upload",
                "When the active Fibre last accepted an upload; hours ago is normal on a quiet network. Fibre "
                "sends its metrics up to a minute apart, so this can be a minute late; an upload in the last "
                "minute reads a minute ago. None since start: Fibre "
                "reports but has accepted no upload since it started. Nothing on your servers tells a quiet "
                "network from a Fibre that receives nothing; the outside check (celestia_mon_tensile) does."
                + more(pages=[FIBRE_DASHBOARD, TENSILE]),
                # At least 45 s back, so the time-ago text starts at "a minute ago": "a few seconds ago" is
                # wider than a cell at the six-cell rows' smallest value size on a 1440-pixel screen.
                one("clamp_max(max(fibre_server_upload_shard_last_success_timestamp_seconds), time() - 45) * 1000 "
                    "or on () ((count(fibre_server_upload_shard_budget_bytes) > 0) * 0 - 1)"),
                unit="dateTimeFromNow", mappings=mapping((-1, "None since start", None)),
                no_value="No Fibre data"),
        ]
        # The longest text the time-ago value shows, which no mapping names ("44 minutes ago"; "a few
        # seconds ago" cannot show, see above).
        samples = {201: ["44 minutes ago"]}
    facts = first + [upload_time, budget, cleanups]
    if len(facts) != len(INTRO_WIDTHS):
        raise SystemExit("The Fibre row needs %d facts, one per column of the top strip" % len(INTRO_WIDTHS))
    strip = fit_cells(list(zip(facts, INTRO_WIDTHS)), "The Fibre facts", samples=samples)

    # The first chart draws what a FibreNotServing rule watches, in the active Fibre's colour, with the
    # rule's level dashed and named in its legend: flat on a healthy server and on a quiet network, a
    # rise when Fibre misses blobs (with the outside check) or fails every upload (without it). Each
    # chart of the row says what the red band of FibreDown's page (annotations) is.
    active = 'max by (host) (node_systemd_unit_state{%s,state="active"}) == 1' % fibre_is
    red_band = " Red band: FibreDown paged; no Fibre server ran, so the %s stop."
    if s.tensile:
        # The missed_uploads rule's value: the newest blob sent to the validator, in Tensile's record of a
        # minute ago (so Fibre's own report of an upload, a few seconds late, is in first), minus the later
        # of Fibre's last accepted upload and its unit's start, for an active Fibre that sends metrics.
        # last_success (job otelcol) and the start time (job node) differ in their labels, so 'or' keeps
        # both and 'max by (host)' takes the later: after a restart the collector still serves the old
        # process's last upload for a few minutes. A blob that Fibre took settles a few seconds after its
        # upload, so the value is that settle time, or negative once Fibre took a newer upload: up to
        # SETTLE_SECONDS it draws as 0, a line on 0 in the Fibre's colour, so a healthy chart reads as
        # checked and on time. Above that it draws as it is. No line at all: no Tensile reading of the
        # newest blob, or no active Fibre that sends metrics ("Not checked").
        level = rule_number("FibreNotServing", "missed_uploads", r"^\s*>\s*(\d+)\s*$")
        gap = ("max(celestia_mon_fibre_last_assigned_timestamp_seconds offset 1m) - on () group_right max by (host) "
               "(fibre_server_upload_shard_last_success_timestamp_seconds or node_systemd_unit_start_time_seconds"
               "{%s}) and on (host) %s and on (host) count by (host) (fibre_server_upload_shard_budget_bytes)"
               % (fibre_is, active))
        first_legend = "{{host}} behind the network (alert at %g min)" % (level / 60.0)
        first_chart = timeseries(
            210, "Fibre behind the network",
            "How far Fibre's last accepted upload trails the newest blob sent to the validator; a line on 0 is good. "
            "FibreNotServing (uploads missed) sends a message when the line stays above the dashed line at %s for %s, "
            "unless that blob carries the validator's signature. "
            "A gap up to %s, the normal time from an upload to its blob settling, draws as 0; so does a quiet "
            "network. Not checked: no Tensile reading, or no Fibre that runs and reports."
            % (minutes_text(level), s.hold("FibreNotServing missed_uploads"), minutes_text(SETTLE_SECONDS))
            + red_band % "lines" + more(["FibreNotServing", "FibreDown"], [TENSILE]),
            [target("A", "clamp_min(%s, 0) * ((%s) > bool %d) / 60" % (gap, gap, SETTLE_SECONDS), first_legend)],
            "m", threshold=(level / 60.0, "orange"), soft_max=level / 60.0 * 1.2, step=True, fill=30)
        first_chart["fieldConfig"]["defaults"]["noValue"] = "Not checked"
    else:
        # The uploads_failing rule: uploads that passed their checks failing here (a failed signature, or
        # a refusal for a full budget) and none accepted in its window, on the active Fibre. Each accepted
        # upload is a signature that worked, so the share failing is 100% (full) exactly then. Failed
        # stores fire the rule too and stay out of the chart: they stand beside the sum there, since their
        # series share their labels with the failed signatures and an 'or' would drop them.
        window = "%ds" % UPLOAD_WINDOW
        full = 1
        sign = "fibre_server_sign_duration_seconds_count"
        here = 'increase(%s{success="false"}[%s]) or increase(%s[%s])' % (sign, window, rejected, window)
        tried = "increase(%s[%s]) or increase(%s[%s])" % (sign, window, rejected, window)
        share = "(sum by (host) (%s) / (sum by (host) (%s) > 0)) > 0 and on (host) %s" % (here, tried, active)
        first_legend = "{{host}} share failing (alert at %g%%)" % (full * 100)
        first_chart = timeseries(
            210, "Uploads failing",
            "Share of the uploads that passed their checks and then failed here, over %s windows: not signed, "
            "or refused while the storage budget was full; an empty chart is good. FibreNotServing (uploads "
            "failing) sends a message once the share has stayed at the dashed %g%% line, no upload accepted, "
            "for %s. Uploads a client gets wrong, such as an invalid payment promise, fail at their checks and "
            "are left out, so a quiet network leaves the chart empty, as does one such client sending again. "
            "Uploads Fibre could not store also send that message, and do not show here: Fibre's log says failed "
            "to store upload data. Blobs that never reached Fibre show only with the outside check "
            "(celestia_mon_tensile)."
            % (minutes_text(UPLOAD_WINDOW).replace(" minutes", "-minute"), full * 100,
               s.hold("FibreNotServing uploads_failing"))
            + red_band % "lines" + more(["FibreNotServing", "FibreDown"], [TENSILE]),
            [target("A", share, first_legend), target("B", "max(%s) * 0" % FIBRE_ALIVE, "Fibre data")],
            "percentunit", threshold=(full, "orange"), maximum=full, fill=30,
            overrides=[override("byFrameRefID", "B", **UNSEEN)])
        first_chart["fieldConfig"]["defaults"]["noValue"] = "No Fibre data"

    # Will the store hold the budget? The active Fibre's free space fills down to what Fibre may still
    # write under its budget (fillBelowTo, one override per Fibre host), so the band between them is the
    # headroom. A standby's store is left out: its disk is on its server's chart, and its free space set
    # the axis.
    frees = ["node_filesystem_avail_bytes{host=%s,mountpoint=%s}" % (q(m["host"]), q(m["mount"]))
             for m in s.fibre_mounts if m["mount"]]
    room_name = "Fibre may still write"
    room = ("(max by (host) (fibre_server_upload_shard_budget_bytes) - max by (host) "
            "(fibre_server_upload_shard_occupancy_bytes)) and on (host) (max by (host) "
            "(fibre_server_upload_shard_budget_bytes) > 0)")
    disk_targets = [target("B", room, "{{host}} " + room_name)]
    disk_overrides = [override("byFrameRefID", "B", custom__lineStyle=dash([10, 10]), custom__fillOpacity=0)]
    if frees:
        free = "max by (host) (%s)" % " or ".join(frees)
        disk_targets.insert(0, target("A", "%s and on (host) %s" % (free, fibre_active(s)), "{{host}} free space"))
        # A flat fill: the default fades towards the bottom of the chart, where the band lies.
        disk_overrides += [override("byFrameRefID", "A", custom__gradientMode="none", custom__fillOpacity=15)]
        disk_overrides += [override("byName", "%s free space" % h, custom__fillBelowTo="%s %s" % (h, room_name))
                           for h in sorted({m["host"] for m in s.fibre_mounts if m["mount"]})]
    # Room above the highest line: an unseen series at 1.2 times it, so the axis ends above the free
    # space rather than on it, where the line would read as the chart's frame.
    disk_targets.append(target("C", "1.2 * max(%s)" % " or ".join(t["expr"] for t in disk_targets), "Room above"))
    disk_overrides.append(override("byFrameRefID", "C", **UNSEEN))
    store = timeseries(
        212, "Fibre disk and budget",
        "Free space on the Fibre store against what Fibre may still write; a wide band between them is good. "
        "Solid: free space; dashed: what Fibre may still write under its budget (the budget minus what it holds). "
        "DiskLow sends a message once the dashed line has been above the solid one for %s (with no budget, at %g%% "
        "free). A standby's store is on its server's Disk used chart."
        % (s.hold("DiskLow budget"), s.DM * 100) + red_band % "lines"
        + more(["DiskLow", "FibreDown"], [FIBRE_DASHBOARD]),
        disk_targets, "bytes", overrides=disk_overrides)

    # Fibre's requests per time block as stacked bars, uploads and downloads each in a chart of their
    # own, counted on a linear axis from 0 whose soft max keeps a single request a short bar. Each bar is
    # the 30 minutes (on 24 hours; more on longer ranges) that end at its time, so the newest is the last
    # whole block; a block without requests draws nothing ('> 0'), so a quiet network leaves a chart
    # empty. Refused uploads also end with success "false" (celestia-app fibre/server_upload.go returns
    # the budget error through the deferred uploadShardDone), so they are taken out of the failed ones
    # and drawn as their own outlined bars, in the text colour: no rule fires on a refusal next to
    # accepted uploads. The uploads_failing rule counts refusals with failed signatures, so once Fibre
    # accepts none the Fibre tile reads Uploads failing.
    # Accepted uploads and served downloads take the server's colour; failed uploads are light grey, and
    # downloads Fibre did not hold or that failed are grey outlines, since most are routine.
    downloads = "fibre_server_download_shard_duration_seconds_count"
    served = "served"

    def requests(pid, title, desc, events, no_value, overrides):
        p = timeseries(pid, title, desc, refs(events), "none", soft_max=3, decimals=0, bars=True, stack=True,
                       fill=60, overrides=overrides)
        p["fieldConfig"]["defaults"]["custom"]["barAlignment"] = -1
        p["fieldConfig"]["defaults"]["noValue"] = no_value
        p.update({"interval": "30m", "maxDataPoints": 60})
        return p

    uploads = requests(
        211, "Uploads",
        "Upload requests Fibre received in each 30-minute block (about 3 hours on 7 days); bars in the server's "
        "colour are good. Grey: failed, as when a client's payment promise is invalid. Outlined: refused while "
        "the storage budget was full. No upload accepted for %s while signatures fail, the store fails or the "
        "budget is full is FibreNotServing (uploads failing)." % failing + red_band % "bars"
        + more(["FibreNotServing", "FibreDown"]),
        [("round(sum by (host) (increase(%s[$__interval]))) > 0" % ok, "{{host}} accepted"),
         ('round(clamp_min(sum(increase(%s{success="false"}[$__interval])) - (sum(increase(%s[$__interval])) '
          "or on () vector(0)), 0)) > 0" % (count, rejected), "Failed"),
         ("round(sum(increase(%s[$__interval]))) > 0" % rejected, "Refused, budget full")],
        "No uploads in this time",
        [override("byFrameRefID", "B", color=fixed(NETWORK), custom__fillOpacity=50),
         override("byFrameRefID", "C", color=fixed(NETWORK), custom__fillOpacity=0)])
    downloads_chart = requests(
        213, "Downloads",
        "Shard download requests Fibre received in each 30-minute block (about 3 hours on 7 days); bars in the "
        "server's colour, shards served, are good. Grey outlines: not held or failed. Most failed "
        "downloads ask for shards Fibre does not hold (pruned after the 4-hour window, or never assigned to it), "
        "which is normal; "
        + ("Rows served tells whether the validator's own rows are served." if s.tensile else
           "whether the validator's own rows are served shows only with the outside check (celestia_mon_tensile).")
        + red_band % "bars" + more(["FibreDown"], [TENSILE]),
        [('round(sum by (host) (increase(%s{success="true"}[$__interval]))) > 0' % downloads, "{{host}} " + served),
         ('round(sum(increase(%s{success="false"}[$__interval]))) > 0' % downloads, "Not held or failed")],
        "No downloads in this time",
        [override("byFrameRefID", "B", color=fixed(darker(NETWORK, 0.6)), custom__fillOpacity=0)])
    # The legend entries each chart can show at once, one line each, so the plots of a line stay level.
    shown = {210: [first_legend], 212: [t["legendFormat"] for t in disk_targets if t["refId"] != "C"],
             211: ["{{host}} accepted", "Failed", "Refused, budget full"],
             213: ["{{host}} " + served, "Not held or failed"]}
    for p in (first_chart, store, uploads, downloads_chart):
        check_legend("Fibre chart %r" % p["title"], shown[p["id"]], 12)
    band_h, band, share = readiness_band(s, axis_width(s, check_lanes(s), server_lanes(s), role_lanes(s)))
    return [(INTRO_ROWS, strip), (band_h, [(band, 24 - UPTIME_W), (share, UPTIME_W)]),
            (CHART_ROWS, [(first_chart, 12), (store, 12)]), (CHART_ROWS, [(uploads, 12), (downloads_chart, 12)])]


def upgrade_row(s):
    """The collapsed Network upgrade row: the numbers behind the Network upgrade tile in six cells of
    the top strip's form: the versions, the support so far against the 5/6 an upgrade needs, and the
    upgrade itself, which says "None pending" once. Each reads the last value of the past 7 days, as
    the upgrade alerts do, so a short collector outage keeps the last reading; with none, the cell
    reads "Not checked" in grey."""
    pending = last(s, "celestia_mon_upgrade_pending")
    total = last(s, "celestia_mon_signal_total_power")
    unread = (-9, "Not checked", GREY)

    def checked(expr):
        return "(%s) or on () vector(%d)" % (expr, unread[0])

    version = cell(
        104, "App version",
        "The app version the chain runs now; it changes only at an upgrade. celestia-mon-signal reads it through a "
        "node's RPC.",
        one(checked(last(s, "celestia_mon_app_version"))), unit="prefix:v", color_mode="value",
        mappings=mapping(unread))
    following = cell(
        119, "Next version",
        "The version validators signal for next: the app version plus one.",
        one(checked(last(s, "celestia_mon_signal_next_version"))), unit="prefix:v", color_mode="value",
        mappings=mapping(unread))
    # The validator's signal counts only while it is for the next version: a signal left from the last
    # round would read as a signal for the old version, so it reads None, as before any signal.
    signalled = last(s, "celestia_mon_signal_validator_version")
    signal = cell(
        106, "Signalled version",
        "The version the validator signalled for the next upgrade. None: no signal for the next version yet, which is "
        "fine until a round opens; then the next version is good. A round opens when any voting power signals the "
        "next version; while the validator has not, the %s tile turns amber, and red once more than 1/3 has "
        "signalled." % upgrade_title(s) + more(["UpgradeSignalMissing"]),
        one(checked("(%s >= on () %s) or on () (%s * 0)"
                    % (signalled, last(s, "celestia_mon_signal_next_version"), signalled))),
        unit="prefix:v", color_mode="value", mappings=mapping((0, "None", "text"), unread))
    support = cell(
        105, "Voting power signalled",
        "The share of all voting power that signalled the next version; it climbs towards Needed to schedule. Once it "
        "reaches that share (5/6, rounded up to whole voting power), anyone can schedule the upgrade."
        + more(["UpgradeSignalMissing"]),
        one(checked("%s / %s" % (last(s, "celestia_mon_signal_tally_power"), total))), unit="percentunit",
        decimals=2, color_mode="value", mappings=mapping(unread))
    needed = cell(
        120, "Needed to schedule",
        "The share of voting power that must signal before anyone can schedule the upgrade: 5/6, rounded up. "
        "celestia-mon-signal reads it from the chain, in whole voting power.",
        one(checked("%s / %s" % (last(s, "celestia_mon_signal_threshold_power"), total))), unit="percentunit",
        decimals=2, color_mode="value", mappings=mapping(unread))
    # One cell for the upgrade: "None pending" under the label Upgrade, or once scheduled the time to
    # it under "Upgrade at <height>" (count_values turns the height into a label), amber under 24
    # hours as UpgradeSoon.
    at = 'count_values("height", %s)' % last(s, "celestia_mon_upgrade_height")
    eta = chain("%s and on () (%s == 1)" % (last(s, "celestia_mon_upgrade_eta_seconds"), pending),
                "(%s == 1) * 0 - 2" % pending)
    upgrade = cell(
        107, None,
        "The scheduled upgrade's height and the time until it; None pending is normal between upgrades. The time "
        "uses the block time of the last 1000 blocks, or 2.6 s per block until one is measured. Amber under 24 "
        "hours: every node must run a release that "
        "supports the new version by then, or it stops at that height. UpgradeSoon stays once sent even if slower "
        "blocks move this back above 24 hours." + more(["UpgradeScheduled", "UpgradeSoon"]),
        [target("A", "(%s) * on () group_left (height) %s" % (eta, at), "Upgrade at {{height}}", instant=True),
         target("B", "(%s == 0) * 0 - 1" % pending, "Upgrade", instant=True),
         target("C", "vector(%d) unless on () %s" % (unread[0], pending), "Upgrade", instant=True)],
        unit="s", decimals=1, color_mode="value", thresholds=steps(("orange", None), ("text", 86400)),
        mappings=mapping((-1, "None pending", "text"), (-2, "Unknown", "text"), unread), no_value="Not checked")
    strip = fit_cells([(version, 4), (following, 4), (signal, 4), (support, 4), (needed, 4), (upgrade, 4)],
                      "Network upgrade cells", labels={107: ["Upgrade", "Upgrade at 999999999"]},
                      samples={107: ["23.9 hour", "6.9 day", "52.1 week"]})
    return [(INTRO_ROWS, strip)]


def alerts_row(s):
    """The collapsed All firing alerts row, right under the first screen: every alert firing or pending,
    each in a plain sentence, with the alert's name as the first line of its notification carries it
    ("PAGE · ValidatorNotSigning · val-1"), so the notification can be found in its channel, linked to
    the rule's entry in the alert reference."""
    levels = " or ".join("%s * 0 + %s" % (by_alert('ALERTS{alertstate="%s",severity="%s"}' % (state, sev)), num(v))
                         for state, sev, v in [("firing", "page", 3), ("pending", "page", 2),
                                               ("firing", "message", 1), ("pending", "message", 0.5)])
    level = "max by (title, alertname) (%s)" % titled(s, "(%s)" % levels)
    since = ("(time() - max by (title, alertname) (%s)) and on (title, alertname) (%s)"
             % (titled(s, by_alert('ALERTS_FOR_STATE{severity=~"page|message"}')), level))
    # The anchor of each rule's entry in the alert reference, its heading in lower case (as more()
    # links it): a label of the level's rows, for the Rule column's link, which can read only the
    # fields of its row.
    anchored = level
    for a in s.alert_names:
        anchored = "label_replace(%s, %s, %s, %s, %s)" % (anchored, q("anchor"), q(a.lower()), q("alertname"), q(a))
    # The Rule column fits the longest alert name. Names in CamelCase run wider than the table's words:
    # AnnouncementsUnreadable measured 188 px in the table's 14 px text (0.58 em), so 0.6 em.
    rule_px = int(math.ceil(max(text_px(a, 14, 0.6) for a in s.alert_names))) + TABLE_PAD
    table = panel(
        301, "table", "Firing alerts",
        "Every alert Prometheus has firing now, plus alerts whose problem is true but has not lasted long enough to be "
        "sent, each in a plain sentence; the Status cell at the top names the worst one. Rule: the alert's name, as "
        "the first line of its notification carries it (PAGE · ValidatorNotSigning · val-1), so you can find the "
        "notification in your channel; it opens the alert's entry in the [alert reference](%s), with its meaning and "
        "first checks. Active for: how long since the problem started, including the waiting time before it fired. "
        "The heartbeat (Watchdog) is left out. Silences do not hide alerts here." % DOCS,
        [target("A", anchored, "", instant=True, fmt="table"), target("B", since, "", instant=True, fmt="table")],
        {"color": {"mode": "thresholds"}, "noValue": "No alerts firing", "thresholds": steps(("text", None)),
         "custom": {"align": "auto", "cellOptions": {"type": "color-text"}, "filterable": False, "inspect": False}},
        [override("byName", "Rule", custom__width=rule_px,
                  links=[{"title": "Alert reference", "url": DOCS + "#${__data.fields.anchor}", "targetBlank": True}]),
         override("byName", "Level", custom__width=190,
                  mappings=mapping((3, "Urgent", "red"), (2, "Urgent, not paged yet", "orange"),
                                   (1, "Warning", "orange"), (0.5, "Warning, not sent yet", "orange"))),
         override("byName", "Active for", custom__width=140, unit="dtdurations", noValue="–"),
         override("byName", "anchor", custom__hidden=True)],
        {"cellHeight": "sm", "enablePagination": False,
         "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
         "showHeader": True, "sortBy": [{"desc": True, "displayName": "Level"}]})
    # With nothing firing, Prometheus still returns empty frames with value fields, and a table would
    # show bare column headers. Grouping by Alert, Rule and anchor drops a frame without those label
    # fields, so the panel has no data and shows its no-value text; with alerts it changes nothing (one
    # row per sentence), and the last step takes the reducer's name back off the columns. anchor keeps
    # its name (renamed to itself, so it is a named column as the others) and is hidden by an override,
    # not left out, so the link still reads it.
    table["transformations"] = [
        {"id": "merge", "options": {}},
        {"id": "organize", "options": {"excludeByName": {"Time": True},
                                       "indexByName": {"title": 0, "alertname": 1, "Value #A": 2, "Value #B": 3,
                                                       "anchor": 4},
                                       "renameByName": {"title": "Alert", "alertname": "Rule", "Value #A": "Level",
                                                        "Value #B": "Active for", "anchor": "anchor"}}},
        {"id": "groupBy", "options": {"fields": {
            "Alert": {"aggregations": [], "operation": "groupby"},
            "Rule": {"aggregations": [], "operation": "groupby"},
            "anchor": {"aggregations": [], "operation": "groupby"},
            "Level": {"aggregations": ["max"], "operation": "aggregate"},
            "Active for": {"aggregations": ["max"], "operation": "aggregate"}}}},
        {"id": "renameByRegex", "options": {"regex": "^(Level|Active for) \\(.*\\)$", "renamePattern": "$1"}}]
    return [(6, [(table, 24)])]


# The Servers section: one column of charts per server, in the server's colour, each chart titled with the
# server's name first, so a server's charts sit under its name and every line of charts compares the servers.
# Two charts show every server at once, across the full width: Restarts and Clock maximum error, where each
# server's own chart would be empty or flat. Ids: the open row 400, the closed row 450, for each line of a column chart a
# base (410, 420, ...) plus the server's place in servers order, and 480 and 490 for the two full-width charts.
SERVERS_MAX = 10                         # servers the ids leave room for
VALUE_AXIS_PX = 76                       # every chart's value axis width, so plots in a column start at one x
# Measured in a column chart at 1440 px with the menu docked (a 1110 px grid; a w8 chart is 365 px wide): a
# title has the panel width less 98 px (padding, the (i) icon and the menu), so inner_px(width) - TITLE_ROOM,
# its letters at 14 px about 0.49 em; a legend line has the panel width less 26 px, so inner_px(width) - 6, and
# each entry takes its text (12 px, about as text_px() says, 3% added) and 32 px of swatch and gaps. The
# sizes are TITLE_PX and LEGEND_PX.
TITLE_ROOM, TITLE_EM = 80, 0.49
LEGEND_ITEM, LEGEND_ROOM = 32, 6
# The parts of a whole in shades of the server's colour, the same for a part on every server: the full colour,
# then darker ones (darker() factors), opaque, so each legend swatch differs; all at one fill opacity. The
# darkest, Other, is often the largest layer: at 0.45 its swatch keeps about 1.7:1 (blue) to 2:1 (light purple)
# against the panel, and neighbouring parts still tell apart. All in use is a line in the text colour, not a part.
PART_SHADES = {"node": 1, "fibre": 0.8, "monitoring": 0.62, "other": 0.45}
ORDER_SHADES = (1, 0.7, 0.5, 0.35)       # a server's disks, its units and the two sides of a mirrored chart
PART_FILL = 60
# The legend words of the stacked charts, the board's names of the services: in full, and with "Node" for
# "Consensus node" where columns are too narrow for the full ones (three servers to a line and more).
PART_NAMES = ({"node": "Consensus node", "fibre": "Fibre", "monitoring": "Monitoring", "other": "Other",
               "other_fibre": "Fibre and other", "total": "In use"},
              {"node": "Node", "fibre": "Fibre", "monitoring": "Monitoring", "other": "Other",
               "other_fibre": "Fibre and other", "total": "In use"})
HIDDEN = {"legend": True, "tooltip": False, "viz": False}
# The services whose restarts ServiceDown counts, in the board's words.
SERVICE_NAMES = {"appd": "Consensus node", "fibre": "Fibre", "tmkms": "Remote signer"}


def server_hue(s, host):
    """A server's colour as hex: its HOST_COLORS entry, or the text colour past the list."""
    return s.color(host)[1] or NETWORK


def tone(hue, k):
    """The server's colour hue, or a darker shade of it (k < 1)."""
    return hue if k == 1 else darker(hue, k)


def darker(hue, k):
    """A '#RRGGBB' colour with each channel scaled by k (0.7: a darker part of the same hue), opaque, so
    its legend swatch differs from the full colour's where alpha alone would not show."""
    return "#" + "".join("%02X" % round(int(hue[i:i + 2], 16) * k) for i in (1, 3, 5))


def server_title(s, host, long_name, short_name, width):
    """'<server> · <what>' in the long words, or the short ones where the long would not fit the title bar
    with the longest server name, so the charts of one line read alike."""
    longest = max(s.servers, key=len)
    name = short_name
    if text_px("%s · %s" % (longest, long_name), TITLE_PX, TITLE_EM) + TITLE_ROOM <= inner_px(width):
        name = long_name
    return "%s · %s" % (host, name)


def legend_px(names):
    """Width in px of a one-line list legend of these entries."""
    return sum(1.03 * text_px(n, LEGEND_PX) + LEGEND_ITEM for n in names)


def phrase(items):
    """Items as an English list: 'a', 'a and b', 'a, b and c'."""
    return items[0] if len(items) == 1 else "%s and %s" % (", ".join(items[:-1]), items[-1])


def stack_parts(s, h, metric):
    """(refId, part, expr) of what is read separately on server h, as a CPU rate or a memory size: the
    consensus node on a node host, Fibre's Go memory on a Fibre host (Fibre reports no CPU time), and
    Prometheus with Alertmanager on the monitor host."""
    hs = "host=%s" % q(h)
    cpu = metric == "cpu"
    own = "sum(rate(process_cpu_seconds_total{%s}[5m]))" if cpu else "sum(process_resident_memory_bytes{%s})"
    parts = []
    if h in s.nodes:
        parts.append(("A", "node", own % ('job="celestia-app",' + hs)))
    if h in s.fibre_hosts and not cpu:
        parts.append(("B", "fibre", 'sum(go_memory_used_bytes{job="otelcol",%s})' % hs))
    if h == s.monitor:
        parts.append(("C", "monitoring", own % ('job=~"prometheus|alertmanager",' + hs)))
    return parts


def stacked(s, h, pid, width, variant, metric):
    """(chart, legend entries) of CPU or memory by service on server h: the parts read separately, stacked
    in shades of the server's colour, the rest of what is in use above them (Other) and the total in use as
    a line on top. The total is a context line over the parts, 1 px in the text colour, so the server's full
    colour names only the consensus node; the legend names it In use where there is room, and the tooltip
    always. The installed memory is not drawn: its line would set the axis and press the parts into the
    bottom of the chart (the Memory bar under the cards shows the share); an unseen series a fifth above the
    top line ends the axis there instead. On a Fibre host the rest of the CPU is "Fibre and other" while the
    Fibre unit is active there and "Other" while it is not, as two series of which one has values at a time.
    variant 0: long words and In use; 1: short ones and In use; 2: the short words without In use, the
    line kept off the legend."""
    hue, hs = server_hue(s, h), "host=%s" % q(h)
    names = PART_NAMES[1 if variant else 0]
    parts = stack_parts(s, h, metric)
    fibre = h in s.fibre_hosts and metric == "cpu"
    if metric == "cpu":
        cores = 'count(node_cpu_seconds_total{%s,mode="idle"})' % hs
        total = 'sum(rate(node_cpu_seconds_total{%s,mode!~"idle|iowait"}[5m]))' % hs
        other = "clamp_min(%s%s, 0)" % (total, "".join(" - (%s or vector(0))" % e for _, _, e in parts))
        targets = [target(r, "100 * %s / %s" % (e, cores), names[k]) for r, k, e in parts]
        rest = "100 * %s / %s" % (other, cores)
        if fibre:
            runs = '(max(node_systemd_unit_state{%s,name=%s,state="active"}) == 1)' % (hs, q(s.unit(h, "fibre")))
            targets += [target("D", "%s unless on () %s" % (rest, runs), names["other"]),
                        target("G", "%s and on () %s" % (rest, runs), names["other_fibre"])]
        else:
            targets.append(target("D", rest, names["other"]))
        targets.append(target("E", "100 * %s / %s" % (total, cores), names["total"]))
    else:
        total = "(max(node_memory_MemTotal_bytes{%s}) - max(node_memory_MemAvailable_bytes{%s}))" % (hs, hs)
        other = "clamp_min(%s%s, 0)" % (total, "".join(" - (%s or vector(0))" % e for _, _, e in parts))
        targets = [target(r, e, names[k]) for r, k, e in parts]
        targets += [target("D", other, names["other"]), target("E", total, names["total"]),
                    target("F", "1.2 * %s" % total, "Room above")]
    overrides = [override("byFrameRefID", r, color=fixed(tone(hue, PART_SHADES[k])), custom__fillOpacity=PART_FILL)
                 for r, k, _ in parts]
    overrides += [override("byFrameRefID", r, color=fixed(tone(hue, PART_SHADES["other"])),
                           custom__fillOpacity=PART_FILL) for r in ("DG" if fibre else "D")]
    # All in use: a context line in the text colour, named in the legend except in variant 2.
    shown = variant < 2
    line = {} if shown else {"custom__hideFrom": HIDDEN}
    overrides.append(override("byFrameRefID", "E", color=fixed(NETWORK), custom__stacking={"group": "A", "mode": "none"},
                              custom__fillOpacity=0, custom__lineWidth=1, **line))
    # The legend as it can look: the parts, the rest under its longer name, and the line where there is room.
    legend = ([names[k] for _, k, _ in parts] + [names["other_fibre" if fibre else "other"]]
              + ([names["total"]] if shown else []))
    said = [{"node": "Consensus node (celestia-appd)", "fibre": "Fibre (its Go runtime's memory, a little under "
             "its real use)", "monitoring": "Monitoring (Prometheus and Alertmanager)"}[k] for _, k, _ in parts]
    if metric == "cpu":
        rest = "Other (everything else%s)" % ("; Fibre and other while Fibre runs here, as Fibre reports no CPU time "
                                              "of its own" if fibre else "")
        desc = ("What uses %s's CPU, as a share of all its cores; well under 100%% is healthy. No alert watches "
                "it. Stacked: %s. The line on top, in the text colour, is all in use (In use), 5-minute averages; "
                "iowait is not counted, steal on a VM is." % (h, phrase(said + [rest])))
        return timeseries(pid, server_title(s, h, "CPU by service", "CPU", width), desc + more(pages=[NODE_EXPORTER_FULL]),
                          targets, "percent", maximum=100, decimals=0, stack=True, color=hue,
                          overrides=overrides), legend
    # Room above the top line: unseen and outside the stack, so it only sets the axis.
    overrides.append(override("byFrameRefID", "F", custom__stacking={"group": "A", "mode": "none"}, **UNSEEN))
    desc = ("What holds %s's memory; healthy while the top line stays well under the installed memory; the share "
            "of installed memory is the Memory bar under the server cards. Stacked: %s. The top line, in the text "
            "colour, is all in use (In use): installed minus available, so cache that can be freed is left out.%s "
            "A marker in the text colour shows each time the kernel killed a process for lack of memory."
            % (h, phrase(said + ["Other"]),
               " Fibre can use about 27 GiB at its default limits." if h in s.fibre_hosts else ""))
    title = server_title(s, h, "Memory by service", "Memory", width)
    return timeseries(pid, title, desc + more(pages=[NODE_EXPORTER_FULL]),
                      targets, "bytes", stack=True, color=hue, overrides=overrides), legend


def cpu_chart(s, h, pid, width, variant):
    return stacked(s, h, pid, width, variant, "cpu")


def memory_chart(s, h, pid, width, variant):
    return stacked(s, h, pid, width, variant, "memory")


def mirrored(hue, names):
    """(overrides, legend) of a chart with A above zero and B below: B in a darker shade, its legend entry
    saying it is drawn below."""
    above, below = names
    return ([override("byFrameRefID", "B", color=fixed(tone(hue, ORDER_SHADES[1])))],
            [above, "%s (below)" % below])


def network_chart(s, h, pid, width, variant):
    """Traffic through server h's network ports, in above zero and out below. The link speed is not drawn:
    on a link used at a few percent its line would flatten the traffic to the floor of the chart, and
    Grafana leaves a series it does not draw out of the tooltip too. Fibre's own shard traffic is a small
    part of the total and is drawn in the Fibre row's Uploads and Downloads."""
    hue = server_hue(s, h)
    sel = devices(h, VIRT)
    overrides, legend = mirrored(hue, ("In", "Out"))
    targets = [target("A", "sum(rate(node_network_receive_bytes_total%s[5m])) * 8" % sel, legend[0]),
               target("B", "sum(rate(node_network_transmit_bytes_total%s[5m])) * 8" % sel, legend[1])]
    desc = ("Traffic through %s's network ports in bits per second: in above zero, out below. No alert watches "
            "it. 5-minute averages; virtual interfaces, bond masters and VLANs are left out, their member ports "
            "count. The Network peak bar under the server cards shows the busiest minute of the last hour against "
            "the link speed. Fibre's own traffic is in the Fibre row's Uploads and Downloads." % h)
    return timeseries(pid, server_title(s, h, "Network in and out", "Network", width),
                      desc + more(pages=[NODE_EXPORTER_FULL]), targets, "bps", color=hue, mirror=["B"],
                      overrides=overrides), legend


def disk_levels(s, h):
    """(message, page) levels of DiskLow on server h's Disk used chart, in % used: (90, 95) where a data disk
    of a node or KMS host is listed, which DiskLow pages for; (90, None) where it only sends messages (a
    Fibre store, a Fibre-only or monitor host); (None, None) where no disk of h is watched."""
    message, page = (int(v) if v == int(v) else v for v in (round(100 * (1 - s.DM), 4), round(100 * (1 - s.DP), 4)))
    if not any(host == h for host, _ in s.data_mounts + s.stores):
        return None, None
    paging = (h in s.nodes or h in s.kms_hosts) and any(host == h for host, _ in s.data_mounts)
    return message, page if paging else None


def disk_used_name(s, h):
    """The chart's name with the levels its dashed lines draw: 'Disk used (alert at 90%, 95%)', 'Disk used
    (alert at 90%)', or 'Disk used' where no alert watches the disk."""
    levels = [v for v in disk_levels(s, h) if v is not None]
    return "Disk used (alert at %s)" % ", ".join("%g%%" % v for v in levels) if levels else "Disk used"


def disk_used_chart(s, h, pid, width, variant):
    """How full each disk DiskLow watches on server h is, with DiskLow's levels as dashed lines: its free
    shares (10% and 5%) drawn as used (90% and 95%), as the Disk used bar under the server cards reads. The
    titles name the levels where the longest title of the line fits its chart, else none of them does."""
    hue = server_hue(s, h)
    # (mount, legend word): the word goes before the mount point, so no legend reads as a lone "/".
    data, store = ("Data disk", "Fibre store") if variant == 0 else ("Data", "Fibre")
    mounts = [(m, data) for host, m in s.data_mounts if host == h] + [(m, store) for host, m in s.stores if host == h]
    message, page = disk_levels(s, h)
    watched, paging = message is not None, page is not None
    if not watched:
        mounts = [("/", "Root disk")]
    targets = refs([("100 * (1 - %s)" % free_share([(h, m)]), "%s %s" % (word, m)) for m, word in mounts])
    overrides = [override("byFrameRefID", t["refId"], color=fixed(tone(hue, ORDER_SHADES[min(i, 3)])))
                 for i, t in enumerate(targets)]
    # The levels in every title of the line, or in none: they go in where the longest of its titles fits.
    full = all(text_px("%s · %s" % (x, disk_used_name(s, x)), TITLE_PX, TITLE_EM) + TITLE_ROOM <= inner_px(width)
               for x in s.servers)
    title = "%s · %s" % (h, disk_used_name(s, h) if full else "Disk used")
    p = timeseries(pid, title, "", targets, "percent",
                   threshold=(message, "orange") if watched else None, maximum=100, decimals=0, fill=15, color=hue,
                   overrides=overrides)
    if paging:
        p["fieldConfig"]["defaults"]["thresholds"] = steps(("transparent", None), ("orange", message), ("red", page))
    if not watched:
        desc = ("How full %s's root filesystem is. No disk of this server is listed in celestia_mon_data_mounts, so no "
                "alert watches it." % h)
    else:
        stores = any(word == store for _, word in mounts)
        desc = ("How full each disk DiskLow watches on %s is; healthy below the amber dashed line. Amber: %g%% used, "
                "DiskLow's %g%% free; above it for %s DiskLow sends a message%s."
                % (h, message, s.DM * 100, s.hold("DiskLow low space"),
                   " (on a Fibre store only while Fibre reports no storage budget)" if stores else ""))
        if paging:
            desc += (" Red: %g%% used (%g%% free), where DiskLow pages for a data disk, as it does for a read-only "
                     "one." % (page, s.DP * 100))
    p["description"] = desc + more(["DiskLow"], [NODE_EXPORTER_FULL])
    return p, [t["legendFormat"] for t in targets]


def disk_latency_chart(s, h, pid, width, variant):
    """The average time one write (above zero) and one read (below) took on server h's physical disks, on an
    axis centred on zero that shows at least 10 ms each way, so the columns read alike."""
    hue = server_hue(s, h)
    dev = devices(h, DISK)
    avg = "sum(rate(node_disk_%s_time_seconds_total%s[5m])) / (sum(rate(node_disk_%s_completed_total%s[5m])) > 0)"
    overrides, legend = mirrored(hue, ("Write", "Read"))
    targets = [target("A", avg % ("write", dev, "writes", dev), legend[0]),
               target("B", avg % ("read", dev, "reads", dev), legend[1])]
    desc = ("How long one disk write (above zero) and one read (below) took on %s on average; a few milliseconds "
            "is healthy. No alert watches it. All physical disks together, 5-minute averages; LVM, RAID and ZFS "
            "volumes are left out, their disks count. Over about 10 ms on NVMe, the disk is overloaded (give Fibre "
            "its own disk) or the provider's storage is slow. Node Exporter Full shows each disk." % h)
    return timeseries(pid, server_title(s, h, "Disk latency", "Latency", width), desc + more(pages=[NODE_EXPORTER_FULL]),
                      targets, "s", soft_max=0.01, soft_min=-0.01, color=hue, mirror=["B"],
                      overrides=overrides), legend


def disk_io_chart(s, h, pid, width, variant):
    """Bytes written (above zero) and read (below) per second on server h's physical disks."""
    hue = server_hue(s, h)
    dev = devices(h, DISK)
    overrides, legend = mirrored(hue, ("Write", "Read"))
    targets = [target("A", "sum(rate(node_disk_written_bytes_total%s[5m]))" % dev, legend[0]),
               target("B", "sum(rate(node_disk_read_bytes_total%s[5m]))" % dev, legend[1])]
    desc = ("Bytes per second %s's physical disks wrote (above zero) and read (below). No alert watches it. "
            "5-minute averages. With Disk latency: high latency and high throughput, something loads the disk; "
            "high latency and little throughput, the disk or the provider's storage is slow." % h)
    return timeseries(pid, server_title(s, h, "Disk reads and writes", "Disk I/O", width),
                      desc + more(pages=[NODE_EXPORTER_FULL]), targets, "Bps", color=hue, mirror=["B"],
                      overrides=overrides), legend


def restarts_chart(s, pid):
    """Automatic restarts of every service ServiceDown watches, on every server, as bars in the server's
    colour (its second service in a darker shade). Each bar is the value the crash-loop rule compares, the
    restarts of the last 30 minutes, at its highest within the bar's time block (at least 30 minutes), so a
    bar above the dashed line means the rule's condition held, on every range, but for Fibre's rule's 40
    minutes after a boot, which the bars do not leave out (BOOTED). A block without a restart draws nothing,
    and a range without any says so in words."""
    targets, overrides = [], []
    for h in s.servers:
        kinds = ((["appd"] if h in s.nodes else []) + (["fibre"] if h in s.fibre_hosts else [])
                 + (["tmkms"] if h in s.kms_hosts else []))
        for j, k in enumerate(kinds):
            ref = chr(65 + len(targets))
            targets.append(target(ref, "max_over_time(floor(sum(increase(node_systemd_service_restart_total{host=%s,"
                                  "name=%s}[30m])))[$__interval:1m]) > 0" % (q(h), q(s.unit(h, k))),
                                  "%s · %s" % (h, SERVICE_NAMES[k]), interval="30m"))
            overrides.append(override("byFrameRefID", ref, color=fixed(tone(server_hue(s, h), ORDER_SHADES[j]))))
    p = timeseries(pid, "Restarts (alert above %g in 30 min)" % s.CL, "", targets, "none", threshold=(s.CL, "orange"),
                   soft_max=s.CL + 1, decimals=0, bars=True, overrides=overrides)
    # A bar covers the time block up to its time, so it is drawn to the left of it. Bars have no outline, so
    # a block without a restart draws nothing.
    p["fieldConfig"]["defaults"]["custom"].update({"barAlignment": -1, "lineWidth": 0})
    # "automatic": the start markers on each server's charts show starts by hand, which this chart does not count.
    p["fieldConfig"]["defaults"]["noValue"] = "No automatic restarts in this time"
    booted = (", except for a Fibre in the 40 minutes after its host booted, which exits until its node answers"
              if s.fibre else "")
    p["description"] = ("Times systemd restarted a service by itself, per server and service. Each bar is the most "
                        "restarts within any 30 minutes that end in its time block (30 minutes on 24 hours, longer on "
                        "long ranges): the value ServiceDown's crash-loop check compares, so a bar above the dashed "
                        "line, more than %g, means ServiceDown reported a crash loop%s. A single restart lifts the bars "
                        "of the 30 minutes after it, so at most two bars show it. Bars at one time can overlap: the "
                        "tooltip lists each. A start by hand is not counted here; it shows as a grey marker on its "
                        "server's charts." % (s.CL, booted) + more(["ServiceDown"]))
    return p


def clock_chart(s, pid):
    """Every server's clock maximum error, the value ClockNotSynced reads, one line per server in its colour,
    with the rule's level dashed. The kernel's offset (node_timex_offset_seconds) stays 0 under chrony, which
    never hands the kernel an offset to slew; the maximum error is set by chrony and systemd-timesyncd alike,
    and the kernel adds 0.5 ms a second until the next update. A linear axis from 0 to a quarter above the
    level, so a healthy clock lies on the floor, well clear of the line, and a stopped sync climbs to it."""
    level = rule_number("ClockNotSynced", None, r"node_timex_maxerror_seconds\{[^}]*\}\[3m\]\) >= (\d+)\s*$")
    top = level * 1.25
    targets = refs([("max(node_timex_maxerror_seconds{host=%s})" % q(h), h) for h in s.servers])
    overrides = [override("byFrameRefID", t["refId"], color=fixed(server_hue(s, h)))
                 for t, h in zip(targets, s.servers)]
    return timeseries(
        pid, "Clock maximum error (alert at %d s)" % level,
        "How far each server's clock may be off at most, by its time sync's own account; a line along the floor, "
        "about a second or less, is good. chrony sets it at each clock update and systemd-timesyncd at each "
        "correction (to 0), and the kernel adds 0.5 ms a second between updates, so working time sync draws a low "
        "ripple. At the dashed %d s line the kernel counts the clock as not synchronised, and after %s "
        "ClockNotSynced sends a warning. A line that climbs steadily towards the dashed %d s line, which it reaches "
        "about %d hours after the last update, means time sync stopped; the kernel caps the value at %d s, so it "
        "then lies on the line. A node stamps its votes with this clock and rejects every block once it runs about 60 s slow; Fibre "
        "reports not ready once it runs about 2 minutes fast (max_block_age). A clock synchronised to a wrong source "
        "still reads low here: chronyc tracking, timedatectl timesync-status and doctor check 9 show the measured "
        "offset. The axis is linear, 0 to %g s."
        % (level, s.hold("ClockNotSynced"), level, round(level / 0.0005 / 3600), level, top)
        + more(["ClockNotSynced"]),
        targets, "s", threshold=(level, "orange"), soft_max=top, overrides=overrides)


# The lines of the Servers section, top to bottom: (id base, chart, height). A column chart takes one column
# per server; a SHARED one is one chart of every server across the full width. The open row first: the three
# charts that move through a day. Then the closed one: the disks together (Disk used fills over days, and its
# value and alert colour are on the first screen's Disk used bar), then the two charts of every server.
SERVER_OPEN = [(410, cpu_chart, 7), (420, memory_chart, 7), (430, network_chart, 7)]
SERVER_CLOSED = [(440, disk_used_chart, 6), (460, disk_latency_chart, 7), (470, disk_io_chart, 7),
                 (480, restarts_chart, 4), (490, clock_chart, 5)]
SHARED = (480, 490)
MEMORY_BASE = 420
SINGLE_PER = 3                           # a single server's column charts to a line, each a third wide


def server_ids(ids, i):
    """The ids of the column charts of the server in place i."""
    bases = {base for base, _, _ in SERVER_OPEN + SERVER_CLOSED if base not in SHARED}
    return sorted(pid for pid in ids if pid // 10 * 10 in bases and pid % 10 == i)


def servers_row(s):
    """(open lines, closed lines) of the Servers section: one column per server, up to four to a line (w12,
    w8, w6; more servers wrap onto lines of equal widths), the charts of one kind side by side at one height,
    so every server's charts keep one column; Restarts and Clock maximum error across the full width. A single
    server takes three column charts to a line (SINGLE_PER), so each row's three fill it. Each line picks the
    longest legend words that fit every chart of it on one line."""
    n = len(s.servers)
    if n > SERVERS_MAX:
        raise SystemExit("the Servers section has room for %d servers, and the settings list %d" % (SERVERS_MAX, n))
    if n == 1:
        per, width = 1, 24 // SINGLE_PER
    else:
        rows = int(math.ceil(n / 4.0))
        per = int(math.ceil(n / float(rows)))
        width = 24 // per

    def charts(base, chart):
        for variant in range(3):
            made = [chart(s, h, base + i, width, variant) for i, h in enumerate(s.servers)]
            if all(legend_px(names) <= inner_px(width) - LEGEND_ROOM for _, names in made):
                break
        return [p for p, _ in made]

    def lines(spec):
        out = []
        run = []
        for base, chart, height in spec:
            if base in SHARED:
                out.append([(height, chart(s, base), 24)])
                run = []
                continue
            made = charts(base, chart)
            if n > 1:
                out += [[(height, p, width) for p in made[i:i + per]] for i in range(0, n, per)]
                continue
            if len(run) in (0, SINGLE_PER):
                run = []
                out.append(run)
            run.append((height, made[0], width))
        return [(max(height for height, _, _ in line), [(p, w) for _, p, w in line]) for line in out]

    return lines(SERVER_OPEN), lines(SERVER_CLOSED)


def place(lines, y):
    """Sets the grid position of every panel of lines from grid row y; returns the panels and the next row."""
    panels = []
    for height, items in lines:
        x = 0
        for p, width in items:
            p["gridPos"] = {"h": height, "w": width, "x": x, "y": y}
            panels.append(p)
            x += width
        y += height
    return panels, y


def host_colors(s):
    """Overrides giving each server's series its identity colour, a single server's too. A pattern in
    slashes is a plain JavaScript regex; without them Grafana anchors it as ^...$."""
    return [override("byRegexp", "/^%s( |$)/" % js_regex(h), color=fixed(s.color(h)[0])) for h in s.servers]


# Routine markers (starts, repoints, announcements, upgrades): a faint grey, so Grafana's dashed line
# sits behind the data and reads fainter than the alert levels' dashed lines.
MARKER = "#8E8E8E66"
# What each routine marker marks, in the words of the sentence build() adds to the (i) of every panel it
# reaches; keyed by the marker's name, "Service starts on <server>" by its start. "%s": the node's units.
MARKER_WORDS = {"Node and signer starts": "%s starts", "Fibre starts": "Fibre starts",
                "Validator signs through": "repoints (the validator moving to another node)",
                "Service starts on": "starts of its services", "Celestia announcement": "Celestia announcements",
                "Network upgrade": "network upgrades"}


def node_units(s):
    """The units whose starts the Validator charts mark: the nodes', and tmkms's where its host is scraped,
    each under every name its hosts use."""
    return [u for k in ["appd"] + (["tmkms"] if s.kms_hosts else []) for u in s.names(k)]


def marker_words(s, name):
    """The MARKER_WORDS of the routine marker called name: 'celestia-appd and tmkms starts', 'network
    upgrades'. A routine marker without words stops the generator, so no grey line goes unexplained."""
    key = next((k for k in MARKER_WORDS if name == k or name.startswith(k + " ")), None)
    if key is None:
        raise SystemExit("the marker %r has no words in MARKER_WORDS, which its charts' (i) print" % name)
    words = MARKER_WORDS[key]
    return words % phrase([u[:-len(".service")] for u in node_units(s)]) if "%s" in words else words


def annotations(s, ids):
    """Markers on the charts, each kept off the status board by its panel list; their toggles are hidden.
    Routine markers are faint grey and Out of memory is in the text colour: no alert watches them. A start
    shows where it explains something: the node's and the signer's on the Validator charts, Fibre's on the
    Fibre charts, each server's own on its column charts; Restarts carries none, as Grafana draws no marker
    on a chart without data, its healthy state. Celestia announcements mark the role band, the Validator
    and the Fibre charts, network upgrades every chart. Two red regions shade the time an outage that
    blanks the charts paged: ValidatorNotSigning on the Validator charts and FibreDown (no Fibre running) on
    the Fibre charts, in the words of TITLES. Grafana joins the samples of an annotation query that lie a
    step apart into one region. The other alerts read a chart's own value and show as its line crossing the
    dashed level."""
    def note(name, expr, title, color, panel_ids):
        return {"datasource": s.ds, "enable": True, "expr": expr,
                "filter": {"exclude": False, "ids": sorted(i for i in panel_ids if i in ids)},
                "hide": True, "iconColor": color, "name": name, "step": "60s", "titleFormat": title}

    def starts(sel):
        # Comparing with the value one step earlier catches every start once at a 60 s step and
        # ignores stops (the start time drops to 0).
        return ("node_systemd_unit_start_time_seconds%s > node_systemd_unit_start_time_seconds%s offset 1m"
                % (sel, sel))
    sentence = {t[0]: t[1] for t in TITLES}
    units = node_units(s)
    node_sel = "{name=%s}" % q(units[0]) if len(units) == 1 else "{name=~%s}" % q("|".join(re2(u) for u in units))
    charts = sorted(i for i in ids if i >= 100 and ids[i] == "timeseries")
    if s.remote:
        moved = ("(sum by (host) (increase(%s[2m])) > 0) unless on (host) (sum by (host) (increase(%s[2m] offset 3m)) > 0)"
                 % (PV, PV))
    else:
        moved = ("(max by (host) (changes(%s[2m])) > 0) unless on (host) (max by (host) (changes(%s[2m] offset 3m)) > 0)"
                 % (LSH, LSH))
    notes = [
        {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": False, "hide": True,
         "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
        note("Node and signer starts", starts(node_sel), "{{name}} started on {{host}}", MARKER,
             [111, 112, 115, 116]),
    ]
    if s.fibre:
        notes.append(note("Fibre starts", starts("{%s}" % s.is_unit("fibre")), "{{name}} started on {{host}}",
                          MARKER, [210, 211, 212, 213, 215]))
    notes.append(note("Validator signs through", moved, "Validator signs through {{host}}", MARKER,
                      [25, 111, 112, 115, 116]))
    notes.append(note("Validator not signing", 'ALERTS{alertname="ValidatorNotSigning",alertstate="firing"}',
                      sentence["ValidatorNotSigning:.*"], ALERT, [111, 112, 115, 116]))
    if s.fibre:
        notes.append(note("No Fibre server running", 'ALERTS{alertname="FibreDown",check="",alertstate="firing"}',
                          sentence["FibreDown:page::::"], ALERT, [210, 211, 212, 213]))
    # The Servers section's charts each show one server: its markers come from that server only.
    for i, h in enumerate(s.servers):
        hs = "{host=%s}" % q(h)
        mine = server_ids(ids, i)
        notes.append(note("Service starts on %s" % h, starts(hs), "{{name}} started on {{host}}", MARKER, mine))
        notes.append(note("Out of memory on %s" % h, "node_vmstat_oom_kill%s > node_vmstat_oom_kill%s offset 1m" % (hs, hs),
                          "Out of memory on {{host}}", NETWORK, [p for p in mine if p // 10 * 10 == MEMORY_BASE]))
    if s.announcements:
        # The counter starts at 0 for every channel when celestia-mon-announcements starts.
        notes.append(note("Celestia announcement", "celestia_mon_announcements_forwarded_total > "
                          "celestia_mon_announcements_forwarded_total offset 1m",
                          "Celestia announcement (the text is in your alert channel)", MARKER,
                          [25] + [i for i in charts if i < 300]))
    if s.signal:
        notes.append(note("Network upgrade", "celestia_mon_app_version > celestia_mon_app_version offset 2m",
                          "Network upgrade: new app version", MARKER, [25] + charts))
    return notes


def check_variables(panels):
    """Only Grafana's built-in variables that CI knows to replace: $__interval and $__range in the status
    board, the role band and the uptime columns; elsewhere only $__interval, in charts and sparklines that
    count per time block."""
    for p in panels:
        allowed = "interval|range" if p["id"] in BOARD_PANELS else "interval"
        for t in p.get("targets", []):
            if re.search(r"\$__(?!(?:%s)\b)" % allowed, t["expr"]):
                raise SystemExit("panel %d (%s) uses a Grafana variable other than %s" % (
                    p["id"], p["title"], " and ".join("$__" + v for v in allowed.split("|"))))


def build(cfg):
    s = Setup(cfg)
    check_titles(s)
    # (row id, title and collapsed, or None for the first screen without a row header; lines)
    # The alert list, closed, right under the first screen: one click from the Status cell.
    sections = [(None, first_screen(s)), ((300, "All firing alerts", True), alerts_row(s)),
                ((100, "Validator", False), validator_row(s))]
    if s.fibre:
        sections.append(((200, "Fibre", False), fibre_row(s)))
    hardware, detail = servers_row(s)
    sections.append(((400, "Servers", False), hardware))
    sections.append(((450, "Servers: disks, restarts and clocks", True), detail))
    if s.signal:
        sections.append(((250, "Network upgrade", True), upgrade_row(s)))
    colors = host_colors(s)

    panels, every, y = [], [], 0
    for row, lines in sections:
        if row is None:
            placed, y = place(lines, y)
            panels += placed
            every += placed
            continue
        (rid, title, collapsed) = row
        row_panel = {"id": rid, "type": "row", "title": title, "collapsed": collapsed, "panels": [],
                     "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}}
        # A collapsed row keeps its panels, placed as if it were open; the next row follows it.
        placed, end = place(lines, y + 1)
        panels.append(row_panel)
        if collapsed:
            row_panel["panels"] = placed
            y += 1
        else:
            panels += placed
            y = end
        every += placed

    for p in every:
        if "targets" in p:
            p["datasource"] = s.ds
            for tg in p["targets"]:
                tg["datasource"] = s.ds
        # Host colours first, so a panel's own colour overrides (threshold series) win.
        if p["type"] == "timeseries":
            p["fieldConfig"]["overrides"] = colors + p["fieldConfig"]["overrides"]
    one_value_size(every)
    check_variables(every)
    check_surfaces(s, every)
    check_words(s, every)
    ids = {p["id"]: p["type"] for p in every}
    notes = annotations(s, ids)
    # Every panel a routine marker reaches says in its (i) what its grey lines mark, generated from the notes
    # that draw them, so the words cannot drift from the lines: one sentence, before the links.
    kinds = {}
    for note in notes:
        if note["iconColor"] == MARKER:
            for pid in note["filter"]["ids"]:
                kinds.setdefault(pid, []).append(marker_words(s, note["name"]))
    for p in every:
        if p["id"] not in kinds:
            continue
        said = "Grey dashed lines: %s; point at the mark at the foot of a line to read it." % phrase(kinds[p["id"]])
        desc = p["description"]
        cut = desc.rfind(" More:")
        p["description"] = desc[:cut] + " " + said + desc[cut:] if cut >= 0 else " ".join(t for t in (desc, said) if t)

    return {
        "uid": UID,
        "title": TITLE,
        "description": "Generated by celestia-mon from the inventory. Do not edit; rerun the playbook instead.",
        "tags": ["celestia", "celestia-mon"],
        "editable": False,
        "graphTooltip": 1,
        "liveNow": False,
        "refresh": "30s",
        "time": {"from": "now-24h", "to": "now"},
        "timepicker": {"refresh_intervals": ["30s", "1m", "5m", "15m"]},
        "timezone": "utc",
        "schemaVersion": 41,
        "version": 1,
        "annotations": {"list": notes},
        "links": [{"asDropdown": False, "icon": "external link", "includeVars": False, "keepTime": False,
                   "placement": "inControlsMenu", "tags": [], "targetBlank": True, "title": lt, "tooltip": "",
                   "type": "link", "url": lu}
                  for lt, lu, fibre in LINKS if s.fibre or not fibre],
        "templating": {"list": []},
        "panels": panels,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="settings JSON rendered from dashboard-config.json.j2")
    parser.add_argument("--out", required=True, help="dashboard JSON to write")
    args = parser.parse_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    text = json.dumps(build(cfg), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


if __name__ == "__main__":
    main()
