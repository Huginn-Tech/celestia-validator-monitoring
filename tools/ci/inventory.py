"""The test inventory of .github/workflows/ci.yml: every check listed, run and passed.

tools/ci/inventory.txt lists each check of ci.yml, a step with an id: <job>/<id> (a matrix case
under the job name it gives, matrix.include[].job), where its code lives, and the step's name. Each
job ends with .github/actions/marks, which uploads <job>/<id> for each of its steps that ran and
passed.

  python tools/ci/inventory.py lint INVENTORY WORKFLOW
      fails unless every step with an id has a line, under that step's name, and every line a step.
  python tools/ci/inventory.py check INVENTORY MARKS EVENT BASE
      fails unless every check that runs on EVENT (a line ending in "| pull_request" runs on pull
      requests only) has a mark in the directory MARKS, and lists what the revision BASE has and
      this inventory does not: the checks of BASE's inventory, or, while BASE has none, the steps
      of its ci.yml, matched by job and name.
  python tools/ci/inventory.py counts BASE
      counts, in HEAD and in BASE: the test cases of the promtool rule tests, the wording
      assertions of the CI code that runs doctor, the checks of every kind in all the CI code
      (Python asserts, jq -e and grep -q tests, amtool routes tests, wherever they live), and the
      tasks of molecule's verify playbook; and lists the wording assertions that only one of them
      has. It only reports.

Run it from the repository root; it reads HEAD and BASE with git. GITHUB_STEP_SUMMARY, when set,
receives the results in Markdown. Needs PyYAML.
"""

import collections
import glob
import os
import re
import subprocess
import sys

import yaml

INVENTORY = "tools/ci/inventory.txt"
WORKFLOW = ".github/workflows/ci.yml"
RULE_TESTS = "monitoring/rules/tests"
VERIFY = "molecule/default/verify.yml"

Check = collections.namedtuple("Check", "key where name event")

# The CI code that names doctor, and in it: a pattern of a doctor line ("^OK +8 ...", "^(OK|WARN)
# +14 ..."), a "says N 'text'" call, a text of a "for want in" list or of a want= line, and the
# pattern of any other grep; a quoted variable alone ("$want") is none of them.
DOCTOR = re.compile(r"\bdoctor\b")
DOCTOR_LINE = re.compile(r"\^\(?(OK|WARN|FAIL|SKIP|INFO|NOT YET)\b")
SAYS = re.compile(r"\bsays (\d+) '([^']*)'")
GREP = re.compile(r"\bgrep(?: +-[A-Za-z]+)*(?: +--)? +$")
VARIABLE = re.compile(r"\$(\w+|\{\w+\}|[@*])")
KINDS = {"line": "doctor line patterns", "says": "says N 'text'", "want": "wanted texts", "grep": "other grep texts"}
# The checks of every kind in all the CI code, every file under .github and tools/ci but this one, so
# that a check moved from a workflow step into a script counts on both sides.
CHECKS = {
    "Python assert statements": re.compile(r"^\s*assert\b", re.M),
    "jq -e tests": re.compile(r"\bjq -e"),
    "grep -q tests": re.compile(r"\bgrep(?: +-[A-Za-z]+)* +-[A-Za-z]*q"),
    "amtool routes tests": re.compile(r"\broutes test\b"),
}
CI_CODE = (".github", "tools/ci")


def git(*args):
    """git's output, or None when git fails (a revision or a file that does not exist)."""
    done = subprocess.run(["git", *args], capture_output=True, encoding="utf-8")
    return done.stdout if done.returncode == 0 else None


def read_inventory(text):
    """The checks of an inventory, in order."""
    checks = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [f.strip() for f in line.split("|")]
        if len(fields) not in (3, 4) or not all(fields) or fields[0].count("/") != 1:
            sys.exit(f"{INVENTORY}:{number}: not '<job>/<id> | where | step name [| event]': {line}")
        checks.append(Check(*fields, *([""] * (4 - len(fields)))))
    return checks


def steps(workflow):
    """(job, step id or None, step name) of every step of a workflow; a matrix job's once for each
    case of matrix.include that names its job. An unnamed step goes by its action."""
    for job_id, job in (workflow.get("jobs") or {}).items():
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        cases = [c for c in matrix.get("include") or [] if "job" in c] or [{}]
        for case in cases:
            for step in job.get("steps") or []:
                name = step.get("name") or step.get("uses", "").split("@")[0]
                for key, value in case.items():
                    name = name.replace("${{ matrix.%s }}" % key, str(value))
                yield case.get("job", job_id), step.get("id"), name


def summarize(lines):
    """Prints lines and appends them to the job summary."""
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")


def lint(inventory, workflow):
    with open(inventory, encoding="utf-8") as f:
        checks = read_inventory(f.read())
    with open(workflow, encoding="utf-8") as f:
        found = {f"{job}/{sid}": name for job, sid, name in steps(yaml.safe_load(f)) if sid}
    problems, listed = [], set()
    for c in checks:
        if c.key in listed:
            problems.append(f"{c.key} is listed twice")
        listed.add(c.key)
        if c.key not in found:
            problems.append(f"{c.key} is listed, but no step of {workflow} has that id in that job")
        elif found[c.key] != c.name:
            problems.append(f"{c.key} is listed as {c.name!r}, but the step is named {found[c.key]!r}")
        if c.event not in ("", "pull_request"):
            problems.append(f"{c.key}: the only event a line can name is pull_request, not {c.event!r}")
    problems += [f"{key}: a step with an id that {inventory} does not list" for key in found if key not in listed]
    for p in problems:
        print(p)
    if problems:
        sys.exit(f"{len(problems)} problems: every step of {workflow} with an id is a check, listed in {inventory} under its name")
    print(f"{len(checks)} checks: each step of {workflow} with an id is listed in {inventory}, under its name.")


def since_base(checks, base):
    """Markdown lines on what the base revision has and this inventory does not."""
    rev = (git("rev-parse", "--short", base) or "").strip()
    if not rev:
        return [f"There is no base revision {base} to compare with."]
    text = git("show", f"{base}:{INVENTORY}")
    if text is not None:
        old = read_inventory(text)
        keys, old_keys = {c.key for c in checks}, {c.key for c in old}
        gone = [c for c in old if c.key not in keys]
        new = [c.key for c in checks if c.key not in old_keys]
        lines = [f"The base ({rev}) lists {len(old)} checks: "
                 + (f"**{len(gone)} of them are not listed here**" if gone else "each is listed here too")
                 + (f"; new here: {', '.join(new)}." if new else "; none is new here.")]
        if gone:
            lines += ["", "| check | where | step |", "|---|---|---|"]
            lines += [f"| {c.key} | {c.where} | {c.name} |" for c in gone]
        return lines
    workflow = git("show", f"{base}:{WORKFLOW}")
    if workflow is None:
        return [f"The base ({rev}) has neither {INVENTORY} nor {WORKFLOW}."]
    named = {(c.key.split("/")[0], c.name): c.key for c in checks}
    kept, other = [], collections.Counter()
    for job, _, name in steps(yaml.safe_load(workflow)):
        if (job, name) in named:
            kept.append(named[(job, name)])
        else:
            other[f"{job}: {name}"] += 1
    new = [c.key for c in checks if c.key not in kept]
    lines = [f"The base ({rev}) has no inventory yet. Of the steps of its {WORKFLOW}, matched by job and step name, "
             f"{len(kept)} are checks of this inventory" + (f"; new here: {', '.join(new)}." if new else "."), "",
             f"Its other {sum(other.values())} steps are not in this inventory:", ""]
    lines += [f"- {step}" + (f" ({n} times)" if n > 1 else "") for step, n in sorted(other.items())]
    return lines


def check(inventory, marks, event, base):
    with open(inventory, encoding="utf-8") as f:
        checks = read_inventory(f.read())
    passed = set()
    for path in glob.glob(os.path.join(marks, "*.txt")):
        with open(path, encoding="utf-8") as f:
            passed.update(line.strip() for line in f if line.strip())
    due = [c for c in checks if c.event in ("", event)]
    missing = [c for c in due if c.key not in passed]
    lines = ["## Test inventory", ""]
    if missing:
        lines += [f"**{len(missing)} of the {len(due)} checks of {INVENTORY} did not run or did not pass:**", "",
                  "| check | where | step |", "|---|---|---|"]
        lines += [f"| {c.key} | {c.where} | {c.name} |" for c in missing]
    else:
        lines.append(f"All {len(due)} checks of {INVENTORY} ran and passed.")
    if len(due) < len(checks):
        lines.append(f"{len(checks) - len(due)} more run on pull requests only.")
    summarize(lines + [""] + since_base(checks, base))
    sys.exit(1 if missing else 0)


def files(rev, folder):
    return (git("ls-tree", "-r", "--name-only", rev, "--", folder) or "").split()


def literals(line):
    """(position, text) of each quoted string of a line of shell, before a comment."""
    found, i = [], 0
    while i < len(line):
        c = line[i]
        if c == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        if c == "\\":
            i += 2
            continue
        if c in "'\"":
            j = i + 1
            while j < len(line) and line[j] != c:
                j += 2 if c == '"' and line[j] == "\\" else 1
            if j >= len(line):
                break
            found.append((i, line[i + 1:j]))
            i = j
        i += 1
    return found


def assertions(text):
    """The wording assertions of a piece of CI code, by kind and text."""
    found = collections.Counter()
    in_list = False
    for line in text.splitlines():
        stripped = line.strip()
        in_list = in_list or stripped.startswith("for want in ")
        wanted = in_list or re.match(r"want\+?=", stripped)
        said = [m.group(2) for m in SAYS.finditer(line)]
        found.update(("says", t) for t in said)
        for at, lit in literals(line):
            if lit in said:
                said.remove(lit)
            elif VARIABLE.fullmatch(lit):
                continue
            elif DOCTOR_LINE.match(lit):
                found["line", lit] += 1
            elif wanted:
                found["want", lit] += 1
            elif GREP.search(line[:at]):
                found["grep", lit] += 1
        in_list = in_list and stripped.endswith("\\")
    return found


def doctor_assertions(rev):
    """The wording assertions of the CI code at rev that runs doctor: the run text of each workflow
    step that names it, and each shell script under tools/ci that does (lib.sh too, which holds
    the helpers of the doctor scripts)."""
    found = collections.Counter()
    for path in files(rev, ".github/workflows"):
        if path.endswith(".yml"):
            for job in (yaml.safe_load(git("show", f"{rev}:{path}")) or {}).get("jobs", {}).values():
                for step in job.get("steps") or []:
                    if DOCTOR.search(step.get("run", "")):
                        found += assertions(step["run"])
    for path in files(rev, "tools/ci"):
        if path.endswith(".sh"):
            text = git("show", f"{rev}:{path}")
            if DOCTOR.search(text):
                found += assertions(text)
    return found


def ci_checks(rev):
    """The number of checks of each kind of CHECKS in all the CI code at rev."""
    found = collections.Counter()
    for path in [p for folder in CI_CODE for p in files(rev, folder)]:
        if path != "tools/ci/inventory.py":
            text = git("show", f"{rev}:{path}") or ""
            for kind, pattern in CHECKS.items():
                found[kind] += len(pattern.findall(text))
    return found


def rule_tests(rev):
    total = 0
    for path in files(rev, RULE_TESTS):
        if path.endswith(".test.yml"):
            for test in (yaml.safe_load(git("show", f"{rev}:{path}")) or {}).get("tests") or []:
                total += len(test.get("alert_rule_test") or []) + len(test.get("promql_expr_test") or [])
    return total


def verify_tasks(rev):
    def count(tasks):
        return sum(count(t.get("block")) + count(t.get("rescue")) + count(t.get("always")) if "block" in t else 1
                   for t in tasks or [])
    text = git("show", f"{rev}:{VERIFY}")
    return sum(count(play.get("tasks")) for play in yaml.safe_load(text) or []) if text else 0


def counts(base):
    rev = (git("rev-parse", "--short", base) or "").strip()
    if not rev:
        summarize(["## Tests here and on the base", "", f"There is no base revision {base} to compare with."])
        return
    here, there = doctor_assertions("HEAD"), doctor_assertions(base)
    rows = [("promtool rule test cases", rule_tests(base), rule_tests("HEAD"))]
    rows += [(f"doctor wording assertions: {KINDS[k]}", sum(n for (kind, _), n in there.items() if kind == k),
              sum(n for (kind, _), n in here.items() if kind == k)) for k in KINDS]
    checks_there, checks_here = ci_checks(base), ci_checks("HEAD")
    rows += [(f"CI code checks: {k}", checks_there[k], checks_here[k]) for k in CHECKS]
    rows += [("molecule verify tasks", verify_tasks(base), verify_tasks("HEAD"))]
    lines = ["## Tests here and on the base", "",
             f"| | base ({rev}) | here | |", "|---|---|---|---|"]
    lines += [f"| {what} | {b} | {h} | {'same' if b == h else 'more' if h > b else '**fewer**'} |" for what, b, h in rows]
    for title, side in (("only in the base", there - here), ("only here", here - there)):
        lines += ["", f"Doctor wording assertions {title}: {sum(side.values()) or 'none'}"]
        if side:
            lines += ["", "| kind | text | times |", "|---|---|---|"]
            lines += [f"| {KINDS[kind]} | `{cell(text)}` | {n} |" for (kind, text), n in sorted(side.items())]
    summarize(lines)


def cell(text):
    """text for a Markdown table cell."""
    return text.replace("|", r"\|")


def main():
    args = sys.argv[1:]
    if args[:1] == ["lint"] and len(args) == 3:
        lint(*args[1:])
    elif args[:1] == ["check"] and len(args) == 5:
        check(*args[1:])
    elif args[:1] == ["counts"] and len(args) == 2:
        counts(args[1])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
