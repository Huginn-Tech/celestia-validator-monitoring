# The progress view of an ansible-playbook run that install.sh shows: one line per part of the
# install (the checks, each service, doctor, the bind check) with what it changed, or would
# change in the dry run (-v dry=1); warnings and errors as they come. Everything else (each
# task, its result, diffs) stays in the log; install.sh --verbose shows it all.

BEGIN {
  label["celestia_mon_node_exporter"] = "node_exporter"
  label["celestia_mon_otelcol"] = "Fibre collector (otelcol)"
  label["celestia_mon_blackbox"] = "Fibre upload readiness"
  label["celestia_mon_prometheus"] = "Prometheus"
  label["celestia_mon_alertmanager"] = "Alertmanager"
  label["celestia_mon_announcements"] = "Celestia announcements"
  label["celestia_mon_tensile"] = "Fibre outside check (Tensile)"
  label["celestia_mon_signal"] = "Upgrade signalling"
  label["celestia_mon_grafana"] = "Grafana"
}

# Ends the open part with its result on its line.
function close_part() {
  if (part == "") return
  if (failed) status = "FAILED"
  else if (changes == 0) status = "no change"
  else if (dry) status = changes " to change"
  else status = changes (changes == 1 ? " change" : " changes")
  if (broken) printf "  %-30s %s\n", part, status
  else printf "%s\n", status
  fflush()
  part = ""
}

# Opens a part: its name now, its result when it ends.
function open_part(name) {
  if (name == part) return
  close_part()
  part = name; changes = 0; failed = 0; broken = 0; seen[name] = 1
  printf "  %-30s ", part
  fflush()
}

# A line shown inside an open part goes on a line of its own.
function say(line) {
  if (part != "" && !broken) { printf "\n"; broken = 1 }
  print "    " line
  fflush()
}

# Ansible's own errors (a broken inventory or vault, a syntax error) end the run: show the rest.
errors { print; next }
/^ERROR! / { if (part != "") { failed = 1; close_part() }; errors = 1; print; next }

# A failed task, unless the next line says it is ignored.
pending != "" {
  if ($0 !~ /^\.\.\.ignoring/) { failed = 1; say(pending) }
  pending = ""
}
/^(fatal|failed): / { pending = $0; next }

/^PLAY \[/ {
  play = $0
  if (play ~ /^PLAY \[Check the inventory/) open_part("checks (system, ports, disk, memory)")
  else if (play ~ /listens on a public address/) open_part("bind check")
  else if (play ~ /^PLAY \[Remove celestia-mon/) open_part("removal")
  next
}

/^(TASK|RUNNING HANDLER) \[/ {
  task_changed = 0
  if (match($0, /\[[a-z_]+ : /)) {
    role = substr($0, RSTART + 1, RLENGTH - 4)
    # doctor is installed in two plays on the monitor host; its line is the first one.
    if ($0 ~ /celestia-mon-doctor/) { if (!("celestia-mon-doctor" in seen)) open_part("celestia-mon-doctor") }
    else if (role in label) open_part(label[role])
  }
  next
}

/^changed: / { if (!task_changed) { changes++; task_changed = 1 }; next }
/^FAILED - RETRYING: / { say($0); next }
/^\[WARNING\]: / { say($0); next }
/^PLAY RECAP/ { close_part(); recap = 1; next }

END {
  if (pending != "") { failed = 1; say(pending) }
  close_part()
}
