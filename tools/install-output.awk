# The progress view of an ansible-playbook run that install.sh shows: one line per part of the
# install (the checks, the install record, each service, doctor, the bind check) with what it
# changed, or would change in the dry run (-v dry=1); warnings and errors as they come, a failed
# task as the message it ends with. Everything else (each task, its result, diffs) stays in the
# log; install.sh --verbose shows it all. It runs under mawk (Ubuntu's awk) and gawk alike.

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

# The text of a JSON string that starts at position i of s (its opening quote), with the
# escapes Ansible writes undone; jend is set to the position of its closing quote.
function json_string(s, i,    out, c, e) {
  out = ""
  for (i++; i <= length(s); i++) {
    c = substr(s, i, 1)
    if (c == "\"") break
    if (c == "\\") {
      e = substr(s, ++i, 1)
      if (e == "n") out = out "\n"
      else if (e == "t") out = out "\t"
      else if (e == "r") { }
      else if (e == "u") { out = out "\\u" substr(s, i + 1, 4); i += 4 }
      else out = out e
      continue
    }
    out = out c
  }
  jend = i
  return out
}

# The value of a key of the outermost object of a result line (fatal: [host]: FAILED! => {...}),
# when it is a string, else "". Keys of nested objects, such as a loop item's, do not count.
function json_field(line, want,    s, i, c, depth, key, value, text) {
  if (!match(line, / => [{]/)) return ""
  s = substr(line, RSTART + 4)
  depth = 0; key = ""; value = 0
  for (i = 1; i <= length(s); i++) {
    c = substr(s, i, 1)
    if (c == "\"") {
      text = json_string(s, i)
      i = jend
      if (depth == 1) {
        if (value) { if (key == want) return text; value = 0 }
        else key = text
      }
    }
    else if (c == ":" && depth == 1) value = 1
    else if (c == "," && depth == 1) { key = ""; value = 0 }
    else if (c == "{" || c == "[") depth++
    else if (c == "}" || c == "]") depth--
  }
  return ""
}

# A failed task as its message (the fail_msg of a check), and the error output of a command
# that failed; the raw line when the result has no message.
function failure(line,    msg, err, n, k, rows) {
  msg = json_field(line, "msg")
  if (msg == "") { say(line); return }
  n = split(msg, rows, "\n")
  for (k = 1; k <= n; k++) if (rows[k] != "") say(rows[k])
  err = json_field(line, "stderr")
  n = split(err, rows, "\n")
  for (k = 1; k <= n && k <= 10; k++) if (rows[k] != "") say("  " rows[k])
}

# Ansible's own errors (a broken inventory or vault, a syntax error) end the run: show the rest.
errors { print; next }
/^ERROR! / { if (part != "") { failed = 1; close_part() }; errors = 1; print; next }

# A failed task, unless the next line says it is ignored.
pending != "" {
  if ($0 !~ /^\.\.\.ignoring/) { failed = 1; failure(pending) }
  pending = ""
}
/^(fatal|failed): / { pending = $0; next }

/^PLAY \[/ {
  play = $0
  if (play ~ /^PLAY \[Check the inventory/) open_part("checks (system, units, mounts, ports, disk, memory)")
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
  # The record of the install this host belongs to (site.yml), a task of no role.
  else if ($0 ~ /^TASK \[Record the install this host belongs to\]/) open_part("install record")
  next
}

/^changed: / { if (!task_changed) { changes++; task_changed = 1 }; next }
/^FAILED - RETRYING: / { say($0); next }
/^\[WARNING\]: / { say($0); next }
/^PLAY RECAP/ { close_part(); recap = 1; next }

END {
  if (pending != "") { failed = 1; failure(pending) }
  close_part()
}
