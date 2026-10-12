"""List the upstream releases that are newer than the pins in versions.yml.

Run once a month by .github/workflows/versions.yml. It only reports, in the log and in
the job summary; it never changes versions.yml. For each pinned component it lists every
newer release on the pinned line (same major.minor) and the newest release of each newer
line, with its date. A release becomes a candidate once it is at least 14 days old; pin
it by changing the version and every sha256 in versions.yml (the comments there name the
official checksum files).

Usage, from the repository root (needs PyYAML):
  python tools/ci/check_versions.py [versions.yml]
GITHUB_TOKEN, when set, raises the GitHub API rate limit. GITHUB_STEP_SUMMARY, when set,
receives the same table in Markdown.
"""

import datetime
import json
import os
import re
import sys
import urllib.request

import yaml

MIN_AGE_DAYS = 14
GITHUB_REPOS = {
    "node_exporter": "prometheus/node_exporter",
    "otelcol_contrib": "open-telemetry/opentelemetry-collector-releases",
    "prometheus": "prometheus/prometheus",
    "alertmanager": "prometheus/alertmanager",
    "grafana": "grafana/grafana",
    "uv": "astral-sh/uv",
}
# 1.2.3, v1.2.3, and Grafana's security releases such as v12.4.11+security-01.
VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:\+security-(\d+))?$")


def get_json(url):
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "celestia-mon-version-check"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def parse(version):
    match = VERSION.match(version)
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def github_releases(repo):
    """(version tuple, version, release date) of each published, non-prerelease release."""
    releases = []
    for page in (1, 2, 3):
        batch = get_json(f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}")
        for release in batch:
            version = parse(release["tag_name"])
            if version and not release["draft"] and not release["prerelease"] and release.get("published_at"):
                releases.append((version, release["tag_name"].removeprefix("v"), release["published_at"][:10]))
        if len(batch) < 100:
            break
    return releases


def pypi_releases(project):
    """(version tuple, version, upload date) of each release with at least one file not yanked."""
    data = get_json(f"https://pypi.org/pypi/{project}/json")
    releases = []
    for name, files in data["releases"].items():
        version = parse(name)
        files = [f for f in files if not f.get("yanked")]
        if version and files:
            releases.append((version, name, min(f["upload_time_iso_8601"] for f in files)[:10]))
    return releases


def newer(component, pinned, releases, today):
    """Rows (component, pinned, newer version, date, status) for the releases worth listing."""
    pinned_version = parse(pinned)
    if pinned_version is None:
        sys.exit(f"versions.yml: {component} {pinned} is not a version")
    rows = []
    lines_seen = set()
    for version, name, date in sorted(set(releases), reverse=True):
        if version <= pinned_version:
            continue
        line = version[:2]
        if line != pinned_version[:2]:
            if line in lines_seen:
                continue
            lines_seen.add(line)
        age = (today - datetime.date.fromisoformat(date)).days
        if age >= MIN_AGE_DAYS:
            status = f"candidate ({age} days old)"
        else:
            status = f"too new, a candidate from {datetime.date.fromisoformat(date) + datetime.timedelta(days=MIN_AGE_DAYS)}"
        rows.append((component, pinned, name, date, status))
    if not rows:
        rows.append((component, pinned, "-", "-", "up to date"))
    return rows


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "versions.yml"
    with open(path, encoding="utf-8") as f:
        pins = yaml.safe_load(f)["celestia_mon_versions"]
    today = datetime.datetime.now(datetime.timezone.utc).date()

    rows = []
    for component, repo in GITHUB_REPOS.items():
        rows += newer(component, str(pins[component]["version"]), github_releases(repo), today)
    rows += newer("ansible_core", str(pins["ansible_core"]), pypi_releases("ansible-core"), today)

    header = ("component", "pinned", "newer", "released", "status")
    widths = [max(len(str(row[i])) for row in rows + [header]) for i in range(len(header))]
    for row in [header] + rows:
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)).rstrip())
    deps = pins.get("python_deps_before")
    if deps:
        print(f"\npython_deps_before is {deps}: move it with the ansible_core pin and keep it at least {MIN_AGE_DAYS} days old.")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## Releases newer than versions.yml ({today})\n\n")
            f.write("| " + " | ".join(header) + " |\n")
            f.write("|" + "---|" * len(header) + "\n")
            for row in rows:
                f.write("| " + " | ".join(str(cell) for cell in row) + " |\n")
            f.write(f"\nA release is a candidate once it is {MIN_AGE_DAYS} days old. Nothing was changed.\n")


if __name__ == "__main__":
    main()
