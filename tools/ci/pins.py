"""Print the pins in versions.yml as KEY=VALUE lines for $GITHUB_ENV.

  ANSIBLE_CORE_VERSION=<celestia_mon_versions.ansible_core>
  PROMETHEUS_VERSION=<celestia_mon_versions.prometheus.version>
  PROMETHEUS_SHA256=<celestia_mon_versions.prometheus.sha256.amd64>
  ... and the same pair for NODE_EXPORTER, OTELCOL_CONTRIB, ALERTMANAGER and GRAFANA.

The sha256 values are those of the linux-amd64 tarballs, the architecture of the CI runners.
Usage, from the repository root in a workflow step (needs PyYAML):
  python tools/ci/pins.py >> "$GITHUB_ENV"
"""

import yaml

with open("versions.yml", encoding="utf-8") as f:
    pins = yaml.safe_load(f)["celestia_mon_versions"]

print("ANSIBLE_CORE_VERSION=" + pins["ansible_core"])
for name in ("node_exporter", "otelcol_contrib", "prometheus", "alertmanager", "grafana"):
    print(f"{name.upper()}_VERSION={pins[name]['version']}")
    print(f"{name.upper()}_SHA256={pins[name]['sha256']['amd64']}")
