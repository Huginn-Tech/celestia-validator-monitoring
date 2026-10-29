"""Compare each scenario's dashboard from the base branch's generator with this branch's, for the job summary.

  python tools/ci/dashboard_diff.py BUILD BASE [--same [OLD NEW]...]

The dashboard-report job renders the settings of every scenario once (tools/ci/dashboard_configs.sh BUILD)
and runs both generators on them: the base branch's into BUILD/<scenario>/base.json (left out when it
failed, its stderr in base.err) and this branch's into BUILD/<scenario>/head.json. A scenario the base
branch does not have (not in its SCENARIOS) gets neither base file, and the report calls it new. BASE
names the base commit in the report. Panels are matched by id, those of collapsed rows too (a row then lists their ids);
the keys outside the panels (links, templating, annotations, ...) are the panel "dashboard". The report, in
Markdown on stdout and in GITHUB_STEP_SUMMARY, gives per scenario "identical", or the number of values that
differ and the text changes behind them, with a table of those values, JSON-encoded and cut to about 60
characters around their first difference (at most 50 rows a scenario, then "and N more").

Without --same it only reports: the exit code is 0 unless this script itself breaks. With --same, each
OLD NEW pair after it a text change this branch makes on purpose, it is a check: it exits 1 unless the
base branch's generator made the dashboard of every scenario but the new ones and each value that differs
is a text whose base value, with every OLD replaced by its NEW, is this branch's. The report names those changes, marks each
value that differs otherwise, and ends with the result. Python 3 standard library only.
"""
import collections
import json
import os
import re
import sys

SCENARIOS = ("single", "split", "mainnet", "five", "nogov", "nopage", "table", "mocha", "fibre-apart", "units")
ROWS = 50
WIDTH = 60
WORD = re.compile(r"[\w.-]")
NONE = object()


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def panels(dashboard):
    """{"panel <id>": panel} for every panel, those of collapsed rows too, and {"dashboard": the rest}."""
    found = {"dashboard": {k: v for k, v in dashboard.items() if k != "panels"}}
    todo = list(dashboard.get("panels", []))
    while todo:
        p = todo.pop(0)
        inner = p.get("panels") or []
        found["panel %s" % p.get("id")] = dict(p, panels=[q.get("id") for q in inner]) if inner else p
        todo += inner
    return found


def leaves(value, path=""):
    """(key path, value) of every scalar, empty list and empty object under value."""
    if isinstance(value, dict) and value:
        for k in sorted(value):
            yield from leaves(value[k], "%s.%s" % (path, k) if path else k)
    elif isinstance(value, list) and value:
        for i, v in enumerate(value):
            yield from leaves(v, "%s[%d]" % (path, i))
    else:
        yield path, value


def encode(value):
    return "(none)" if value is NONE else json.dumps(value, ensure_ascii=False)


def order(name):
    """The dashboard's own keys first, then the panels by id."""
    if name == "dashboard":
        return (0, 0, "")
    number = name.split(" ", 1)[1]
    return (1, int(number), "") if number.isdigit() else (2, 0, number)


def differences(base, head):
    """(panel, title, key, base value, branch value) of each value that differs, NONE where a side has none."""
    rows = []
    a, b = panels(base), panels(head)
    for name in sorted(set(a) | set(b), key=order):
        pa, pb = a.get(name), b.get(name)
        title = "" if name == "dashboard" else str((pb or pa).get("title", ""))
        if pa is None or pb is None:
            rows.append((name, title, "(the panel)", NONE if pa is None else "here", NONE if pb is None else "here"))
            continue
        la, lb = dict(leaves(pa)), dict(leaves(pb))
        for key in sorted(set(la) | set(lb)):
            va, vb = la.get(key, NONE), lb.get(key, NONE)
            if encode(va) != encode(vb):
                rows.append((name, title, key, va, vb))
    return rows


def change(a, b):
    """The words of two strings around their difference: ("v1.2.0", "v1.3.0") for ".../v1.2.0/..." and ".../v1.3.0/..."."""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    j = 0
    while j < n - i and a[-1 - j] == b[-1 - j]:
        j += 1
    while i > 0 and WORD.match(a[i - 1]):
        i -= 1
    while j > 0 and WORD.match(a[len(a) - j]):
        j -= 1
    return a[i:len(a) - j], b[i:len(b) - j]


def clip(text, start):
    end = start + WIDTH
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def shorten(a, b):
    """The JSON of a and of b, each cut to about WIDTH characters from a little before their first difference."""
    ja, jb = encode(a), encode(b)
    i = 0
    while i < min(len(ja), len(jb)) and ja[i] == jb[i]:
        i += 1
    start = max(0, i - 20)
    return clip(ja, start), clip(jb, start)


def code(text):
    """text as a code span in a Markdown table cell."""
    text = text.replace("|", r"\|").replace("\n", " ")
    return "`` %s ``" % text if "`" in text else "`%s`" % text


def result(rows):
    """One line on the values that differ and the text changes behind them."""
    changes = collections.Counter(change(a, b) for _, _, _, a, b in rows if isinstance(a, str) and isinstance(b, str))
    other = len(rows) - sum(changes.values())
    said = ", ".join("%s → %s (%d)" % (code(old), code(new), n) for (old, new), n in changes.most_common(5))
    if len(changes) > 5:
        said += ", and %d more" % (len(changes) - 5)
    if not changes:
        return "%d values differ" % len(rows)
    if not other:
        return "%d values differ, each by a text change: %s" % (len(rows), said)
    return "%d values differ: %d by text changes (%s), %d otherwise" % (len(rows), sum(changes.values()), said, other)


def failed(build, scenario):
    """Why the base branch's generator made no dashboard for scenario."""
    try:
        with open(os.path.join(build, scenario, "base.err"), encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
    except OSError:
        lines = []
    return "the base branch's generator failed" + (": " + code(lines[-1][:200]) if lines else "")


def named(a, b, changes):
    """Whether a and b are texts and b is a with each named change (OLD, NEW) made."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    for old, new in changes:
        a = a.replace(old, new)
    return a == b


def main():
    args = sys.argv[1:]
    if len(args) < 2 or (len(args) > 2 and (args[2] != "--same" or len(args) % 2 == 0)):
        sys.exit(__doc__)
    build, base_name = args[:2]
    same = len(args) > 2
    changes = list(zip(args[3::2], args[4::2]))
    lines = ["## The dashboards against the base branch", "",
             "Each scenario's settings, rendered by this branch, made into a dashboard by the generator of the "
             "base branch (%s) and by this branch's." % base_name, ""]
    if same:
        lines += ["Each must come out the same as the base branch's, apart from the text changes this branch makes on "
                  "purpose: %s." % (", ".join("%s → %s" % (code(old), code(new)) for old, new in changes) or "none"), ""]
    lines += ["| scenario | result |", "|---|---|"]
    details, failing = [], []
    for s in SCENARIOS:
        head = load(os.path.join(build, s, "head.json"))
        base_path = os.path.join(build, s, "base.json")
        if not os.path.exists(base_path) and not os.path.exists(os.path.join(build, s, "base.err")):
            lines.append("| %s | new: the base branch has no such scenario |" % s)
            continue
        if not os.path.exists(base_path):
            lines.append("| %s | %s |" % (s, failed(build, s)))
            failing.append(s)
            continue
        rows = differences(load(base_path), head)
        if not rows:
            lines.append("| %s | identical |" % s)
            continue
        other = [r for r in rows if not named(r[3], r[4], changes)]
        if not same:
            lines.append("| %s | %s |" % (s, result(rows)))
        elif not other:
            lines.append("| %s | identical apart from the named text changes: %s |" % (s, result(rows)))
        else:
            lines.append("| %s | **%d values differ otherwise**: %s |" % (s, len(other), result(rows)))
            failing.append(s)
        # With --same, the values that differ otherwise first, each row marked.
        rows = sorted(rows, key=lambda r: named(r[3], r[4], changes)) if same else rows
        details += ["", "<details><summary>%s: %d values</summary>" % (s, len(rows)), "",
                    "| panel | title | key | base | this branch |" + (" change |" if same else ""),
                    "|---|---|---|---|---|" + ("---|" if same else "")]
        for name, title, key, a, b in rows[:ROWS]:
            ca, cb = shorten(a, b)
            mark = (" named |" if named(a, b, changes) else " **other** |") if same else ""
            details.append("| %s | %s | %s | %s | %s |%s"
                           % (name, title.replace("|", r"\|"), code(key), code(ca), code(cb), mark))
        if len(rows) > ROWS:
            details.append("| and %d more | | | | |" % (len(rows) - ROWS) + (" |" if same else ""))
        details += ["", "</details>"]
    if same:
        lines += ["", "**Every scenario comes out the same as on the base branch, apart from the named text changes.**"
                  if not failing else "**Not the same as on the base branch: %s.**" % ", ".join(failing)]
    text = "\n".join(lines + details) + "\n"
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    sys.exit(1 if same and failing else 0)


if __name__ == "__main__":
    main()
