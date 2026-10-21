#!/usr/bin/env python3
"""Send every query of a generated dashboard to Prometheus; fail on any error, warning or info.

    check_queries.py --prometheus http://localhost:9090 DASHBOARD.json [DASHBOARD.json ...]
    check_queries.py --prometheus http://localhost:9090 --stub OUT DASHBOARD.json [...]

Each panel target's expr and each annotation's expr goes to /api/v1/query, with
Grafana's built-in $__interval and $__range (the only variables the dashboard uses: both in
its status board, $__interval also in the charts that count per time block) replaced by
60s and 3600s, as Grafana would replace them. A reply other than status "success" (for
example a PromQL parse error) fails the check, and so does a reply that carries a warning or
an info: Grafana shows those as a notice icon on the panel (for example "metric might not be a
counter" for increase() over a name without a _total suffix). An empty result is fine.
Prometheus raises most notices per series, so they need data. With --stub the script writes,
instead of checking, a text exposition with series for every series selector of the dashboards
(as Prometheus's /api/v1/parse_query reads them), for a CI Prometheus to scrape before the check.
Table panels are also checked without Prometheus: their columns are named from
Grafana's 'Value #<refId>' fields, so queries, column names and overrides must line up.
Python 3 standard library only.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


def exprs(panels):
    """(panel title, expr) for every target, rows included."""
    for p in panels:
        yield from exprs(p.get("panels", []))
        for t in p.get("targets", []):
            if "expr" in t:
                yield p.get("title", ""), t["expr"]


def table_problems(panels):
    """(panel title, problem) for table panels whose queries and columns do not line up.
    Grafana calls the value column of query X 'Value #X' only when a table has more than one
    query, and gen_dashboard.py renames those columns, so every query must be an instant table
    query with a column name, every column name must belong to a query, and every override
    by name must match a column."""
    for p in panels:
        yield from table_problems(p.get("panels", []))
        if p.get("type") != "table":
            continue
        title = p.get("title", "")
        targets = p.get("targets", [])
        refids = {t.get("refId") for t in targets}
        rename = {}
        for tr in p.get("transformations", []):
            if tr.get("id") == "organize":
                rename = tr.get("options", {}).get("renameByName", {})
        if len(refids) < 2:
            yield title, "fewer than two queries: Grafana would call the value column 'Value'"
        for t in targets:
            if t.get("format") != "table" or not t.get("instant"):
                yield title, "query %s is not an instant query in table format" % t.get("refId")
            if "Value #%s" % t.get("refId") not in rename:
                yield title, "query %s has no column name" % t.get("refId")
        for key in rename:
            if key.startswith("Value #") and key[len("Value #"):] not in refids:
                yield title, "column name for %s, which is no query of the panel" % key
        for o in p.get("fieldConfig", {}).get("overrides", []):
            m = o.get("matcher", {})
            if m.get("id") == "byName" and m.get("options") not in rename.values():
                yield title, "override for %r matches no column" % m.get("options")


def annotation_exprs(dashboard):
    """(annotation name, expr) for every Prometheus annotation, in the expr/step or the target form."""
    for a in dashboard.get("annotations", {}).get("list", []):
        expr = a.get("expr") or a.get("target", {}).get("expr")
        if expr:
            yield "annotation " + a.get("name", ""), expr


def all_exprs(dashboard):
    return list(exprs(dashboard.get("panels", []))) + list(annotation_exprs(dashboard))


def substitute(expr):
    """expr with Grafana's built-in variables replaced as Grafana does: [$__interval:1m] offset
    -$__interval becomes [60s:1m] offset -60s."""
    return expr.replace("$__interval", "60s").replace("$__range", "3600s")


def call(base, path, expr):
    """Prometheus's JSON reply to expr on path, or an error text."""
    data = urllib.parse.urlencode({"query": substitute(expr)}).encode()
    try:
        with urllib.request.urlopen(base.rstrip("/") + path, data=data, timeout=30) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        body = e.read()
    except urllib.error.URLError as e:
        return "Prometheus unreachable: %s" % e.reason
    try:
        return json.loads(body)
    except ValueError:
        return "not a Prometheus reply: %r" % body[:200]


def query(base, expr):
    """Return None on success, else the error, warning or info text."""
    reply = call(base, "/api/v1/query", expr)
    if isinstance(reply, str):
        return reply
    if reply.get("status") != "success":
        return "%s: %s" % (reply.get("errorType", "error"), reply.get("error", reply))
    notes = (["warning: %s" % w for w in reply.get("warnings") or []]
             + ["info: %s" % i for i in reply.get("infos") or []])
    return "; ".join(notes) or None


def selectors(node):
    """The series selectors, plain and range, in a query tree from /api/v1/parse_query."""
    if isinstance(node, dict):
        if "matchers" in node:
            yield node
        for v in node.values():
            yield from selectors(v)
    elif isinstance(node, list):
        for v in node:
            yield from selectors(v)


def stub_value(matcher):
    """A label value the matcher accepts, or None where a missing label does."""
    kind, v = matcher["type"], matcher["value"]
    if kind == "=":
        return v or None
    if kind == "!=":
        return "stub" if v == "" else None
    if kind == "!~" and not re.fullmatch(v, ""):
        return None
    candidates = ([v.split("|")[0]] if kind == "=~" else []) + ["stub", "x"]
    ok = [c for c in candidates if bool(re.fullmatch(v, c)) == (kind == "=~")]
    return ok[0] if ok else None


def stub(base, dashboards):
    """A text exposition with one series per selector of the dashboards: its name, a value each
    matcher accepts, and host="stub" where no matcher names a host. A selector whose labels all
    appear on another selector of the same name adds no series of its own, as on a real target,
    where one series carries them all; a _bucket series without a le matcher comes as two buckets."""
    found = {}
    for path in dashboards:
        with open(path, encoding="utf-8") as f:
            dashboard = json.load(f)
        for _, expr in all_exprs(dashboard):
            reply = call(base, "/api/v1/parse_query", expr)
            if isinstance(reply, str) or reply.get("status") != "success":
                raise SystemExit("cannot parse %r: %s" % (expr, reply))
            for sel in selectors(reply["data"]):
                name = sel.get("name") or ""
                if not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", name):
                    continue
                labels = {m["name"]: stub_value(m) for m in sel["matchers"] if m["name"] != "__name__"}
                found.setdefault(name, set()).add(tuple(sorted((k, v) for k, v in labels.items() if v is not None)))
    lines = []
    for name in sorted(found):
        sets = found[name]
        for labels in sorted(sets):
            if any(set(labels) < set(other) for other in sets):
                continue
            labels = dict(labels)
            labels.setdefault("host", "stub")
            buckets = ["0.005", "+Inf"] if name.endswith("_bucket") and "le" not in labels else [None]
            for i, le in enumerate(buckets, 1):
                if le:
                    labels["le"] = le
                text = ",".join('%s="%s"' % (k, v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n"))
                                for k, v in sorted(labels.items()))
                lines.append("%s{%s} %d" % (name, text, i))
    return "".join(line + "\n" for line in lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prometheus", required=True, help="base URL, e.g. http://localhost:9090")
    parser.add_argument("--stub", metavar="OUT", help="write the stub exposition to OUT instead of checking")
    parser.add_argument("dashboards", nargs="+")
    args = parser.parse_args()

    if args.stub:
        text = stub(args.prometheus, args.dashboards)
        with open(args.stub, "w", encoding="utf-8") as f:
            f.write(text)
        print("%s: %d series" % (args.stub, text.count("\n")))
        return 0 if text else 1
    failed = checked = 0
    for path in args.dashboards:
        with open(path, encoding="utf-8") as f:
            dashboard = json.load(f)
        found = all_exprs(dashboard)
        for title, expr in found:
            error = query(args.prometheus, expr)
            if error:
                failed += 1
                print("FAIL %s [%s]\n  %s\n  %s" % (path, title, expr, error))
        for title, problem in table_problems(dashboard.get("panels", [])):
            failed += 1
            print("FAIL %s [%s]\n  %s" % (path, title, problem))
        checked += len(found)
        print("%s: %d queries" % (path, len(found)))
    if checked == 0:
        print("no queries found")
        return 1
    print("%d queries, %d failed" % (checked, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
