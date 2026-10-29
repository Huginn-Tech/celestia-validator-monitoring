"""Check that the upstream links, version phrases and metric names follow the Celestia pin.

versions.yml pins the celestia-app release this repository follows (celestia_app).
monitoring/upstream.yml names the celestia-core and Cosmos SDK fork releases that celestia-app's
go.mod builds with, the releases kept as floors, and every name the repository takes from the
three sources, with the file of each release that defines it.

Offline (the lint job of .github/workflows/ci.yml) it reads every text file of the repository
and fails on:
- a celestiaorg/celestia-app, celestiaorg/celestia-core or celestiaorg/cosmos-sdk link (blob or
  tree) at another ref than its pinned release, a cosmos/cosmos-sdk link (celestia-app builds
  with the fork), and any other celestiaorg or cosmos link at main or master;
- "celestia-app" or "Fibre" followed by another version than the pin, unless the version is a
  floor and the phrase says so ("and newer", "or later", "older than", "before", ...);
  "celestia-app" followed by another major version; "celestia-core" or "cosmos-sdk" followed by
  another version than upstream.yml's;
- a cometbft_ or fibre_server_ metric name that no metrics entry of upstream.yml lists;
- an entry of upstream.yml that is malformed or whose expression does not compile, and an
  exception that no line holds any more.
For a link or a phrase it prints the sed that moves it to the pin: check that the sentence
still holds at that release before running it.

With --online, given the three releases cloned (.github/workflows/upstream.yml), it also fails
on each expression of upstream.yml that does not match in its file, on a celestia-app go.mod
that puts other celestia-core or Cosmos SDK versions in place, and on a pinned link whose file,
directory or Markdown heading does not exist at its release.

Usage, from the repository root (needs PyYAML):
  python tools/ci/check_upstream.py
  python tools/ci/check_upstream.py --online <celestia-app> <celestia-core> <cosmos-sdk>
  python tools/ci/check_upstream.py --env
--env prints CELESTIA_APP, CELESTIA_CORE and COSMOS_SDK as KEY=VALUE lines for $GITHUB_ENV.
GITHUB_STEP_SUMMARY, when set, receives the counts and the problems in Markdown.
"""

import os
import re
import sys

import yaml

TABLE = "monitoring/upstream.yml"
# The repositories of upstream.yml's repo keys, all under github.com/celestiaorg.
REPOS = {"app": "celestia-app", "core": "celestia-core", "sdk": "cosmos-sdk"}
SKIP_DIRS = {".git", ".cache", ".venv", "__pycache__", "build", "node_modules"}
# https://github.com/<org>/<repo>/<blob|tree>/<ref>/<path>#<anchor>
LINK = re.compile(r"https://github\.com/(celestiaorg|cosmos)/([\w.-]+)/(blob|tree)/([^/\s]+)/([^\s)\]\"'`<>#]*)(#[^\s)\]\"'`<>]*)?")
APP_VERSION = re.compile(r"\b(celestia-app|fibre) v(\d+\.\d+\.\d+)\b", re.IGNORECASE)
APP_MAJOR = re.compile(r"\bcelestia-app v(\d+)\b(?!\.\d)", re.IGNORECASE)
CORE_VERSION = re.compile(r"\bcelestia-core v(\d+\.\d+\.\d+)\b", re.IGNORECASE)
SDK_VERSION = re.compile(r"\bcosmos-sdk v(\d+\.\d+\.\d+)\b", re.IGNORECASE)
# The words that make a floor phrase, right after the version or right before the name.
FLOOR_AFTER = (" and newer", " or newer", " and later", " or later")
FLOOR_BEFORE = ("older than", "before")
METRIC = re.compile(r"\b(?:cometbft|fibre_server)_[a-z0-9_]*")
VERSION = re.compile(r"v\d+\.\d+\.\d+")
# A replace line of celestia-app's go.mod: module, then the celestiaorg repository in its place.
GO_MOD = r"^\s*(?:replace\s+)?github\.com/{} => github\.com/celestiaorg/{} (\S+)\s*$"


class Report:
    """The problems found, each with its place and, when there is one, the command that fixes it."""

    def __init__(self):
        self.problems = []

    def fail(self, where, message, fix=None):
        self.problems.append((where, message, fix))


def sed(path, number, old, new):
    """The sed command that replaces old with new on one line of a file."""
    pattern = old.replace(".", "\\.")
    return f"sed -i '{number}s#{pattern}#{new}#g' {path}"


def load_pins(report):
    """(celestia-app pin, upstream.yml) from versions.yml and the table."""
    with open("versions.yml", encoding="utf-8") as f:
        app = str(yaml.safe_load(f)["celestia_mon_versions"]["celestia_app"]["version"])
    with open(TABLE, encoding="utf-8") as f:
        table = yaml.safe_load(f)
    if not VERSION.fullmatch(app):
        report.fail("versions.yml", f"celestia_app.version {app} is not a release tag such as v1.2.3")
    for key in ("celestia_core", "cosmos_sdk"):
        if not VERSION.fullmatch(str(table.get(key))):
            report.fail(TABLE, f"{key} {table.get(key)} is not a release tag such as v1.2.3")
    for floor in table.get("floors") or []:
        if not VERSION.fullmatch(str(floor)):
            report.fail(TABLE, f"floor {floor} is not a release tag such as v1.2.3")
    return app, table


def load_entries(table, report):
    """The expressions of the table as (repo, file, what, compiled expression), and the metric
    names its metrics entries list."""
    exprs, metrics = [], set()
    for i, entry in enumerate(table.get("files") or []):
        where = f"{TABLE}: files[{i}]"
        if not isinstance(entry, dict) or entry.get("repo") not in REPOS or not isinstance(entry.get("file"), str):
            report.fail(where, "needs repo (app, core or sdk) and file")
            continue
        repo, path = entry["repo"], entry["file"]
        named = entry.get("metrics") or {}
        found = entry.get("find") or []
        if not isinstance(named, dict) or not isinstance(found, list) or not (named or found):
            report.fail(where, f"{repo} {path}: needs metrics (a name to expression map) or find (a list), or both")
            continue
        items = []
        for name, value in named.items():
            if not METRIC.fullmatch(str(name)):
                report.fail(where, f"{name} is not a cometbft_ or fibre_server_ metric name")
            metrics.add(str(name))
            items += [(str(name), v) for v in (value if isinstance(value, list) else [value])]
        items += [("find", v) for v in found]
        for what, pattern in items:
            try:
                exprs.append((repo, path, what, re.compile(pattern)))
            except (re.error, TypeError) as e:
                report.fail(where, f"{repo} {path}: {what}: {pattern!r} is not a regular expression ({e})")
    return exprs, metrics


def load_exceptions(table, report):
    """The exceptions of the table as (file, text) pairs."""
    out = []
    for i, e in enumerate(table.get("exceptions") or []):
        if isinstance(e, dict) and isinstance(e.get("file"), str) and isinstance(e.get("text"), str) and e["text"]:
            out.append((e["file"], e["text"]))
        else:
            report.fail(f"{TABLE}: exceptions[{i}]", "needs file and text")
    return out


def known(name, metrics):
    """Whether a metric name found in the repository is one the table lists: the name itself, a
    histogram's _bucket, _count or _sum, or a prefix (ending in _) of a listed name."""
    if name in metrics:
        return True
    if name.endswith("_"):
        return any(m.startswith(name) for m in metrics)
    stem = re.sub(r"_(bucket|count|sum)$", "", name)
    return stem != name and stem in metrics


def repo_files():
    """Every text file of the repository as (path, its lines)."""
    for top, dirs, names in os.walk("."):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            path = os.path.relpath(os.path.join(top, name)).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError):
                continue
            yield path, text.splitlines()


def scan(app, table, metrics, exceptions, report, counts):
    """The offline checks over every text file; returns the links at a pinned release as
    (where, repository, blob or tree, path, anchor) for the online check."""
    core, sdk = str(table.get("celestia_core")), str(table.get("cosmos_sdk"))
    pins = {"celestia-app": app, "celestia-core": core, "cosmos-sdk": sdk}
    floors = {str(f) for f in table.get("floors") or []}
    major = app.lstrip("v").split(".")[0]
    used, pinned, names = set(), [], set()
    for path, lines in repo_files():
        for number, line in enumerate(lines, 1):
            where = f"{path}:{number}"
            for m in LINK.finditer(line):
                counts["upstream links"] += 1
                org, repo, kind, ref, target, anchor = m.groups()
                if anchor:
                    anchor = anchor[1:].rstrip(".,;:")
                else:
                    target = target.rstrip(".,;:")
                old = f"github.com/{org}/{repo}/{kind}/{ref}/"
                if (org, repo) == ("cosmos", "cosmos-sdk"):
                    report.fail(where, f"links cosmos/cosmos-sdk; celestia-app builds with the fork celestiaorg/cosmos-sdk {sdk}",
                                sed(path, number, old, f"github.com/celestiaorg/cosmos-sdk/{kind}/{sdk}/"))
                elif org == "celestiaorg" and repo in pins:
                    if ref == pins[repo]:
                        pinned.append((where, repo, kind, target, anchor))
                    else:
                        report.fail(where, f"links {repo} at {ref}, not at the pinned {pins[repo]}",
                                    sed(path, number, old, f"github.com/{org}/{repo}/{kind}/{pins[repo]}/"))
                elif ref in ("main", "master"):
                    report.fail(where, f"links {org}/{repo} at {ref}, which moves: link a release tag")
            for name in METRIC.findall(line):
                names.add(name)
                if not known(name, metrics):
                    report.fail(where, f"{name}: no metrics entry of {TABLE} lists it; confirm it in the pinned source and add it")
            skip = [(f, t) for f, t in exceptions if f == path and t in line]
            if skip:
                used.update(skip)
                continue
            for m in APP_VERSION.finditer(line):
                counts["version phrases"] += 1
                version = "v" + m.group(2)
                if version == app:
                    continue
                if version in floors and (line[m.end():].lower().startswith(FLOOR_AFTER)
                                          or line[:m.start()].rstrip().lower().endswith(FLOOR_BEFORE)):
                    continue
                report.fail(where, f'"{m.group(0)}" names another release than the pin {app}',
                            sed(path, number, m.group(0), f"{m.group(1)} {app}"))
            for m in APP_MAJOR.finditer(line):
                counts["version phrases"] += 1
                if m.group(1) != major:
                    report.fail(where, f'"{m.group(0)}" names another major release than the pin {app}',
                                sed(path, number, m.group(0), f"celestia-app v{major}"))
            for regex, want, what in ((CORE_VERSION, core, "celestia-core"), (SDK_VERSION, sdk, "cosmos-sdk")):
                for m in regex.finditer(line):
                    counts["version phrases"] += 1
                    if "v" + m.group(1) != want:
                        report.fail(where, f'"{m.group(0)}" names another release than {TABLE} ({what} {want})',
                                    sed(path, number, m.group(0), m.group(0)[:-len(m.group(1)) - 1] + want))
    for f, t in exceptions:
        if (f, t) not in used:
            report.fail(TABLE, f'exception for {f}: no line holds "{t}" any more; update or remove it')
    counts["upstream metric names in use"] = len(names)
    return pinned


def anchors(text):
    """The anchors GitHub gives the headings of a Markdown file (code blocks left out)."""
    out, seen, fence = set(), {}, None
    for line in text.splitlines():
        m = re.match(r" {0,3}(`{3,}|~{3,})", line)
        if m:
            if fence is None:
                fence = m.group(1)[0]
            elif m.group(1)[0] == fence:
                fence = None
            continue
        m = None if fence else re.match(r" {0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            slug = re.sub(r"[^\w\- ]", "", m.group(1).lower()).replace(" ", "-")
            n = seen.get(slug, 0)
            seen[slug] = n + 1
            out.add(slug if n == 0 else f"{slug}-{n}")
    return out


def read(path):
    """A file's text, None when it cannot be read (no such file)."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def online(roots, app, table, exprs, pinned, report, counts):
    """The checks against the cloned releases."""
    dirs = dict(zip(("app", "core", "sdk"), roots))
    gomod = read(os.path.join(dirs["app"], "go.mod")) or ""
    for module, fork, key in (("cometbft/cometbft", "celestia-core", "celestia_core"),
                              ("cosmos/cosmos-sdk", "cosmos-sdk", "cosmos_sdk")):
        m = re.search(GO_MOD.format(re.escape(module), re.escape(fork)), gomod, re.MULTILINE)
        found = m.group(1) if m else "nothing"
        if found != str(table.get(key)):
            report.fail(f"celestia-app {app} go.mod", f"puts celestiaorg/{fork} {found} in place of github.com/{module},"
                        f" and {TABLE} has {key}: {table.get(key)}; check the release notes of both, then update {key}")
    texts, missing = {}, set()
    for repo, path, what, regex in exprs:
        full = os.path.join(dirs[repo], path)
        if full not in texts:
            texts[full] = read(full)
        if texts[full] is None:
            if full not in missing:
                missing.add(full)
                report.fail(f"{REPOS[repo]} {path}", f"no such file at the release; find where its names went and update {TABLE}")
        elif regex.search(texts[full]):
            counts["expressions that match"] += 1
        else:
            report.fail(f"{REPOS[repo]} {path}", f"{what}: no match for {regex.pattern}; find the new name, update"
                        f" {TABLE} and every use of the old one")
    roots_by_repo = {REPOS[k]: v for k, v in dirs.items()}
    pins = {"celestia-app": app, "celestia-core": table.get("celestia_core"), "cosmos-sdk": table.get("cosmos_sdk")}
    headings = {}
    for where, repo, kind, target, anchor in pinned:
        full = os.path.join(roots_by_repo[repo], target)
        exists = os.path.isfile(full) if kind == "blob" else os.path.isdir(full)
        if not exists:
            report.fail(where, f"{repo} {pins[repo]} has no {'file' if kind == 'blob' else 'directory'} {target or '/'}")
            continue
        if anchor and target.endswith(".md"):
            if full not in headings:
                headings[full] = anchors(read(full) or "")
            if anchor.lower() not in headings[full]:
                report.fail(where, f"{repo} {pins[repo]} {target} has no heading for #{anchor}")
                continue
        counts["links that resolve"] += 1


def finish(app, table, report, counts):
    """Prints the result, writes the job summary, and exits 1 on any problem."""
    title = (f"celestia-app {app}, celestia-core {table.get('celestia_core')},"
             f" Cosmos SDK fork {table.get('cosmos_sdk')}")
    for where, message, fix in report.problems:
        print(f"{where}: {message}")
        if fix:
            print(f"  fix: {fix}")
    tally = ", ".join(f"{n} {what}" for what, n in counts.items())
    if report.problems:
        print(f"\n{len(report.problems)} problems with the upstream names of {title} ({tally}).")
    else:
        print(f"Upstream links, version phrases and metric names follow {title}: {tally}.")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## Upstream names: {title}\n\n| checked | count |\n|---|---|\n")
            for what, n in counts.items():
                f.write(f"| {what} | {n} |\n")
            if report.problems:
                f.write(f"\n**{len(report.problems)} problems.** Each with its fix, where there is one:\n\n```\n")
                for where, message, fix in report.problems:
                    f.write(f"{where}: {message}\n" + (f"  fix: {fix}\n" if fix else ""))
                f.write("```\n")
            else:
                f.write("\nNo problems.\n")
    sys.exit(1 if report.problems else 0)


def main():
    args = sys.argv[1:]
    if args not in ([], ["--env"]) and not (len(args) == 4 and args[0] == "--online"):
        sys.exit(__doc__)
    report = Report()
    app, table = load_pins(report)
    if args == ["--env"]:
        if report.problems:
            sys.exit("; ".join(message for _, message, _ in report.problems))
        print(f"CELESTIA_APP={app}\nCELESTIA_CORE={table['celestia_core']}\nCOSMOS_SDK={table['cosmos_sdk']}")
        return
    exprs, metrics = load_entries(table, report)
    exceptions = load_exceptions(table, report)
    counts = {"upstream links": 0, "version phrases": 0}
    pinned = scan(app, table, metrics, exceptions, report, counts)
    counts[f"expressions in {TABLE}"] = len(exprs)
    if args:
        counts["expressions that match"] = 0
        counts["links that resolve"] = 0
        online(args[1:], app, table, exprs, pinned, report, counts)
    finish(app, table, report, counts)


if __name__ == "__main__":
    main()
