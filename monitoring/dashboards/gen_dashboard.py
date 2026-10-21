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
      "announcements_enabled": true | false,       # celestia_mon_announcements
      "units": {"appd": "...", "fibre": "...", "tmkms": "..."},
      "thresholds": {...},                         # celestia_mon_thresholds
      "nodes": ["..."],                            # celestia-appd hosts: validator, then backup_node
      "kms_hosts": ["..."],                        # kms hosts with celestia_mon_kms_scrape
      "monitor_host": "...",                       # celestia_mon_monitor_host
      "prometheus_retention": "30d",               # celestia_mon_prometheus_retention
      "fibre_mounts": [{"host": "...", "mount": "..."}],  # every fibre host, active or standby
      "fibre_endpoints": [{"host": "...", "endpoint": "..."}],  # every fibre host; celestia_mon_fibre_public_endpoint or ""
      "data_mounts": [{"host": "...", "mount": "..."}],
      "links": [{"host": "...", "mbps": 1000}]     # every node_exporter host; mbps: celestia_mon_link_mbps or null
    }

Layout (docs/panels.md): a first screen without a row header that reads like a status page, top
to bottom: one framed strip with the validator's name and network, four facts and the Status (the
worst alert in a few words); up to six one-word answers; the status board, one line of bars per
check and per server with its uptime; the role band, which server held each role; a colour key;
and the Servers now table. Then the rows
Validator and Fibre (expanded) and Network upgrade, All firing alerts and Servers in detail
(collapsed). Panels that do not apply to the install are left out.

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
order and colour the servers (every configured server gets a status line and a table row, even
when nothing is read from it), to select a server's own disks and link, and to pair each Fibre
host with its public endpoint.

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
import json
import math
import os
import re

UID = "celestia-mon-validator"
TITLE = "Celestia validator"
DOCS = "https://github.com/s0urledd/celestia-validator-monitoring/blob/main/docs/alerts.md"
QUICKSTART = "https://github.com/s0urledd/celestia-validator-monitoring/blob/main/docs/quickstart.md"
# Pages the descriptions link to, as (text, url). Panels carry no links of their own: one (i) per title.
NODE_EXPORTER_FULL = ("Node Exporter Full", "https://grafana.com/grafana/dashboards/1860-node-exporter-full/")
FIBRE_DASHBOARD = ("Fibre dashboard (celestia-app)",
                   "https://github.com/celestiaorg/celestia-app/blob/v10.4.0/observability/docker/grafana/dashboards/fibre.json")
TENSILE = ("Tensile", "https://tensile.huginn.tech")
MAINNET_LAYOUT = ("mainnet layout", QUICKSTART + "#mainnet-layout")

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
# server, its name and the role band's tints (the hex at 20% alpha). Never green, yellow, orange,
# red or grey, which mean state; neighbours differ in hue and lightness, and each keeps at least
# 5:1 against the panel background. Servers past the list are drawn in the text colour.
HOST_COLORS = [("#5794F2", "#5794F2"), ("#CA95E5", "#CA95E5"), ("#6ED0E0", "#6ED0E0"), ("#8AB8FF", "#8AB8FF")]
# What belongs to the whole network rather than one server (block time, the network's missed
# share, expected counts, capacity lines): the text colour, dashed where it is a reference.
NETWORK = "#CCCCDC"
REFERENCE = "#CCCCDC99"

# Bar fills and key swatches: fine (a muted green, so a healthy board is calm), look soon, urgent
# (Grafana's dark-theme red, so red words and red bars match), and no data or not in use.
OK, WARN, ALERT, NODATA = "#56A64B99", "#FF9830", "#F2495C", "#8E8E8E40"
# Words use named colours; grey words (no data, not checked, standby) this fixed grey.
GREY = "#8E8E8E"
# A zero-width space: the uptime columns' lane names, distinct and invisible.
ZERO_WIDTH = chr(0x200B)

# Grafana 12.4's panel chrome: a titled panel loses 58 px of its height (header, padding, border),
# an untitled one 18 px. A grid row is 30 px, plus 8 px between rows.
CHROME_TITLED = 58
CHROME_UNTITLED = 18
LANE_PX = 24          # the least height of a status board line or role lane
BAR_PX = 20           # the thickness of every status bar
BAND_BAR_PX = 24      # the thickness of the role band's segments
AXIS_PX = 24          # the role band's time axis
TABLE_ROW_PX = 42     # cellHeight "md": each table row
# The table's header row. At most 40 px, as the render showed: two rows then fit h5 with no
# scrollbar. Set it to the measured height to keep the band under the last row as small as can be.
TABLE_HEADER_PX = 40

# Text checks, at 1440 px wide with Grafana's menu docked (Grafana 12 docks it from 1200 px), with
# widths measured in the rendered dashboard (Grafana's Inter at weight 500): a letter 0.52 em, a
# digit 0.60 em, a separator (. ,) 0.30 em. Each strip pins one text size; a pinned size is never
# shrunk by Grafana, so the generator makes sure the longest text fits.
CHAR_EM, DIGIT_EM, SEP_EM = 0.52, 0.60, 0.30
LINE_EM = 1.2                            # a line of text is 1.2 times its size high
STACK_GAP_PX = 7                         # between label and value in a centred value_and_name stack
# The intro strip at the top: six framed, untitled cells of one height and width (identity, Latest
# block, Block time, Voting power, Signed and Status), each a label over a value, all in one size
# pair, so every label sits on one line and every value on another, and the column edges line up
# with the answer tiles below. The values take the first size of INTRO_VALUE_SIZES at which the
# validator's name fits; every other word must fit at the first.
INTRO_ROWS, INTRO_LABEL_PX, INTRO_VALUE_SIZES = 3, 13, (22, 20, 18)
INTRO_WIDTHS = (4, 4, 4, 4, 4, 4)
NAME_CHARS = 17                          # the validator's name: 13 characters at 22 px, 15 at 20, 17 at 18
DEFAULT_NAME = "Validator"               # the name when celestia_mon_validator_name is empty
INTRO_NUMBER = "99.999.999"              # the widest number a cell shows (a block height)
KEY_ROWS, KEY_PX = 2, 12                 # the colour key: one 12 px line, centred in an untitled h2
TILE_SIZES = (22, 20, 18, 16)            # the answer tiles: the largest at which every word fits its tile
# Facts inside the rows: an untitled h2 with the label over the value, never larger than the answers.
FACT_ROWS, FACT_LABEL_PX, FACT_PX, FACT_SMALL_PX = 2, 12, 18, 16
# The table's 14 px text. Its columns are sized by short words ("no data", "Peers"), which run up to
# 0.56 em, so it keeps 0.55 em.
TABLE_CHAR = 0.55 * 14
TABLE_PAD = 16                           # a cell's padding
TABLE_ROOM = 1060                        # what the Servers now table may fill without a horizontal scrollbar
GAUGE_MIN, GAUGE_FLOOR = 90, 60          # the gauge columns' minimum width, and the least it may drop to

# Block height of every celestia-appd node: the validator and the backup nodes.
H = 'cometbft_consensus_height{job="celestia-app"}'
LSH = 'cometbft_consensus_validator_last_signed_height{job="celestia-app"}'
MISSED = 'cometbft_consensus_validator_missed_blocks{job="celestia-app"}'
POWER = 'cometbft_consensus_validator_power{job="celestia-app"}'
TOTAL = 'cometbft_consensus_validators_power{job="celestia-app"}'
# Signatures the node's privval client made: votes and proposals (PV), and Fibre's raw bytes (RAW).
# A node that only forwards Fibre's requests is never taken for the node the validator signs through.
PV = 'cometbft_privval_signing_latency_seconds_count{job="celestia-app",message_type=~"prevote|precommit|proposal"}'
RAW = 'cometbft_privval_signing_latency_seconds_count{job="celestia-app",message_type="raw_bytes"}'

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
NNA = 'max(ALERTS{alertname="NodeNotAdvancing"})'
# 10 while the page alert fires, else 0: added to a code, it turns the amber word red.
VNS10 = '(max(ALERTS{alertname="ValidatorNotSigning",alertstate="firing"}) * 10 or vector(0))'
NNA10 = '(max(ALERTS{alertname="NodeNotAdvancing",alertstate="firing"}) * 10 or vector(0))'
NE_UP = '(max by (host) (up{job="node"}) == 1)'
UP_LOST = 'or on () (max(up{job="prometheus"}) * 0 - 1)'

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
BOARD_PANELS = {20, 21, 22, 23, 25}

# How long after a check stopped the alert that reports it fires, on top of its hold time: the
# 30 minutes in TensileUnreadable's expression and the 15 in SignalUnreadable's. FibreNotServing
# (uploads_failing) reads a 5-minute window before its hold.
TENSILE_STALE, SIGNAL_STALE, UPLOAD_WINDOW = 1800, 900, 300

# Plain sentences for the alerts, the words of the notifications, for the alert list, and short
# names for the Status cell: (key, sentence, short, needs). The key is a regex over
# alertname:severity:unit:reason:check:host of a series grouped by by_alert(); Prometheus anchors it,
# and $1 is the host at the end of the key. short: the Status cell's words, without a host, at most
# what the cell holds (the intro strip's fit check). needs: the switch the alert needs (Setup.has).
TITLES = [
    ("NodeNotAdvancing:.*", "No new blocks on any node", "Nodes stalled", None),
    ("ValidatorNotSigning:.*", "Validator not signing", "Not signing", None),
    ("SignaturesMissed:.*", "Validator missed blocks", "Missed blocks", None),
    ("NodeBehind:.*:([^:]*)", "Node behind on $1", "Node behind", None),
    ("RemoteSignerProblem:message::::.*", "Remote signer slow", "Signer slow", "remote"),
    ("RemoteSignerProblem:message::[^:]+::.*", "Remote signer fails proposals", "Bad proposals", "remote"),
    ("FibreDown:page::::", "No Fibre server running", "Fibre down", "fibre"),
    ("FibreDown:page:::reachability:.*", "Fibre unreachable from outside", "Fibre offline", "tensile"),
    ("FibreDown:page:::registration:.*", "Fibre not registered or wrong host", "Unregistered", "tensile"),
    ("FibreBothActive:.*", "Fibre running on two servers", "Two Fibres", "fibre"),
    ("FibreNotServing:message::stale::([^:]*)", "No Fibre uploads on $1", "No uploads", "fibre"),
    ("FibreNotServing:message::uploads_failing::([^:]*)", "Fibre uploads fail on $1", "Upload errors", "fibre"),
    ("FibreNotServing:message::no_telemetry::([^:]*)", "No Fibre metrics on $1", "No Fibre data", "fibre"),
    ("ServiceDown:message:node_exporter:::([^:]*)", "$1 not reporting", "Not reporting", None),
    ("ServiceDown:message:celestia_app:::([^:]*)", "Node metrics lost on $1", "Node silent", None),
    ("ServiceDown:message:otelcol:::([^:]*)", "Fibre metrics lost on $1", "No Fibre data", "fibre"),
    ("ServiceDown:message:appd:::([^:]*)", "Celestia node stopped on $1", "Node stopped", None),
    ("ServiceDown:message:appd:crashloop::([^:]*)", "Node keeps restarting on $1", "Node crashing", None),
    ("ServiceDown:message:appd:not_loaded::([^:]*)", "Node service missing on $1", "Node missing", None),
    ("ServiceDown:message:tmkms:::([^:]*)", "Remote signer stopped on $1", "Signer down", "tmkms"),
    ("ServiceDown:message:tmkms:crashloop::([^:]*)", "Signer keeps restarting on $1", "Signer crash", "tmkms"),
    ("ServiceDown:message:tmkms:not_loaded::([^:]*)", "Signer service missing on $1", "No signer", "tmkms"),
    ("ServiceDown:message:fibre:crashloop::([^:]*)", "Fibre keeps restarting on $1", "Fibre crash", "fibre"),
    ("DiskLow:page::::([^:]*)", "Disk full or read-only on $1", "Disk critical", None),
    ("DiskLow:message::::([^:]*)", "Disk low or read-only on $1", "Disk low", None),
    ("MonitoringBroken:.*", "Alert rules failing", "Rules failing", None),
    ("NotificationsFailing:.*", "Alerts not delivered", "Alerts unsent", None),
    ("DeadmanFailing:.*", "Dead-man service not reached", "No heartbeat", None),
    ("TensileUnreadable:.*", "Outside check stopped", "Check stopped", "tensile"),
    ("AnnouncementsUnreadable:.*", "Announcements not read", "Feed stopped", "announcements"),
    ("UpgradeSignalMissing:.*", "Upgrade signal needed", "Not signalled", "signal"),
    ("UpgradeScheduled:.*", "Network upgrade scheduled", "Upgrade set", "signal"),
    ("UpgradeSoon:.*", "Network upgrade within 24 hours", "Upgrade soon", "signal"),
    ("SignalUnreadable:.*", "Upgrade check stopped", "Signal stale", "signal"),
]
# The Status cell's words for an alert whose labels match no key of TITLES.
SHORT_FALLBACK = "See alerts"
# Message alerts with a host label that colour a line of their own, not the generic warning of a
# server's line (and of the Servers tile).
SERVER_OTHERS = ("RemoteSignerProblem|FibreNotServing|MonitoringBroken|NotificationsFailing|DeadmanFailing|"
                 "TensileUnreadable|AnnouncementsUnreadable|SignalUnreadable|UpgradeSignalMissing|UpgradeScheduled|"
                 "UpgradeSoon")
# The heartbeat goes to the dead-man service only and is never shown as an alert.
UNTITLED = {"Watchdog"}
# Worst first between alerts of one level, so the order stays the same between refreshes.
PRIORITY = {"NodeNotAdvancing": 0.09, "ValidatorNotSigning": 0.08, "FibreDown": 0.07, "DiskLow": 0.06,
            "UpgradeSignalMissing": 0.05, "ServiceDown": 0.04, "SignaturesMissed": 0.035,
            "RemoteSignerProblem": 0.03, "FibreNotServing": 0.025, "NodeBehind": 0.02,
            "FibreBothActive": 0.015, "UpgradeSoon": 0.012}

# The Role column: a server's parts add up to one code. (code, words, short words).
NODE_PARTS = [(1, "Signing node", "Signing"), (2, "Backup node", "Backup"), (3, "Node, not signing", "Not signing")]
FIBRE_PARTS = [(4, "Fibre active", "Fibre"), (8, "Fibre standby", "Fibre standby")]
KMS_PARTS = [(16, "Remote signer", "Remote signer"), (32, "Signer stopped", "Signer stopped"),
             (64, "Signer not loaded", "Signer not loaded")]


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
    of an alert rule's threshold, drawn as a dashed line with the zone past it faintly tinted, so
    colour appears only where the alert's condition holds (low_bad: the zone is below the value);
    scheme: a single series that takes the threshold's colour where it crosses it. soft_max,
    soft_min: the range the axis always shows (a soft max stops Grafana's 0..100 axis on flat data);
    minimum, maximum: hard limits, None for none. step: counts drawn as steps; smooth: smooth curves.
    span_nulls: False, or the longest gap in ms a line bridges. centered: zero in the middle of the
    axis. mirror: refIds drawn below zero (receive above, transmit below). bars: events per time
    bucket as bars, so a quiet chart is empty and a single event shows. stack: the series are parts
    of a whole, stacked in flat fills (a total drawn over them needs an override with
    custom__stacking={"mode": "none", "group": "A"}). fill: the fill opacity, when the default does
    not suit. legend False: one series, without a legend."""
    custom = {"axisBorderShow": False, "drawStyle": "bars" if bars else "line",
              "fillOpacity": 20, "gradientMode": "opacity",
              "lineInterpolation": "stepAfter" if step else "smooth" if smooth else "linear",
              "lineWidth": 2, "showPoints": "never", "spanNulls": span_nulls,
              "thresholdsStyle": {"mode": "dashed+area" if threshold else "off"}}
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
        elif low_bad:
            defaults["thresholds"] = steps((tone, None), ("transparent", value))
        else:
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


def bargauge(pid, label, desc, targets, unit, thresholds, decimals=0, no_value=None):
    """One horizontal bar of a share, from 0 to 1, with its value as text, in the row facts' form:
    untitled, the label (FACT_LABEL_PX) above the bar and the value (FACT_PX)."""
    defaults = {"color": {"mode": "thresholds"}, "decimals": decimals, "displayName": label, "max": 1, "min": 0,
                "thresholds": thresholds, "unit": unit}
    if no_value is not None:
        defaults["noValue"] = no_value
    options = {"displayMode": "basic", "maxVizHeight": 20, "minVizHeight": 16, "namePlacement": "top",
               "orientation": "horizontal",
               "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
               "showUnfilled": True, "sizing": "manual",
               "text": {"titleSize": FACT_LABEL_PX, "valueSize": FACT_PX}, "valueMode": "text"}
    return panel(pid, "bargauge", "", desc, targets, defaults, (), options)


def grid_px(height):
    """Pixel height of a panel height grid rows tall."""
    return 38 * height - 8


def grid_rows(content, chrome):
    """Grid rows a panel needs to show content px under chrome px of panel chrome."""
    return int(math.ceil((content + chrome + 8) / 38.0))


def inner_px(width):
    """Content width in px of a panel width grid columns wide, at 1440 px with the menu docked."""
    return 46.25 * width - 25


def text_px(text, size):
    """Estimated width in px of text at size px: digits DIGIT_EM, separators SEP_EM, the rest CHAR_EM."""
    return size * sum(DIGIT_EM if c.isdigit() else SEP_EM if c in ".," else CHAR_EM for c in text)


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


def strip_widths(n):
    """Grid widths of n panels side by side in one strip of 24 columns: the first one is widest."""
    return {1: [24], 2: [12, 12], 3: [8, 8, 8], 4: [6, 6, 6, 6], 5: [8, 4, 4, 4, 4], 6: [4] * 6}[n]


def flow(panels):
    """Chart lines: three charts at width 8 and height 8; a line of two at width 12, or one at 24, at
    height 7. One chart left over after lines of three makes the last four two lines of two."""
    n = len(panels)
    sizes = [3] * (n // 3) + ([n % 3] if n % 3 else [])
    if n % 3 == 1 and n >= 4:
        sizes = [3] * ((n - 4) // 3) + [2, 2]
    lines, i = [], 0
    for k in sizes:
        lines.append((8 if k == 3 else 7, [(p, 24 // k) for p in panels[i:i + k]]))
        i += k
    return lines


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


def load_rules():
    """(holds, alert names): the hold times in monitoring/rules/holds.json by name, and the name of
    every alert in the rules template."""
    with open(HOLDS_FILE, encoding="utf-8") as f:
        holds = json.load(f)["holds"]
    with open(RULES_TEMPLATE, encoding="utf-8") as f:
        names = sorted(set(re.findall(r"^\s*- alert: (\w+)", f.read(), re.M)))
    return holds, names


class Setup:
    """The settings, with what the panels derive from them.

    remote, multi (tmkms signs through several nodes on purpose), fibre, tensile, signal and
    announcements are the switches. nodes are the celestia-appd hosts, the validator first;
    fibre_hosts every Fibre host; kms_hosts the KMS hosts whose node_exporter runs, with a remote
    signer; servers the nodes, then the other node_exporter hosts, in inventory order: the order of
    the status board, the table and the host colours. data_mounts and stores are the disks DiskLow
    watches as data mounts and as Fibre stores, as the rules template picks them."""

    def __init__(self, cfg):
        t = cfg["thresholds"]
        self.ds = {"type": "prometheus", "uid": cfg["datasource_uid"]}
        self.name = (cfg.get("name") or "").strip()
        self.remote = cfg["signer"] == "remote"
        self.multi = self.remote and bool(cfg.get("signer_multi_node"))
        self.fibre = bool(cfg["fibre_enabled"])
        self.tensile = self.fibre and bool(cfg.get("tensile_enabled"))
        self.signal = bool(cfg.get("signal_enabled"))
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
        self.fibre_mounts = list(cfg.get("fibre_mounts", [])) if self.fibre else []
        self.fibre_hosts = [m["host"] for m in self.fibre_mounts]
        if self.fibre and not self.fibre_hosts:
            raise SystemExit("celestia_mon_fibre_enabled is true, but no host is in the fibre group")
        self.fibre_endpoints = [(e["host"], e["endpoint"]) for e in cfg.get("fibre_endpoints", [])
                                if self.fibre and e.get("endpoint")]
        scraped = [h for h in cfg.get("kms_hosts", []) if h in hosts]
        self.kms_hosts = scraped if self.remote else []
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
        self.N = t["not_signing_blocks"]
        self.R = t.get("signatures_missed_ratio", 0.05)
        self.NB = t.get("node_behind_blocks", 20)
        self.S = t["signer_median_seconds"]
        self.DP = t["disk_page_free_ratio"]
        self.DM = t["disk_message_free_ratio"]
        self.CL = t["crashloop_restarts"]
        self.SU = t["fibre_stale_uploads"]
        self.SM = t["fibre_stale_min_seconds"]
        self.holds, self.alert_names = load_rules()

    def has(self, need):
        """Whether the alerts that need need exist in this install (None: always)."""
        return {None: True, "remote": self.remote, "fibre": self.fibre, "tensile": self.tensile,
                "signal": self.signal, "announcements": self.announcements, "tmkms": bool(self.kms_hosts)}[need]

    def color(self, host):
        """(named colour, hex) of a server's identity colour; the text colour past the list."""
        i = self.servers.index(host) if host in self.servers else len(HOST_COLORS)
        return HOST_COLORS[i] if i < len(HOST_COLORS) else ("text", None)

    def tint(self, host):
        """A server's identity colour at 20% alpha, for the role band."""
        hexa = self.color(host)[1]
        return hexa + "33" if hexa else GREY + "33"

    def hold(self, name, extra=0):
        """How long after its condition a rule fires, as text, from monitoring/rules/holds.json, plus
        extra seconds of the rule's own window."""
        if name not in self.holds:
            raise SystemExit("%s has no entry %r, which the dashboard prints" % (HOLDS_FILE, name))
        return minutes_text(seconds(self.holds[name]["for"]) + extra)

    def ms(self):
        """The signer's median limit in milliseconds, as text."""
        return "%g" % (self.S * 1000)


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


def count_24h(expr, agg, job="celestia-app"):
    """How much a counter (expr) of job's targets grew over the last 24 hours, or since monitoring
    started when that is less, as a whole number. Each target is read once a minute, combined per host
    with agg (sum or max); a target that is up but has not counted yet reads 0, so a counter that
    appears with its first increment counts from 0, and a restart counts as a reset, never as a drop.
    A plain "now minus 24 hours ago" would count from the process start when Prometheus holds less
    than a day, and lose the count before a restart."""
    return ("round(%s(increase((%s by (host) (%s) or on (host) (max by (host) (up{job=%s} == 1) * 0))"
            "[24h:1m]))) or on () vector(0)" % (agg, agg, expr, q(job)))


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


def newer(s):
    """A node that began signing in the last 2 minutes (a tmkms repoint)."""
    return "(%s unless on (host) %s)" % (signing_nodes(s), signing_nodes(s, "[2m] offset 2m"))


def two_signers(s):
    """True while two nodes have signed for 3 minutes running: longer than a repoint takes."""
    return "(min_over_time(((count(%s) or vector(0)) > bool 1)[3m:30s]) == 1)" % signing_nodes(s)


def fibre_active(s):
    """One series per host whose Fibre unit systemd reports active, labelled host only."""
    return '(max by (host) (node_systemd_unit_state{name=%s,state="active"} == 1))' % q(s.units["fibre"])


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
    registration rule, one term per Fibre host with an endpoint."""
    return "(%s)" % " or ".join(
        "(max by (endpoint) (celestia_mon_fibre_registered{endpoint!=%s} == 1) and on () "
        '(max(node_systemd_unit_state{host=%s,name=%s,state="active"}) == 1))' % (q(e), q(h), q(s.units["fibre"]))
        for h, e in s.fibre_endpoints)


def last(s, metric):
    """The last value of a celestia-mon-signal metric in the past 7 days, as the upgrade rules read it."""
    return "max(last_over_time(%s{job=\"signal\",host=%s}[7d]))" % (metric, q(s.monitor))


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


def active(sel):
    """An alert matching sel was pending or firing at some moment of the bar's time block."""
    return "(max(%s))" % in_block("max_over_time", "ALERTS{%s}" % sel)


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
    short, also a short label: the Status cell's words, else SHORT_FALLBACK."""
    expr = 'label_join(%s, "key", ":", "alertname", "severity", "unit", "reason", "check", "host")' % x
    expr = 'label_replace(%s, "title", "$1", "alertname", "(.+)")' % expr
    if short:
        expr = label(expr, "short", SHORT_FALLBACK)
    for key, sentence, words, need in TITLES:
        if s.has(need):
            expr = "label_replace(%s, %s, %s, %s, %s)" % (expr, q("title"), q(sentence), q("key"), q(key))
            if short:
                expr = "label_replace(%s, %s, %s, %s, %s)" % (expr, q("short"), q(words), q("key"), q(key))
    return expr


def check_titles(s):
    """Every alert of the rules template has a sentence and a short name."""
    named = {key.split(":")[0] for key, sentence, words, _ in TITLES if sentence and words}
    missing = [a for a in s.alert_names if a not in named and a not in UNTITLED]
    if missing:
        raise SystemExit("gen_dashboard.py has no sentence and short name in TITLES for the alert(s) %s of %s; add "
                         "them, so the Status cell and the alert list name it in plain words"
                         % (", ".join(missing), RULES_TEMPLATE))


def message(sel):
    """Message alerts matching sel: 2.2 firing, 2 pending (both amber)."""
    return "(%s * 0 + 2.2 or %s * 0 + 2)" % (by_alert('ALERTS{alertstate="firing",%s}' % sel),
                                             by_alert('ALERTS{alertstate="pending",%s}' % sel))


def alert_items(s):
    """What is wrong now, one series per sentence (labels title and short), valued by level: 3 a page
    fires (red), 2.5 a page is pending, 2 to 2.95 a message or monitoring trouble (amber); plus a
    priority between alerts of one level. Alertmanager's inhibitions are mirrored, and follow-on alerts
    are grouped into their cause. A scheduled upgrade is no item: the Network upgrade tile shows it."""
    pages = ("(%s * 0 + 3 or %s * 0 + 2.5) unless on (alertname) (%s and on () %s)"
             % (by_alert('ALERTS{alertstate="firing",severity="page"}'),
                by_alert('ALERTS{alertstate="pending",severity="page"}'),
                label("vector(1)", "alertname", "ValidatorNotSigning"), NNA))
    drop = " or ".join("(%s and on () %s)" % (label("vector(1)", "alertname", a), cond) for a, cond in [
        ("SignaturesMissed", 'max(ALERTS{alertname=~"NodeNotAdvancing|ValidatorNotSigning"})'),
        ("RemoteSignerProblem", NNA),
        ("NodeBehind", NNA),
        ("FibreNotServing", 'max(ALERTS{alertname=~"NodeNotAdvancing|FibreDown"})')])
    messages = ("%s unless on (alertname) (%s) unless on (alertname, host) %s"
                % (message('severity="message",alertname!~"UpgradeScheduled|ServiceDown|DiskLow"'), drop,
                   label('max by (host) (ALERTS{alertname="ServiceDown",unit=~"fibre|otelcol"})',
                         "alertname", "FibreNotServing")))
    # A host whose node_exporter does not answer shows once; a stopped celestia-appd not also as lost metrics.
    reporting = message('alertname="ServiceDown",unit="node_exporter"')
    service = ("%s or ((%s unless on (host) %s) unless on (host, unit) %s)"
               % (reporting, message('alertname="ServiceDown",unit!="node_exporter"'), reporting,
                  'label_replace(%s, "unit", "celestia_app", "", "")' % message('alertname="ServiceDown",unit="appd"')))
    disk = ('%s unless on (host) ALERTS{alertname="DiskLow",severity="page"}'
            % message('alertname="DiskLow",severity="message"'))
    items = [pages, messages, service, disk]
    # Every alert of the rules has a priority (0 when none is set), so group_left keeps every item.
    # "group_left ()" names no labels to copy; a bare "group_left (" would read the operand as that list.
    prio = "(%s)" % " or ".join(label("vector(%s)" % num(PRIORITY.get(a, 0)), "alertname", a) for a in s.alert_names)
    alerts = "(%s) + on (alertname) group_left () %s" % (" or ".join("(%s)" % i for i in items), prio)
    return "(max by (title, short) (%s) or %s)" % (titled(s, alerts, short=True), synth(s))


def synth_items(s):
    """(level, sentence, short, condition) of the Status cell's items no alert rule carries."""
    evaluated = 'prometheus_rule_group_last_evaluation_timestamp_seconds{job="prometheus"}'
    items = [
        (2.95, "Alert rules are not running", "Rules stopped", "(time() - max(%s) > 120)" % evaluated),
        (2.95, "Alert rules are not running", "Rules stopped", "max(absent(%s))" % evaluated),
        (2.9, "Alertmanager down, alerts not sent", "Alerting down", '(min(up{job="alertmanager"}) == 0)'),
        # Not while Alertmanager is down: "Alerting down" already says so, as one item.
        (2.85, "Alerts not reaching Alertmanager", "Alerts unsent",
         '(%s unless on () (min(up{job="alertmanager"}) == 0))' % unsent("3m")),
        # A failed notification stops the Watchdog for an hour, also after NotificationsFailing cleared.
        (2.8, "A notification failed in the last hour", "No heartbeat",
         '(((((count(ALERTS{alertname="Watchdog",alertstate="firing"}) or vector(0)) == 0) * 0 + 1) '
         "and on () (time() - max(%s) <= 120)) unless on () max(ALERTS{alertname=\"NotificationsFailing\"}))"
         % evaluated),
    ]
    if s.remote and not s.multi:
        items.append((2.45, "Validator signs through two nodes", "Two signers", two_signers(s)))
    if s.announcements:
        # A Celestia announcement goes to Alertmanager directly, not through the rules.
        items.append((2.3, "New Celestia announcement, see your alerts", "Announcement",
                      "(sum(increase(celestia_mon_announcements_forwarded_total[30m])) > 0)"))
    return items


def synth(s):
    """The Status cell's items no alert rule carries: the monitoring itself, two signers, an announcement."""
    return "(%s)" % " or ".join(label(label("%s * 0 + %s" % (cond, num(level)), "title", text), "short", words)
                                for level, text, words, cond in synth_items(s))


def status_words(s):
    """(short name, sentence) of every item the Status cell can show in this install, each once, in
    TITLES order and then the items no rule carries. A scheduled upgrade is no item (alert_items)."""
    pairs = [(words, sentence) for key, sentence, words, need in TITLES
             if s.has(need) and not key.startswith("UpgradeScheduled:")]
    pairs += [(words, sentence) for _, sentence, words, _ in synth_items(s)]
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
    """The Status cell: one series with label status ("Status", or "Status · N alerts" with more than
    one item) whose value is a code for the worst item's short name and colour (status_shorts); with
    no item, "All online" (code 1). The worst item has the highest level, then the highest PRIORITY."""
    t = alert_items(s)
    lookup = "(%s)" % " or ".join(label("vector(%d)" % (10 * i), "short", w) for i, w in enumerate(status_shorts(s), 1))
    # 1 amber, 2 red, plus ten times the short name's place in the lookup.
    code = "(((topk(1, %s) >= bool 3) + 1) + on (short) %s)" % (t, lookup)
    # count_values turns the number of items into a label, which names the cell.
    counted = '(%s * on () group_left (n) count_values("n", count(%s)))' % (code, t)
    named = ('label_replace(label_replace(%s, "status", "Status", "", ""), "status", %s, "n", "([2-9]|[1-9][0-9]+)")'
             % (counted, q("Status · $1 alerts")))
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


def fit_strip(items, height, what, samples=None):
    """Pins one value size on a strip of row facts, (panel, width) items height grid rows tall: FACT_PX
    when every value fits its panel, else FACT_SMALL_PX; stops when a value does not fit even then,
    when a label (FACT_LABEL_PX) does not fit, or when label over value is taller than the panel.
    samples: {panel id: [texts]} a panel shows that its mappings do not name, such as "a few
    seconds ago" of a time-ago value."""
    samples = samples or {}
    for p, w in items:
        check_fit(what, [p["fieldConfig"]["defaults"]["displayName"]], FACT_LABEL_PX, w)
    texts = [(t, w) for p, w in items for t in panel_texts(p) + samples.get(p["id"], [])]
    size = FACT_PX
    if any(text_px(t, size) > inner_px(w) for t, w in texts):
        size = FACT_SMALL_PX
        for t, w in texts:
            check_fit(what, [t], size, w)
    content = grid_px(height) - CHROME_UNTITLED
    if stack_px(FACT_LABEL_PX, size, True) > content:
        raise SystemExit("%s: a label over a value needs %d px; an untitled h%d panel has %d"
                         % (what, stack_px(FACT_LABEL_PX, size, True), height, content))
    for p, _ in items:
        p["options"]["text"]["valueSize"] = size
    return items


# The first screen.

def status_cell(s):
    """(id, label, description, query, options) of the Status cell: "All online" in green, or the worst
    item's short name in amber, red once a page fires; the label counts the items when there are
    several. The full sentences are in the description and in the All firing alerts row."""
    shorts = status_shorts(s)
    sentences = {}
    for words, sentence in status_words(s):
        text = sentence.replace("$1", "a server")
        sentences.setdefault(words, []).append(text[0].upper() + text[1:])
    desc = ("What is wrong right now, or about to be: the short name of the worst item (urgent before warning, then "
            "by how much the alert matters). Red: an urgent (page) alert fires. Amber: a warning (message) alert "
            "fires, or an alert's problem is true and it will be sent if it lasts. With several items the label counts "
            "them ('Status · 3 alerts'). "
            "Follow-on alerts are grouped into their cause, as Alertmanager's inhibitions do (no 'Missed blocks' while "
            "'Not signing' shows); your notifications may still list some of them separately. Silenced alerts still "
            "show: a silence only stops notifications. 'All online' (green): nothing is wrong, the alert rules ran in "
            "the last 2 minutes and Alertmanager is up and reached. Every alert in full, with its server and how long it has "
            "been active: open the collapsed row 'All firing alerts' below.")
    if s.announcements:
        desc += " A new Celestia announcement shows here for 30 minutes; its text is in your alert channel."
    if s.signal:
        desc += " A scheduled network upgrade is no fault and does not show here: the Network upgrade tile shows it."
    desc += " The short names: %s." % "; ".join("%s: %s" % (w, ", or ".join(sentences[w])) for w in shorts
                                               if w in sentences)
    desc += more(pages=[("Alert reference", DOCS)])
    words = [(1, "All online", "semi-dark-green")]
    for i, w in enumerate(shorts, 1):
        words += [(10 * i + 1, w, "orange"), (10 * i + 2, w, "red")]
    return (10, "{{status}}", desc, status_expr(s),
            dict(color_mode="value", mappings=mapping(*words), no_value="No data"))


def intro(s):
    """The intro strip, one band at the top: the validator's name under its network, four neutral
    facts and the Status, in six framed, untitled cells of one height and width. Each cell is a label
    over a value, set from the left, in one size pair, so the labels share one line and the values
    another. The generator stops when a text does not fit its cell."""
    name = s.name or DEFAULT_NAME
    if len(name) > NAME_CHARS:
        raise SystemExit("celestia_mon_validator_name %r has %d characters; at most %d fit the dashboard's top strip"
                         % (name, len(name), NAME_CHARS))
    # The value size: the largest at which the name fits its cell. Longer names make the whole strip
    # smaller, so the values stay one size.
    fits = [v for v in INTRO_VALUE_SIZES if text_px(name, v) <= inner_px(INTRO_WIDTHS[0])]
    if not fits:
        raise SystemExit("celestia_mon_validator_name %r needs %d px even at %d px; the top strip's identity cell has "
                         "%d. Use a shorter name." % (name, text_px(name, INTRO_VALUE_SIZES[-1]),
                                                      INTRO_VALUE_SIZES[-1], inner_px(INTRO_WIDTHS[0])))
    size = fits[0]
    # The network over the name: Mocha with its chain id while that fits the cell, else without.
    mocha = "Mocha testnet (mocha-5)"
    with_id = text_px(mocha, INTRO_LABEL_PX) <= inner_px(INTRO_WIDTHS[0])
    networks = [mocha if with_id else "Mocha testnet", "Celestia mainnet"]
    mocha_label = "Mocha testnet ($1)" if with_id else "Mocha testnet"
    net = ('max by (net) (label_replace(label_replace(label_replace(max by (chain_id) (%s), '
           '"net", "$1", "chain_id", "(.*)"), "net", "Celestia mainnet", "chain_id", "celestia"), '
           '"net", %s, "chain_id", "(mocha-.*)")) * 0 + 1' % (H, q(mocha_label)))
    signed = "Signed, %d d" % s.days
    cells = [
        (1, "{{net}}",
         "Celestia validator%s monitoring: which validator this is and which network its nodes are on, read from the "
         "nodes' own metrics (chain_id: celestia on mainnet, mocha-5 on the Mocha testnet), so a Mocha and a mainnet "
         "dashboard cannot be confused. The name is celestia_mon_validator_name in the inventory. No node data: no "
         "node reports its chain." % (" and Fibre" if s.fibre else ""),
         net, dict(mappings=mapping((1, name, "text")), no_value="No node data")),
        (2, "Latest block",
         "The newest block committed by any node of this setup. It grows by about 20 a minute, so it changes on every "
         "refresh. Compare it with an explorer or [status.celestia.org](https://status.celestia.org/) to see that the "
         "nodes are at the network's head.",
         "max(%s) - 1" % H, dict(unit="locale", no_value="No data")),
        (3, "Block time",
         "Average time between blocks over the last 5 minutes, from the blocks' own timestamps. Celestia is designed "
         "for about 2.6 s; about 2.9 s is normal on mainnet and Mocha today. Slower blocks affect every validator, "
         "not only this one.",
         'max(rate(cometbft_consensus_block_interval_seconds_sum{job="celestia-app"}[5m]) / '
         'rate(cometbft_consensus_block_interval_seconds_count{job="celestia-app"}[5m]))',
         dict(unit="s", decimals=2, color_mode="value", no_value="No data",
              mappings=special_map("nan", "Stalled", "orange"))),
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
        status_cell(s),
    ]
    # The labels each cell can show, where they are not its query's legend, and the values its words
    # do not name: the widest number of each fact.
    labels = {1: networks, 10: ["Status", "Status · 99 alerts"]}
    samples = {2: [INTRO_NUMBER], 3: ["9.99 s"], 4: ["99.99%"], 5: ["99.99%"]}
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
        # Every word but the name fits at the largest size, so a long name alone sets a smaller one.
        check_fit(what, [t for t in panel_texts(p) if t != name] + samples.get(pid, []), INTRO_VALUE_SIZES[0], w)
        panels.append((p, w))
    return panels


def validator_tile(s):
    """(id, title, description, query, words) of the Validator tile."""
    n = num(s.N)
    expr = chain(
        "(max(absent(%s)) * 0 + 7) + on () %s" % (H, NNA10),
        "(%s * 0 + 5) + on () %s" % (STALL, NNA10),
        '((((%s > %s) * 0 + 6 and on () (min(up{job="celestia-app"}) == 0)) or ((max(%s) * 0 + 4) unless on () %s) '
        "or (%s > %s) * 0 + 3) + on () %s)" % (BLOCKS_SINCE, n, POWER, IN_SET_OR_NEW, BLOCKS_SINCE, n, VNS10),
        "(max(max by (host) (%s) > on (host) %s * max by (host) (delta(%s[1h])))) * 0 + 2"
        % (missed_in("1h"), num(s.R), H),
        "max(%s) * 0 + 1" % H)
    words = [(1, "Signing", "text"), (2, "Missed blocks", "orange"),
             (3, "Not signing", "orange"), (13, "Not signing", "red"),
             (4, "Out of set", "orange"), (14, "Out of set", "red"),
             (5, "Nodes stalled", "orange"), (15, "Nodes stalled", "red"),
             (6, "Not seen", "orange"), (16, "Not seen", "red"),
             (7, "Nodes offline", "orange"), (17, "Nodes offline", "red")]
    desc = ("Is the validator signing, and in the active set? Signing: its signature is on one of the last %g blocks. "
            "Missed blocks: more than %g%% of the last hour's blocks were committed without its signature on some node "
            "(SignaturesMissed sends a message after %s). Not signing: more than %g blocks since its last signature; "
            "red once ValidatorNotSigning pages (%s later). Out of set: the chain moves but no node records blocks for "
            "its key: jailed, unbonded or outside the active set. Not seen: as Not signing, but a node Prometheus cannot "
            "read may still be signing unseen; check an explorer before you move the key anywhere, because two signers "
            "of one key double-sign. Nodes stalled: no node of this setup committed a block for 2 minutes "
            "(NodeNotAdvancing pages %s later): the nodes are stuck, for example at an upgrade height, or the whole "
            "chain halted; compare Latest block with status.celestia.org. Nodes offline: Prometheus reads no node at "
            "all." % (s.N, s.R * 100, s.hold("SignaturesMissed"), s.N, s.hold("ValidatorNotSigning"),
                      s.hold("NodeNotAdvancing")))
    return 11, "Validator", desc + more(["ValidatorNotSigning", "SignaturesMissed", "NodeNotAdvancing"]), expr, words


def signing_tile(s):
    """(id, title, description, query, words) of the Signing through tile."""
    sig = signing_nodes(s)
    expr = chain(
        "%s * 0 + 31" % STALL,
        "%s * 0 + 35" % two_signers(s) if s.remote and not s.multi else None,
        "(count(%s) > 1) * 0 + 30" % sig if s.remote else None,
        "max(%s and on (host) %s)" % (lookup(s.nodes), sig),
        '((min(up{job="celestia-app"}) == 0) * 0 + 32) + on () %s' % VNS10,
        "((max(%s) * 0 + 34) unless on () %s)" % (POWER, IN_SET_OR_NEW),
        "(max(%s) * 0 + 33) + on () %s" % (H, VNS10))
    words = [(i + 1, h, "text") for i, h in enumerate(s.nodes)]
    if s.remote:
        words.append((30, "Two nodes" if s.multi else "Switching", "text"))
    if s.remote and not s.multi:
        words.append((35, "Two signers", "orange"))
    words += [(31, "Nodes stalled", "orange"), (32, "Not visible", "orange"), (42, "Not visible", "red"),
              (33, "No node signing", "orange"), (43, "No node signing", "red"), (34, "Out of set", GREY)]
    if s.remote:
        desc = ("The server whose node the remote signer signs through now: the node whose privval client signed a vote "
                "or proposal in the last 2 minutes. It follows a tmkms repoint. A node that tmkms left keeps reading the "
                "validator's signatures from the chain, so only signatures made through the node count. ")
        if s.multi:
            desc += ("Two nodes: tmkms signs through both nodes, as it is set up to "
                     "(celestia_mon_signer_multi_node). ")
        else:
            desc += ("Switching: for up to 2 minutes after tmkms is pointed at another node, both nodes have recent "
                     "signatures; this is normal. Two signers: two nodes have signed for 3 minutes running; one key "
                     "signing through two nodes can double-sign, so stop one at once (unless tmkms is set up to sign "
                     "through both: set celestia_mon_signer_multi_node). ")
        desc += ("No node signing: the validator is in the active set but no node signed for 2 minutes; check tmkms on "
                 "the KMS host and the link to it (red once ValidatorNotSigning pages). ")
    else:
        desc = ("The server whose node the validator signs through now: the node whose last signed height advanced in "
                "the last 2 minutes. No node signing: the validator is in the active set but no node signed for 2 "
                "minutes (red once ValidatorNotSigning pages). ")
    desc += ("Not visible: no node is seen signing and a node is not reporting; that node may still be signing. Out of "
             "set: nothing is asked to sign. Nodes stalled: no node committed a block for 2 minutes.")
    return 12, "Signing through", desc + more(["ValidatorNotSigning"], [MAINNET_LAYOUT]), expr, words


def backup_tile(s):
    """(id, title, description, query, words) of the Backup node tile (two nodes or more)."""
    recent = signing_nodes(s, "[3m]")
    cand = '(max by (host) (up{job="celestia-app"}) unless on (host) %s)' % recent
    behind = ("(((scalar(max(%s)) - max by (host) (%s)) > %s) or (max by (host) (changes(%s[2m])) == 0 "
              "and on () (changes(max(%s)[2m:]) > 0)))" % (H, H, num(s.NB), H, H))
    ready = "((%s == 1) unless on (host) %s)" % (cand, behind)
    expr = chain(
        "(count(%s) > 1) * 0 + 5" % recent,
        "(count(%s) > 0) * 0 + 1" % ready,
        "(count(%s == 1) > 0) * 0 + 2" % cand,
        "(count(%s) > 0) * 0 + 3" % cand,
        'max(up{job="prometheus"}) * 0 + 4')
    words = [(1, "Ready", "text"), (2, "Not ready", "orange"), (3, "Down", "orange"), (4, "None", GREY),
             (5, "Switching", "text")]
    desc = ("A node other than the one signing now that could take over: Prometheus reads it, it is at most %g blocks "
            "behind the highest node and it advances (NodeBehind's own levels). Not ready: such a node lags or does not "
            "advance (NodeBehind sends a message after %s). Down: Prometheus cannot read any other node (ServiceDown "
            "after %s). Switching: two nodes signed in the last 3 minutes (a repoint in progress). During a signer "
            "outage the node that last signed counts again after 3 minutes, since pointing tmkms back at it is a valid "
            "way out. Taking over means pointing tmkms (and Fibre's signer address) at that node, as the mainnet "
            "layout describes." % (s.NB, s.hold("NodeBehind"), s.hold("ServiceDown unreachable")))
    return 13, "Backup node", desc + more(["NodeBehind", "ServiceDown"], [MAINNET_LAYOUT]), expr, words


def fibre_tile(s):
    """(id, title, description, query, words) of the Fibre tile."""
    seen_f = fibre_seen(s)
    lost = fibre_lost(s)
    ok = 'fibre_server_upload_shard_duration_seconds_count{success="true"}'
    failing = ('((sum(increase(fibre_server_upload_shard_duration_seconds_count{success="false"}[5m])) > 0) '
               "unless on () (sum(increase(%s[5m])) > 0))" % ok)
    expr = chain(
        '(max(ALERTS{alertname="FibreDown",check="",alertstate="firing"}) * 0 + 15) + on () '
        "((count(%s) > 0) * 0 + 4 or vector(0))" % lost,
        '((count(%s) > 0) and on () max(ALERTS{alertname="FibreDown",check=""})) * 0 + 9' % lost,
        '(((count(%s) or vector(0)) == 0) * 0 + 5 and on () (count(up{job="node"} == 1) > 0))' % seen_f,
        "(count(%s) > 1) * 0 + 3" % seen_f,
        'max(ALERTS{alertname="FibreNotServing",alertstate="firing"}) * 0 + 4',
        "((count(%s) > 0) unless on () count(fibre_server_upload_shard_budget_bytes)) * 0 + 7" % seen_f,
        "(%s and on () (sum(increase(fibre_server_upload_shard_rejected_total[5m])) > 0)) * 0 + 8" % failing,
        "%s * 0 + 2" % failing,
        "((sum(increase(%s[1h])) > 0) unless on () "
        'max(ALERTS{alertname="FibreDown",check=~"reachability|registration"})) * 0 + 1' % ok,
        "count(%s) * 0 + 6" % seen_f)
    words = [(1, "Serving", "text"), (6, "Running", "text"), (2, "Uploads failing", "orange"),
             (8, "Storage full", "orange"), (7, "No metrics", "orange"), (4, "Not serving", "orange"),
             (3, "On 2 servers", "orange"), (5, "Not running", "orange"), (15, "Not running", "red"),
             (9, "Not seen", "orange"), (19, "Not seen", "red")]
    if s.tensile:
        outside = " and is reachable from outside"
        running = ("no upload in the last hour (normal on a quiet network), or the outside check reports a problem "
                   "(see Fibre from outside)")
    else:
        outside = " (the outside check is not set up, celestia_mon_tensile, so it is not part of Serving)"
        running = "no upload in the last hour (normal on a quiet network)"
    desc = ("Is Fibre doing its job? Serving: one Fibre server runs, sends its metrics and accepted an upload in the last "
            "hour%s. Running: it runs, but %s. Uploads failing: in the last 5 minutes uploads failed and none was "
            "accepted, most often because Fibre still signs through a node that tmkms left; point Fibre's signer "
            "address at the signing node (FibreNotServing sends a message after %s). Storage full: the same, but "
            "because the storage budget is full and uploads are refused. No metrics: the Fibre unit runs but Fibre's "
            "metrics stopped arriving (FibreNotServing after %s, unless otelcol is down). Not serving: FibreNotServing "
            "fires. On 2 servers: Fibre runs on more than one host; stop the standby (FibreBothActive after %s). Not "
            "running: no Fibre unit is active anywhere; amber during a failover, red once FibreDown pages after %s. Not "
            "seen: no Fibre unit is reported active and a Fibre host's node_exporter does not answer, so its unit state "
            "is unknown; if the Fibre facts below still update, Fibre runs and only that host's metrics are missing "
            "(FibreDown still pages after %s). Which server runs Fibre: the role band below and the table's Role "
            "column." % (outside, running, s.hold("FibreNotServing uploads_failing", UPLOAD_WINDOW),
                         s.hold("FibreNotServing no_telemetry"), s.hold("FibreBothActive"), s.hold("FibreDown"),
                         s.hold("FibreDown")))
    return 14, "Fibre", desc + more(["FibreDown", "FibreNotServing", "FibreBothActive"], [FIBRE_DASHBOARD]), expr, words


def outside_tile(s):
    """(id, title, description, query, words) of the Fibre from outside tile (Tensile on)."""
    registration = 'max(ALERTS{alertname="FibreDown",check="registration",alertstate="firing"})'
    reach = "max(celestia_mon_fibre_reachable and on (endpoint) celestia_mon_fibre_registered == 1)"
    expr = chain(
        "((%s * 0 + 14) and on () (max(celestia_mon_fibre_registered) == 0))" % registration,
        "%s * 0 + 13" % registration,
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
    words = [(1, "Reachable", "text"), (2, "Unreachable", "orange"), (12, "Unreachable", "red"),
             (3, "Wrong host", "orange"), (13, "Wrong host", "red"), (4, "Not registered", "orange"),
             (14, "Not registered", "red"), (10, "Check stopped", "orange"), (0, "Not checked", GREY),
             (9, "Check pending", GREY)]
    desc = ("From [Tensile](https://tensile.huginn.tech), which dials every validator's registered Fibre endpoint from outside; "
            "celestia-mon-tensile on the monitor reads its verdict every minute. Reachable: Tensile's last check, under "
            "15 minutes old, completed TCP, TLS and the validator's identity. Unreachable: it did not (FibreDown pages "
            "after %s). Wrong host: the chain registers an endpoint that is not the active Fibre host's "
            "(celestia_mon_fibre_public_endpoint); after a failover, send set-host. Not registered: no Fibre endpoint "
            "for the validator in x/valaddr. Not checked: no Tensile check in 15 minutes, or celestia-mon-tensile is "
            "down; an outage seen from outside cannot page meanwhile. Check stopped: TensileUnreadable fires (after %s "
            "without a check, or %s with the service down). Check pending: Tensile has not checked a new "
            "registration." % (s.hold("FibreDown"), s.hold("TensileUnreadable", TENSILE_STALE),
                               s.hold("TensileUnreadable")))
    return 15, "Fibre from outside", desc + more(["FibreDown", "TensileUnreadable"]), expr, words


def upgrade_tile(s):
    """(id, title, description, query, words) of the Network upgrade tile (signal collector on)."""
    expr = chain(
        'max(ALERTS{alertname="UpgradeSignalMissing",alertstate="firing"}) * 0 + 13',
        'max(ALERTS{alertname="UpgradeSoon",alertstate="firing"}) * 0 + 5',
        'max(ALERTS{alertname="SignalUnreadable",alertstate="firing"}) * 0 + 10',
        'max(absent(up{job="signal"})) * 0',
        '(max(up{job="signal"}) == 0) * 0',
        "(time() - max(celestia_mon_signal_last_success_timestamp_seconds) > 900) * 0",
        "((max(celestia_mon_upgrade_pending) == 1) * 0 + 5 and on () (max(celestia_mon_upgrade_eta_seconds) < 86400))",
        "(max(celestia_mon_upgrade_pending) == 1) * 0 + 4",
        "((max(celestia_mon_signal_tally_power) > 0) * 0 + 3 unless on () "
        "(max(celestia_mon_signal_validator_version) == on () max(celestia_mon_signal_next_version)))",
        "(max(celestia_mon_signal_tally_power) > 0) * 0 + 2",
        "max(celestia_mon_upgrade_pending) * 0 + 1")
    words = [(1, "None pending", "text"), (2, "Signal sent", "text"), (3, "Signal needed", "orange"),
             (13, "Signal needed", "red"), (4, "Scheduled", "text"), (5, "Within 24 h", "orange"),
             (10, "Check stopped", "orange"), (0, "Not checked", GREY)]
    desc = ("Celestia switches app versions through signalling (x/signal): validators signal the next version, and once "
            "5/6 of the voting power has signalled, an upgrade is scheduled at a fixed height. None pending: no "
            "validator has signalled a next version. Signal sent: a round is open and this validator has signalled the "
            "next version. Signal needed: a round is open and this validator has not (yet) signalled it; red once "
            "UpgradeSignalMissing pages (after %s). Signal only after every node runs a release that supports the new "
            "version. A round opened early by one validator, before any release exists, still shows red while you "
            "silence the page (see alerts.md): the dashboard cannot see silences. Scheduled: the upgrade height is set; "
            "every node must run a supporting release by then. Within 24 h: the upgrade is less than 24 hours away "
            "(UpgradeSoon, which stays once sent): confirm every node runs a release that supports the target version, "
            "or it stops at that height. Not checked: celestia-mon-signal has not read the chain for 15 minutes or is "
            "down. Check stopped: SignalUnreadable fires (after %s without a read, or %s with the service down). "
            "Version, support so far, height and time to go: the Validator row and the collapsed Network upgrade row."
            % (s.hold("UpgradeSignalMissing"), s.hold("SignalUnreadable", SIGNAL_STALE), s.hold("SignalUnreadable")))
    return 16, "Network upgrade", desc + more(["UpgradeSignalMissing", "UpgradeScheduled", "UpgradeSoon",
                                               "SignalUnreadable"]), expr, words


def servers_tile(s):
    """(id, title, description, query, words) of the Servers tile: the server lines of the status
    board now, the worst first, in their words and colours. It only turns five answers into six, so
    the tiles line up with the six cells of the top strip."""
    sel = "host=~%s" % q("|".join(re2(h) for h in s.servers))

    def alert(match):
        """An alert matching match fires now for one of the servers."""
        return 'max(ALERTS{alertstate="firing",%s,%s})' % (match, sel)

    # As a line's green: the alert rules ran in the last 2 minutes and none failed.
    rules = ('((time() - max(prometheus_rule_group_last_evaluation_timestamp_seconds{job="prometheus"}) <= 120) '
             'unless on () max(ALERTS{alertname="MonitoringBroken",alertstate="firing"}))')
    expr = chain(
        "%s * 0 + 13" % alert('severity="page"'),
        "%s * 0 + 7" % alert('alertname="ServiceDown",unit=~"celestia_app|node_exporter|otelcol"'),
        "%s * 0 + 6" % alert('alertname="ServiceDown"'),
        "%s * 0 + 5" % alert('alertname="DiskLow"'),
        "%s * 0 + 4" % alert('alertname="NodeBehind"'),
        "%s * 0 + 3" % alert('severity="message",alertname!~%s' % q(SERVER_OTHERS)),
        '((count(up{job="node",%s} == 1) == %d) and on () %s) * 0 + 1' % (sel, len(s.servers), rules),
        'max(up{job="prometheus"}) * 0')
    words = [(1, "All up", "text"), (3, "Warning", "orange"), (4, "Node behind", "orange"),
             (5, "Disk low", "orange"), (6, "Service down", "orange"), (7, "Not reporting", "orange"),
             (13, "Disk critical", "red"), (0, "No data", GREY)]
    desc = ("Every server's metrics and the disks DiskLow watches, now: the worst of the server lines on the status "
            "board, in their colours. All up: every server's node_exporter answers, no server alert fires and the "
            "alert rules ran. Disk critical: a disk is full or read-only and DiskLow pages. Not reporting: a metrics "
            "endpoint of a server (node_exporter, celestia-appd's, the OpenTelemetry collector's) does not answer "
            "(ServiceDown after %s). Service down: celestia-appd, Fibre or tmkms stopped, keeps restarting or is not "
            "installed. Disk low: a watched disk is low on space or read-only (DiskLow sends a message). Node behind: "
            "a node lags the highest node (NodeBehind). Warning: another message alert names a server. No data: a "
            "server's node_exporter is not read yet, or the alert rules did not run. Which server: its line on the "
            "status board and the Servers now table." % s.hold("ServiceDown unreachable"))
    return 17, "Servers", desc + more(["ServiceDown", "DiskLow", "NodeBehind"]), expr, words


def answer_tiles(s):
    """(panel, width) of the one-word answers, in one pinned text size and equal widths, so their
    edges line up with the cells of the top strip."""
    tiles = [validator_tile(s), signing_tile(s)]
    if len(s.nodes) > 1:
        tiles.append(backup_tile(s))
    if s.fibre:
        tiles.append(fibre_tile(s))
    if s.tensile:
        tiles.append(outside_tile(s))
    if s.signal:
        tiles.append(upgrade_tile(s))
    # Five equal tiles would not line up with the strip's six cells: Servers makes them six, in the
    # place a backup node's tile has.
    if len(tiles) == 5:
        tiles.insert(2, servers_tile(s))
    if 24 % len(tiles):
        raise SystemExit("%d answer tiles cannot share the 24 columns equally" % len(tiles))
    widths = [24 // len(tiles)] * len(tiles)
    items = [(stat(pid, title, desc, [target("A", expr, "", instant=True)], mappings=mapping(*words),
                   no_value="No data"), w)
             for (pid, title, desc, expr, words), w in zip(tiles, widths)]
    # One size for the strip: the largest at which every word fits its tile.
    texts = [(t, w) for p, w in items for t in panel_texts(p)]
    for size in TILE_SIZES:
        if all(text_px(t, size) <= inner_px(w) for t, w in texts):
            for p, _ in items:
                p["options"]["text"]["valueSize"] = size
            return items
    bad = [t for t, w in texts if text_px(t, TILE_SIZES[-1]) > inner_px(w)]
    raise SystemExit("The answer tile word %r does not fit its tile even at %d px; shorten the host name in the "
                     "inventory" % (bad[0], TILE_SIZES[-1]))


CHECK_DESC = ("One line per check, one bar per time block: 10 minutes on a 24-hour range, 1 hour on 7 days, 6 hours on "
              "30 days. The newest bar on the right is the current block and fills in as its minutes pass; in its first "
              "30 seconds it shows the 2 minutes before it. A bar shows "
              "the worst moment of its block, read from the alert states Prometheus evaluated at the time, so the bars "
              "always agree with the alerts you received. Green: fine, and the alert rules ran. Amber: a warning alert "
              "fired, or an urgent alert's problem was true for a moment and cleared before it paged. Red: an urgent "
              "alert fired. Grey: no data (before the install, monitoring down, the alert rules not running, or nothing "
              "to check). Each alert colours one line; NodeBehind colours Node in sync and the line of the server that "
              "lags. Network upgrades have no line: the Network upgrade tile shows them.")


def check_lanes(s):
    """The status board's check lines, in board order: (refId, label, bars, uptime, words). bars: the
    worst state of each time block as a code; uptime: the share of the selected time the line was
    fine (instant); words: (code, text, fill) per code. Every green branch needs the alert rules to
    have run in the block, so a bar is never green when no rule checked it."""
    gate = rules_ran()
    rsp = 'max(ALERTS{alertname="RemoteSignerProblem",alertstate="firing"})'
    behind = 'max(ALERTS{alertname="NodeBehind",alertstate="firing"})'
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
         [(1, "In sync", OK), (2, "Stalled briefly", WARN), (3, "Stalled", ALERT), (4, "Node behind", WARN),
          (0, "No data", NODATA)]),
        # While the nodes are stalled, ValidatorNotSigning is inhibited: grey, not a second red line for one cause.
        ("B", "Validator signing",
         chain("(%s unless on () %s) * 0 + 3" % (firing('alertname="ValidatorNotSigning"'),
                                                firing('alertname="NodeNotAdvancing"')),
               "(%s unless on () %s) * 0 + 2" % (active('alertname="ValidatorNotSigning"'),
                                                active('alertname="NodeNotAdvancing"')),
               "%s * 0 + 5" % firing('alertname="NodeNotAdvancing"'),
               "%s * 0 + 4" % firing('alertname="SignaturesMissed"'),
               "(%s and on () %s) * 0 + 1" % (seen(H), gate),
               nodata()),
         signed_share(s, "$__range", "1m", 60),
         [(1, "Signing", OK), (2, "Short gap in signing", WARN), (3, "Not signing", ALERT),
          (4, "Missed some blocks", WARN), (5, "Nodes stalled", NODATA), (0, "No data", NODATA)]),
    ]
    if s.remote:
        # Green needs real signatures in the block, never the median gauge, which freezes when tmkms stops.
        lanes.append(
            ("C", "Remote signer",
             chain("%s * 0 + 2" % firing('alertname="RemoteSignerProblem"'),
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
             [(1, "Signing, fast enough", OK), (2, "Slow or failing", WARN), (4, "No signatures", WARN),
              (0, "Not asked to sign, or no data", NODATA)]))
    if s.fibre:
        not_failing = 'max(ALERTS{alertname="FibreNotServing",reason="uploads_failing",alertstate="firing"})'
        lanes.append(
            ("D", "Fibre serving",
             chain("%s * 0 + 3" % firing('alertname="FibreDown",check=""'),
                   "%s * 0 + 2" % active('alertname="FibreDown",check=""'),
                   "%s * 0 + 4" % firing('alertname="FibreNotServing"'),
                   "%s * 0 + 5" % firing('alertname="FibreBothActive"'),
                   '((%s == 1) and on () %s) * 0 + 1' % (seen('node_systemd_unit_state{name=%s,state="active"}'
                                                              % q(s.units["fibre"])), gate),
                   nodata()),
             uptime("((((count(%s) > bool 0) or on () (%s * 0)) unless on () %s) or on () (%s * 0)) and on () %s"
                    % (fibre_active(s), PROM, not_failing, PROM, PROM)),
             [(1, "Serving", OK), (2, "Briefly not running", WARN), (3, "Not running", ALERT),
              (4, "Not serving", WARN), (5, "Running on two servers", WARN), (0, "No data", NODATA)]))
    if s.tensile:
        # celestia-mon-tensile keeps exporting the last reading when its reads fail, so green also
        # needs a check under 15 minutes old at every minute of the block, as FibreDown does.
        lanes.append(
            ("E", "Fibre from outside",
             chain("%s * 0 + 3" % firing('alertname="FibreDown",check="reachability"'),
                   "%s * 0 + 4" % firing('alertname="FibreDown",check="registration"'),
                   "%s * 0 + 5" % firing('alertname="TensileUnreadable"'),
                   "%s * 0 + 2" % active('alertname="FibreDown",check="reachability"'),
                   "%s * 0 + 8" % active('alertname="FibreDown",check="registration"'),
                   "(%s or %s) * 0 + 6" % (stale("celestia_mon_fibre_last_check_timestamp_seconds", 900),
                                           down("tensile")),
                   "((%s == 1) and on () %s) * 0 + 1" % (seen("celestia_mon_fibre_reachable"), gate),
                   nodata()),
             uptime('(((max(ALERTS{alertname="FibreDown",check="registration",alertstate="firing"}) * 0) '
                    "or (max(celestia_mon_fibre_registered) == 0) "
                    "or max(celestia_mon_fibre_reachable and on (endpoint) celestia_mon_fibre_registered == 1)) "
                    "unless on () (time() - max(celestia_mon_fibre_last_check_timestamp_seconds) > 900)) "
                    "and on () %s" % PROM),
             [(1, "Reachable", OK), (2, "Unreachable briefly", WARN), (8, "Registration wrong briefly", WARN),
              (3, "Unreachable", ALERT), (4, "Not registered or wrong host", ALERT), (5, "Check stopped", WARN),
              (6, "Not checked", NODATA), (0, "No data", NODATA)]))
    # Monitoring is a function, not a server: the last check line. Alertmanager down or not reached is
    # read from Prometheus's own metrics; no rule watches it (the Watchdog keeps firing meanwhile).
    lanes.append(
        ("M", "Monitoring",
         chain("%s * 0 + 4" % firing('alertname="MonitoringBroken"'),
               "%s * 0 + 5" % firing('alertname=~"NotificationsFailing|DeadmanFailing"'),
               "%s * 0 + 6" % down("alertmanager"),
               # Alerts not handed over in the block and none handed over, as unsent().
               "((((sum(%s) > 0) or (sum(%s) > 0)) unless on () (sum(%s) > 0)) or (max(%s) == 0)) * 0 + 9"
               % (in_block("increase", NOTIFY_ERRORS), in_block("increase", NOTIFY_DROPPED),
                  in_block("increase", NOTIFY_SENT), in_block("min_over_time", AM_DISCOVERED)),
               "%s * 0 + 7" % firing('alertname="AnnouncementsUnreadable"') if s.announcements else None,
               # No heartbeat in the block, except the block in which Prometheus started: its rules
               # have not run yet.
               "((%s * 0 + 8 unless on () %s) unless on () (max(%s) > time()))"
               % (seen('up{job="prometheus"}'), seen('ALERTS{alertname="Watchdog",alertstate="firing"}'),
                  in_block("max_over_time", 'process_start_time_seconds{job="prometheus"}')),
               "%s * 0 + 1" % seen('ALERTS{alertname="Watchdog",alertstate="firing"}'),
               "%s * 0" % seen('up{job="prometheus"}')),
         uptime('(((max(ALERTS{alertname="Watchdog",alertstate="firing"}) * 0 + 1) unless on () '
                'max(ALERTS{alertname=~"MonitoringBroken|NotificationsFailing|DeadmanFailing|AnnouncementsUnreadable",'
                'alertstate="firing"}) unless on () (min(up{job="alertmanager"}) == 0) unless on () %s) '
                "or on () (%s * 0)) and on () %s" % (unsent("2m"), PROM, PROM)),
         [(1, "Working", OK), (4, "Alert rules failing", WARN), (5, "Alerts not delivered", WARN),
          (6, "Alertmanager down", WARN), (9, "Alertmanager not reached", WARN), (7, "Announcements not read", WARN),
          (8, "Heartbeat stopped", WARN), (0, "No data", NODATA)]))
    return lanes


def server_lanes(s):
    """One status line per server, in servers order: (refId, label, bars, uptime, words). The alerts
    excluded from the generic warning colour lines Remote signer, Fibre serving, Fibre from outside
    and Monitoring instead; the upgrade alerts colour no line (the Network upgrade tile shows them).
    NodeBehind colours its server's line and Node in sync."""
    others = SERVER_OTHERS
    words = [(1, "OK", OK), (2, "Warning", WARN), (3, "Disk full or read-only", ALERT), (4, "Not reporting", WARN),
             (5, "Service down", WARN), (6, "Disk low or read-only", WARN), (7, "Node behind", WARN),
             (0, "No data", NODATA)]
    lanes = []
    for i, h in enumerate(s.servers):
        sel = "host=%s" % q(h)
        up = 'up{%s,job=~"node|celestia-app|otelcol"}' % sel
        page = 'max(ALERTS{alertname="DiskLow",severity="page",%s,alertstate="firing"})' % sel
        lanes.append((
            "S%d" % (i + 1), h,
            chain("%s * 0 + 3" % firing('%s,severity="page"' % sel),
                  "%s * 0 + 4" % firing('%s,alertname="ServiceDown",unit=~"celestia_app|node_exporter|otelcol"' % sel),
                  "%s * 0 + 5" % firing('%s,alertname="ServiceDown"' % sel),
                  "%s * 0 + 6" % firing('%s,alertname="DiskLow"' % sel),
                  "%s * 0 + 7" % firing('%s,alertname="NodeBehind"' % sel),
                  "%s * 0 + 2" % firing('%s,severity="message",alertname!~"%s"' % (sel, others)),
                  "((%s == 1) and on () %s) * 0 + 1" % (seen('up{job="node",%s}' % sel), rules_ran()),
                  nodata()),
            uptime("((min(%s) unless on () %s) or on () (min(%s) * 0)) and on () %s" % (up, page, up, PROM)),
            words))
    return lanes


def axis_width(s, check, servers, roles):
    """The label column of the board and the band: wide enough for the longest line label."""
    labels = [lbl for _, lbl, _, _, _ in check + servers] + [lbl for _, lbl, _, _ in roles]
    return max(140, 7 * max(len(lbl) for lbl in labels) + 24)


def board_row_height(title, height, lanes):
    """The status history's rowHeight (a share of each lane) that makes every bar BAR_PX thick."""
    lane = (grid_px(height) - (CHROME_TITLED if title else CHROME_UNTITLED)) / float(len(lanes))
    return round(min(1.0, BAR_PX / lane), 2)


def status_history(pid, title, desc, lanes, height, axis):
    """One line of bars per lane, every bar BAR_PX thick; the time axis is the role band's."""
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
    height, chrome, step and rowHeight as the bars beside it, invisible fill and no axes, so each
    value sits on its line. Each query is the uptime read once, at the end of the range, and held
    over it; every query returns a value, so no value slides into another line's place. The lane
    names are distinct zero-width spaces, so no label shows. No description: the (i) would crowd the
    title; the bars' panel explains the values."""
    targets = [target(ref, "last_over_time((%s %s)[1m:1m] @ end())" % (up, UP_LOST), ZERO_WIDTH * (i + 1),
                      interval="1m")
               for i, (ref, _, _, up, _) in enumerate(lanes)]
    defaults = {"color": {"fixedColor": "transparent", "mode": "fixed"}, "decimals": 2, "unit": "percentunit",
                "thresholds": steps(("text", None)),
                "custom": {"axisPlacement": "hidden", "fillOpacity": 0, "lineWidth": 0},
                "mappings": range_map(0.99995, 1, "100%") + mapping((-1, "No data", GREY)) + special_map("nan", "No data")}
    options = {"alignValue": "center", "legend": {"displayMode": "list", "placement": "bottom", "showLegend": False},
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
    lanes = [("A", "Signing through", gated(chain(
        "(%s unless on () %s) * 0 - 4" % (firing('alertname="ValidatorNotSigning"'),
                                          firing('alertname="NodeNotAdvancing"')),
        "(max(%s) == 1) * 0 - 3" % in_block("max_over_time", "((changes(max(%s)[2m:]) == bool 0) and on () %s)"
                                            % (H, STALL_SEEN), sub=True),
        # Out of the set at some step of the block, as the board reads every line: never from one reading.
        "(max(%s) == 1) * 0 - 2" % in_block("max_over_time", "((max(%s) * 0 + 1) unless on () %s)"
                                            % (POWER, IN_SET_OR_NEW), sub=True),
        "(max(%s) == 1) * 0 - 5" % in_block("max_over_time", two_signers(s), sub=True)
        if s.remote and not s.multi else None,
        "(max(%s) == 1) * 0 - 1" % in_block("max_over_time", "((count(%s) or vector(0)) > bool 1)" % sig, sub=True)
        if s.remote else None,
        "(min(%s) == 0) * 0" % in_block("min_over_time", "(count(%s) or vector(0))" % sig, sub=True),
        "max(%s and on (host) (%s))" % (lookup(s.nodes), moved),
        "max(%s) * 0" % H)),
        [(i + 1, h, s.tint(h)) for i, h in enumerate(s.nodes)]
        + ([(-1, "Two nodes" if s.multi else "Switching", NODATA)] if s.remote else [])
        + ([(-5, "Two signers", WARN)] if s.remote and not s.multi else [])
        + [(0, "No node signing", WARN), (-4, "No node signing", ALERT), (-2, "Out of set", NODATA),
           (-3, "Nodes stalled", NODATA)] + no_data)]
    if s.fibre:
        seen_f = fibre_seen(s)
        lanes.append(("B", "Fibre active on", gated(chain(
            "%s * 0 - 4" % firing('alertname="FibreDown",check=""'),
            "(max(%s) == 1) * 0 - 1" % in_block("max_over_time", "(count(%s) > bool 1)" % seen_f, sub=True),
            # No Fibre at some step of the block, so a short outage still shows on long ranges.
            '((min(%s) == 0) * 0 and on () (count(up{job="node"} == 1) > 0))'
            % in_block("min_over_time", "(count(%s) or vector(0))" % seen_f, sub=True),
            "max(%s and on (host) %s)" % (lookup(s.fibre_hosts), seen_f),
            "(count(%s) > 0) * 0 - 5" % fibre_lost(s),
            '(((count(%s) or vector(0)) == 0) * 0 and on () (count(up{job="node"} == 1) > 0))' % seen_f)),
            [(i + 1, h, s.tint(h)) for i, h in enumerate(s.fibre_hosts)]
            + [(-1, "Two servers", WARN), (0, "No Fibre running", WARN), (-4, "No Fibre running", ALERT),
               (-5, "Not seen", WARN)] + no_data))
    if s.fibre and s.remote:
        # The node Fibre sends its signing requests to: raw bytes in the last 15 minutes, else the last hour.
        raw = ["max(%s and on (host) (sum by (host) (increase(%s[%s])) > 0))" % (lookup(s.nodes), RAW, w)
               for w in ("15m", "1h")]
        fsv = "(%s or %s)" % tuple(raw)
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
    """The role band: who held each role over the selected time, with the time axis of the whole board."""
    lane = (grid_px(height) - CHROME_UNTITLED - AXIS_PX) / float(len(lanes))
    desc = ("Which server held each role over the selected time, read from the metrics, never from the inventory. "
            "Signing through: the server whose node made the validator's signatures; a tmkms repoint shows as the "
            "name changing, marked by a thin line")
    if s.remote and not s.multi:
        desc += (" ('Switching' while both nodes have recent signatures, up to 2 minutes; 'Two signers' in amber if "
                 "that lasts 3 minutes)")
    desc += "."
    if s.fibre:
        desc += (" Fibre active on: the server whose Fibre unit systemd reported active; a failover shows the same "
                 "way.")
    if s.fibre and s.remote:
        desc += (" Fibre signs through: the node Fibre sends its signing requests to (raw bytes, last 15 minutes, else "
                 "last hour); amber when it is not the node the validator signs through, because after a repoint a "
                 "Fibre that still dials the old node fails every upload: point Fibre's signer address at the signing "
                 "node.")
    desc += (" Amber: nobody held the role, or two servers did; red: the page alert for it fired. Grey 'No data' "
             "where monitoring had no data, as on the bars. On long ranges each step shows its worst moment.")
    desc += more(["ValidatorNotSigning"] + (["FibreDown", "FibreNotServing"] if s.fibre else []), [MAINNET_LAYOUT])
    targets = [target(ref, expr, lbl, interval="1m") for ref, lbl, expr, _ in lanes]
    defaults = {"color": {"fixedColor": "#8E8E8E26", "mode": "fixed"},
                "custom": {"axisWidth": axis, "fillOpacity": 100, "lineWidth": 0},
                "mappings": special_map("null", "No data", NODATA)}
    overrides = [override("byFrameRefID", ref, mappings=lane_map(words)) for ref, _, _, words in lanes]
    options = {"alignValue": "left", "legend": {"displayMode": "list", "placement": "bottom", "showLegend": False},
               "mergeValues": True, "rowHeight": round(min(1.0, BAND_BAR_PX / lane), 2), "showValue": "always",
               "tooltip": {"mode": "single", "sort": "none"}}
    p = panel(25, "state-timeline", "", desc, targets, defaults, overrides, options)
    p.update({"interval": "1m", "maxDataPoints": 1500, "transparent": True})
    return p


def key_line(axis):
    """The colour key under the role band: the one rule for every colour on the screen. It is one
    line of KEY_PX in an untitled h2, moved down to the middle of the panel's content by a top
    padding; the line and its padding stay well inside the content, so no scrollbar appears (an h1
    leaves exactly KEY_PX, and Grafana scrolled it). The swatches are flex items, so they never make
    the line taller than its line height."""
    top = (grid_px(KEY_ROWS) - CHROME_UNTITLED - KEY_PX) // 2
    swatch = ('<span style="display:flex;align-items:center"><span style="display:block;width:10px;height:10px;'
              'border-radius:2px;background-color:%s;margin-right:4px"></span>%s</span>')
    content = ('<div style="display:flex;justify-content:space-between;align-items:center;height:%dpx;font-size:%dpx;'
               'line-height:%dpx;color:#8e8e8e;padding-top:%dpx;padding-left:%dpx"><span>Times in UTC</span>'
               '<span style="display:flex;align-items:center;gap:14px">%s</span></div>'
               % (KEY_PX, KEY_PX, KEY_PX, top, axis + 24, "".join(swatch % (c, t) for c, t in [
                   (OK, "Fine (green or plain text)"), (WARN, "Look soon"), (ALERT, "Urgent alert"),
                   (NODATA, "No data or not in use")])))
    return {"id": 24, "type": "text", "title": "", "transparent": True,
            "options": {"content": content, "mode": "html"}}


def role_codes(s, short, hosts=None):
    """(code, words, colour) of every Role the servers (or hosts) can show. A server's parts add up to
    one code: node 1-3, Fibre 4 or 8, KMS 16-64. Amber while the node does not sign or tmkms is
    stopped or not loaded; grey for a standby Fibre alone."""
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
    return codes


def table_width(words):
    """Fixed width in px of a text column for its longest text."""
    return int(math.ceil(max(len(w) for w in words) * TABLE_CHAR + TABLE_PAD))


def servers_table(s):
    """(panel, height) of Servers now: one row per configured server, even when nothing is read from it.
    Every cell has a word for "no data" (the server should report it and does not) and "–" (does not
    apply), so no cell is blank and a silent server never looks fine."""
    order = lookup(s.servers)
    na = "or on (host) (%s * 0 - 9)" % order
    nodata_all = "or on (host) (%s * 0 - 8)" % order
    sig = signing_nodes(s)
    shared = [(-8, "no data", GREY), (-9, "–", GREY)]

    node_up = '(max by (host) (up{job="celestia-app"}) == 1)'
    first = "%s * 0 + 1" % sig
    if s.remote and not s.multi:
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
        tmkms = 'node_systemd_unit_state{name=%s,state="active",host=~%s}' % (
            q(s.units["tmkms"]), q("|".join(re2(h) for h in s.kms_hosts)))
        kms = "(max by (host) (%s) == 1) * 0 + 16 or (max by (host) (%s) == 0) * 0 + 32" % (tmkms, tmkms)
        # The ServiceDown rule for a unit that is not loaded, per KMS host.
        for k in s.kms_hosts:
            kms += (' or (absent(node_systemd_unit_state{host=%s,name=%s}) and on () (up{job="node",host=%s} == 1)) '
                    "* 0 + 64" % (q(k), q(s.units["tmkms"]), q(k)))
        parts.append(label(kms, "part", "kms"))
    # A server whose node_exporter does not answer never shows a role it may no longer hold.
    role = ("((%s unless on (host) %s) * 0 - 1) or on (host) sum by (host) (%s) or on (host) (%s * 0)"
            % (order, NE_UP, " or ".join(parts), order))

    node = "%s %s" % (chain(
        '(max by (host) (up{job="celestia-app"}) == 0) * 0 - 1',
        "(max by (host) (changes(%s[2m])) == 0) * 0 - 2" % H,
        '(max by (host) (cometbft_blocksync_syncing{job="celestia-app"}) == 1) * 0 - 3',
        "(scalar(max(%s)) - max by (host) (%s))" % (H, H)), na)
    peers = 'max by (host) (cometbft_p2p_peers{job="celestia-app"}) or on (host) (%s * 0 - 8) %s' % (lookup(s.nodes), na)
    median = 'max by (host) (cometbft_privval_signing_latency_median_seconds{job="celestia-app"})'
    signer = "%s %s" % (chain(
        '(max by (host) (increase(cometbft_privval_signing_failures_total{job="celestia-app",message_type="proposal",'
        'reason!~"timeout|no_connection"}[6h])) > 0) * 0 - 5',
        "(%s and on (host) %s)" % (median, sig),
        "(%s > %s) * 0 - 6" % (median, num(s.S)),
        "((%s * 0 - 7) and on () ((count(%s) or vector(0)) == 0) and on () %s)" % (lookup(s.nodes), sig, IN_SET)), na)
    cpu = ('(1 - sum by (host) (rate(node_cpu_seconds_total{mode=~"idle|iowait"}[5m])) '
           '/ count by (host) (node_cpu_seconds_total{mode="idle"})) %s' % nodata_all)
    memory = ("(1 - max by (host) (node_memory_MemAvailable_bytes) / max by (host) (node_memory_MemTotal_bytes)) %s"
              % nodata_all)
    peaks = []
    for link in s.links:
        sel = "{host=%s,device!~%s}" % (q(link["host"]), q(VIRT))
        cap = link_capacity(link, sel)
        # A 75 s window holds a full minute of 15 s scrapes; [1m] would hold 45 s of them and read
        # the busiest 45 seconds instead of the busiest minute.
        rates = ["max_over_time((sum by (host) (rate(node_network_%s_bytes_total%s[75s])))[1h:1m])" % (m, sel)
                 for m in ("receive", "transmit")]
        peaks.append('(max by (host) (%s or %s) or ((max by (host) (up{job="node",host=%s}) == 1) * 0 - 4))'
                     % (label("%s / %s" % (rates[0], cap), "dir", "in"), label("%s / %s" % (rates[1], cap), "dir", "out"),
                        q(link["host"])))
    network = "%s %s" % (" or ".join(peaks), nodata_all)
    units = [s.units["appd"]] + ([s.units["fibre"]] if s.fibre else []) + ([s.units["tmkms"]] if s.kms_hosts else [])
    restart_sel = "{name=~%s}" % q("|".join(re2(u) for u in units))
    restarts = ("((floor(max by (host) (increase(node_systemd_service_restart_total%s[30m]))) > %s) * 0 - 10 "
                "or floor(sum by (host) (increase(node_systemd_service_restart_total%s[24h])))) and on (host) %s %s"
                % (restart_sel, num(s.CL), restart_sel, NE_UP, nodata_all))

    gauge = {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}
    behind = min(5, s.NB)
    stale_word = "> %s ms, stale" % s.ms()
    # (refId, header, query, words a text column can show or None for a gauge column, other properties)
    columns = [
        ("A", "Role", role, None, {}),
        ("B", "Node", node, ["in sync", "stalled", "syncing", "down", "9999 behind"] + [w for _, w, _ in shared],
         dict(unit="suffix: behind", thresholds=steps(("text", None), ("orange", s.NB + 1)),
              mappings=range_map(0, behind, "in sync", "text") + mapping(
                  (-1, "down", "orange"), (-2, "stalled", "orange"), (-3, "syncing", "text"), *shared))),
        ("C", "Peers", peers, ["999"] + [w for _, w, _ in shared],
         dict(custom__align="right", mappings=mapping(*shared))),
    ]
    if s.remote:
        columns.append(
            ("D", "Signer time", signer, ["Proposals fail", stale_word, "No signing", "9.99 ms"] + [w for _, w, _ in shared],
             dict(unit="s", decimals=2, custom__align="right", thresholds=steps(("text", None), ("orange", s.S)),
                  mappings=mapping((-5, "Proposals fail", "orange"), (-6, stale_word, "orange"),
                                   (-7, "No signing", "orange"), *shared))))
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
                             mappings=mapping((-7, "Full or read-only", "red"), (-6, "Read-only", "orange"),
                                              (-5, "Low space", "orange"), (-4, "Too small", "orange"), *shared))))
    columns += [
        ("H", "Network peak", network, None,
         dict(thresholds=steps((GREY, None)), mappings=mapping((-4, "speed unknown", GREY), *shared))),
        ("I", "Restarts", restarts, ["Crash loop", "999"] + [w for _, w, _ in shared],
         dict(custom__align="right", mappings=mapping((-10, "Crash loop", "orange"), *shared))),
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
            "from it. Role: what the server does now, read from the metrics, so it follows a tmkms repoint or a Fibre "
            "failover ('Not reporting' while its node_exporter does not answer). Node: blocks behind the highest node "
            "of this setup; 'in sync' within %g; amber above %g (NodeBehind sends a message after %s); a backup node "
            "must stay in sync to take over signing. Peers: connected peers; a node with none falls behind. "
            % (behind, s.NB, s.hold("NodeBehind")))
    if s.remote:
        desc += ("Signer time: median of the last 50 signatures on the node that signs now; amber from %s ms, Celestia's "
                 "limit (RemoteSignerProblem after %s); '%s': a node tmkms left keeps its last median, and the alert "
                 "keeps firing for it until that node restarts; 'No signing': the validator is asked to sign and no node "
                 "signs; 'Proposals fail': proposal signing failed for another reason than a timeout in 6 hours. "
                 % (s.ms(), s.hold("RemoteSignerProblem slow"), stale_word))
    desc += "CPU, Memory and Network peak: share in use, in grey; no alert watches them. "
    if watched:
        desc += ("Disk used: the fullest disk DiskLow watches on the server, the data disks you listed and the Fibre "
                 "store, as a grey bar. Amber 'Low space' while one has less than %g%% free (a Fibre store only while "
                 "Fibre reports no storage budget; DiskLow sends a message after %s); amber 'Read-only'; red 'Full or "
                 "read-only' when DiskLow pages. " % (s.DM * 100, s.hold("DiskLow low space")))
        if tight:
            desc += ("Amber 'Too small' while a Fibre store has less free space than its storage budget still needs "
                     "(DiskLow sends a message after %s). " % s.hold("DiskLow budget"))
    desc += ("Network peak: the busiest minute of the last hour against the link speed (celestia_mon_link_mbps or the "
             "NIC speed). Restarts: automatic restarts of %s by systemd in 24 hours; 'Crash loop' when more than %g in "
             "30 minutes (ServiceDown). 'no data': the server should report this but does not. '–': does not apply."
             % (", ".join(u[:-len(".service")] for u in units), s.CL))
    desc += more(["NodeBehind", "RemoteSignerProblem", "DiskLow", "ServiceDown"] if s.remote else
                 ["NodeBehind", "DiskLow", "ServiceDown"], [NODE_EXPORTER_FULL])
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


def first_screen(s):
    """The lines of the first screen: the intro strip (with the Status), the answers, the status board
    with its uptime columns, the role band, the key and Servers now."""
    check, servers, roles = check_lanes(s), server_lanes(s), role_lanes(s)
    axis = axis_width(s, check, servers, roles)
    h_check = grid_rows(LANE_PX * len(check), CHROME_TITLED)
    h_servers = max(2, grid_rows(LANE_PX * len(servers), CHROME_UNTITLED))
    h_roles = grid_rows(LANE_PX * len(roles) + AXIS_PX, CHROME_UNTITLED)
    desc = CHECK_DESC
    desc += (" Monitoring: the alert rules, alert delivery, Alertmanager, the heartbeat to your dead-man service"
             "%s. Hover a bar to see every line at that moment. Prometheus keeps %s of data, or less at its size "
             "limit; older bars are grey. Each bar is drawn centred on the start of its block, so it sits half a block "
             "to the left of the charts below." % (" and Celestia's announcements" if s.announcements else "",
                                                     s.retention))
    # The uptime columns beside the bars have no description of their own: it is here.
    desc += (" Node in sync: the nodes advance (NodeNotAdvancing) and none lags behind the others (NodeBehind). "
             "Uptime, the value at the right of each line, over the selected time: Node in sync, minutes in which "
             "the nodes advanced and none was behind; Validator signing, the share of blocks that carry the "
             "validator's signature (blocks while it was not signing at all count as unsigned)")
    if s.remote:
        desc += "; Remote signer, minutes in which it signed without a signer alert"
    if s.fibre:
        desc += "; Fibre serving, minutes with a Fibre server running and not failing every upload"
    if s.tensile:
        desc += "; Fibre from outside, minutes in which a recent outside check reached the right endpoint"
    desc += ("; Monitoring, minutes with alert rules running, Alertmanager up and reached, and the heartbeat flowing. "
             "Time without monitoring data is left out, the minute Prometheus starts included." + more(pages=[("Alert reference", DOCS)]))
    server_desc = ("One line per server: red when its disk is full or read-only (DiskLow pages), amber when it is not "
                   "reporting, a service is down, its disk runs low or turns read-only, or its node falls behind. "
                   "Uptime at the right: minutes in which all of the server's metrics answered and its disk was not "
                   "full or read-only." + more(["ServiceDown", "DiskLow", "NodeBehind"]))
    table, h_table = servers_table(s)
    return [
        (INTRO_ROWS, intro(s)),
        (3, answer_tiles(s)),
        (h_check, [(status_history(20, "Status", desc, check, h_check, axis), 22),
                   (uptime_column(21, "Uptime", check, h_check), 2)]),
        (h_servers, [(status_history(22, "", server_desc, servers, h_servers, axis), 22),
                     (uptime_column(23, "", servers, h_servers), 2)]),
        (h_roles, [(role_band(s, roles, h_roles, axis), 22)]),
        (KEY_ROWS, [(key_line(axis), 22)]),
        (h_table, [(table, 24)]),
    ]


# The rows below the first screen.

def fact(pid, label, desc, targets, **kw):
    """A neutral fact inside a row, in a framed, untitled panel: the label (FACT_LABEL_PX) centred over
    the value (FACT_PX, or the strip's size) in the text colour. The label is the field's display name,
    so the panel's title bar stays empty and its (i) shows on hover."""
    kw.setdefault("color_mode", "none")
    p = stat(pid, "", desc, targets, size=FACT_PX, text_mode="value_and_name", wide=False,
             title_size=FACT_LABEL_PX, **kw)
    p["fieldConfig"]["defaults"]["displayName"] = label
    return p


def one(expr):
    """A panel's only query, instant."""
    return [target("A", expr, "", instant=True)]


# The Validator row. Its facts and charts carry the host label of the node the validator signs
# through, so host_colors() draws them in that server's colour and a repoint shows as a colour
# change; what belongs to the whole network keeps the text colour.

# A fact with a sparkline: an untitled h4 cell, the label over the value and the sparkline under
# them. Grafana draws a stacked stat's sparkline only above 100 px of content, which h3 does not
# have at 1440 px. SPARK_POINTS: the sparkline's steps (15 minutes on 24 hours), so the counts of
# the last 24 hours stay cheap for Prometheus.
SPARK_ROWS, SPARK_POINTS, SPARK_MIN_PX = 4, 96, 40
# The charts: one height, with a legend line.
CHART_ROWS = 7


def as_signer(expr, sig):
    """expr, one series without labels, with the host label of each node of sig (a signing_nodes()
    expression), so host_colors() draws it in that server's colour; empty while sig is."""
    return "(%s) * on () group_right () (%s * 0 + 1)" % (expr, sig)


def unsigned(expr, sig):
    """expr, without a host, while sig is empty: the same reading while no node signs."""
    return "(%s) unless on () %s" % (expr, sig)


def grown(fn, agg, job="celestia-app"):
    """How much a counter grew per host in each time block of a chart ($__interval): its value at the
    block's end minus its value at its start. fn(offset) is the counter's selector followed by the
    offset text; agg (sum or max) combines a host's series. A host that is up but has not counted
    yet reads 0, so a counter that appears with its first increment counts it, as in count_24h();
    after a restart the block counts from the restart, never a negative number."""
    def at(off):
        return ("(%s by (host) (%s) or on (host) (max by (host) (up{job=%s}%s) == 1) * 0)"
                % (agg, fn(off), q(job), off))
    end, start = at(""), at(" offset $__interval")
    return "(((%s - %s) >= 0) or (%s < %s))" % (end, start, end, start)


def spark_fact(s, pid, label, desc, expr, kind="signer", spark=None, unit="none", decimals=0, minimum=None,
               maximum=None, mappings=(), no_value="No data"):
    """A fact of the Validator row: the label over the value now, and under them a sparkline of the
    selected time, the value in the text colour and the sparkline in a server's colour. kind
    "signer": expr is one reading of the validator, drawn in the colour of the node it signs through
    at the end of the selected time (a fact names the node that signs now), or in the text colour
    while no node signs; "own": expr has a series per host, and the signing node's own is shown, in
    its colour; "network": a reading of the whole network, in the text colour. A range query's newest
    step lies up to one step before now, so an instant query reads the value now and the merge
    transformation appends it to the sparkline. spark: the sparkline's expression when it should
    differ from expr, e.g. the worst moment of each step. minimum, maximum: the sparkline's range."""
    spark = spark or expr
    sig = signing_nodes(s, "[2m] @ end()")
    overrides = []
    if kind == "network":
        targets = [target("A", spark, label), target("C", expr, label, instant=True)]
    elif kind == "own":
        targets = [target("A", "(%s) and on (host) %s" % (spark, sig), "{{host}}"),
                   target("C", "(%s) and on (host) %s" % (expr, sig), "{{host}}", instant=True)]
    else:
        targets = [target("A", as_signer(spark, sig), "{{host}}"), target("B", unsigned(spark, sig), label),
                   target("C", as_signer(expr, sig), "{{host}}", instant=True),
                   target("D", unsigned(expr, sig), label, instant=True)]
    if kind != "network":
        # Matched on the host name the legend gives; the override then names the field.
        overrides = [override("byRegexp", "/^%s$/" % js_regex(h), color=fixed(s.color(h)[0]), displayName=label)
                     for h in s.servers]
    p = stat(pid, "", desc, targets, unit=unit, decimals=decimals, mappings=mappings, no_value=no_value,
             overrides=overrides, size=FACT_PX, text_mode="value_and_name", color_mode="none", graph="area",
             wide=False, title_size=FACT_LABEL_PX)
    defaults = p["fieldConfig"]["defaults"]
    defaults["color"] = fixed(NETWORK)
    if minimum is not None:
        defaults["min"] = minimum
    if maximum is not None:
        defaults["max"] = maximum
    p["maxDataPoints"] = SPARK_POINTS
    p["transformations"] = [{"id": "merge", "options": {}}]
    return p, label


def fit_sparks(items, what):
    """Pins one value size on a strip of spark_fact() cells ((panel, label), width), as fit_strip()
    does for plain facts: FACT_PX when every value fits its cell, else FACT_SMALL_PX. Stops when a
    label or a value does not fit, or when the label and the value leave the sparkline less than
    SPARK_MIN_PX, or Grafana's stacked layout would drop it (100 px of content)."""
    texts = []
    for (p, label), w in items:
        check_fit(what, [label], FACT_LABEL_PX, w)
        texts += [(t, w) for t in panel_texts(p)]
    size = FACT_PX if all(text_px(t, FACT_PX) <= inner_px(w) for t, w in texts) else FACT_SMALL_PX
    for t, w in texts:
        check_fit(what, [t], size, w)
    content = grid_px(SPARK_ROWS) - CHROME_UNTITLED
    if content <= 100 or content - stack_px(FACT_LABEL_PX, size, False) < SPARK_MIN_PX:
        raise SystemExit("%s: an untitled h%d panel has %d px, too little for a label, a value and a sparkline"
                         % (what, SPARK_ROWS, content))
    for (p, _), _ in items:
        p["options"]["text"]["valueSize"] = size
    return [(p, w) for (p, _), w in items]


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
    """The Validator row: six facts (the three about signing now, with sparklines that top out at the
    alert levels, then the proposals of the last 24 hours), then the charts behind the Validator and Signing through answers, two to a line
    (three with a local key, which has no signer chart)."""
    n = s.N
    sig_now = signing_nodes(s)
    sig_block = signing_nodes(s, "[$__interval]")
    span = "over the last 24 hours, or since monitoring started when that is less"
    if s.remote:
        prop = 'cometbft_privval_signing_latency_seconds_count{job="celestia-app",message_type="proposal"}'
        prop_label, prop_word, prop_desc = "Proposals signed, 24 h", "signed", "the proposals tmkms actually signed"
    else:
        prop = 'cometbft_consensus_proposal_create_count{job="celestia-app"}'
        prop_label, prop_word, prop_desc = "Proposals built, 24 h", "built", "with a local key, the proposals the node built"
    mpp = 'cometbft_consensus_proposer_missed_proposals{job="celestia-app"}'
    own = ('label_replace(max by (validator_address) (%s), "proposer_address", "$1", "validator_address", "(.+)")'
           % POWER)
    # As SignaturesMissed, per node: the share of the last hour's blocks without the validator's
    # signature. A node that knows the validator's key but has missed no block since its start has
    # no missed-block count yet: it reads 0 once it has seen a signature.
    missed = ("(max by (host) (%s) or max by (host) (%s) * 0) / (max by (host) (delta(%s[1h])) > 0)"
              % (missed_in("1h"), LSH, H))
    share = "max(%s)" % missed
    # The blocks of the last 24 hours: the height 24 hours ago, or the lowest one Prometheus has.
    day = "(max(%s) - (max(%s offset 24h) or max(min_over_time(%s[24h]))))" % (H, H, H)
    if s.remote:
        # The signer's median on the node the validator signs through, as RemoteSignerProblem reads it.
        third = spark_fact(
            s, 118, "Signer time",
            "How fast the remote signer answers now: the median of the last 50 signatures on the node the validator "
            "signs through, in its colour. Celestia requires it to stay at or under %s ms; above that for %s "
            "RemoteSignerProblem sends a message, and the sparkline tops out there. No reading: no node has signed in "
            "the last 2 minutes, or the node has made fewer than 50 signatures since it started."
            % (s.ms(), s.hold("RemoteSignerProblem slow")) + more(["RemoteSignerProblem"]),
            'max by (host) (cometbft_privval_signing_latency_median_seconds{job="celestia-app"})', kind="own",
            unit="s", decimals=1, minimum=0, maximum=s.S, no_value="No reading")
    else:
        # With a local key there is no signer; the signing node's peers. A count from 40 to 50 has no
        # trend worth a sparkline, so the fact carries none.
        third = spark_fact(
            s, 118, "Peers",
            "Peers of the node the validator signs through now. A node with few peers receives blocks and votes "
            "late, which shows as missed blocks; no alert watches the count. No node signing: no node has signed in "
            "the last 2 minutes.",
            'max by (host) (cometbft_p2p_peers{job="celestia-app"})', kind="own", no_value="No node signing")
        third[0]["options"]["graphMode"] = "none"

    facts = [
        spark_fact(
            s, 109, "Blocks since signature",
            "Is the validator signing right now? Blocks since its last signature on any node, as ValidatorNotSigning "
            "counts them, so the count carries on after a repoint: 0 or 1 while it signs. Above %g blocks for %s that "
            "alert pages and the Validator tile turns red. The value stops at %g; right after a node start it reads that "
            "until the first signature. The sparkline shows the highest count of each step and tops out at %g. In the "
            "colour of the node the validator signs through now." % (n, s.hold("ValidatorNotSigning"), 10 * n, n)
            + more(["ValidatorNotSigning"]),
            blocks_since_signature(10 * n), spark="max_over_time(%s[$__interval:1m])" % blocks_since_signature(10 * n),
            minimum=0, maximum=n),
        spark_fact(
            s, 117, "Missed blocks, 1 h",
            "The share of the last hour's blocks committed without the validator's signature, as SignaturesMissed "
            "computes it: above %g%% for %s that alert sends a message, and the sparkline tops out there. The Missed "
            "blocks chart below shows each miss. In the colour of the node the validator signs through now."
            % (s.R * 100, s.hold("SignaturesMissed")) + more(["SignaturesMissed"]),
            share, unit="percentunit", decimals=1, minimum=0, maximum=s.R),
        third,
        (fact(101, prop_label,
              "Block proposals the validator %s %s (%s), across node restarts. Compare with Expected: a signer that "
              "signs votes but not proposals shows here as far fewer. Proposals per hour below has them hour by hour."
              % (prop_word, span, prop_desc), one(count_24h(prop, "sum"))), prop_label),
        (fact(102, "Expected, 24 h",
              "Proposals the validator's share of voting power should bring %s: the blocks in that time times that "
              "share. Proposers take turns by stake, so %s should be close. Assumes the share stayed the same."
              % (span, prop_label),
              one(chain("(max(absent(%s)) * 0 - 1)" % H, "(%s * 0 - 2)" % STALL,
                        "(round(%s * max(%s) / max(%s)) and on () %s)" % (day, POWER, TOTAL, IN_SET_OR_NEW),
                        "(max(%s) * 0 - 3)" % H)),
              mappings=mapping((-1, "No node", GREY), (-2, "No new blocks", GREY), (-3, "Out of set", GREY))),
         "Expected, 24 h"),
        (fact(103, "Missed proposals, 24 h",
              "Times %s when it was this validator's turn to propose and no proposal reached the nodes in time, so the "
              "block went to another round. Causes: the remote signer could not sign the proposal, the node built the "
              "block too slowly, or the upload to peers was too slow. Occasional misses on a busy network are normal; "
              "several a day are not. Proposals per hour below shows each one." % span,
              one(count_24h("%s and on (proposer_address) %s" % (mpp, own), "max"))), "Missed proposals, 24 h"),
    ]
    strip = fit_sparks(list(zip(facts, strip_widths(len(facts)))), "Validator facts")

    # Missed blocks: each miss as a bar in its time block, the share of the last hour and the network.
    bucket = "max(%s) > 0" % grown(lambda off: MISSED + off, "max")
    net = 'max(avg_over_time(cometbft_consensus_missing_validators_power{job="celestia-app"}[1h])) / max(%s)' % TOTAL
    # One visible axis, the shares, with SignaturesMissed's zone at its top; the bars are events on a
    # hidden axis of their own, a bar of 10 missed blocks (a twentieth of a 10-minute block) as high
    # as the chart, so a single miss shows; their count is in the tooltip.
    shares = dict(custom__axisPlacement="left", unit="percentunit", decimals=1, min=0, custom__axisSoftMax=1.25 * s.R,
                  custom__drawStyle="line", custom__fillOpacity=0, custom__gradientMode="none")
    missed_chart = timeseries(
        111, "Missed blocks",
        "Is the validator signing every block, and is a miss this setup's fault? Bars: blocks committed without the "
        "validator's signature in each bar's time (10 minutes on 24 hours, 1 hour on 7 days), in the colour of the "
        "node it signed through; hover a bar for its count. None while every block is signed. Solid line (missed, "
        "1 h): the share of the last hour's blocks without its signature, as SignaturesMissed computes it; above the "
        "dashed %g%% line it sends a message after %s. Dashed line (network, 1 h): the whole network, the share of all "
        "stake missing from recent blocks. The validator's line high and the network's low: a problem on this setup's "
        "side (signer, its link, a node). Both high: the network is struggling. Not signing at all shows as tall bars "
        "and a line climbing out of the chart; ValidatorNotSigning pages after %g blocks without a signature (the "
        "Validator tile turns red)."
        % (s.R * 100, s.hold("SignaturesMissed"), n) + more(["SignaturesMissed", "ValidatorNotSigning"]),
        refs([(as_signer(bucket, sig_block), "{{host}} missed blocks"),
              (unsigned(bucket, sig_block), "missed blocks, no node signing"),
              (as_signer(share, sig_now), "{{host}} missed, 1 h"),
              (unsigned(share, sig_now), "missed, 1 h, no node signing"),
              (net, "network, 1 h")]),
        "none", decimals=0, soft_max=10, bars=True,
        # The bars are what the title names: the legend keeps one line for the two lines.
        overrides=[override("byFrameRefID", r, custom__hideFrom={"legend": True, "tooltip": False, "viz": False})
                   for r in "AB"] + [
                   override("byFrameRefID", "C", custom__lineWidth=2, **shares),
                   override("byFrameRefID", "D", custom__lineWidth=2, **shares),
                   override("byFrameRefID", "E", color=fixed(NETWORK), custom__lineWidth=1,
                            custom__lineStyle=dash([6, 4]), thresholds=steps(("transparent", None), ("orange", s.R)),
                            custom__thresholdsStyle={"mode": "dashed+area"}, **shares)])
    missed_chart["fieldConfig"]["defaults"]["custom"]["axisPlacement"] = "hidden"

    # Proposals: each hour's proposals per node, missed ones on top, against the expected count.
    props = timeseries(
        116, "Proposals per hour",
        "Is the validator proposing its share of blocks? Bars: proposals %s through each node per hour, in that "
        "server's colour, with missed proposals stacked on top in grey (its turn came and no proposal reached the "
        "nodes in time). Dashed line: the proposals its share of voting power should bring in that hour, the blocks "
        "times that share. Proposers take turns by stake, so the bars should reach the line; a signer that signs "
        "votes but not proposals leaves them short. The newest bar is the last full hour; the facts above count the "
        "last 24 hours." % prop_word,
        refs([("%s > 0" % grown(lambda off: prop + off, "sum"), "{{host}} proposals"),
              ("max(%s) > 0" % grown(lambda off: "(%s%s and on (proposer_address) %s)" % (mpp, off, own), "max"),
               "missed"),
              ("(max(%s) - max(%s offset $__interval)) * max(%s) / max(%s) and on () %s" % (H, H, POWER, TOTAL, IN_SET),
               "expected")]),
        "none", decimals=0, bars=True, stack=True, fill=80,
        overrides=[override("byFrameRefID", "B", color=fixed("#CCCCDC73")),
                   override("byFrameRefID", "C", color=fixed(NETWORK), custom__drawStyle="line",
                            custom__lineInterpolation="stepBefore", custom__lineWidth=1, custom__fillOpacity=0,
                            custom__lineStyle=dash([6, 4]), custom__stacking={"group": "A", "mode": "none"})])

    # One line per node: the slowest 1% of checking a proposed block plus the slowest 1% of applying
    # a committed one, the time a block costs a node at worst.
    handling = timeseries(
        115, "Block handling time",
        "Is a node too slow to keep up? How long each node takes, at worst, to handle a block: the slowest 1% of "
        "checking a proposed block plus the slowest 1% of applying a committed one, over 10 minutes, one line per "
        "node in its server's colour, the backup node's too. A node that is too slow votes late or against the block, "
        "which shows as missed blocks. Blocks arrive about every 3 seconds; times near a second leave too little "
        "room. All nodes slow: a heavy block. One node slow: its CPU or disk.",
        refs([('sum by (host) (histogram_quantile(0.99, sum by (host, method, le) (rate('
               'cometbft_abci_connection_method_timing_seconds_bucket{job="celestia-app",'
               'method=~"process_proposal|finalize_block"}[10m]))))', "{{host}}")]),
        "s", soft_max=0.1, fill=10)

    # Counts per time block: a bar covers the block that ends at its time. The missed blocks share the
    # status board's blocks (10 minutes on 24 hours); proposals are counted per hour.
    for p, points, step in [(missed_chart, 150, "1m"), (props, 1000, "1h")]:
        p["fieldConfig"]["defaults"]["custom"]["barAlignment"] = -1
        p["maxDataPoints"] = points
        p["interval"] = step
    lines = [[missed_chart, props, handling]]
    if s.remote:
        bucket_le = 'cometbft_privval_signing_latency_seconds_bucket{job="celestia-app",message_type%s}'
        targets = [
            ('max by (host) (cometbft_privval_signing_latency_median_seconds{job="celestia-app"}) and on (host) %s'
             % sig_now, "{{host}} median"),
            ("histogram_quantile(0.99, sum by (host, le) (rate(%s[5m]))) and on (host) %s"
             % (bucket_le % '=~"prevote|precommit"', sig_now), "{{host}} votes")]
        if s.fibre:
            targets.append(("histogram_quantile(0.99, sum by (host, le) (rate(%s[5m]))) and on (host) "
                            "(sum by (host) (increase(%s[5m])) > 0)" % (bucket_le % '="raw_bytes"', RAW),
                            "{{host}} Fibre"))
        targets.append(("sum(%s) > 0" % grown(lambda off: 'cometbft_privval_signing_failures_total{job="celestia-app"}'
                                              + off, "sum"), "signing failures"))
        fails = chr(65 + len(targets) - 1)
        styles = [override("byFrameRefID", "B", custom__lineStyle=dash([6, 4]), custom__lineWidth=1, custom__fillOpacity=0)]
        if s.fibre:
            styles.append(override("byFrameRefID", "C", custom__lineStyle=dash([2, 4]), custom__lineWidth=1,
                                   custom__fillOpacity=0))
        latency = timeseries(
            112, "Signer latency",
            "How fast does the remote signer answer? On the node the validator signs through, in that server's colour: "
            "the median of the last 50 signatures (solid, median), which Celestia requires to stay at or under %s ms "
            "(dashed line; RemoteSignerProblem sends a message after %s above it), and the slowest 1%% of vote "
            "signatures over 5 minutes (dashed, votes).%s The axis is logarithmic, from 0.1 to 100 ms, so a rare slow "
            "signature (up to a second) does not flatten the rest; one above 100 ms rises out of the top edge. Grey "
            "bars: failed signing requests in each bar's time (timeouts, lost connections and others; every retry "
            "counts), hover a bar for its count; none on a healthy setup. Proposals that fail for another reason than "
            "a timeout are RemoteSignerProblem too. A node that tmkms left is not drawn. The median appears after the "
            "50th signature since the node started."
            % (s.ms(), s.hold("RemoteSignerProblem slow"),
               " Dotted (Fibre): the slowest 1% of Fibre's signatures (raw bytes) through its node." if s.fibre else "")
            + more(["RemoteSignerProblem"]),
            refs(targets), "s", threshold=(s.S, "orange"),
            overrides=styles + [
                       override("byFrameRefID", fails, color=fixed("#CCCCDC73"), unit="none", decimals=0, min=0,
                                custom__drawStyle="bars", custom__fillOpacity=80, custom__gradientMode="none",
                                custom__lineWidth=1, custom__barAlignment=-1, custom__axisPlacement="hidden",
                                max=10, custom__scaleDistribution={"type": "linear"},
                                custom__thresholdsStyle={"mode": "off"},
                                custom__hideFrom={"legend": True, "tooltip": False, "viz": False})])
        custom = latency["fieldConfig"]["defaults"]["custom"]
        # Medians near a millisecond and a rare slow signature of up to a second (the histogram's top
        # bucket) on one axis: logarithmic over three whole decades (Grafana rounds a log axis to them),
        # 0.1 ms to 100 ms, so the lines run mid-chart, the zone holds the top third and a slow median
        # stays in sight; the rare signature above 100 ms rises out of the top edge. A log axis draws
        # eight minor grid lines a decade, which stripe the zone: this chart draws none.
        custom["scaleDistribution"] = {"log": 10, "type": "log"}
        custom["axisGridShow"] = False
        latency["fieldConfig"]["defaults"].update({"min": 0.0001, "max": 0.1})
        latency["maxDataPoints"] = 150
        latency["interval"] = "1m"
        lines = [[missed_chart, latency], [props, handling]]
    # The legend entries each chart can show at once (a miss adds "missed" to the proposals), with
    # {{host}} for the node's name: one line each, so the plots of a line stay level.
    shown = {111: ["{{host}} missed, 1 h", "network, 1 h"], 116: ["{{host}} proposals", "missed", "expected"],
             115: ["{{host}}"] * len(s.nodes),
             112: ["{{host}} median", "{{host}} votes"] + (["{{host}} Fibre"] if s.fibre else [])}
    for line in lines:
        for p in line:
            check_legend("Validator chart %r" % p["title"], shown[p["id"]], 24 // len(line))
    return [(SPARK_ROWS, strip)] + [(CHART_ROWS, [(p, 24 // len(line)) for p in line]) for line in lines]


def fibre_row(s):
    """The Fibre row: one strip of six facts, then the charts that explain the Fibre answer, two by
    two. The facts carry no sparkline: in a short tile it runs under the number, and the charts below
    hold the history."""
    ok = 'fibre_server_upload_shard_duration_seconds_count{success="true"}'
    count = "fibre_server_upload_shard_duration_seconds_count"
    active = 'node_systemd_unit_state{name=%s,state="active"} == 1' % q(s.units["fibre"])
    budget = fact(
        204, "Storage budget used",
        "Share of this server's stake-based storage budget held by shards (on disk plus in flight). At 100% Fibre "
        "refuses uploads (the Fibre tile reads Storage full). It follows the whole network's Fibre data over the 4-hour "
        "retention window, so near 100% usually means network load, not a fault. No limit: unlimited, or the validator "
        "was out of the set when Fibre started." + more(pages=[FIBRE_DASHBOARD]),
        # '> 0' hides an unlimited budget and one never derived because the validator
        # was out of the active set when Fibre started; B names those as 'No limit'.
        refs([("(max by (host) (fibre_server_upload_shard_occupancy_bytes) / max by (host) "
               "(fibre_server_upload_shard_budget_bytes > 0)) and on (host) (%s)" % active, "")])
        + [target("B", "(max by (host) (fibre_server_upload_shard_budget_bytes) == 0) and on (host) (%s)" % active, "",
                  instant=True)],
        unit="percentunit", decimals=1, no_value="No active Fibre",
        overrides=[override("byFrameRefID", "B", unit="none", mappings=mapping((0, "No limit", "text")))])
    uploads = fact(
        202, "Uploads accepted, 1 h",
        "Uploads the active Fibre server accepted and signed in the last hour; Uploads and downloads below shows them "
        "over the selected time. A low number on a quiet network is normal." + more(pages=[FIBRE_DASHBOARD, TENSILE]),
        refs([("round(sum(increase(%s[1h])))" % ok, "")]), no_value="No Fibre metrics")
    success = fact(
        203, "Upload success, 1 h",
        "Share of upload requests accepted in the last hour. Failures include clients' invalid payment promises and "
        "uploads refused because the storage budget is full, so a few are not this setup's fault. Every upload "
        "failing for %s is FibreNotServing." % s.hold("FibreNotServing uploads_failing", UPLOAD_WINDOW)
        + more(["FibreNotServing"], [FIBRE_DASHBOARD]),
        refs([("sum(rate(%s[1h])) / (sum(rate(%s[1h])) > 0)" % (ok, count), "")]), unit="percentunit",
        decimals=1, no_value="No uploads")
    upload_time = fact(
        205, "Upload time",
        "How long an accepted upload took for 95 of 100 uploads over 15 minutes: checking the payment promise, "
        "storing the shard and signing it through the validator's signer. Failed uploads are left out, so quick "
        "rejections cannot hide slowness." + more(pages=[FIBRE_DASHBOARD]),
        refs([('histogram_quantile(0.95, sum by (le) (rate(fibre_server_upload_shard_duration_seconds_bucket'
               '{success="true"}[15m])))', "")]), unit="s", decimals=None,
        mappings=special_map("nan", "No uploads"))

    last_ok = "max by (host) (fibre_server_upload_shard_last_success_timestamp_seconds)"
    since = timeseries(
        210, "Since last accepted upload",
        "Is Fibre serving, not just running? Time since the server last accepted an upload, in hours. Until its first "
        "accepted upload after a start, the line is labelled no accepted upload yet and counts from Fibre start. Dashed amber "
        "line: the alert level, the time %g uploads take at this server's 7-day average and never under %g minutes; no "
        "line when the server had no upload in 7 days. FibreNotServing sends a message when the line stays above the "
        "alert level for %s. A sawtooth that drops at each upload is healthy. The axis stops at %g hours; in the first "
        "days of upload data the 7-day average is still young, so the alert level can sit above the chart. A quiet "
        "network also makes the time grow: to tell it from a fault, check on Tensile whether the network is carrying "
        "Fibre uploads." % (s.SU, s.SM / 60.0, s.hold("FibreNotServing stale"), 3 * s.SM / 3600.0)
        + more(["FibreNotServing"], [TENSILE, FIBRE_DASHBOARD]),
        # The timestamp is not reported before the first accepted upload after each Fibre
        # start, so B counts from the unit start until A has a value. Every line is in hours, so the
        # axis ticks fall on half hours.
        refs([("(time() - %s) / 3600" % last_ok, "{{host}}"),
              ("((time() - max by (host) (node_systemd_unit_start_time_seconds{name=%s} > 0)) unless on (host) %s) / 3600"
               % (q(s.units["fibre"]), last_ok), "{{host}} no accepted upload yet (since Fibre start)")])
        # The 7-day rate barely moves; a 5 min step keeps this query cheap for Prometheus.
        + [target("C", "clamp_min(%s / (sum by (host) (rate(%s[7d])) > 0), %s) / 3600" % (num(s.SU), count, num(s.SM)),
                  "{{host}} alert level", interval="5m")],
        # A hard top: the alert level counts from a 7-day rate, which starts weeks high on young data.
        "suffix: h", maximum=3 * s.SM / 3600.0, decimals=1,
        overrides=[override("byFrameRefID", "C", color=fixed("orange"), custom__lineStyle=dash([10, 10]),
                            custom__lineWidth=1)])
    last_upload = fact(
        201, "Last accepted upload",
        "When the active Fibre server last accepted an upload. Fibre pushes its metrics at its OTLP export interval "
        "(up to a minute by default), so this can be up to a minute late. None since start: Fibre sends metrics but has accepted no upload since it started. No Fibre "
        "metrics: no Fibre metrics arrive at all (the Fibre tile reads No metrics or Not running)."
        + more(["FibreNotServing"], [FIBRE_DASHBOARD]),
        one("max(fibre_server_upload_shard_last_success_timestamp_seconds) * 1000 or on () "
            "((count(fibre_server_upload_shard_budget_bytes) > 0) * 0 - 1)"),
        unit="dateTimeFromNow", mappings=mapping((-1, "None since start", "text")), no_value="No Fibre metrics")
    shards = timeseries(
        211, "Uploads and downloads",
        "Requests per minute by outcome, averaged over 30 minutes so a quiet network still draws a readable line. "
        "Accepted uploads at zero while failed or refused uploads go on for %s is FibreNotServing (uploads failing): "
        "most often Fibre still signs through a node that tmkms left (see 'Fibre signs through' in the role band). "
        "Refused uploads (dashed) mean the storage budget is full. Downloads not served (dotted) are mostly shards "
        "this server does not hold, which is normal."
        % s.hold("FibreNotServing uploads_failing", UPLOAD_WINDOW) + more(["FibreNotServing"], [FIBRE_DASHBOARD]),
        # Refused uploads also end with success "false" (celestia-app fibre/server_upload.go returns the
        # budget error through the deferred uploadShardDone), so they are taken out of the failed ones.
        refs([("sum(rate(%s[30m])) * 60" % ok, "uploads accepted"),
              ('clamp_min(sum(rate(%s{success="false"}[30m])) - (sum(rate(fibre_server_upload_shard_rejected_total[30m])) '
               "or on () vector(0)), 0) * 60" % count, "uploads failed"),
              ("sum(rate(fibre_server_upload_shard_rejected_total[30m])) * 60 or on () sum(rate(%s[30m])) * 0" % count,
               "uploads refused (budget full)"),
              ('sum(rate(fibre_server_download_shard_duration_seconds_count{success="true"}[30m])) * 60',
               "downloads served"),
              ('sum(rate(fibre_server_download_shard_duration_seconds_count{success="false"}[30m])) * 60',
               "downloads not served")]),
        "suffix:/min", soft_max=1, decimals=2, smooth=True,
        overrides=[override("byFrameRefID", "A", color=fixed("text"), custom__lineWidth=2, custom__fillOpacity=10),
                   override("byFrameRefID", "B", color=fixed("orange")),
                   override("byFrameRefID", "C", color=fixed("orange"), custom__lineStyle=dash([10, 10])),
                   override("byFrameRefID", "D", color=fixed(GREY)),
                   override("byFrameRefID", "E", color=fixed(GREY), custom__lineStyle=dash([2, 4]))])
    frees = ["node_filesystem_avail_bytes{host=%s,mountpoint=%s}" % (q(m["host"]), q(m["mount"]))
             for m in s.fibre_mounts if m["mount"]]
    disk_targets = [target("A", " or ".join(frees), "{{host}} free space")] if frees else []
    disk_targets.append(target(
        "B", "(max by (host) (fibre_server_upload_shard_budget_bytes) - max by (host) "
             "(fibre_server_upload_shard_occupancy_bytes)) and on (host) (max by (host) "
             "(fibre_server_upload_shard_budget_bytes) > 0)", "{{host}} room the budget still needs"))
    store = timeseries(
        212, "Fibre disk and budget",
        "Will the Fibre disk hold the storage budget? Solid: free space on each Fibre server's store (standby "
        "included). Dashed: the room the active Fibre's budget may still fill. The dashed line must stay below the solid "
        "one; DiskLow sends a message when it has been above for %s. With no budget, DiskLow watches %g%% free instead "
        "(Free space per data disk draws that store's free share). A read-only store shows on the server's line of the "
        "status board." % (s.hold("DiskLow budget"), s.DM * 100) + more(["DiskLow"], [FIBRE_DASHBOARD]),
        disk_targets, "bytes", overrides=[override("byFrameRefID", "B", custom__lineStyle=dash([10, 10]))])
    # One-hour windows: with sparse uploads a shorter window has no sample, and the line breaks up.
    p95 = 'histogram_quantile(0.95, sum by (le) (rate(%s{success="true"}[1h])))'
    response = timeseries(
        213, "Fibre response time",
        "How long Fibre took for 95 of 100 requests over the last hour. Upload: the whole accepted upload. Disk write: "
        "storing the shard. Signing: signing the payment promise through the node and its signer, which is part of the "
        "upload. A rising disk write points at the Fibre disk; a rising signing time at the signer or the link to it. "
        "A gap of up to an hour without uploads is bridged." + more(pages=[FIBRE_DASHBOARD]),
        refs([(p95 % "fibre_server_upload_shard_duration_seconds_bucket", "upload"),
              (p95 % "fibre_server_store_put_duration_seconds_bucket", "disk write"),
              (p95 % "fibre_server_sign_duration_seconds_bucket", "signing")]),
        "s", soft_max=0.05, span_nulls=3600000,
        overrides=[override("byFrameRefID", "A", color=fixed("text"), custom__lineWidth=2),
                   override("byFrameRefID", "B", color=fixed(GREY)),
                   override("byFrameRefID", "C", color=fixed(GREY), custom__lineStyle=dash([6, 6]))])
    prune = 'fibre_server_prune_duration_seconds_count{job="otelcol",success="false"}'
    cleanups = fact(
        214, "Failed clean-ups, 24 h",
        "Fibre removes shards older than the retention window (4 hours by default) about once a minute. Failed runs "
        "over the last 24 hours, or since monitoring started when that is less, across Fibre restarts. A failed run "
        "means old shards stay on disk, so the store fills and DiskLow fires later. 0 on a healthy server; the first "
        "failure after a Fibre start counts too (the series appears with it). Fibre's log names the error."
        + more(["DiskLow"], [FIBRE_DASHBOARD]),
        one(count_24h(prune, "sum", "otelcol")))
    facts = [uploads, success, upload_time, last_upload, budget, cleanups]
    # The longest text a time-ago value shows, which no mapping names.
    strip = fit_strip(list(zip(facts, strip_widths(len(facts)))), FACT_ROWS, "Fibre facts",
                      samples={201: ["a few seconds ago"]})
    return [(FACT_ROWS, strip)] + [(7, [(since, 12), (shards, 12)]), (7, [(response, 12), (store, 12)])]


def upgrade_row(s):
    """The collapsed Network upgrade row: the numbers behind the Network upgrade tile, in one strip of
    five facts, the support bar twice as wide in the middle."""
    pending = last(s, "celestia_mon_upgrade_pending")
    version = fact(
        104, "App version",
        "The app version the chain runs now, read through a node's RPC by celestia-mon-signal (the last reading of "
        "the past 7 days, as the upgrade alerts use).",
        one(last(s, "celestia_mon_app_version")), unit="prefix:v", no_value="Not checked")
    signal = fact(
        106, "Our signal",
        "The version this validator signalled; None until it signals. It should equal the app version plus one while "
        "a round is open (the Network upgrade tile turns amber, then red, when it does not)." + more(["UpgradeSignalMissing"]),
        one(last(s, "celestia_mon_signal_validator_version")), unit="prefix:v",
        mappings=mapping((0, "None", "text")), no_value="Not checked")
    support = bargauge(
        105, "Support for next version",
        "Share of all voting power that has signalled the next version. At 5/6 (83.3%, rounded up to whole voting "
        "power) an upgrade can be scheduled. Reads the last value of the past 7 days, as the upgrade alerts do."
        + more(["UpgradeSignalMissing"]),
        [target("A", "%s / %s" % (last(s, "celestia_mon_signal_tally_power"), last(s, "celestia_mon_signal_total_power")),
                "", instant=True)],
        "percentunit", steps((GREY, None)), no_value="Not checked")
    height = fact(
        107, "Upgrade height",
        "The block height at which the chain switches to the new version; only while an upgrade is scheduled. Reads "
        "the last value of the past 7 days, as the upgrade alerts do, so a short collector outage keeps the last "
        "reading (the Network upgrade tile says Not checked meanwhile)." + more(["UpgradeScheduled"]),
        [target("A", chain("(%s and on () (%s == 1))" % (last(s, "celestia_mon_upgrade_height"), pending),
                           "((%s == 0) * 0 - 1)" % pending), "", instant=True)],
        unit="locale", mappings=mapping((-1, "None pending", "text")), no_value="Not checked")
    eta = fact(
        108, "Time to upgrade",
        "Time until that height at the measured block time (about 2.9 s). Amber under 24 hours: every node must run a "
        "release that supports the new version, or it stops at that height. UpgradeSoon stays once sent even if slower "
        "blocks move this back above 24 hours; the Network upgrade tile keeps 'Within 24 h' then. Reads the last value "
        "of the past 7 days, as the upgrade alerts do." + more(["UpgradeScheduled", "UpgradeSoon"]),
        [target("A", chain("(%s and on () (%s == 1))" % (last(s, "celestia_mon_upgrade_eta_seconds"), pending),
                           "((%s == 1) * 0 - 2)" % pending, "((%s == 0) * 0 - 1)" % pending), "", instant=True)],
        unit="dtdurations", color_mode="value", thresholds=steps(("orange", None), ("text", 86400)),
        mappings=mapping((-1, "None pending", "text"), (-2, "Unknown", "text")), no_value="Not checked")
    # A duration as Grafana writes it, which no mapping names.
    strip = fit_strip([(version, 4), (signal, 4), (support, 8), (height, 4), (eta, 4)], FACT_ROWS,
                      "Network upgrade facts", samples={108: ["6 days, 23 hours"]})
    return [(FACT_ROWS, strip)]


def alerts_row(s):
    """The collapsed All firing alerts row: every alert firing or pending, in the notifications' sentences."""
    levels = " or ".join("%s * 0 + %s" % (by_alert('ALERTS{alertstate="%s",severity="%s"}' % (state, sev)), num(v))
                         for state, sev, v in [("firing", "page", 3), ("pending", "page", 2),
                                               ("firing", "message", 1), ("pending", "message", 0.5)])
    level = "max by (title) (%s)" % titled(s, "(%s)" % levels)
    since = ("(time() - max by (title) (%s)) and on (title) (%s)"
             % (titled(s, by_alert('ALERTS_FOR_STATE{severity=~"page|message"}')), level))
    table = panel(
        301, "table", "Firing alerts",
        "Every alert Prometheus has firing now, plus alerts whose problem is true but has not lasted long enough to be "
        "sent, in the sentences of your notifications; the Status cell at the top names the worst one. Active for: how "
        "long since the problem started, "
        "including the waiting time before it fired. The heartbeat (Watchdog) is left out. Silences do not hide alerts "
        "here. Each alert's meaning and first checks: the [alert reference](%s)." % DOCS,
        [target("A", level, "", instant=True, fmt="table"), target("B", since, "", instant=True, fmt="table")],
        {"color": {"mode": "thresholds"}, "noValue": "No alerts firing", "thresholds": steps(("text", None)),
         "custom": {"align": "auto", "cellOptions": {"type": "color-text"}, "filterable": False, "inspect": False}},
        [override("byName", "Level", custom__width=190,
                  mappings=mapping((3, "Urgent", "red"), (2, "Urgent, not paged yet", "orange"),
                                   (1, "Warning", "orange"), (0.5, "Warning, not sent yet", "orange"))),
         override("byName", "Active for", custom__width=140, unit="dtdurations", noValue="–")],
        {"cellHeight": "sm", "enablePagination": False,
         "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
         "showHeader": True, "sortBy": [{"desc": True, "displayName": "Level"}]})
    # With nothing firing, Prometheus still returns empty frames with value fields, and a table would
    # show bare column headers. Grouping by Alert drops a frame without that field, so the panel has
    # no data and shows its no-value text; with alerts it changes nothing (one row per sentence), and
    # the last step takes the reducer's name back off the columns.
    table["transformations"] = [
        {"id": "merge", "options": {}},
        {"id": "organize", "options": {"excludeByName": {"Time": True},
                                       "indexByName": {"title": 0, "Value #A": 1, "Value #B": 2},
                                       "renameByName": {"title": "Alert", "Value #A": "Level",
                                                        "Value #B": "Active for"}}},
        {"id": "groupBy", "options": {"fields": {
            "Alert": {"aggregations": [], "operation": "groupby"},
            "Level": {"aggregations": ["max"], "operation": "aggregate"},
            "Active for": {"aggregations": ["max"], "operation": "aggregate"}}}},
        {"id": "renameByRegex", "options": {"regex": "^(Level|Active for) \\(.*\\)$", "renamePattern": "$1"}}]
    return [(6, [(table, 24)])]


def servers_row(s):
    """The collapsed Servers in detail row: the history behind the Servers now table."""
    nic = "{device!~%s}" % q(VIRT)
    traffic = [("sum by (host) (rate(node_network_receive_bytes_total%s[5m])) * 8" % nic, "{{host}} in"),
               ("sum by (host) (rate(node_network_transmit_bytes_total%s[5m])) * 8" % nic, "{{host}} out")]
    desc = ("What is using each server's link over time? Traffic of the whole machine, not just Celestia, in bits per "
            "second to compare with your link speed: receive above zero, transmit below, as 5-minute averages. The "
            "Servers now table's Network peak has the busiest minute of the last hour.")
    overrides = []
    if s.fibre:
        traffic += [("sum by (host) (rate(fibre_server_upload_shard_bytes_total[5m])) * 8", "{{host}} Fibre shards accepted"),
                    ("sum by (host) (rate(fibre_server_download_shard_bytes_total[5m])) * 8", "{{host}} Fibre shards served")]
        desc += (" Fibre pushes its metrics up to a minute apart, so every line uses the same 5-minute window. The dashed "
                 "Fibre lines count "
                 "shard row data that was signed, including re-uploads of a shard already held, without proofs, RLC or "
                 "rejected uploads. They are drawn over the NIC line, not subtracted from it; the gap covers consensus "
                 "P2P, RPC, row proofs and RLC, TLS, and rejected uploads.")
        overrides = [override("byFrameRefID", r, custom__lineStyle=dash([10, 10]),
                              custom__hideFrom={"legend": True, "tooltip": False, "viz": False}) for r in "CD"]
    desc += more(pages=[NODE_EXPORTER_FULL])
    network = timeseries(401, "Network traffic", desc, refs(traffic), "bps", overrides=overrides,
                         mirror=["B", "D"] if s.fibre else ["B"])
    # Idle and iowait are idle time; steal counts as busy.
    cpu = timeseries(
        402, "CPU busy",
        "Is a server short of CPU? Share of all cores busy, 5-minute average; on a VM, steal counts as busy. A validator "
        "host near 100% votes late and adds to signer latency; a Fibre host near 100% slows uploads: size the host up "
        "or move Fibre off it. The KMS host appears only with celestia_mon_kms_scrape. One pegged core may not show: "
        "see Node Exporter Full. No alert watches CPU; the Servers now table shows it in grey."
        + more(pages=[NODE_EXPORTER_FULL]),
        refs([('1 - sum by (host) (rate(node_cpu_seconds_total{mode=~"idle|iowait"}[5m])) '
               '/ count by (host) (node_cpu_seconds_total{mode="idle"})', "{{host}}")]),
        "percentunit", maximum=1)
    memory = timeseries(
        403, "Memory used",
        "Share of memory in use per server. Fibre can use about 27 GiB at its default limits. A marker shows each time "
        "the kernel killed a process for lack of memory; journalctl -k names it." + more(pages=[NODE_EXPORTER_FULL]),
        refs([("1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes", "{{host}}")]),
        "percentunit", maximum=1)
    dev = "{device!~%s}" % q(DISK)
    # Both directions together; '> 0' leaves a gap instead of 0/0 while a disk is idle.
    latency = ("(rate(node_disk_read_time_seconds_total%s[5m]) + rate(node_disk_write_time_seconds_total%s[5m])) / "
               "((rate(node_disk_reads_completed_total%s[5m]) + rate(node_disk_writes_completed_total%s[5m])) > 0)"
               % (dev, dev, dev, dev))
    disk_latency = timeseries(
        404, "Disk latency",
        "Average time one read or write took, per physical disk. Above about 10 ms on NVMe the disk is overloaded (give "
        "Fibre its own disk) or the provider's storage is slow. LVM, RAID, ZFS volumes and CD drives are left out; their "
        "physical disks count." + more(pages=[NODE_EXPORTER_FULL]),
        refs([(latency, "{{host}} {{device}}")]), "s", soft_max=0.01)
    free = [(free_share([m]), "{{host}} {{mountpoint}}") for m in s.data_mounts]
    free += [(free_share([m]), "{{host}} {{mountpoint}} (Fibre store)") for m in s.stores]
    middle = [memory, disk_latency]
    if free:
        middle.append(timeseries(
            405, "Free space per data disk",
            "Free space on each data disk you listed and on each Fibre store. Dashed amber: %g%% free, where DiskLow "
            "sends a message after %s for a data disk, and for a Fibre store only while Fibre reports no storage budget "
            "(with a budget, Fibre disk and budget shows the rule that watches it). On servers with celestia-appd or "
            "tmkms, DiskLow also pages below %g%% free or when the disk turns read-only (the Servers now table turns the "
            "disk cell red); on Fibre-only servers, read-only sends a message. A read-only disk shows as 'Read-only' in "
            "the Servers now table." % (s.DM * 100, s.hold("DiskLow low space"), s.DP * 100)
            + more(["DiskLow"], [NODE_EXPORTER_FULL]),
            refs(free), "percentunit", threshold=(s.DM, "orange"), maximum=1))
    load = "rate(node_disk_read_bytes_total%s[5m]) + rate(node_disk_written_bytes_total%s[5m])" % (dev, dev)
    rw = timeseries(
        406, "Disk reads and writes",
        "Bytes read and written per disk. High latency with high throughput: something loads the disk. High latency with "
        "little throughput: the disk or the provider's storage is slow." + more(pages=[NODE_EXPORTER_FULL]),
        refs([(load, "{{host}} {{device}}")]), "Bps")
    clock = timeseries(
        407, "Clock offset",
        "How far each server's clock is from the time servers it syncs to. Vote timestamps come from the signer's clock, "
        "so skew shifts block times and quorum delays; Fibre refuses payment promises more than 10 minutes off. Anything "
        "above a few tens of milliseconds means time sync (chrony or systemd-timesyncd) is not working."
        + more(pages=[NODE_EXPORTER_FULL]),
        refs([("max by (host) (node_timex_offset_seconds)", "{{host}}")]), "s", soft_min=-0.05, soft_max=0.05,
        centered=True)
    # Two charts a line are h7, three h8, as flow() lays them out.
    return [(7, [(network, 12), (cpu, 12)])] + flow(middle) + [(7, [(rw, 12), (clock, 12)])]


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


def annotations(s, ids):
    """Markers on the charts, each kept off the status board by its panel list; their toggles are hidden.
    Routine markers (starts, repoints, announcements, upgrades) are grey; only Out of memory is amber."""
    def note(name, expr, title, color, panel_ids):
        return {"datasource": s.ds, "enable": True, "expr": expr,
                "filter": {"exclude": False, "ids": sorted(i for i in panel_ids if i in ids)},
                "hide": True, "iconColor": color, "name": name, "step": "60s", "titleFormat": title}
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
        # Comparing with the value one step earlier catches every start once at a 60 s step and
        # ignores stops (the start time drops to 0).
        note("Service starts", "node_systemd_unit_start_time_seconds > node_systemd_unit_start_time_seconds offset 1m",
             "{{name}} started on {{host}}", GREY, [111, 112, 115, 116, 210, 211, 212, 213] + list(range(401, 408))),
        note("Signing through", moved, "Signing through {{host}}", GREY, [25, 111, 112, 115, 116]),
        note("Out of memory", "node_vmstat_oom_kill > node_vmstat_oom_kill offset 1m", "Out of memory on {{host}}",
             "orange", [403]),
    ]
    if s.announcements:
        # The counter starts at 0 for every channel when celestia-mon-announcements starts.
        notes.append(note("Celestia announcement", "celestia_mon_announcements_forwarded_total > "
                          "celestia_mon_announcements_forwarded_total offset 1m",
                          "Celestia announcement (the text is in your alert channel)", GREY, [25] + charts))
    if s.signal:
        notes.append(note("Network upgrade", "celestia_mon_app_version > celestia_mon_app_version offset 2m",
                          "Network upgrade: new app version", GREY, [25] + charts))
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
    sections = [(None, first_screen(s)), ((100, "Validator", False), validator_row(s))]
    if s.fibre:
        sections.append(((200, "Fibre", False), fibre_row(s)))
    if s.signal:
        sections.append(((250, "Network upgrade", True), upgrade_row(s)))
    sections.append(((300, "All firing alerts", True), alerts_row(s)))
    sections.append(((400, "Servers in detail", True), servers_row(s)))
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
    check_variables(every)
    ids = {p["id"]: p["type"] for p in every}

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
        "annotations": {"list": annotations(s, ids)},
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
