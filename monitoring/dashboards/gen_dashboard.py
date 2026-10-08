#!/usr/bin/env python3
"""Generate Taut's Grafana dashboard from the inventory settings.

    gen_dashboard.py --config <settings.json> --out <dashboard.json>

The settings file is rendered by the taut_grafana Ansible role from
ansible/roles/taut_grafana/templates/dashboard-config.json.j2:

    {
      "datasource_uid": "taut-prometheus",
      "signer": "local" | "remote",
      "fibre_enabled": true | false,
      "units": {"appd": "...", "fibre": "...", "tmkms": "..."},
      "thresholds": {...},                         # taut_thresholds
      "fibre_mounts": [{"host": "...", "mount": "..."}],
      "data_mounts": [{"host": "...", "mount": "..."}]
    }

Python 3 standard library only. The same settings always give the same bytes.
Every query is plain PromQL (no Grafana variables), so CI can send each one to
Prometheus as it is (tools/ci/check_queries.py).
"""
import argparse
import json

UID = "taut-validator"
TITLE = "Taut: Celestia validator"

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


def q(value):
    """A PromQL string literal."""
    return json.dumps(value)


def num(value):
    """A number as PromQL and Grafana expect it."""
    return json.dumps(value)


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


def timeseries(pid, title, desc, targets, unit, line=None, overrides=()):
    """Lines over time. line: a threshold drawn as a dashed red line."""
    custom = {"drawStyle": "line", "fillOpacity": 0, "lineWidth": 1, "showPoints": "never", "spanNulls": False,
              "thresholdsStyle": {"mode": "dashed" if line is not None else "off"}}
    defaults = {"color": {"mode": "palette-classic"}, "custom": custom, "min": 0, "unit": unit,
                "thresholds": steps(("green", None), ("red", line)) if line is not None else steps(("green", None))}
    options = {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
               "tooltip": {"mode": "multi", "sort": "desc"}}
    return panel(pid, "timeseries", title, desc, targets, defaults, overrides, options)


def stat(pid, title, desc, targets, thresholds, overrides=()):
    """Current values, one tile per series, coloured by thresholds or value mappings."""
    defaults = {"color": {"mode": "thresholds"}, "decimals": 0, "thresholds": thresholds, "unit": "none"}
    options = {"colorMode": "value", "graphMode": "none", "justifyMode": "auto", "orientation": "auto",
               "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
               "showPercentChange": False, "textMode": "value_and_name", "wideLayout": True}
    return panel(pid, "stat", title, desc, targets, defaults, overrides, options)


def bargauge(pid, title, desc, targets, unit, thresholds, overrides=()):
    defaults = {"color": {"mode": "thresholds"}, "decimals": 1, "max": 1, "min": 0, "thresholds": thresholds,
                "unit": unit}
    options = {"displayMode": "basic", "namePlacement": "top", "orientation": "horizontal",
               "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
               "showUnfilled": True, "sizing": "auto", "valueMode": "color"}
    return panel(pid, "bargauge", title, desc, targets, defaults, overrides, options)


def panel(pid, kind, title, desc, targets, defaults, overrides, options):
    return {"id": pid, "type": kind, "title": title, "description": desc, "targets": targets,
            "fieldConfig": {"defaults": defaults, "overrides": list(overrides)}, "options": options}


def validator_row(cfg):
    t = cfg["thresholds"]
    return [
        stat(1, "Node at chain head",
             "Is the node following new blocks, or stuck, syncing, or watching a halted chain? Is it peered? "
             "NodeNotAtHead alerts when new blocks (3 min) stays at 0. If the whole chain stopped "
             "(check status.celestia.org and an explorer), coordinate instead of fixing locally.",
             refs([("changes(cometbft_consensus_height[3m])", "new blocks (3 min)"),
                   ("cometbft_blocksync_syncing", "block sync"),
                   ("cometbft_p2p_peers", "peers")], instant=True),
             steps(("red", None), ("green", 1)),
             [override("byFrameRefID", "B", mappings=mapping((0, "no", "green"), (1, "yes", "orange")))]),
        timeseries(2, "Blocks since my last signature",
                   "Is my validator signing right now? Works after restarts and jails, when missed-block "
                   "counters freeze. Dashed line: the ValidatorNotSigning threshold.",
                   refs([("cometbft_consensus_height - (max without (validator_address) "
                          "(cometbft_consensus_validator_last_signed_height) or cometbft_consensus_height * 0)",
                          "{{host}}")]),
                   "none", line=t["not_signing_blocks"]),
    ]


def signer_row(cfg):
    limit = cfg["thresholds"]["signer_median_seconds"]
    return [
        timeseries(3, "Signer latency vs %g ms" % (limit * 1000),
                   "Am I within Celestia's 10 ms median requirement? Is consensus or Fibre (raw_bytes) signing "
                   "getting slower? The median covers the last 50 successful signatures and appears after the "
                   "50th. Dashed line: the RemoteSignerProblem threshold.",
                   refs([("cometbft_privval_signing_latency_median_seconds", "median (last 50)"),
                         ("histogram_quantile(0.99, sum by (le, message_type) "
                          "(rate(cometbft_privval_signing_latency_seconds_bucket[5m])))", "p99 {{message_type}}")]),
                   "s", line=limit),
        timeseries(4, "Signing failures by reason",
                   "Which path fails? no_connection or timeout: the KMS host or the link. Proposal only: the KMS "
                   "cannot propose. raw_bytes only: tmkms is too old for Fibre. Each retry counts as a failure. "
                   "Empty on a healthy setup.",
                   refs([("sum by (message_type, reason) (rate(cometbft_privval_signing_failures_total[5m]))",
                          "{{message_type}} {{reason}}")]),
                   "reqps"),
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
    return [
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


def hosts_row(cfg):
    t = cfg["thresholds"]
    disks = []
    for m in cfg["data_mounts"]:
        sel = "{host=%s,mountpoint=%s}" % (q(m["host"]), q(m["mount"]))
        disks += [("node_filesystem_avail_bytes%s / node_filesystem_size_bytes%s" % (sel, sel),
                   "{{host}} {{mountpoint}} free"),
                  ("node_filesystem_readonly%s" % sel, "{{host}} {{mountpoint}} read-only")]
    return [
        stat(8, "Services per host",
             "Are celestia-appd, Fibre and tmkms running, and how often did systemd restart them in the last "
             "24 hours? Restarts count only automatic restarts under Restart=.",
             refs([('node_systemd_unit_state{state="active"}', "{{host}} {{name}}"),
                   ("increase(node_systemd_service_restart_total[24h])", "{{host}} {{name}} restarts (24h)")],
                  instant=True),
             steps(("green", None), ("orange", 1)),
             [override("byFrameRefID", "A", mappings=mapping((0, "not active", "red"), (1, "active", "green")))]),
        bargauge(9, "Free space per data disk",
                 "Which data disk is close to full or read-only? Colours follow the DiskLow thresholds.",
                 refs(disks, instant=True), "percentunit",
                 steps(("red", None), ("orange", t["disk_page_free_ratio"]), ("green", t["disk_message_free_ratio"])),
                 [override("byRegexp", ".* read-only", decimals=0, unit="none",
                           thresholds=steps(("green", None), ("red", 1)),
                           mappings=mapping((0, "no", "green"), (1, "yes", "red")))]),
        timeseries(10, "Memory per host",
                   "Is Fibre (worst case about 27 GiB at default limits) or the node running out of RAM? "
                   "OOM kills in the last hour on the right axis.",
                   refs([("node_memory_MemAvailable_bytes", "{{host}} available"),
                         ("increase(node_vmstat_oom_kill[1h])", "{{host}} OOM kills (1h)")]),
                   "bytes",
                   overrides=[override("byFrameRefID", "B", decimals=0, unit="none", custom__axisPlacement="right")]),
    ]


def build(cfg):
    ds = {"type": "prometheus", "uid": cfg["datasource_uid"]}
    groups = [(101, "Validator", validator_row(cfg), [])]
    if cfg["signer"] == "remote":
        groups.append((102, "Remote signer", signer_row(cfg), []))
    if cfg["fibre_enabled"]:
        groups.append((103, "Fibre", fibre_row(cfg), FIBRE_LINKS))
    groups.append((104, "Hosts", hosts_row(cfg), HOSTS_LINKS))

    panels, y = [], 0
    for rid, title, row, links in groups:
        panels.append({"id": rid, "type": "row", "title": title, "collapsed": False, "panels": [],
                       "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}})
        y += 1
        width = 24 // len(row)
        for i, p in enumerate(row):
            p["gridPos"] = {"h": 8, "w": width, "x": i * width, "y": y}
            p["datasource"] = ds
            p["links"] = [{"targetBlank": True, "title": lt, "url": lu} for lt, lu in links]
            for tg in p["targets"]:
                tg["datasource"] = ds
            panels.append(p)
        y += 8

    return {
        "uid": UID,
        "title": TITLE,
        "description": "Generated by Taut from the inventory. Do not edit; rerun the playbook instead.",
        "tags": ["celestia", "taut"],
        "editable": False,
        "graphTooltip": 1,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "timezone": "utc",
        "schemaVersion": 41,
        "version": 1,
        "annotations": {"list": []},
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
