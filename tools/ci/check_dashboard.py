"""Checks that a dashboard panel and the alert rule it shows read the same, for the dashboard job of
.github/workflows/ci.yml, on the dashboards and rules it generated under build/<scenario>/.

  python tools/ci/check_dashboard.py CHECK [FILE...]

  missed-blocks         the Validator tile and Missed blocks read the SignaturesMissed rule
  upload-readiness      Upload readiness and the Fibre tile, History and cards read the not_ready rules
  fibre-behind DASHBOARD RULES  Fibre behind the network draws the missed_uploads rule (the mainnet example)
  clock                 Clock maximum error draws the ClockNotSynced rule
  restarts              Restarts draw the crash-loop rule
  first-screen          the tiles, History and the Status cell mirror the rules and the inhibitions
  signed-share DASHBOARD OUT  writes the promtool test of the signed share, which the job then runs

Each fails with an assertion. Run from the repo root; Python 3 and PyYAML.
"""
import json
import re
import sys

import yaml

# The word colours of the tiles (named) and of History (its hex values).
RED, AMBER = ("red", "#F2495C"), ("orange", "#FF9830")
# The clause of ServiceDown's crash-loop rule for Fibre, which Servers now's Restarts repeats.
BOOT_GUARD = ' unless on (host) (time() - node_boot_time_seconds{job="node"} < 2400)'


def walk(path):
    """Every panel of a dashboard by id, those of collapsed rows too."""
    panels = {}
    todo = json.load(open(path))["panels"]
    while todo:
        p = todo.pop()
        panels[p["id"]] = p
        todo += p.get("panels", [])
    return panels


def rendered(scenario, alert):
    """The rules of alert in a scenario's rendered rules file."""
    path = "build/%s/rules/celestia-mon.rules.yml" % ("mainnet" if scenario == "five" else scenario)
    return [r for g in yaml.safe_load(open(path))["groups"] for r in g["rules"] if r.get("alert") == alert]


def branches(expr):
    """The branches of a chain, first to last: expr split at its 'or's outside every parenthesis and
    quoted string."""
    masked = re.sub(r'"(?:[^"\\]|\\.)*"', lambda m: " " * len(m.group(0)), expr)
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(masked):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth == 0 and masked.startswith(" or ", i):
            parts.append(expr[start:i])
            start = i + 4
    return parts + [expr[start:]]


def code(branch):
    """The code a chain's branch gives, from its '* 0 + 3' or '* 0 - 1' at the end; None without one."""
    m = re.search(r"\* 0 ([+-]) (\d+)\)*$", branch)
    return float(m.group(1) + m.group(2)) if m else None


def words(panel, ref):
    """{code: (word, colour)} of query ref: the value mappings of its override, else the panel's."""
    fc = panel["fieldConfig"]
    own = [p["value"] for o in fc["overrides"] if o["matcher"] == {"id": "byFrameRefID", "options": ref}
           for p in o["properties"] if p["id"] == "mappings"]
    found = {}
    for m in own[0] if own else fc["defaults"].get("mappings", []):
        if m["type"] == "value":
            found.update({float(k): (v["text"], v.get("color")) for k, v in m["options"].items()})
    return found


def worst_first(name, panel, target):
    """Every branch of target's chain that shows a red word comes before any that shows an amber one."""
    shown = words(panel, target["refId"])
    colours = [shown.get(code(b), (None, None))[1] for b in branches(target["expr"])]
    reds = [i for i, c in enumerate(colours) if c in RED]
    ambers = [i for i, c in enumerate(colours) if c in AMBER]
    assert reds and ambers and max(reds) < min(ambers), "%s: an amber word comes first: %s" % (name, colours)


def missed_blocks(args):
    """The Validator tile holds SignaturesMissed's comparison; Missed blocks draws its two sides at its share."""
    for s in ("single", "split", "mainnet", "five"):
        rules = "build/%s/rules/celestia-mon.rules.yml" % ("mainnet" if s == "five" else s)
        rule = [r for g in yaml.safe_load(open(rules))["groups"] for r in g["rules"]
                if r.get("alert") == "SignaturesMissed"]
        assert len(rule) == 1, "%s: one SignaturesMissed rule, not %d" % (s, len(rule))
        expr = " ".join(rule[0]["expr"].split())
        m = re.fullmatch(r"max\((max by \(host\) \(.+\)) > on \(host\) ([0-9.]+) \* "
                         r"(max by \(host\) \(delta\(cometbft_consensus_height\{[^}]*\}\[1h\]\)\)) "
                         r"(unless on \(host\) \(.+ > \d+\))\)", expr)
        assert m, expr
        a, r, b, lag = m.groups()
        panels = walk("build/%s/dashboard.json" % s)
        text = lambda i: [" ".join(t["expr"].split()) for t in panels[i]["targets"]]
        assert any(expr in t for t in text(11)), "%s: tile 11 lacks %s" % (s, expr)
        for i in (117, 111):
            shares = [t for t in text(i) if a in t and b in t and lag in t]
            assert shares, "%s: %d draws no %s over %s, %s" % (s, i, a, b, lag)
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
    # The chart draws the gap also when the blob carries the validator's signature (the rule's unless).
    m = re.fullmatch(r"(.*) > (\d+) (and on .*?)(?: unless on .*)?", " ".join(rules[0]["expr"].split()))
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
        m = re.fullmatch(r"min_over_time\((node_timex_maxerror_seconds)\{[^}]*\}\[3m\]\) >= (\d+)",
                         " ".join(found[0]["expr"].split()))
        assert m, found[0]["expr"]
        panels = walk("build/%s/dashboard.json" % s)
        chart = panels[490]
        assert chart["targets"] and all(m.group(1) + "{" in t["expr"] for t in chart["targets"]), chart["targets"]
        level = chart["fieldConfig"]["defaults"]["thresholds"]["steps"][-1]["value"]
        assert level == int(m.group(2)), "%s: 490's line at %s, the rule's %s" % (s, level, m.group(2))
        assert chart["title"].endswith("(alert at %d s)" % level), "%s: 490 is titled %r" % (s, chart["title"])
        print("%s: 490 draws %s, its line at %s s" % (s, m.group(1), level))


def restarts(args):
    """Restarts draw the value the crash-loop rule compares, its line and title at the rule's number. Only
    Fibre's rule leaves out a host that just booted, and Servers now's Restarts does the same for Fibre."""
    for s in ("single", "split", "mainnet", "five"):
        crash = [r for r in rendered(s, "ServiceDown") if "node_systemd_service_restart_total" in r["expr"]]
        exprs = [" ".join(r["expr"].split()) for r in crash]
        found = [re.fullmatch(r"floor\(increase\(node_systemd_service_restart_total\{[^}]*\}\[30m\]\)\) > (\d+)(%s)?"
                              % re.escape(BOOT_GUARD), e) for e in exprs]
        assert found and all(found), "%s: the crash-loop rules %s" % (s, exprs)
        levels = {int(m.group(1)) for m in found}
        assert len(levels) == 1, "%s: crash-loop levels %s" % (s, levels)
        guarded = {r["labels"]["unit"] for r, m in zip(crash, found) if m.group(2)}
        assert guarded == {r["labels"]["unit"] for r in crash} & {"fibre"}, "%s: the boot guard on %s" % (s, guarded)
        n = levels.pop()
        panels = walk("build/%s/dashboard.json" % s)
        chart = panels[480]
        assert chart["targets"] and all("increase(node_systemd_service_restart_total{" in t["expr"] and "[30m]" in t["expr"]
                                        for t in chart["targets"]), chart["targets"]
        level = chart["fieldConfig"]["defaults"]["thresholds"]["steps"][-1]["value"]
        assert level == n, "%s: 480's line at %s, the rule's %s" % (s, level, n)
        assert chart["title"] == "Restarts (alert above %d in 30 min)" % n, "%s: 480 is titled %r" % (s, chart["title"])
        print("%s: 480 draws %d series, its line at %s" % (s, len(chart["targets"]), level))
    table = [t["expr"] for t in walk("build/table/dashboard.json")[26]["targets"] if BOOT_GUARD in t["expr"]]
    assert len(table) == 1 and table[0].count(BOOT_GUARD) == 1, table
    print("table: Servers now leaves out a host that just booted, for Fibre only")


def first_screen(args):
    """What the first screen repeats of the rules and the inhibitions, where nothing else ties the two."""
    for s in ("split", "mainnet", "five"):
        panels = walk("build/%s/dashboard.json" % s)
        # The Fibre tile reads the crash loop, ahead of the reasons it holds back as messages.
        tile = branches(panels[14]["targets"][0]["expr"])
        crash = next(b for b in tile if 'alertname="ServiceDown",unit="fibre",alertstate="firing"' in b)
        reasons = [i for i, b in enumerate(tile) if 'alertname="FibreNotServing",reason=' in b and "severity=" not in b]
        assert reasons and tile.index(crash) < min(reasons), "%s: 14 reads %s after a reason" % (s, crash)
        assert words(panels[14], "A")[code(crash)] == ("Fibre crash", "orange"), crash
        # Grafana's lastNotNull turns NaN into no value, so a word mapped from NaN never shows.
        for p in panels.values():
            if p.get("type") == "stat" and "lastNotNull" in p["options"]["reduceOptions"]["calcs"]:
                d = p["fieldConfig"]["defaults"]
                for m in d.get("mappings", []):
                    if m["type"] == "special" and m["options"]["match"] == "nan":
                        assert m["options"]["result"]["text"] == d.get("noValue"), "%s: %d maps NaN" % (s, p["id"])
        for i, word in ((3, "Stalled"), (205, "No uploads")):
            last = branches(panels[i]["targets"][0]["expr"])[-1]
            assert code(last) == -1 and words(panels[i], "A")[-1][0] == word, "%s: %d: %s" % (s, i, last)
        print("%s: 14 reads the crash loop; 3 and 205 say their words from a code" % s)
    for s in ("mainnet", "five"):
        panels = walk("build/%s/dashboard.json" % s)
        worst_first("%s: tile 17" % s, panels[17], panels[17]["targets"][0])
    for s in ("single", "split", "mainnet", "five"):
        panels = walk("build/%s/dashboard.json" % s)
        lines = [t for t in panels[20]["targets"] if t["refId"].startswith("S")]
        assert lines, "%s: History has no server line" % s
        for t in lines:
            worst_first("%s: History's %s" % (s, t["legendFormat"]), panels[20], t)
        # A long block keeps a page apart from a stall at another moment of it.
        vns, nna = 'alertname="ValidatorNotSigning"', 'alertname="NodeNotAdvancing"'
        fired = '(ALERTS{alertstate="firing",%s} unless on () ALERTS{alertstate="firing",%s})' % (vns, nna)
        line = next(t["expr"] for t in panels[20]["targets"] if t["refId"] == "B")
        band = next(t["expr"] for t in panels[25]["targets"] if t["refId"] == "A")
        assert fired in line and "(ALERTS{%s} unless on () ALERTS{%s})" % (vns, nna) in line, line
        assert fired in band, band
        print("%s: the server lines read red first; a page stays apart from a stall, minute by minute" % s)
    for s in ("split", "mainnet"):
        panels = walk("build/%s/dashboard.json" % s)
        # Alertmanager holds an alert back only while its cause fires, and a message never holds a page.
        status = next(t["expr"] for t in panels[10]["targets"] if "* 0 + 3 unless on (alertname) (" in t["expr"])
        start = status.index("* 0 + 3 unless on (alertname) (")
        end = status.index('alertstate="pending",severity="page"', start)
        held = re.findall(r"ALERTS\{[^}]*\}", status[start:end])
        causes = [a for a in held if 'alertname="FibreNotServing"' not in a]
        assert causes and all('alertstate="firing"' in a for a in causes), causes
        assert not any('unit="fibre"' in a or "FibreBothActive" in a for a in causes), causes
        assert 'ALERTS{alertname="ServiceDown",unit="fibre"}' in status[end:], "%s: 10 lacks the crash loop's hold" % s
        assert 'ALERTS{alertname="FibreBothActive"}' in status[end:], "%s: 10 lacks FibreBothActive's hold" % s
        assert 'reason!~"not_ready|no_answer|not_served"' in status and 'reason!="not_ready"' not in status, s
        # The card goes red for the page that went out, ahead of the crash loop.
        page = 'ALERTS{alertname="FibreNotServing",reason="not_ready",severity="page",alertstate="firing"}'
        loop = 'max by (host) (ALERTS{alertname="ServiceDown",unit="fibre",alertstate="firing"})'
        cards = [t["expr"] for p in panels.values() for t in p.get("targets", []) if loop in t.get("expr", "")]
        assert cards and all(page in e and e.index(page) < e.index(loop) for e in cards), "%s: %s" % (s, cards)
        print("%s: firing pages fold into firing causes only; %d cards read Not ready first" % (s, len(cards)))
    panels = walk("build/mainnet/dashboard.json")
    # wrong_host: a fresh reading of Tensile, and not while two Fibres run.
    gate = "time() - celestia_mon_tensile_last_success_timestamp_seconds < 540"
    wrong = [" ".join(r["expr"].split()) for r in rendered("mainnet", "FibreDown") if r["labels"].get("reason") == "wrong_host"]
    assert wrong and all(gate in e and "unless on () count(" in e for e in wrong), wrong
    tile = panels[15]["targets"][0]["expr"]
    assert tile.count(gate) == len(wrong) and "unless on () (count(" in tile, tile
    # UpgradeSignalMissing: a message, then a page at 1/3 on the same conditions; only the page is red.
    signal = sorted(rendered("mainnet", "UpgradeSignalMissing"), key=lambda r: r["labels"]["severity"])
    assert [(r["labels"]["severity"], r["for"]) for r in signal] == [("message", "10m"), ("page", "10m")], signal
    message, page = (" ".join(r["expr"].split()) for r in signal)
    assert " > 0 and " in message and message.replace(" > 0 and ", " > 1/3 and ", 1) == page, (message, page)
    asks = [b for b in branches(panels[16]["targets"][0]["expr"]) if 'alertname="UpgradeSignalMissing"' in b]
    shown = [words(panels[16], "A")[code(b)] for b in asks]
    assert shown == [("Not signalled", "red"), ("Not signalled", "orange")], asks
    assert 'severity="page"' in asks[0] and "severity" not in asks[1], asks
    print("mainnet: 15 reads %d wrong_host rules; 16 is red for the page alone" % len(wrong))


def signed_share(args):
    """A promtool test of the signed share on one dashboard's two queries, for each story of an hour in
    three. The share stays whole only where the node with the key runs unread."""
    path, out = args
    lsh = 'cometbft_consensus_validator_last_signed_height{job="celestia-app"}'
    exprs = [t["expr"] for p in walk(path).values() for t in p.get("targets", [])
             if "sum_over_time(" in t.get("expr", "") and "absent(%s)" % lsh in t["expr"]]
    assert len(exprs) == 2, exprs
    unit = re.search(r'node_systemd_unit_state\{name="([^"]+)",state="active"\}', exprs[0]).group(1)
    exprs = [e.replace("$__range", "3h").replace("[30d:", "[3h:") for e in exprs]

    whole, hour = "1x180", "1x59 0x59 1x60"
    climbs, gap = "1000+20x180", "1000+20x59 stale _x59 3400+20x60"
    frozen = "1000+20x59 2180x120"
    loops = "1x59 %s 1x60" % " ".join(["0", "1"] * 30)

    def series(app=hour, app2=whole, height=gap, height2=climbs, key=gap, node=whole, active=whole):
        """A minute's samples over 3 hours: val-1 holds the key, fibre-1 is the backup node."""
        rows = [('up{job="prometheus",host="mon-1"}', whole),
                ('up{job="celestia-app",host="val-1"}', app),
                ('up{job="celestia-app",host="fibre-1"}', app2),
                ('up{job="node",host="val-1"}', node),
                ('up{job="node",host="fibre-1"}', whole),
                ('cometbft_consensus_height{job="celestia-app",host="val-1"}', height),
                ('cometbft_consensus_height{job="celestia-app",host="fibre-1"}', height2),
                (lsh.replace("}", ',host="val-1"}'), key),
                ('node_systemd_unit_state{host="val-1",name="%s",state="active"}' % unit, active),
                ('node_systemd_unit_state{host="fibre-1",name="%s",state="active"}' % unit, whole)]
        return [{"series": name, "values": values} for name, values in rows]

    stories = [
        ("the node runs unread: its hour leaves the share", series(), "> bool 0.99"),
        ("the node stopped: unsigned", series(active=hour), "< bool 0.9"),
        ("the node crash loops: unsigned", series(active=loops), "< bool 0.9"),
        ("the server does not answer: unsigned", series(node=hour, active="1x59 stale _x59 1x60"), "< bool 0.9"),
        ("the node answers and does not sign: unsigned", series(app=whole, height=climbs, key=frozen), "< bool 0.9"),
        ("the backup node runs unread and the validator does not sign: unsigned",
         series(app=whole, app2=hour, height=climbs, height2=gap, key=frozen), "< bool 0.9"),
    ]
    tests = [{"name": name, "interval": "1m", "input_series": rows,
              "promql_expr_test": [{"expr": "%s %s" % (e, want), "eval_time": "3h",
                                    "exp_samples": [{"labels": "{}", "value": 1}]} for e in exprs]}
             for name, rows, want in stories]
    with open(out, "w") as f:
        yaml.safe_dump({"rule_files": [], "evaluation_interval": "1m", "tests": tests}, f)
    print("%s: %d stories, %d queries" % (out, len(stories), len(exprs)))


CHECKS = {"missed-blocks": missed_blocks, "upload-readiness": upload_readiness, "fibre-behind": fibre_behind,
          "clock": clock, "restarts": restarts, "first-screen": first_screen, "signed-share": signed_share}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CHECKS:
        sys.exit(__doc__)
    CHECKS[sys.argv[1]](sys.argv[2:])
