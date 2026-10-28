"""The text tests of tools/ci/fixtures/messages.yml: Alertmanager's templates rendered by amtool.

  python tools/ci/check_messages.py SECTION

Renders each test of SECTION (telegram-discord or slack-pagerduty) with
`amtool template render` over monitoring/alertmanager-templates.tmpl, prints what it rendered, and
fails at the first output that is not the test's "equals" (trailing newlines aside, as the shell's
$(...) drops them) or lacks the test's "line". Run from the repo root with amtool on PATH; Python 3
and PyYAML.
"""
import os
import subprocess
import sys

import yaml

FIXTURES = os.path.join("tools", "ci", "fixtures")
TEMPLATES = os.path.join("monitoring", "alertmanager-templates.tmpl")


def render(test):
    done = subprocess.run(["amtool", "template", "render", "--template.glob=" + TEMPLATES,
                           "--template.data=" + os.path.join(FIXTURES, test["data"]),
                           "--template.type=" + test["type"], "--template.text=" + test["template"]],
                          capture_output=True, encoding="utf-8")
    if done.returncode:
        sys.exit("amtool failed on %s %s: %s" % (test["data"], test["template"], done.stderr.strip()))
    return done.stdout.rstrip("\n")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(os.path.join(FIXTURES, "messages.yml"), encoding="utf-8") as f:
        tests = yaml.safe_load(f)[sys.argv[1]]
    for test in tests:
        out = render(test)
        print("%s %s %s\n%s\n---" % (test["data"], test["type"], test["template"], out))
        if "equals" in test and out != test["equals"].rstrip("\n"):
            sys.exit("not the text wanted:\n%s" % test["equals"])
        if "line" in test and test["line"] not in out.split("\n"):
            sys.exit("no line reads: %s" % test["line"])
    print("%d texts as wanted" % len(tests))


if __name__ == "__main__":
    main()
