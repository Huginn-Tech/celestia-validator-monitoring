"""Checks of the configuration tools/ci/render.yml renders, for the rules job of .github/workflows/ci.yml.

  python tools/ci/check_render.py CHECK FILE...

  holds HOLDS RULES...   each hold time the dashboard prints (monitoring/rules/holds.json) is the for: of
                         every rule it names
  votes MOCHA NOGOV      the vote rules on Mocha (GovVoteMissing sends its message only) and with gov off
                         (no GovVoteMissing; SignalUnreadable about x/signal only); the two rendered rules files
  twice SPLIT MULTI      ValidatorSigningTwice pages in the split example, and is left out of it rendered with
                         celestia_mon_signer_multi_node (tmkms signs through several nodes); the two rules files
  limits PROMETHEUS      alerts leave Prometheus without instance, and every scrape job has its limits
  targets PROMETHEUS     the mainnet example's scrape targets follow the inventory groups
  ports PROMETHEUS       a host's own ports stay that host's (the mainnet example with ports set per host)
  receivers-text ALERTMANAGER  PagerDuty and Slack receivers send celestia-mon's text
  routing ALERTMANAGER   routes, inhibitions and receivers of the split example with every receiver type

Each fails with an assertion (holds with the list of what does not match). Python 3 and PyYAML.
"""
import json
import re
import sys

import yaml


def holds(args):
    """Each hold time the dashboard prints is the for: of every rule it names."""
    def seconds(text):
        return sum(int(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in re.findall(r"(\d+)([hms])", text))

    holds = json.load(open(args[0]))["holds"]
    rules = [r for path in args[1:] for g in yaml.safe_load(open(path))["groups"] for r in g["rules"] if "alert" in r]
    failed = False
    for name, h in sorted(holds.items()):
        found = [r for r in rules if r["alert"] == h["alert"] and h.get("expr", "") in r["expr"]
                 and all(re.fullmatch(v, str(r.get("labels", {}).get(k, ""))) for k, v in h.get("labels", {}).items())]
        wrong = sorted({str(r.get("for", "0s")) for r in found if seconds(str(r.get("for", "0s"))) != seconds(h["for"])})
        if not found:
            failed = True
            print("%s: the dashboard prints %s, but no rule matches" % (name, h["for"]))
        elif wrong:
            failed = True
            print("%s: the dashboard prints %s, but the rules hold for %s" % (name, h["for"], ", ".join(wrong)))
        else:
            print("%s: %s in %d rules" % (name, h["for"], len(found)))
    sys.exit(1 if failed else 0)


def votes(args):
    """GovVoteMissing on Mocha sends its message only; with gov off there is none."""
    def rules(path, name):
        return [r for g in yaml.safe_load(open(path))["groups"] for r in g["rules"] if r.get("alert") == name]

    gov = rules(args[0], "GovVoteMissing")
    assert [r["labels"]["severity"] for r in gov] == ["message"], gov
    assert "ALERTS" not in gov[0]["expr"] and "It pages" not in gov[0]["annotations"]["description"], gov
    unread = rules(args[0], "SignalUnreadable")
    assert len(unread) == 1 and "celestia_mon_gov_last_success_timestamp_seconds" in unread[0]["expr"], unread
    assert rules(args[1], "GovVoteMissing") == []
    unread = rules(args[1], "SignalUnreadable")
    assert len(unread) == 1 and "_gov_" not in unread[0]["expr"], unread
    assert unread[0]["annotations"]["summary"] == "The upgrade signal is not being read on mon-1", unread
    print("mocha: GovVoteMissing, message only; nogov: no GovVoteMissing, SignalUnreadable about x/signal only")


def twice(args):
    """ValidatorSigningTwice pages with a remote signer; with tmkms signing through several nodes there is none."""
    def rules(path):
        return [r for g in yaml.safe_load(open(path))["groups"] for r in g["rules"] if r.get("alert") == "ValidatorSigningTwice"]

    found = rules(args[0])
    assert [r["labels"]["severity"] for r in found] == ["page"], found
    assert rules(args[1]) == [], rules(args[1])
    print("split: ValidatorSigningTwice pages; with celestia_mon_signer_multi_node: none")


def limits(args):
    """Alerts leave Prometheus without instance, and every scrape job caps its samples and labels."""
    config = yaml.safe_load(open(args[0]))
    assert config["alerting"]["alert_relabel_configs"] == [{"action": "labeldrop", "regex": "instance"}], config["alerting"]
    want = {"prometheus": 20000, "alertmanager": 10000, "celestia-app": 100000, "node": 50000, "otelcol": 20000,
            "fibre_health": 1000, "tensile": 1000, "signal": 1000, "announcements": 1000}
    for job in config["scrape_configs"]:
        name = job["job_name"]
        assert (job.get("sample_limit"), job.get("label_limit")) == (want[name], 128), job
    print(args[0], sorted(j["job_name"] for j in config["scrape_configs"]))


def targets(args):
    """The mainnet example's scrape targets follow the inventory groups."""
    jobs = {j["job_name"]: j for j in yaml.safe_load(open(args[0]))["scrape_configs"]}
    def hosts(job):
        return sorted((c["labels"]["host"], c["labels"]["role"]) for c in jobs[job]["static_configs"])
    assert hosts("celestia-app") == [("fibre-1", "backup_node"), ("val-1", "validator")], hosts("celestia-app")
    assert hosts("node") == [("fibre-1", "fibre"), ("mon-1", "monitor"), ("val-1", "validator")], hosts("node")
    assert [c["targets"] for c in jobs["node"]["static_configs"] if c["labels"]["host"] == "mon-1"] == [["10.20.0.3:9910"]], jobs["node"]
    assert hosts("otelcol") == [("fibre-1", "fibre"), ("val-1", "fibre")], hosts("otelcol")
    assert hosts("announcements") == [("mon-1", "monitor")], hosts("announcements")
    assert hosts("tensile") == [("mon-1", "monitor")], hosts("tensile")
    assert hosts("signal") == [("mon-1", "monitor")], hosts("signal")
    assert [c["targets"] for c in jobs["signal"]["static_configs"]] == [["127.0.0.1:9918"]], jobs["signal"]
    # Fibre's readiness: every Fibre host's own port, asked through celestia-mon-blackbox on the monitor.
    fh = jobs["fibre_health"]
    assert hosts("fibre_health") == [("fibre-1", "fibre"), ("val-1", "fibre")], hosts("fibre_health")
    assert sorted(c["targets"] for c in fh["static_configs"]) == [["10.20.0.1:7980"], ["10.20.0.2:7980"]], fh
    assert fh["metrics_path"] == "/probe" and fh["params"] == {"module": ["fibre_health"]}, fh
    # blackbox takes only requests with the API password, as Prometheus and Alertmanager do.
    assert fh["basic_auth"] == jobs["prometheus"]["basic_auth"] == jobs["alertmanager"]["basic_auth"], fh
    assert fh["basic_auth"]["username"] == "celestia-mon" and "password_file" in fh["basic_auth"], fh
    assert fh["relabel_configs"] == [
        {"source_labels": ["__address__"], "target_label": "__param_target"},
        {"source_labels": ["__param_target"], "target_label": "instance"},
        {"target_label": "__address__", "replacement": "127.0.0.1:9919"},
    ], fh["relabel_configs"]
    print({j: hosts(j) for j in ("celestia-app", "node", "otelcol", "announcements", "signal", "fibre_health")})


def ports(args):
    """A port set on one host is that host's only."""
    jobs = {j["job_name"]: j for j in yaml.safe_load(open(args[0]))["scrape_configs"]}
    def targets(job):
        return sorted((c["labels"]["host"], c["targets"][0]) for c in jobs[job]["static_configs"])
    assert targets("celestia-app") == [("fibre-1", "10.20.0.2:26670"), ("val-1", "10.20.0.1:26660")], targets("celestia-app")
    assert targets("fibre_health") == [("fibre-1", "10.20.0.2:7980"), ("val-1", "10.20.0.1:7980")], targets("fibre_health")
    print(targets("celestia-app"), targets("fibre_health"))


def receivers_text(args):
    """PagerDuty's description and details and Slack's title, text and fallback are celestia-mon's text."""
    receivers = {r["name"]: r for r in yaml.safe_load(open(args[0]))["receivers"]}
    pd = receivers["page"]["pagerduty_configs"][0]
    assert pd["description"] == '{{ template "celestia.pagerduty.description" . }}', pd
    assert pd["details"] == {"firing": '{{ template "celestia.pagerduty.alerts" .Alerts.Firing }}',
                             "resolved": '{{ template "celestia.pagerduty.alerts" .Alerts.Resolved }}'}, pd
    for name in ("page", "message", "announcement-page", "announcement-message"):
        slack = receivers[name]["slack_configs"][0]
        assert slack["title"] == slack["fallback"] == '{{ template "celestia.slack.title" . }}', slack
        assert slack["text"] == '{{ template "celestia.slack.text" . }}' and slack["title_link"] == "", slack


def routing(args):
    """Routing follows the receiver type; the inhibitions of one cause; the receivers of each route."""
    config = yaml.safe_load(open(args[0]))
    # UpgradeScheduled and UpgradeSoon: their own route, each alert sent once, before the message route.
    routes = config["route"]["routes"]
    upgrade = [i for i, r in enumerate(routes) if r.get("matchers") == ['alertname=~"UpgradeScheduled|UpgradeSoon"']]
    assert len(upgrade) == 1, routes
    r = routes[upgrade[0]]
    assert (r["receiver"], r["group_by"], r["group_wait"], r["repeat_interval"]) == ("message", ["..."], "0s", "96h"), r
    assert "routes" not in r and not r.get("continue", False), r
    assert upgrade[0] < [i for i, r in enumerate(routes) if r.get("receiver") == "message" and i != upgrade[0]][0], routes
    # RaidDegraded: its own route before the page and message routes, a reminder once a day.
    raid = [i for i, r in enumerate(routes) if r.get("matchers") == ['alertname="RaidDegraded"']]
    assert len(raid) == 1 and raid[0] < [i for i, r in enumerate(routes) if r.get("matchers") == ['severity="page"']][0], routes
    r = routes[raid[0]]
    assert (r["receiver"], r["repeat_interval"]) == ("message", "24h") and "group_by" not in r, r
    assert "routes" not in r and not r.get("continue", False), r
    # GovVoteMissing: its own route before the page and message routes, one notification per proposal and
    # severity, sent at once and not again while the vote lasts; by severity to page or message.
    gov = [i for i, r in enumerate(routes) if r.get("matchers") == ['alertname="GovVoteMissing"']]
    assert len(gov) == 1 and gov[0] < [i for i, r in enumerate(routes) if r.get("matchers") == ['severity="page"']][0], routes
    r = routes[gov[0]]
    assert (r["group_by"], r["group_wait"], r["repeat_interval"]) == (["..."], "0s", "96h") and "receiver" not in r, r
    assert [(c["receiver"], c["matchers"]) for c in r["routes"]] == [("page", ['severity="page"']),
                                                                     ("message", ['severity="message"'])], r
    assert not r.get("continue", False), r
    # A validator out of the active set or a dead signer pages once (ValidatorNotSigning), not also as
    # Fibre not ready; a collector that is down holds back every FibreNotServing but not_ready.
    inhibit = config["inhibit_rules"]
    assert {"source_matchers": ['alertname="ValidatorNotSigning"'],
            "target_matchers": ['alertname="FibreNotServing"', 'reason="not_ready"']} in inhibit, inhibit
    assert {"source_matchers": ['alertname="ServiceDown"', 'unit="otelcol"'],
            "target_matchers": ['alertname="FibreNotServing"', 'reason!="not_ready"'], "equal": ["host"]} in inhibit, inhibit
    # The signing node without peers pages once: not also as the signatures that stop or the nodes that
    # stop advancing (no host), nor as that host's lag and its message about few peers.
    peers = ['alertname="NodePeersLow"', 'severity="page"']
    assert {"source_matchers": peers,
            "target_matchers": ['alertname=~"ValidatorNotSigning|SignaturesMissed|NodeNotAdvancing"']} in inhibit, inhibit
    assert {"source_matchers": peers, "target_matchers": ['alertname=~"NodeBehind|NodePeersLow"', 'severity="message"'],
            "equal": ["host"]} in inhibit, inhibit
    receivers = {r["name"]: r for r in config["receivers"]}
    def count(name):
        return {k: len(v) for k, v in receivers[name].items() if k.endswith("_configs")}
    chat = {"telegram_configs": 1, "discord_configs": 1, "slack_configs": 2, "webhook_configs": 1}
    assert count("page") == {"pagerduty_configs": 1, **chat}, count("page")
    assert count("message") == chat, count("message")
    # Announcements go to the same receivers, without a "resolved" message.
    assert count("announcement-page") == count("page"), count("announcement-page")
    assert count("announcement-message") == chat, count("announcement-message")
    def resolved(name):
        return {c["send_resolved"] for k, v in receivers[name].items() if k.endswith("_configs") for c in v}
    assert resolved("page") == resolved("message") == {True}, (resolved("page"), resolved("message"))
    assert resolved("announcement-message") == {False}, resolved("announcement-message")
    # PagerDuty closes its incident when the announcement ends; the chats get no "resolved".
    ann = receivers["announcement-page"]
    assert {c["send_resolved"] for c in ann["pagerduty_configs"]} == {True}, ann
    assert {c["send_resolved"] for k, v in ann.items() if k.endswith("_configs") and k != "pagerduty_configs" for c in v} == {False}
    print("page:", count("page"), "message:", count("message"))


CHECKS = {"holds": holds, "votes": votes, "twice": twice, "limits": limits, "targets": targets, "ports": ports,
          "receivers-text": receivers_text, "routing": routing}

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in CHECKS:
        sys.exit(__doc__)
    CHECKS[sys.argv[1]](sys.argv[2:])
