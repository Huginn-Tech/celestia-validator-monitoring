"""An Alertmanager webhook receiver in CI that can fail.

  python3 tools/ci/receiver_stub.py PORT FILE

A POST answers 500 while FILE.fail exists and appends its body to FILE.tries; otherwise it answers
200 and appends its body to FILE, one JSON object per line. Any GET answers 200.
"""

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FILE = sys.argv[2]


class Handler(BaseHTTPRequestHandler):
    def reply(self, code):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.reply(200)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).rstrip()
        failing = os.path.exists(FILE + ".fail")
        with open(FILE + ".tries" if failing else FILE, "ab") as f:
            f.write(body + b"\n")
        self.reply(500 if failing else 200)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
