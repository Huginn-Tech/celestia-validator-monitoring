"""Checks that a dashboard panel and the alert rule it shows read the same, for the dashboard job of
.github/workflows/ci.yml, on the dashboards and rules it generated under build/<scenario>/.

  python tools/ci/check_dashboard.py CHECK [FILE...]

  missed-blocks         the Validator tile and Missed blocks read the SignaturesMissed rule
  upload-readiness      Upload readiness and the Fibre tile, History and cards read the not_ready rules
  fibre-behind DASHBOARD RULES  Fibre behind the network draws the missed_uploads rule (the mainnet example)
  clock                 Clock maximum error draws the ClockNotSynced rule
  restarts              Restarts draw the crash-loop rule

Each fails with an assertion. Run from the repo root; Python 3 and PyYAML.
"""
import json
import re
import sys

import yaml


def walk(path):
    """Every panel of a dashboard by id, those of collapsed rows too."""
    panels = {}
    todo = json.load(open(path))["panels"]
    while todo:
        p = todo.pop()
        panels[p["id"]] = p
        todo += p.get("panels", [])
    return panels


def missed_blocks(args):
    """The Validator tile holds SignaturesMissed's comparison; Missed blocks draws its two sides at its share."""
    for s in ("single", "split", "mainnet", "five"):
        rules = "build/%s/rules/celestia-mon.rules.yml" % ("mainnet" if s == "five" else s)
        rule = [r for g in yaml.safe_load(open(rules))["groups"] for r in g["rules"]
                if r.get("alert") == "SignaturesMissed"]
        assert len(rule) == 1, "%s: one SignaturesMissed rule, not %d" % (s, len(rule))
        expr = " ".join(rule[0]["expr"].split())
        m = re.fullmatch(r"max\((max by \(host\) \(.+\)) > on \(host\) ([0-9.]+) \* "
                         r"(max by \(host\) \(delta\(cometbft_consensus_height\{[^}]*\}\[1h\]\)\))\)", expr)
        assert m, expr
        a, r, b = m.groups()
        panels = walk("build/%s/dashboard.json" % s)
        text = lambda i: [" ".join(t["expr"].split()) for t in panels[i]["targets"]]
        assert any(expr in t for t in text(11)), "%s: tile 11 lacks %s" % (s, expr)
        for i in (117, 111):
            shares = [t for t in text(i) if a in t and b in t]
            assert shares, "%s: %d draws no %s over %s" % (s, i, a, b)
            level = panels[i]["fieldConfig"]["defaults"]["thresholds"]["steps"][-1]["value"]
            assert level == float(r), "%s: %d's level %s, the rule's %s" % (s, i, level, r)
        print("%s: tile 11, 117 and 111 read %s over %s at %s" % (s, a[:60] + "...", b, r))


def upload_readiness(args):
    """Both not_ready rules watch one selector, which Upload readiness reads; its red and two panels are the page."""
    for s in ("split", "mainnet", "five"):
        rules = "build/%s/rules/celestia-mon.rules.yml" % ("mainnet" if s == "five" else s)
        found = [r for g in yaml.safe_load(open(rules))["groups"] for r in g["rules"]
                 if r.get("alert") == "FibreNotServing" and r.get("labels", {}).get("reason") == "not_ready"]
        assert sorted((r["labels"]["severity"], r["for"]) for r in found) == [("message", "3m"), ("page", "10m")], found
        exprs = {" ".join(r["expr"].split()) for r in found}
        assert len(exprs) == 1, exprs
        m = re.fullmatch(r"max by \(host\) \((probe_grpc_healthcheck_response\{[^}]*\})\) == 1", exprs.pop())
        assert m, found
        panels = walk("build/%s/dashboard.json" % s)
        band = " ".join(next(t["expr"] for t in panels[215]["targets"] if t["legendFormat"] == "Upload readiness").split())
        assert "max_over_time(%s[$__interval] offset -$__interval)" % m.group(1) in band, band
        page = 'alertname="FibreNotServing",reason="not_ready",severity="page"'
        assert 'ALERTS{alertstate="firing",%s}' % page in band, band
        for i in (14, 20):
            assert any(page in t["expr"] for t in panels[i]["targets"]), "%s: %d lacks the page" % (s, i)
        print("%s: 215 reads %s; 14 and 20 the page" % (s, m.group(1)))


def fibre_behind(args):
    """Fibre behind the network draws the value the missed_uploads rule compares, its line at the rule's number."""
    dash = json.load(open(args[0]))
    rules = [r for g in yaml.safe_load(open(args[1]))["groups"] for r in g["rules"]
             if r.get("alert") == "FibreNotServing" and r.get("labels", {}).get("reason") == "missed_uploads"]
    assert len(rules) == 1, "one missed_uploads rule, not %d" % len(rules)
    m = re.fullmatch(r"(.*) > (\d+) (and on .*)", " ".join(rules[0]["expr"].split()))
    assert m, rules[0]["expr"]
    chart = next(p for p in dash["panels"] if p.get("id") == 210)
    drawn = " ".join(chart["targets"][0]["expr"].split())
    value = "%s %s" % (m.group(1), m.group(3))
    # A gap up to 60 s (an upload settling) draws as 0, a larger one as it is, in minutes.
    assert drawn == "clamp_min(%s, 0) * ((%s) > bool 60) / 60" % (value, value), (drawn, value)
    level = chart["fieldConfig"]["defaults"]["thresholds"]["steps"][-1]["value"]
    assert level * 60 == int(m.group(2)), (level, m.group(2))
    print("210 draws %s %s, its line at %s s" % (m.group(1), m.group(3), m.group(2)))


def clock(args):
    """Clock maximum error draws the metric ClockNotSynced compares, its line at the rule's number."""
    for s in ("single", "split", "mainnet", "five"):
        rules = "build/%s/rules/celestia-mon.rules.yml" % ("mainnet" if s == "five" else s)
        found = [r for g in yaml.safe_load(open(rules))["groups"] for r in g["rules"]
                 if r.get("alert") == "ClockNotSynced"]
        assert len(found) == 1, "%s: one ClockNotSynced rule, not %d" % (s, len(found))
        m = re.fullmatch(r"(node_timex_maxerror_seconds)\{[^}]*\} >= (\d+)", " ".join(found[0]["expr"].split()))
        assert m, found[0]["expr"]
        panels = walk("build/%s/dashboard.json" % s)
        chart = panels[490]
        assert chart["targets"] and all(m.group(1) + "{" in t["expr"] for t in chart["targets"]), chart["targets"]
        level = chart["fieldConfig"]["defaults"]["thresholds"]["steps"][-1]["value"]
        assert level == int(m.group(2)), "%s: 490's line at %s, the rule's %s" % (s, level, m.group(2))
        assert chart["title"].endswith("(alert at %d s)" % level), "%s: 490 is titled %r" % (s, chart["title"])
        print("%s: 490 draws %s, its line at %s s" % (s, m.group(1), level))


def restarts(args):
    """Restarts draw the value the crash-loop rule compares, its line and title at the rule's number."""
    for s in ("single", "split", "mainnet", "five"):
        rules = "build/%s/rules/celestia-mon.rules.yml" % ("mainnet" if s == "five" else s)
        exprs = [" ".join(r["expr"].split()) for g in yaml.safe_load(open(rules))["groups"] for r in g["rules"]
                 if r.get("alert") == "ServiceDown" and "node_systemd_service_restart_total" in r["expr"]]
        found = [re.fullmatch(r"floor\(increase\(node_systemd_service_restart_total\{[^}]*\}\[30m\]\)\) > (\d+)", e)
                 for e in exprs]
        assert found and all(found), "%s: the crash-loop rules %s" % (s, exprs)
        levels = {int(m.group(1)) for m in found}
        assert len(levels) == 1, "%s: crash-loop levels %s" % (s, levels)
        n = levels.pop()
        panels = walk("build/%s/dashboard.json" % s)
        chart = panels[480]
        assert chart["targets"] and all("increase(node_systemd_service_restart_total{" in t["expr"] and "[30m]" in t["expr"]
                                        for t in chart["targets"]), chart["targets"]
        level = chart["fieldConfig"]["defaults"]["thresholds"]["steps"][-1]["value"]
        assert level == n, "%s: 480's line at %s, the rule's %s" % (s, level, n)
        assert chart["title"] == "Restarts (alert above %d in 30 min)" % n, "%s: 480 is titled %r" % (s, chart["title"])
        print("%s: 480 draws %d series, its line at %s" % (s, len(chart["targets"]), level))


CHECKS = {"missed-blocks": missed_blocks, "upload-readiness": upload_readiness, "fibre-behind": fibre_behind,
          "clock": clock, "restarts": restarts}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CHECKS:
        sys.exit(__doc__)
    CHECKS[sys.argv[1]](sys.argv[2:])
