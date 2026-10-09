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
      "fibre_mounts": [{"host": "...", "mount": "..."}],
      "data_mounts": [{"host": "...", "mount": "..."}],
      "links": [{"host": "...", "mbps": 1000}]     # every node_exporter host; mbps: celestia_mon_link_mbps or null
    }

Layout: a 'Now' strip without a row header, then the rows 'Validator and signer',
'Fibre' and 'Hosts'. Panels that do not apply are left out: no signer panels with
a local signer, no Fibre panels without Fibre.

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
    ("Valopers: Fibre providers (testnet)", "https://testnet.celestia.valopers.com/fibre/providers"),
]
HOSTS_LINKS = [
    ("Node Exporter Full (grafana.com 1860)", "https://grafana.com/grafana/dashboards/1860-node-exporter-full/"),
]

# Interfaces left out of NIC traffic: loopback, containers, bridges, VPNs and tunnels,
# bond and team masters (their member NICs count) and VLAN sub-interfaces (their parent counts).
VIRT = (r"lo|docker.*|veth.*|br.*|vmbr.*|virbr.*|cni.*|flannel.*|cali.*|cilium.*|vxlan.*|kube.*"
        r"|tailscale.*|wg.*|tun.*|tap.*|bond.*|team.*|.+\..+")

# One fixed colour per node_exporter host, in inventory order, when there is more than
# one. Green, orange and red stay free for thresholds. Hosts past the list keep
# Grafana's palette rather than repeat a colour.
HOST_COLORS = ["blue", "purple", "yellow", "light-blue", "light-purple", "light-yellow"]


def q(value):
    """A PromQL string literal."""
    return json.dumps(value)


def num(value):
    """A number as PromQL and Grafana expect it."""
    return json.dumps(value)


def js_regex(text):
    """text with JavaScript regex metacharacters escaped."""
    return re.sub(r"[\\^$.*+?()\[\]{}|/]", lambda m: "\\" + m.group(0), text)


def target(ref, expr, legend, instant=False, interval=None):
    """A Prometheus query. interval: Grafana's minimum step for it, e.g. "5m"."""
    t = {"refId": ref, "expr": expr, "legendFormat": legend, "instant": instant, "range": not instant}
    if interval:
        t["interval"] = interval
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


def timeseries(pid, title, desc, targets, unit, line=None, overrides=(), mirror=()):
    """Lines over time. line: a threshold drawn as a dashed red line.
    mirror: refIds drawn below zero (receive above, transmit below); the legend keeps
    their positive values and becomes a table with Max and Last."""
    custom = {"drawStyle": "line", "fillOpacity": 0, "lineWidth": 1, "showPoints": "never", "spanNulls": False,
              "thresholdsStyle": {"mode": "dashed" if line is not None else "off"}}
    defaults = {"color": {"mode": "palette-classic"}, "custom": custom, "min": 0, "unit": unit,
                "thresholds": steps(("green", None), ("red", line)) if line is not None else steps(("green", None))}
    legend = {"displayMode": "list", "placement": "bottom", "showLegend": True}
    if mirror:
        del defaults["min"]
        custom["axisCenteredZero"] = True
        overrides = [override("byFrameRefID", r, custom__transform="negative-Y") for r in mirror] + list(overrides)
        legend = {"calcs": ["max", "last"], "displayMode": "table", "placement": "bottom", "showLegend": True}
    options = {"legend": legend, "tooltip": {"mode": "multi", "sort": "desc"}}
    return panel(pid, "timeseries", title, desc, targets, defaults, overrides, options)


def stat(pid, title, desc, targets, thresholds, overrides=(), unit="none", decimals=0, graph="none",
         no_value=None):
    """Current values, one tile per series, coloured by thresholds or value mappings.
    graph: "area" draws a sparkline over the dashboard range (needs range queries)."""
    defaults = {"color": {"mode": "thresholds"}, "decimals": decimals, "thresholds": thresholds, "unit": unit}
    if no_value is not None:
        defaults["noValue"] = no_value
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


def now_strip(cfg):
    """(panel, width, links) for the top strip."""
    fibre = cfg["fibre_enabled"]
    loads = []
    for link in cfg["links"]:
        sel = "{host=%s,device!~%s}" % (q(link["host"]), q(VIRT))
        cap = link_capacity(link, sel)
        # A host that is up but has no known speed shows -1, mapped to a hint, so its bar stays visible.
        unknown = 'max by (host) (up{job="node",host=%s} == 1) * 0 - 1' % q(link["host"])
        for direction, metric in (("in", "receive"), ("out", "transmit")):
            peak = "max_over_time((sum by (host) (rate(node_network_%s_bytes_total%s[1m])))[1h:1m])" % (metric, sel)
            loads.append(("%s / %s or %s" % (peak, cap, unknown), "{{host}} %s" % direction))
    strip = [
        (stat(1, "Node at chain head",
              "Is the node following new blocks, or stuck, syncing, or watching a halted chain? Is it peered? "
              "NodeNotAtHead alerts when new blocks (3 min) stays at 0. If the whole chain stopped "
              "(check status.celestia.org and an explorer), coordinate instead of fixing locally.",
              refs([("changes(cometbft_consensus_height[3m])", "new blocks (3 min)"),
                    ("cometbft_blocksync_syncing", "block sync"),
                    ("cometbft_p2p_peers", "peers")], instant=True),
              steps(("red", None), ("green", 1)),
              [override("byFrameRefID", "B", mappings=mapping((0, "no", "green"), (1, "yes", "orange")))]),
         5 if fibre else 6, []),
        (stat(8, "Services per host",
              "Are celestia-appd, Fibre and tmkms running, and how often did systemd restart them in the last "
              "24 hours? Restarts count only automatic restarts under Restart=.",
              refs([('node_systemd_unit_state{state="active"}', "{{host}} {{name}}"),
                    ("increase(node_systemd_service_restart_total[24h])", "{{host}} {{name}} restarts (24h)")],
                   instant=True),
              steps(("green", None), ("orange", 1)),
              [override("byFrameRefID", "A", mappings=mapping((0, "not active", "red"), (1, "active", "green")))]),
         8 if fibre else 10, HOSTS_LINKS),
        (bargauge(11, "Link load (busiest minute, last hour)",
                  "Busiest 1-minute average in the last hour, against celestia_mon_link_mbps or, when that is not set, "
                  "the NIC port speed. Port speed can be above what your provider lets through. Counts every "
                  "physical NIC; VPN, container, bridge, bond master and VLAN interfaces are left out.",
                  refs(loads, instant=True), "percentunit",
                  steps(("green", None), ("orange", 0.7), ("red", 0.9)), decimals=0,
                  mappings=[{"type": "range", "options": {"from": -1, "to": -1, "result": {
                      "color": "text", "index": 0, "text": "speed unknown: set celestia_mon_link_mbps"}}}]),
         6 if fibre else 8, HOSTS_LINKS),
    ]
    if fibre:
        strip.append(
            (stat(12, "Fibre load vs limit",
                  "Shard bytes this server holds over its stake-based budget. Shards are kept for the "
                  "shard-retention window (4 h by default). The budget is the full-stake limit (2 TiB per window "
                  "by default, about 146 MiB/s) times this validator's share of rows, and every blob gives this "
                  "validator that same share. So the reading follows the network's Fibre data in the window "
                  "against the full-stake limit. Each blob also adds proofs and a 64 KiB RLC: about 2.2 MB at "
                  "full stake and 3.9 MB at the 148-row minimum. Small blobs therefore read high (a 1 MiB blob "
                  "counts as 3-5 MB), and low-stake validators reach the limit first. Falls to 0 while this "
                  "validator is out of the active set.",
                  # '> 0' hides an unlimited budget and one never derived because the validator
                  # was out of the active set when Fibre started.
                  refs([("max by (host) (fibre_server_upload_shard_occupancy_bytes) / "
                         "max by (host) (fibre_server_upload_shard_budget_bytes > 0)", "{{host}}")]),
                  steps(("text", None), ("orange", 0.9)), unit="percentunit", decimals=1, graph="area",
                  no_value="No limit: unlimited budget, or not in the active set when Fibre started"),
             5, FIBRE_LINKS))
    return strip


def validator_row(cfg):
    t = cfg["thresholds"]
    remote = cfg["signer"] == "remote"
    cap = 10 * t["not_signing_blocks"]
    row = [
        (timeseries(2, "Blocks since my last signature",
                    "Is my validator signing right now? Works after restarts and jails, when missed-block "
                    "counters freeze. Dashed line: the ValidatorNotSigning threshold. The line stops at %g "
                    "blocks (10 times the threshold): right after a node start the value is the full block "
                    "height until the first own signature, and one restart would otherwise flatten the chart. "
                    "The alert uses the uncapped value." % cap,
                    refs([("clamp_max(cometbft_consensus_height - (max without (validator_address) "
                           "(cometbft_consensus_validator_last_signed_height) or cometbft_consensus_height * 0), "
                           "%s)" % num(cap), "{{host}}")]),
                    "none", line=t["not_signing_blocks"]),
         8 if remote else 24, []),
    ]
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
         8, []),
        (timeseries(4, "Signing failures by reason",
                    "Which path fails? no_connection or timeout: the KMS host or the link. Proposal only: the KMS "
                    "cannot propose. raw_bytes only: tmkms is too old for Fibre. Each retry counts as a failure. "
                    "Empty on a healthy setup.",
                    refs([("sum by (message_type, reason) (rate(cometbft_privval_signing_failures_total[5m]))",
                           "{{message_type}} {{reason}}")]),
                    "reqps"),
         8, []),
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
    panels = [
        timeseries(5, "Time since last successful upload",
                   "Is Fibre serving, not just running? Counted from Fibre's start until its first upload. "
                   "Dashed line: the FibreNotServing threshold, the time ten uploads take at this server's "
                   "7-day average and never under the minimum; no line when the server had no upload in 7 days.",
                   refs([("time() - max by (host) (fibre_server_upload_shard_last_success_timestamp_seconds "
                          "or on (host) (node_systemd_unit_start_time_seconds{name=%s} > 0))" % unit, "{{host}}")])
                   # The 7-day rate barely moves; a 5 min step keeps this query cheap for Prometheus.
                   + [target("B", "clamp_min(%s / (sum by (host) "
                              "(rate(fibre_server_upload_shard_duration_seconds_count[7d])) > 0), %s)"
                              % (num(t["fibre_stale_uploads"]), num(t["fibre_stale_min_seconds"])),
                              "{{host}} alert threshold", interval="5m")],
                   "s",
                   overrides=[override("byFrameRefID", "B", color={"fixedColor": "red", "mode": "fixed"},
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
    return [
        (timeseries(13, "Network traffic", desc, refs(traffic), "Bps", overrides=dashed,
                    mirror=["B", "D"] if cfg["fibre_enabled"] else ["B"]),
         12, HOSTS_LINKS),
        (timeseries(10, "Memory per host",
                    "Is Fibre (worst case about 27 GiB at default limits) or the node running out of RAM? "
                    "OOM kills in the last hour as a dashed line on the right axis.",
                    refs([("node_memory_MemAvailable_bytes", "{{host}} available"),
                          ("increase(node_vmstat_oom_kill[1h])", "{{host}} OOM kills (1h)")]),
                    "bytes",
                    overrides=[override("byFrameRefID", "B", decimals=0, unit="none", custom__axisPlacement="right",
                                        custom__lineStyle={"dash": [10, 10], "fill": "dash"})]),
         6, HOSTS_LINKS),
        (bargauge(9, "Free space per data disk",
                  "Which data disk is close to full or read-only? Colours follow the DiskLow thresholds.",
                  refs(disks, instant=True), "percentunit",
                  steps(("red", None), ("orange", t["disk_page_free_ratio"]), ("green", t["disk_message_free_ratio"])),
                  [override("byRegexp", ".* read-only", decimals=0, unit="none",
                            thresholds=steps(("green", None), ("red", 1)),
                            mappings=mapping((0, "no", "green"), (1, "yes", "red")))]),
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
            p["gridPos"] = {"h": height, "w": width, "x": x, "y": y}
            p["datasource"] = ds
            p["links"] = [{"targetBlank": True, "title": lt, "url": lu} for lt, lu in links]
            for tg in p["targets"]:
                tg["datasource"] = ds
            # Host colours first, so a panel's own colour overrides (threshold series) win.
            # Fibre disk (7) shows four series of the Fibre host, so it keeps Grafana's palette.
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
