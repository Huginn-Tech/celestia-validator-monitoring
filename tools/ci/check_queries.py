#!/usr/bin/env python3
"""Send every query of a generated dashboard to Prometheus; fail on any error.

    check_queries.py --prometheus http://localhost:9090 DASHBOARD.json [DASHBOARD.json ...]

Each panel target's expr and each annotation's expr goes to /api/v1/query, with
Grafana's built-in $__interval and $__range (the only variables the dashboard uses: both in
its status board, $__interval also in the charts that count per time block) replaced by
60s and 3600s, as Grafana would replace them. An empty
result is fine (the CI Prometheus has no Celestia data); a response other than status
"success" (for example a PromQL parse error) fails the check.
Table panels are also checked without Prometheus: their columns are named from
Grafana's 'Value #<refId>' fields, so queries, column names and overrides must line up.
Python 3 standard library only.
"""
import argparse
import json
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


def substitute(expr):
    """expr with Grafana's built-in variables replaced as Grafana does: [$__interval:1m] offset
    -$__interval becomes [60s:1m] offset -60s."""
    return expr.replace("$__interval", "60s").replace("$__range", "3600s")


def query(base, expr):
    """Return None on success, else the error text."""
    data = urllib.parse.urlencode({"query": substitute(expr)}).encode()
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/v1/query", data=data, timeout=30) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        body = e.read()
    except urllib.error.URLError as e:
        return "Prometheus unreachable: %s" % e.reason
    try:
        reply = json.loads(body)
    except ValueError:
        return "not a Prometheus reply: %r" % body[:200]
    if reply.get("status") != "success":
        return "%s: %s" % (reply.get("errorType", "error"), reply.get("error", reply))
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prometheus", required=True, help="base URL, e.g. http://localhost:9090")
    parser.add_argument("dashboards", nargs="+")
    args = parser.parse_args()

    failed = checked = 0
    for path in args.dashboards:
        with open(path, encoding="utf-8") as f:
            dashboard = json.load(f)
        found = list(exprs(dashboard.get("panels", []))) + list(annotation_exprs(dashboard))
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
