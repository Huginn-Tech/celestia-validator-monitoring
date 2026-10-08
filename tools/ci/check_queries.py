#!/usr/bin/env python3
"""Send every query of a generated dashboard to Prometheus; fail on any error.

    check_queries.py --prometheus http://localhost:9090 DASHBOARD.json [DASHBOARD.json ...]

Each panel target's expr and each annotation's expr goes to /api/v1/query as it
is. An empty result is fine (the CI Prometheus has no Celestia data); a response
other than status "success" (for example a PromQL parse error) fails the check.
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


def annotation_exprs(dashboard):
    """(annotation name, expr) for every Prometheus annotation, in the expr/step or the target form."""
    for a in dashboard.get("annotations", {}).get("list", []):
        expr = a.get("expr") or a.get("target", {}).get("expr")
        if expr:
            yield "annotation " + a.get("name", ""), expr


def query(base, expr):
    """Return None on success, else the error text."""
    data = urllib.parse.urlencode({"query": expr}).encode()
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
        checked += len(found)
        print("%s: %d queries" % (path, len(found)))
    if checked == 0:
        print("no queries found")
        return 1
    print("%d queries, %d failed" % (checked, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
