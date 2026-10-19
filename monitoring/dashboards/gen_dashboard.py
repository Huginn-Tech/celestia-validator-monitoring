#!/usr/bin/env python3
"""Generate celestia-mon's Grafana dashboard from the inventory settings.

    gen_dashboard.py --config <settings.json> --out <dashboard.json>

The settings file is rendered by the celestia_mon_grafana Ansible role from
ansible/roles/celestia_mon_grafana/templates/dashboard-config.json.j2:

    {
      "datasource_uid": "celestia-mon-prometheus",
      "signer": "local" | "remote",
      "fibre_enabled": true | false,
      "units": {"appd": "...", "fibre": "...", "tmkms": "..."},
      "thresholds": {...},                         # celestia_mon_thresholds
      "nodes": ["..."],                            # celestia-appd hosts: validator, then backup_node
      "fibre_mounts": [{"host": "...", "mount": "..."}],  # every fibre host, active or standby
      "fibre_endpoints": [{"host": "...", "endpoint": "..."}],  # every fibre host; celestia_mon_fibre_public_endpoint or ""
      "data_mounts": [{"host": "...", "mount": "..."}],
      "links": [{"host": "...", "mbps": 1000}]     # every node_exporter host; mbps: celestia_mon_link_mbps or null
    }

Layout: a 'Now' strip without a row header, then the rows 'Validator and signer',
'Fibre' and 'Hosts'. Panels that do not apply are left out: no signer panels with
a local signer, no Fibre panels without Fibre, no node lag chart with one node. A row
whose panels are wider than the 24 grid columns continues on a second line.

Roles can move between hosts (a backup node takes over signing after a tmkms repoint,
a standby Fibre is started on another host), so no query takes the host that holds a
role from the settings: the signing node is the one the signatures go through, and
the active Fibre is the one whose unit systemd reports active. Host names from the
settings appear in queries only as display text (the Signing tile) and to pair each
Fibre host with its public endpoint.

Python 3 standard library only. The same settings always give the same bytes.
Every query is plain PromQL (no Grafana variables), so CI can send each one to
Prometheus as it is (tools/ci/check_queries.py).
"""
import argparse
import json
import re

UID = "celestia-mon-validator"
TITLE = "celestia-mon: Celestia validator"

FIBRE_LINKS = [
    ("Fibre dashboard (celestia-app)",
     "https://github.com/celestiaorg/celestia-app/blob/v10.4.0/observability/docker/grafana/dashboards/fibre.json"),
    ("Fibre runtime dashboard (celestia-app)",
     "https://github.com/celestiaorg/celestia-app/blob/v10.4.0/observability/docker/grafana/dashboards/fibre-runtime.json"),
    ("Tensile: Fibre from outside", "https://tensile.huginn.tech"),
]
HOSTS_LINKS = [
    ("Node Exporter Full (grafana.com 1860)", "https://grafana.com/grafana/dashboards/1860-node-exporter-full/"),
]

# Interfaces left out of NIC traffic: loopback, containers, bridges, VPNs and tunnels,
# bond and team masters (their member NICs count) and VLAN sub-interfaces (their parent counts).
VIRT = (r"lo|docker.*|veth.*|br.*|vmbr.*|virbr.*|cni.*|flannel.*|cali.*|cilium.*|vxlan.*|kube.*"
        r"|tailscale.*|wg.*|tun.*|tap.*|bond.*|team.*|.+\..+")

# Block devices left out of disk latency: LVM/LUKS (dm), software RAID (md) and ZFS volumes (zd)
# count the I/O of their physical disks again; CD drives (sr) and network block devices (nbd)
# are noise. node_exporter already leaves out partitions, loop, ram and zram devices.
DISK = r"dm-.*|md.*|zd.*|sr.*|nbd.*"

# One fixed colour per node_exporter host, in inventory order, when there is more than
# one. Green, orange and red stay free for thresholds. Hosts past the list keep
# Grafana's palette rather than repeat a colour.
HOST_COLORS = ["blue", "purple", "yellow", "light-blue", "light-purple", "light-yellow"]

# A standby Fibre unit, or a check that does not run: grey, neither fine-green nor alarm-red.
GREY = "#808080"

# Block height of every celestia-appd node: the validator and the backup nodes.
HEIGHT = 'cometbft_consensus_height{job="celestia-app"}'

# True while the validator's key is in the active set: a node recorded a block for it in the
# last 2 minutes, as a signature (the last signed height changes) or a miss (the missed-block
# count grows). celestia-core sets the validator's voting power, last signed height and missed
# blocks only while its key is in the set (consensus/state.go recordMetrics) and never writes a
# power of 0, so after a jail or an unbond the power keeps its last value; these two show it.
IN_SET = ('(max(changes(cometbft_consensus_validator_last_signed_height{job="celestia-app"}[2m])) > 0 '
          'or max(increase(cometbft_consensus_validator_missed_blocks{job="celestia-app"}[2m])) > 0)')


def q(value):
    """A PromQL string literal."""
    return json.dumps(value)


def num(value):
    """A number as PromQL and Grafana expect it."""
    return json.dumps(value)


def js_regex(text):
    """text with JavaScript regex metacharacters escaped."""
    return re.sub(r"[\\^$.*+?()\[\]{}|/]", lambda m: "\\" + m.group(0), text)


def label(expr, name, value):
    """expr with the label name set to the fixed text value ('$' kept as it is)."""
    return 'label_replace(%s, %s, %s, "", "")' % (expr, q(name), q(value.replace("$", "$$")))


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
    """Value mappings from (value, text, color) triples."""
    return [{"type": "value",
             "options": {str(v): {"color": c, "index": i, "text": t} for i, (v, t, c) in enumerate(items)}}]


def override(matcher, option, **props):
    """A field override. Property names use '__' for '.', e.g. custom__axisPlacement."""
    return {"matcher": {"id": matcher, "options": option},
            "properties": [{"id": k.replace("__", "."), "value": v} for k, v in props.items()]}


def timeseries(pid, title, desc, targets, unit, line=None, overrides=(), mirror=(), maximum=None, decimals=None):
    """Lines over time. line: a threshold drawn as a dashed red line.
    mirror: refIds drawn below zero (receive above, transmit below); the legend keeps
    their positive values and becomes a table with Max and Last.
    maximum: top of the axis (e.g. 1 for a share); decimals: fixed decimals of the values."""
    custom = {"drawStyle": "line", "fillOpacity": 0, "lineWidth": 1, "showPoints": "never", "spanNulls": False,
              "thresholdsStyle": {"mode": "dashed" if line is not None else "off"}}
    defaults = {"color": {"mode": "palette-classic"}, "custom": custom, "min": 0, "unit": unit,
                "thresholds": steps(("green", None), ("red", line)) if line is not None else steps(("green", None))}
    if maximum is not None:
        defaults["max"] = maximum
    if decimals is not None:
        defaults["decimals"] = decimals
    legend = {"displayMode": "list", "placement": "bottom", "showLegend": True}
    if mirror:
        del defaults["min"]
        custom["axisCenteredZero"] = True
        overrides = [override("byFrameRefID", r, custom__transform="negative-Y") for r in mirror] + list(overrides)
        legend = {"calcs": ["max", "last"], "displayMode": "table", "placement": "bottom", "showLegend": True}
    options = {"legend": legend, "tooltip": {"mode": "multi", "sort": "desc"}}
    return panel(pid, "timeseries", title, desc, targets, defaults, overrides, options)


def stat(pid, title, desc, targets, thresholds, overrides=(), unit="none", decimals=0, graph="none",
         no_value=None, mappings=()):
    """Current values, one tile per series, coloured by thresholds or value mappings.
    graph: "area" draws a sparkline over the dashboard range (needs range queries)."""
    defaults = {"color": {"mode": "thresholds"}, "decimals": decimals, "thresholds": thresholds, "unit": unit}
    if no_value is not None:
        defaults["noValue"] = no_value
    if mappings:
        defaults["mappings"] = list(mappings)
    options = {"colorMode": "value", "graphMode": graph, "justifyMode": "auto", "orientation": "auto",
               "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
               "showPercentChange": False, "textMode": "value_and_name", "wideLayout": True}
    return panel(pid, "stat", title, desc, targets, defaults, overrides, options)


def bargauge(pid, title, desc, targets, unit, thresholds, overrides=(), decimals=1, mappings=()):
    defaults = {"color": {"mode": "thresholds"}, "decimals": decimals, "max": 1, "min": 0,
                "thresholds": thresholds, "unit": unit}
    if mappings:
        defaults["mappings"] = list(mappings)
    options = {"displayMode": "basic", "namePlacement": "top", "orientation": "horizontal",
               "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
               "showUnfilled": True, "sizing": "auto", "valueMode": "color"}
    return panel(pid, "bargauge", title, desc, targets, defaults, overrides, options)


def host_table(pid, title, desc, columns):
    """One row per host. columns: (header, expr, overrides) per column; each expr is an
    instant query aggregated 'by (host)'. Grafana names the value column of query X
    'Value #X'; merge joins the queries' rows on host, organize names the columns.
    Each column's colours come from its overrides (properties of an override by name)."""
    refids = [chr(65 + i) for i in range(len(columns))]
    targets = [target(r, expr, "", instant=True, fmt="table") for r, (_, expr, _) in zip(refids, columns)]
    index = {"host": 0}
    rename = {"host": "Node"}
    overrides = [override("byName", "Node", custom__cellOptions={"type": "auto"})]
    for i, (r, (header, _, props)) in enumerate(zip(refids, columns)):
        index["Value #" + r] = i + 1
        rename["Value #" + r] = header
        overrides.append(override("byName", header, **props))
    # Grafana's default minimum column width (150 px) would not fit a narrow tile.
    defaults = {"color": {"mode": "thresholds"}, "decimals": 0, "unit": "none", "thresholds": steps(("text", None)),
                "custom": {"align": "auto", "cellOptions": {"type": "color-text"}, "inspect": False, "minWidth": 40}}
    options = {"cellHeight": "sm", "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
               "frameIndex": 0, "showHeader": True, "sortBy": [{"desc": False, "displayName": "Node"}]}
    p = panel(pid, "table", title, desc, targets, defaults, overrides, options)
    p["transformations"] = [{"id": "merge", "options": {}},
                            {"id": "organize", "options": {"excludeByName": {"Time": True},
                                                           "indexByName": index, "renameByName": rename}}]
    return p


def panel(pid, kind, title, desc, targets, defaults, overrides, options):
    return {"id": pid, "type": kind, "title": title, "description": desc, "targets": targets,
            "fieldConfig": {"defaults": defaults, "overrides": list(overrides)}, "options": options}


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


def blocks_since_signature(cap):
    """Blocks since the validator's last signature on any node, as ValidatorNotSigning computes
    it, capped at cap: the highest height of all nodes minus the highest last signed height.
    Only the node the signer is connected to reports a last signed height, so the value follows
    the signer from node to node. Until the first signature after a start, it is the full height."""
    return ("clamp_max(max(%s) - (max(cometbft_consensus_validator_last_signed_height{job=\"celestia-app\"}) "
            "or max(%s) * 0), %s)" % (HEIGHT, HEIGHT, num(cap)))


def signing_nodes(cfg):
    """The node or nodes the validator signs through now, one series per host. With a remote signer:
    the nodes whose privval client made a signature in the last 2 minutes. A node that tmkms left
    keeps the validator's key and reads its signatures from the chain's commits, so the last signed
    height cannot tell which node signs. With a local key: the node whose last signed height
    advanced in the last 2 minutes."""
    if cfg["signer"] == "remote":
        return 'sum by (host) (increase(cometbft_privval_signing_latency_seconds_count{job="celestia-app"}[2m])) > 0'
    return 'max by (host) (changes(cometbft_consensus_validator_last_signed_height{job="celestia-app"}[2m])) > 0'


def signing_tile(cfg, signing):
    """The Signing tile: blocks since the last signature, the active set, and the node that signs."""
    blocks = cfg["thresholds"]["not_signing_blocks"]
    remote = cfg["signer"] == "remote"
    nodes = cfg.get("nodes", [])
    power = 'max(cometbft_consensus_validator_power{job="celestia-app"})'
    tiles = [
        (blocks_since_signature(10 * blocks), "blocks since my last signature",
         {"mappings": [{"type": "range", "options": {"from": 10 * blocks, "to": 10 * blocks, "result": {
             "color": "red", "index": 0, "text": "%g+" % (10 * blocks)}}}]}),
        ("%s and on () %s" % (power, IN_SET), "in active set, voting power",
         {"unit": "locale", "thresholds": steps(("green", None))}),
        # Only a node that saw the validator's key in the set since its start has a power series.
        ("%s * 0 unless on () %s and on () changes(max(%s)[2m:]) > 0" % (power, IN_SET, HEIGHT), "active set",
         {"mappings": mapping((0, "not in active set", "red"))}),
    ]
    if nodes:
        # The signing node as its position in the settings' node list, which a value mapping
        # turns into its host name: the big text of a tile is its value.
        lookup = " or ".join(label("vector(%d)" % (i + 1), "host", h) for i, h in enumerate(nodes))
        signer = "(%s) and on (host) (%s)" % (lookup, signing)
        names = [(i + 1, h, "green") for i, h in enumerate(nodes)]
        if remote:
            # A validator out of the active set asks for no signatures, so only a validator in
            # the set without signatures reads 'no signer connected'.
            signer += " or (vector(0) and on () %s unless on () count(%s))" % (IN_SET, signing)
            names = [(0, "no signer connected", "red")] + names
        tiles.append((signer, "signing on", {"mappings": mapping(*names)}))
    targets = refs([(expr, legend) for expr, legend, _ in tiles], instant=True)
    signer_desc = (
        "the node whose privval client made a signature in the last 2 minutes; red 'no signer connected' while "
        "the validator is in the active set and no node made one" if remote else
        "the node whose last signed height advanced in the last 2 minutes")
    # Red from one block above the threshold, because ValidatorNotSigning fires on '> not_signing_blocks'.
    return stat(16, "Signing",
                "Is my validator in the active set and signing right now, and through which node? Blocks since "
                "the validator's last signature, counted across all nodes as ValidatorNotSigning does, so it "
                "follows a move of the signer to a backup node; red above the ValidatorNotSigning threshold "
                "(%g blocks), history in Blocks since my last signature. Active set: the voting power while a "
                "node records each block for the validator's key, as a signature or a miss; red 'not in active "
                "set' while the chain moves and no node does: the validator is jailed, unbonded or outside the "
                "active set. Signing on: %s." % (blocks, signer_desc),
                targets, steps(("green", None), ("red", blocks + 1)),
                [override("byFrameRefID", tg["refId"], **props) for tg, (_, _, props) in zip(targets, tiles)])


def fibre_active(cfg):
    """One series per host whose Fibre unit systemd reports active, labelled host only."""
    return 'max by (host) (node_systemd_unit_state{name=%s,state="active"} == 1)' % q(cfg["units"]["fibre"] + ".service")


def roles(cfg, signing):
    """(expr, mappings) for one role tile per server, read from the metrics: the Validator (signing)
    while the signer signs through the node, a Backup node while another node signs, Node (not
    signing) while no node signs; Fibre (active) while the host's Fibre unit is active, else Fibre
    (standby). A host's parts add up to one code (node 1-3, Fibre 4 or 8), which the mappings name."""
    nodes = 'max by (host) (up{job="celestia-app"})'
    # 'or' keeps the first series per host: the signing node, then the others.
    node = "(%s) * 0 + 1 or %s * 0 + 2 and on () count(%s) or %s * 0 + 3" % (signing, nodes, signing, nodes)
    node_parts = [(1, "Validator (signing)", "green"), (2, "Backup node", "text"),
                  (3, "Node (not signing)", "red")]
    if not cfg["fibre_enabled"]:
        return node, mapping(*node_parts)
    # Every Fibre host has a collector target, so up{job="otelcol"} lists the active one and the standbys.
    fibre = '%s * 0 + 4 or max by (host) (up{job="otelcol"}) * 0 + 8' % fibre_active(cfg)
    fibre_parts = [(4, "Fibre (active)", "green"), (8, "Fibre (standby)", GREY)]
    # A server with both roles names both, coloured by the part that matters most.
    rank = ["red", "green", "text", GREY]
    items = node_parts + fibre_parts + [(n + f, "%s, %s" % (nt, ft), min(nc, fc, key=rank.index))
                                        for n, nt, nc in node_parts for f, ft, fc in fibre_parts]
    return ("sum by (host) (%s or %s)" % (label(node, "part", "node"), label(fibre, "part", "fibre")),
            mapping(*items))


def fibre_endpoint(cfg):
    """The Fibre endpoint tile, from celestia-mon-tensile on the monitor (job "tensile"), which reads
    Tensile's check of the validator's registered Fibre endpoint from outside."""
    reg = "max by (endpoint) (celestia_mon_fibre_registered == 1)"
    active = fibre_active(cfg)
    tiles = [
        # A registration Tensile has not checked yet reads -1.
        ("max by (endpoint) (celestia_mon_fibre_reachable and on (endpoint) %s) or %s * 0 - 1" % (reg, reg),
         "{{endpoint}}",
         {"mappings": mapping((1, "reachable from outside", "green"), (0, "unreachable", "red"),
                              (-1, "not checked yet", GREY))}),
        # Only an endpoint Tensile reached can show its identity.
        ("max by (endpoint) (celestia_mon_fibre_identity_verified "
         "and on (endpoint) celestia_mon_fibre_reachable == 1 and on (endpoint) %s)" % reg, "TLS identity",
         {"mappings": mapping((1, "verified", "green"), (0, "not verified", "red"))}),
    ]
    endpoints = [(e["host"], e["endpoint"]) for e in cfg.get("fibre_endpoints", []) if e.get("endpoint")]
    if endpoints:
        # The registration against the public endpoint of the host whose Fibre unit is active, while
        # exactly one is and it has one configured: group_left needs that single host.
        lookup = " or ".join(label(label("vector(1)", "host", h), "endpoint", ep) for h, ep in endpoints)
        tiles.append(("(%s unless on (endpoint) ((%s) and on (host) %s)) * on () group_left (host) "
                      "(%s and on (host) (%s) and on () count(%s) == 1)"
                      % (reg, lookup, active, active, lookup, active),
                      "points to {{endpoint}}, Fibre runs on {{host}}",
                      {"mappings": mapping((1, "wrong endpoint", "red"))}))
    tiles += [
        ('max(celestia_mon_fibre_registered{endpoint=""} == 0) '
         "unless on () count(celestia_mon_fibre_registered == 1)",
         "registered endpoint", {"mappings": mapping((0, "none", "red"))}),
        ("time() - max by (endpoint) (celestia_mon_fibre_last_check_timestamp_seconds > 0 "
         "and on (endpoint) %s)" % reg,
         "since last outside check", {"unit": "s"}),
        # celestia-mon-tensile is optional (celestia_mon_tensile): without it there is no tensile job.
        ('absent(up{job="tensile"}) * -1 or (max(up{job="tensile"}) == 0) - 2', "outside check",
         {"mappings": mapping((-1, "not checked (Tensile not configured)", GREY),
                              (-2, "celestia-mon-tensile not reporting", "red"))}),
    ]
    targets = refs([(expr, legend) for expr, legend, _ in tiles], instant=True)
    return stat(19, "Fibre endpoint",
                "Is the Fibre endpoint registered on chain reachable from outside, and is it the active Fibre "
                "server's? From Tensile (https://tensile.huginn.tech), which dials every validator's registered "
                "endpoint from outside, read by celestia-mon-tensile on the monitor. The first tile names the "
                "registered host:port: green 'reachable from outside' or red 'unreachable' at Tensile's last "
                "check. TLS identity: whether the endpoint's TLS identity is endorsed by the validator's consensus "
                "key. Red 'wrong endpoint' while the registration does not name the public endpoint "
                "(celestia_mon_fibre_public_endpoint) of the host whose Fibre unit is active, and red 'none' while "
                "nothing is registered. Grey 'not checked (Tensile not configured)' without celestia_mon_tensile.",
                targets, steps(("text", None)),
                [override("byFrameRefID", tg["refId"], **props) for tg, (_, _, props) in zip(targets, tiles)],
                no_value="No data from celestia-mon-tensile yet")


def node_progress(cfg):
    """Node progress: with one node, tiles for that node; with backup nodes, one row per node."""
    if len(cfg.get("nodes", [])) < 2:
        return stat(1, "Node progress",
                    "Is the node committing new blocks, and is it peered? New blocks committed locally in the last "
                    "3 minutes, block sync and peers. This shows that the node is advancing, not that it is at the "
                    "network's head: compare its height with an explorer or status.celestia.org. NodeNotAdvancing "
                    "pages when no new block arrives for 3 minutes. If the whole chain stopped, coordinate instead "
                    "of fixing locally.",
                    refs([("changes(cometbft_consensus_height[3m])", "new blocks (3 min)"),
                          ("cometbft_blocksync_syncing", "block sync"),
                          ("cometbft_p2p_peers", "peers")], instant=True),
                    steps(("red", None), ("green", 1)),
                    [override("byFrameRefID", "B", mappings=mapping((0, "no", "green"), (1, "yes", "orange")))])
    behind = cfg["thresholds"].get("node_behind_blocks", 20)
    # A node Prometheus cannot read reads 'down' (-1) at once; its other cells stay empty.
    blocks = ('max by (host) (up{job="celestia-app"} == 0) - 1 '
              "or max by (host) (changes(%s[3m]))" % HEIGHT)
    return host_table(
        1, "Node progress",
        "Is every node committing new blocks, in step with the others, and peered? One row per node: the "
        "validator and each backup node. Blocks: new blocks the node committed in the last 3 minutes; red at 0, "
        "'down' while Prometheus cannot read the node's metrics. Lag: blocks behind the highest node of this "
        "install; a backup node must stay near 0 to take over signing. Orange above %g, where NodeBehind sends a "
        "message after 10 minutes; a node in block sync also lags; history in Blocks behind the highest node. "
        "Peers: red at 0. This shows that the nodes "
        "advance, not that they are at the network's head: compare with an explorer or status.celestia.org. "
        "NodeNotAdvancing pages when no node commits a block for 3 minutes. If the whole chain stopped, "
        "coordinate instead of fixing locally." % behind,
        [("Blocks", blocks,
          {"thresholds": steps(("red", None), ("green", 1)), "mappings": mapping((-1, "down", "red"))}),
         ("Lag", "scalar(max(%s)) - max by (host) (%s)" % (HEIGHT, HEIGHT),
          {"thresholds": steps(("green", None), ("orange", behind + 1))}),
         ("Peers", 'max by (host) (cometbft_p2p_peers{job="celestia-app"})',
          {"thresholds": steps(("red", None), ("green", 1))})])


def now_strip(cfg):
    """(panel, width, links) for the top strip."""
    fibre = cfg["fibre_enabled"]
    signing = signing_nodes(cfg)
    role, role_names = roles(cfg, signing)
    loads = []
    for link in cfg["links"]:
        sel = "{host=%s,device!~%s}" % (q(link["host"]), q(VIRT))
        cap = link_capacity(link, sel)
        # A host that is up but has no known speed shows -1, mapped to a hint, so its bar stays visible.
        unknown = 'max by (host) (up{job="node",host=%s} == 1) * 0 - 1' % q(link["host"])
        for direction, metric in (("in", "receive"), ("out", "transmit")):
            peak = "max_over_time((sum by (host) (rate(node_network_%s_bytes_total%s[1m])))[1h:1m])" % (metric, sel)
            loads.append(("%s / %s or %s" % (peak, cap, unknown), "{{host}} %s" % direction))
    states = mapping((0, "not active", "red"), (1, "active", "green"))
    units = 'node_systemd_unit_state{state="active"}'
    units_desc = ""
    if fibre:
        # A stopped Fibre unit is a standby (2) while another Fibre unit is active, else
        # 'none active' (3). Other units keep 0 or 1. node_exporter lists loaded units only, and
        # systemd unloads a stopped, disabled standby, so a Fibre host (one with a collector
        # target) whose node_exporter answers but lists no Fibre unit gets the same tile.
        name = cfg["units"]["fibre"] + ".service"
        fibre_unit = 'node_systemd_unit_state{state="active",name=%s}' % q(name)
        unloaded = ('(label_replace(max by (host) (up{job="node"} == 1 and on (host) up{job="otelcol"}), '
                    '"name", %s, "", "") * 0 unless on (host) %s)' % (q(name), fibre_unit))
        units = ('node_systemd_unit_state{state="active",name!=%s} or (%s == 1) '
                 "or (%s + 2 and on () count(%s == 1)) or (%s + 3) "
                 "or (%s + 2 and on () count(%s == 1)) or (%s + 3)"
                 % (q(name), fibre_unit, fibre_unit, fibre_unit, fibre_unit,
                    unloaded, fibre_unit, unloaded))
        states = mapping((0, "not active", "red"), (1, "active", "green"), (2, "standby", GREY),
                         (3, "none active", "red"))
        units_desc = (" A stopped Fibre unit reads 'standby' while another host's Fibre unit is active, and "
                      "'none active' when no Fibre unit is active (FibreDown pages after 10 minutes); so does "
                      "a Fibre host that lists no Fibre unit, as systemd unloads a stopped, disabled standby.")
    fibre_roles = (" A Fibre host is Fibre (active) while its Fibre unit is active, else Fibre (standby); "
                   "a server with both a node and a Fibre names both roles." if fibre else "")
    strip = [
        (node_progress(cfg), 4, []),
        (signing_tile(cfg, signing), 4, []),
        (stat(8, "Services per host",
              "Which role does each server hold now, are celestia-appd, Fibre and tmkms running, and how often "
              "did systemd restart them in the last 24 hours? The role tiles come first and are read from the "
              "metrics, so they follow a tmkms repoint or a Fibre failover: a node is the Validator (signing) "
              "while the signer signs through it (as Signing on in the Signing tile), a Backup node while "
              "another node signs, and red Node (not signing) while no node signs.%s Restarts count only "
              "automatic restarts under Restart=.%s A 'not reporting' tile appears while Prometheus cannot read "
              "a host's metrics (celestia-app, node_exporter or the Fibre collector); that host's other tiles "
              "are then missing, not fine." % (fibre_roles, units_desc),
              refs([(role, "{{host}} role"),
                    (units, "{{host}} {{name}}"),
                    ("increase(node_systemd_service_restart_total[24h])", "{{host}} {{name}} restarts (24h)"),
                    ('up{job=~"celestia-app|node|otelcol"} == 0', "{{host}} {{job}} metrics")],
                   instant=True),
              steps(("green", None), ("orange", 1)),
              [override("byFrameRefID", "A", mappings=role_names),
               override("byFrameRefID", "B", mappings=states),
               override("byFrameRefID", "D", mappings=mapping((0, "not reporting", "red")))]),
         7 if fibre else 9, HOSTS_LINKS),
        (bargauge(11, "Link load (busiest minute, last hour)",
                  "Busiest 1-minute average in the last hour, against celestia_mon_link_mbps or, when that is not set, "
                  "the NIC port speed. Port speed can be above what your provider lets through. Counts every "
                  "physical NIC; VPN, container, bridge, bond master and VLAN interfaces are left out. "
                  "Current load over time: Network traffic.",
                  refs(loads, instant=True), "percentunit",
                  steps(("green", None), ("orange", 0.7), ("red", 0.9)), decimals=0,
                  mappings=[{"type": "range", "options": {"from": -1, "to": -1, "result": {
                      "color": "text", "index": 0, "text": "speed unknown: set celestia_mon_link_mbps"}}}]),
         3 if fibre else 7, HOSTS_LINKS),
    ]
    if fibre:
        active = 'node_systemd_unit_state{name=%s,state="active"} == 1' % q(cfg["units"]["fibre"] + ".service")
        strip.append(
            (stat(12, "Fibre storage budget used",
                  "Which host runs the active Fibre, and how full is its storage budget? The tile names the host "
                  "whose Fibre unit systemd reports active (Active Fibre) and follows it when a standby Fibre "
                  "takes over. Share of this server's stake-based storage budget held by shards, on disk plus "
                  "in-flight reservations; at 100% the storage limiter rejects uploads (budget_exceeded). Shards "
                  "are kept for the shard-retention window (4 h by default). The budget is the full-stake limit "
                  "(2 TiB per window by default, about 146 MiB/s) times this validator's share of rows, and "
                  "every blob gives this validator that same share. So the reading follows the network's Fibre "
                  "data in the window against the full-stake limit. Each blob also adds proofs and a 64 KiB RLC: "
                  "about 2.2 MB at full stake and 3.9 MB at the 148-row minimum. Small blobs therefore read high "
                  "(a 1 MiB blob counts as 3-5 MB), and low-stake validators reach the limit first. Falls to 0 "
                  "while this validator is out of the active set. 'No limit': the budget is unlimited, or the "
                  "validator was not in the active set when Fibre started. No value: no Fibre unit is active, "
                  "or the active Fibre sends no metrics.",
                  # '> 0' hides an unlimited budget and one never derived because the validator
                  # was out of the active set when Fibre started; B names those as 'No limit'.
                  refs([("(max by (host) (fibre_server_upload_shard_occupancy_bytes) / "
                         "max by (host) (fibre_server_upload_shard_budget_bytes > 0)) and on (host) (%s)" % active,
                         "Active Fibre: {{host}}")])
                  + [target("B", "(max by (host) (fibre_server_upload_shard_budget_bytes) == 0) and on (host) (%s)"
                            % active, "Active Fibre: {{host}}", instant=True)],
                  steps(("text", None), ("orange", 0.9)), unit="percentunit", decimals=1, graph="area",
                  no_value="No active Fibre, or no Fibre metrics",
                  overrides=[override("byFrameRefID", "B", unit="none",
                                      mappings=mapping((0, "No limit", "text")))]),
             3, FIBRE_LINKS))
        strip.append((fibre_endpoint(cfg), 3, FIBRE_LINKS))
    return strip


def validator_row(cfg):
    t = cfg["thresholds"]
    remote = cfg["signer"] == "remote"
    cap = 10 * t["not_signing_blocks"]
    ratio = t.get("signatures_missed_ratio", 0.05)
    behind = t.get("node_behind_blocks", 20)
    # As SignaturesMissed, per node. A node that knows the validator's key but has missed no
    # block since its start has no missed-block count yet: it reads 0 once it has seen a signature.
    missed = ('(max by (host) (increase(cometbft_consensus_validator_missed_blocks{job="celestia-app"}[1h])) '
              'or max by (host) (cometbft_consensus_validator_last_signed_height{job="celestia-app"}) * 0) '
              "/ (max by (host) (delta(%s[1h])) > 0)" % HEIGHT)
    signs = [
        timeseries(2, "Blocks since my last signature",
                   "Is my validator signing right now? Works after restarts and jails, when missed-block "
                   "counters freeze. Counted across all nodes, as ValidatorNotSigning does: the line goes on when "
                   "the signer moves to a backup node. Dashed line: the ValidatorNotSigning threshold (%g blocks). "
                   "The line stops at %g blocks (10 times the threshold): right after a node start the value is "
                   "the full block height until the first own signature, and one restart would otherwise flatten "
                   "the chart. The alert uses the uncapped value." % (t["not_signing_blocks"], cap),
                   refs([(blocks_since_signature(cap), "blocks since my last signature")]),
                   "none", line=t["not_signing_blocks"]),
        timeseries(17, "Missed blocks, last hour",
                   "Does my validator miss signatures now and then? Per node, the share of the blocks it committed "
                   "in the last hour that carry no signature from the validator, as SignaturesMissed computes it. "
                   "Each node that knows the validator's key reads the signatures from the chain's commits, so a "
                   "node that tmkms left keeps counting; a node without the key has no line. Dashed line: the "
                   "SignaturesMissed threshold (%g%%), where it sends a message after 10 minutes. Timeouts on the "
                   "link to the KMS host show here while Blocks since my last signature stays low. The count "
                   "starts over when the node restarts. The share says how often signatures are missed, not how "
                   "close the validator is to being jailed." % (ratio * 100),
                   refs([(missed, "{{host}}")]), "percentunit", line=ratio, decimals=1),
    ]
    # Like NodeBehind, only with more than one node.
    if len(cfg.get("nodes", [])) > 1:
        signs.append(
            timeseries(18, "Blocks behind the highest node",
                       "Is every node keeping up with the others? One line per node: the blocks it trails the "
                       "highest node of this install. A backup node must stay near 0 to take over signing; a node "
                       "in block sync also lags. Dashed line: the NodeBehind threshold (%g blocks), where it sends a "
                       "message after 10 minutes. NodeBehind also fires for a node that stops while another "
                       "advances: Node progress shows that as 0 new blocks." % behind,
                       refs([("max(%s) - on () group_right %s" % (HEIGHT, HEIGHT), "{{host}}")]),
                       "none", line=behind, decimals=0))
    # One line for the validator's signatures; with a remote signer a second line for the signer.
    row = [(p, 24 // len(signs), []) for p in signs]
    if not remote:
        return row
    limit = t["signer_median_seconds"]
    return row + [
        (timeseries(3, "Signer latency vs %g ms" % (limit * 1000),
                    "Am I within Celestia's 10 ms median requirement? Is consensus or Fibre (raw_bytes) signing "
                    "getting slower? The median covers the last 50 successful signatures and appears after the "
                    "50th. Dashed line: the RemoteSignerProblem threshold.",
                    refs([("cometbft_privval_signing_latency_median_seconds", "median (last 50)"),
                          ("histogram_quantile(0.99, sum by (le, message_type) "
                           "(rate(cometbft_privval_signing_latency_seconds_bucket[5m])))", "p99 {{message_type}}")]),
                    "s", line=limit),
         12, []),
        (timeseries(4, "Signing failures by reason",
                    "Which path fails? no_connection or timeout: the KMS host or the link. Proposal only: the KMS "
                    "cannot propose. raw_bytes only: tmkms is too old for Fibre. Each retry counts as a failure. "
                    "Empty on a healthy setup. Failures in each 5-minute window. A failure counter appears with "
                    "its first failure, so the first failure of each kind after a node start does not show.",
                    refs([("sum by (message_type, reason) (increase(cometbft_privval_signing_failures_total[5m]))",
                           "{{message_type}} {{reason}}")]),
                    "none", decimals=0),
         12, []),
    ]


def fibre_row(cfg):
    t = cfg["thresholds"]
    unit = q(cfg["units"]["fibre"] + ".service")
    disk = []
    for m in cfg["fibre_mounts"]:
        if not m["mount"]:
            continue
        sel = "{host=%s,mountpoint=%s}" % (q(m["host"]), q(m["mount"]))
        disk += [("node_filesystem_size_bytes%s - node_filesystem_free_bytes%s" % (sel, sel),
                  "{{host}} {{mountpoint}} used"),
                 ("node_filesystem_avail_bytes%s" % sel, "{{host}} {{mountpoint}} free")]
    last = "max by (host) (fibre_server_upload_shard_last_success_timestamp_seconds)"
    panels = [
        timeseries(5, "Time since last accepted upload",
                   "Is Fibre serving, not just running? Time since the server last accepted an upload. Until its "
                   "first accepted upload after a start, the line is labelled no accepted upload yet and counts "
                   "from Fibre start. Dashed line: the FibreNotServing threshold, the time %g uploads take at this "
                   "server's 7-day average and never under %g minutes; no line when the server had no upload in "
                   "7 days. A quiet network also makes the time grow. To tell a quiet network from a fault, check "
                   "on Tensile (https://tensile.huginn.tech) whether the network is carrying Fibre uploads."
                   % (t["fibre_stale_uploads"], t["fibre_stale_min_seconds"] / 60),
                   # The timestamp is not reported before the first accepted upload after each Fibre
                   # start, so B counts from the unit start until A has a value.
                   refs([("time() - %s" % last, "{{host}}"),
                         ("(time() - max by (host) (node_systemd_unit_start_time_seconds{name=%s} > 0)) "
                          "unless on (host) %s" % (unit, last), "{{host}} no accepted upload yet (since Fibre start)")])
                   # The 7-day rate barely moves; a 5 min step keeps this query cheap for Prometheus.
                   + [target("C", "clamp_min(%s / (sum by (host) "
                              "(rate(fibre_server_upload_shard_duration_seconds_count[7d])) > 0), %s)"
                              % (num(t["fibre_stale_uploads"]), num(t["fibre_stale_min_seconds"])),
                              "{{host}} alert threshold", interval="5m")],
                   "s",
                   overrides=[override("byFrameRefID", "C", color={"fixedColor": "red", "mode": "fixed"},
                                       custom__lineStyle={"dash": [10, 10], "fill": "dash"})]),
        timeseries(6, "Shard requests",
                   "Are uploads and downloads arriving and succeeding? Is the storage limiter refusing uploads? "
                   "Are uploads slow? Requests per second; upload p95 on the right axis.",
                   refs([("sum by (success) (rate(fibre_server_upload_shard_duration_seconds_count[5m]))",
                          "upload success={{success}}"),
                         ("sum by (success) (rate(fibre_server_download_shard_duration_seconds_count[5m]))",
                          "download success={{success}}"),
                         ("sum by (reason) (rate(fibre_server_upload_shard_rejected_total[5m]))",
                          "upload rejected: {{reason}}"),
                         ("histogram_quantile(0.95, sum by (le) "
                          "(rate(fibre_server_upload_shard_duration_seconds_bucket[5m])))", "upload p95")]),
                   "reqps",
                   overrides=[override("byFrameRefID", "D", unit="s", custom__axisPlacement="right")]),
        timeseries(7, "Fibre disk",
                   "Will the Fibre disk hold the stake-based budget? Occupancy near the budget is normal at full "
                   "load. A budget of 0 means unlimited. Used and free space of the Fibre mount.",
                   refs([("fibre_server_upload_shard_occupancy_bytes", "{{host}} occupancy"),
                         ("fibre_server_upload_shard_budget_bytes", "{{host}} budget")] + disk),
                   "bytes"),
    ]
    return [(p, 8, FIBRE_LINKS) for p in panels]


def hosts_row(cfg):
    t = cfg["thresholds"]
    nic = "{device!~%s}" % q(VIRT)
    traffic = [("sum by (host) (rate(node_network_receive_bytes_total%s[5m]))" % nic, "{{host}} in"),
               ("sum by (host) (rate(node_network_transmit_bytes_total%s[5m]))" % nic, "{{host}} out")]
    desc = ("What is using each host's link over time? Traffic of the whole machine, not just Celestia: "
            "receive above zero, transmit below. 5-minute averages; the Link load tile has the 1-minute peaks.")
    dashed = []
    if cfg["fibre_enabled"]:
        traffic += [("sum by (host) (rate(fibre_server_upload_shard_bytes_total[5m]))",
                     "{{host}} Fibre shards accepted"),
                    ("sum by (host) (rate(fibre_server_download_shard_bytes_total[5m]))",
                     "{{host}} Fibre shards served")]
        desc += (" Fibre reports once a minute, so every line uses the same 5-minute window. The dashed Fibre "
                 "lines count shard row data that was signed, including re-uploads of a shard already held, "
                 "without proofs, RLC or rejected uploads. They are drawn over the NIC line, not subtracted "
                 "from it; the gap covers consensus P2P, RPC, row proofs and RLC, TLS, and rejected uploads.")
        dashed = [override("byFrameRefID", r, custom__lineStyle={"dash": [10, 10], "fill": "dash"}) for r in "CD"]
    disks = []
    for m in cfg["data_mounts"]:
        sel = "{host=%s,mountpoint=%s}" % (q(m["host"]), q(m["mount"]))
        disks += [("node_filesystem_avail_bytes%s / node_filesystem_size_bytes%s" % (sel, sel),
                   "{{host}} {{mountpoint}} free"),
                  ("node_filesystem_readonly%s" % sel, "{{host}} {{mountpoint}} read-only")]
    dev = "{device!~%s}" % q(DISK)
    # Both directions together; '> 0' leaves a gap instead of 0/0 while a disk is idle.
    latency = ("(rate(node_disk_read_time_seconds_total%s[5m]) + rate(node_disk_write_time_seconds_total%s[5m])) / "
               "((rate(node_disk_reads_completed_total%s[5m]) + rate(node_disk_writes_completed_total%s[5m])) > 0)"
               % (dev, dev, dev, dev))
    load = "rate(node_disk_read_bytes_total%s[5m]) + rate(node_disk_written_bytes_total%s[5m])" % (dev, dev)
    # Two lines of 24 columns: build() wraps after CPU per host.
    return [
        (timeseries(13, "Network traffic", desc, refs(traffic), "Bps", overrides=dashed,
                    mirror=["B", "D"] if cfg["fibre_enabled"] else ["B"]),
         12, HOSTS_LINKS),
        # Idle and iowait are idle time; steal counts as busy.
        (timeseries(14, "CPU per host",
                    "Is a host short of CPU? Share of all cores busy, 5-minute average; on a VM, steal counts as "
                    "busy. A validator host near 100% votes late and adds to signer latency; a Fibre host near "
                    "100% slows uploads: size the host up or move Fibre off it. The KMS host appears only with "
                    "celestia_mon_kms_scrape. One pegged core may not show: see Node Exporter Full.",
                    refs([('1 - sum by (host) (rate(node_cpu_seconds_total{mode=~"idle|iowait"}[5m])) '
                           '/ count by (host) (node_cpu_seconds_total{mode="idle"})', "{{host}}")]),
                    "percentunit", maximum=1),
         12, HOSTS_LINKS),
        (timeseries(15, "Disk latency and load",
                    "Is each disk answering in time? Average time one read or write took, per physical disk; "
                    "bytes moved as dashed lines on the right axis. High latency with high load: something on the "
                    "host loads the disk (Fibre, compaction, Prometheus); give Fibre its own disk. High latency "
                    "with low load: the disk or the provider storage is slow. LVM, RAID, ZFS volume and CD "
                    "devices are left out; their physical disks count. lsblk maps mounts to devices.",
                    refs([(latency, "{{host}} {{device}}"), (load, "{{host}} {{device}} read+write")]),
                    "s",
                    overrides=[override("byFrameRefID", "B", unit="Bps", custom__axisPlacement="right",
                                        custom__lineStyle={"dash": [10, 10], "fill": "dash"})]),
         12, HOSTS_LINKS),
        (bargauge(9, "Free space per data disk",
                  "Which data disk is close to full or read-only? Colours follow the DiskLow thresholds.",
                  refs(disks, instant=True), "percentunit",
                  steps(("red", None), ("orange", t["disk_page_free_ratio"]), ("green", t["disk_message_free_ratio"])),
                  [override("byRegexp", ".* read-only", decimals=0, unit="none",
                            thresholds=steps(("green", None), ("red", 1)),
                            mappings=mapping((0, "no", "green"), (1, "yes", "red")))]),
         6, HOSTS_LINKS),
        (timeseries(10, "Memory per host",
                    "Is Fibre (worst case about 27 GiB at default limits) or the node running out of RAM? "
                    "OOM kills in the last hour as a dashed line on the right axis.",
                    refs([("node_memory_MemAvailable_bytes", "{{host}} available"),
                          ("increase(node_vmstat_oom_kill[1h])", "{{host}} OOM kills (1h)")]),
                    "bytes",
                    overrides=[override("byFrameRefID", "B", decimals=0, unit="none", custom__axisPlacement="right",
                                        custom__lineStyle={"dash": [10, 10], "fill": "dash"})]),
         6, HOSTS_LINKS),
    ]


def host_colors(cfg):
    """Overrides giving each node_exporter host's series one fixed colour, or none for a single host.
    A pattern in slashes is a plain JavaScript regex; without them Grafana anchors it as ^...$."""
    hosts = [link["host"] for link in cfg["links"]]
    if len(hosts) < 2:
        return []
    return [override("byRegexp", "/^%s( |$)/" % js_regex(h), color={"fixedColor": c, "mode": "fixed"})
            for h, c in zip(hosts, HOST_COLORS)]


def build(cfg):
    ds = {"type": "prometheus", "uid": cfg["datasource_uid"]}
    remote = cfg["signer"] == "remote"
    # (row id and title, or None for the strip without a row header; panel height; panels)
    sections = [(None, 6, now_strip(cfg)),
                ((101, "Validator and signer" if remote else "Validator"), 8, validator_row(cfg))]
    if cfg["fibre_enabled"]:
        sections.append(((103, "Fibre"), 8, fibre_row(cfg)))
    sections.append(((104, "Hosts"), 8, hosts_row(cfg)))
    colors = host_colors(cfg)

    panels, y = [], 0
    for row, height, items in sections:
        if row:
            panels.append({"id": row[0], "type": "row", "title": row[1], "collapsed": False, "panels": [],
                           "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}})
            y += 1
        x = 0
        for p, width, links in items:
            # A panel that does not fit the 24 columns starts the next line.
            if x + width > 24:
                x = 0
                y += height
            p["gridPos"] = {"h": height, "w": width, "x": x, "y": y}
            p["datasource"] = ds
            p["links"] = [{"targetBlank": True, "title": lt, "url": lu} for lt, lu in links]
            for tg in p["targets"]:
                tg["datasource"] = ds
            # Host colours first, so a panel's own colour overrides (threshold series) win.
            # Fibre disk (7) shows up to four series per Fibre host, so it keeps Grafana's palette.
            if p["type"] == "timeseries" and p["id"] != 7:
                p["fieldConfig"]["overrides"] = colors + p["fieldConfig"]["overrides"]
            panels.append(p)
            x += width
        y += height

    # One marker per start of a watched unit. Comparing with the value one step earlier
    # catches every start once at a 60 s step and ignores stops (the start time drops to 0).
    restarts = {"datasource": ds, "enable": True, "iconColor": "text", "name": "Service starts",
                "expr": "node_systemd_unit_start_time_seconds > node_systemd_unit_start_time_seconds offset 1m",
                "step": "60s", "titleFormat": "{{name}} started on {{host}}"}

    return {
        "uid": UID,
        "title": TITLE,
        "description": "Generated by celestia-mon from the inventory. Do not edit; rerun the playbook instead.",
        "tags": ["celestia", "celestia-mon"],
        "editable": False,
        "graphTooltip": 1,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "timezone": "utc",
        "schemaVersion": 41,
        "version": 1,
        "annotations": {"list": [restarts]},
        "links": [],
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
