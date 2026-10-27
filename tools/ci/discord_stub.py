"""A stand-in for Discord's API, and for Alertmanager's alerts API, in CI.

  STUB_TOKEN=<bot token> python3 tools/ci/discord_stub.py PORT POSTS_FILE

Channel 111111111111111111 follows two announcement channels: message ...100 was there before;
...101 and ...102 (and ...103, an alert a webhook posted there) appear once the newest message has
been asked for (the reader's first look).
Channel 666666666666666666 does the same with odd messages: ...201 holds a time too far off to
show, ...202 cannot be read (its embeds are no list), Alertmanager refuses ...203 as invalid (400),
and ...204 is a plain announcement after them.
Channel 222222222222222222 is one the bot may not read (403); 777777777777777777 answers with a
redirect to /followed, and 888888888888888888 with more than 1 MiB. Any other token gets 401.
POST /api/v2/alerts appends the posted alerts to POSTS_FILE, one JSON list per line, as
Alertmanager would take them; a request for /followed is noted in POSTS_FILE.followed.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = os.environ["STUB_TOKEN"]
GUILD = "999999999999999999"
CHANNEL = "111111111111111111"
ODD = "666666666666666666"
FORBIDDEN = "222222222222222222"
REDIRECT = "777777777777777777"
LARGE = "888888888888888888"
CELESTIA_GUILD = "638338779505229824"
# The text that makes the stand-in's Alertmanager refuse an alert.
REFUSED = "Alertmanager refuses this one"


def crosspost(n, author, source_channel, **fields):
    return {
        "id": str(1300000000000000000 + n),
        "content": "",
        "author": {"username": author, "bot": True},
        "flags": 2,
        "message_reference": {"guild_id": CELESTIA_GUILD, "channel_id": source_channel, "message_id": str(5000 + n)},
        **fields,
    }


# Oldest first. Discord answers newest first.
MESSAGES = {
    CHANNEL: [
        crosspost(100, "Celestia #mocha-announcements", "333333333333333333", content="An older announcement"),
        crosspost(101, "Celestia #mocha-announcements", "333333333333333333",
                  content="<@&123456789012345678> Mocha upgrades to v10.5.0 at <t:1792252800:F>. "
                          "Details: <https://docs.celestia.org/upgrade?network=mocha&v=10.5.0>"),
        crosspost(102, "Celestia #mainnet-announcements", "444444444444444444",
                  embeds=[{"title": "Mainnet notice", "description": "Read <#555555555555555555> before the upgrade @everyone"}]),
        # An alert that a Discord receiver posted into the same channel: not an announcement.
        {"id": "1300000000000000103", "content": "", "author": {"username": "Spidey Bot", "bot": True},
         "webhook_id": "777777777777777777", "flags": 0, "embeds": [{"title": "[FIRING:1] NotificationTest mfnode"}]},
    ],
    ODD: [
        crosspost(200, "Celestia #mocha-announcements", "333333333333333333", content="An older announcement"),
        crosspost(201, "Celestia #mocha-announcements", "333333333333333333", content="Far off: <t:99999999999999999999:F>"),
        crosspost(202, "Celestia #mocha-announcements", "333333333333333333", embeds="not a list"),
        crosspost(203, "Celestia #mocha-announcements", "333333333333333333", content=REFUSED),
        crosspost(204, "Celestia #mocha-announcements", "333333333333333333", content="After the odd ones"),
    ],
}


# The channels whose newest message was asked for (the reader's first look).
seen = set()


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            return self.reply(200, {})
        if url.path == "/followed":
            with open(sys.argv[2] + ".followed", "a") as f:
                f.write(self.headers.get("Authorization", "") + "\n")
            return self.reply(200, {})
        if self.headers.get("Authorization") != "Bot " + TOKEN:
            return self.reply(401, {"message": "401: Unauthorized", "code": 0})
        parts = url.path.strip("/").split("/")
        if len(parts) < 4 or parts[:3] != ["api", "v10", "channels"]:
            return self.reply(404, {"message": "404: Not Found", "code": 0})
        channel = parts[3]
        if channel == FORBIDDEN:
            return self.reply(403, {"message": "Missing Access", "code": 50001})
        if channel == REDIRECT:
            self.send_response(302)
            self.send_header("Location", "/followed")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if channel == LARGE:
            return self.reply(200, [{"id": "1300000000000000300", "content": "x" * (2 << 20)}])
        if channel not in MESSAGES:
            return self.reply(404, {"message": "Unknown Channel", "code": 10003})
        if len(parts) == 4:
            return self.reply(200, {"id": channel, "guild_id": GUILD, "type": 0})
        query = parse_qs(url.query)
        limit = int(query.get("limit", ["50"])[0])
        after = int(query.get("after", ["0"])[0])
        visible = MESSAGES[channel] if channel in seen else MESSAGES[channel][:1]
        if "after" not in query:
            seen.add(channel)
        newer = [m for m in visible if int(m["id"]) > after]
        return self.reply(200, list(reversed(newer))[:limit])

    def do_POST(self):
        if urlparse(self.path).path != "/api/v2/alerts":
            return self.reply(404, {})
        if not self.headers.get("Authorization", "").startswith("Basic "):
            return self.reply(401, {})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if any(REFUSED in a["annotations"]["description"] for a in body):
            return self.reply(400, {"code": 400, "message": "invalid alert"})
        with open(sys.argv[2], "a") as f:
            f.write(json.dumps(body) + "\n")
        return self.reply(200, {})

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
