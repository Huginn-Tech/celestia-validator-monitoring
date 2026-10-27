"""A stand-in for a celestia-appd node's CometBFT RPC, as celestia-mon-signal reads it, in CI.

  python3 tools/ci/rpc_stub.py PORT STATE_FILE [ADDRESS]  serve the RPC on ADDRESS:PORT (127.0.0.1 by default)
  python3 tools/ci/rpc_stub.py address HEX                 print the celestiavaloper1 address of these bytes

STATE_FILE is JSON, read again for every request, so a test changes the chain by rewriting it:
  network      node_info.network in /status
  app_version  and version (the binary) in /abci_info
  height       the last block height (/abci_info and /status)
  lowest       the lowest block the node keeps (earliest_block_height in /status); /blockchain
               below it fails as on a pruned node
  tally        {version: base64 QueryVersionTallyResponse}; other versions get no voting power
  upgrade      base64 QueryGetUpgradeResponse, "" when none is pending
  signals      {operator address hex: version}, the signal store
and optionally:
  catching_up  true for a node that is catching up
  age          how many seconds old the last block is (a stalled node); 0 by default
  abci_behind  how many blocks the /abci_info height trails the /status one (behind a provider's
               URL, the two calls can reach two nodes); 0 by default
  no_version   true for a signal store that does not hold the last height yet (on a node one
               block behind the one that answered /abci_info)
  refuse       the methods the node refuses, such as ["blockchain"]
Blocks are 2.87 s apart, and the last one is from the current minute, so every request of one
read sees the same times. Queries are answered at the last height only, so a reader must ask for
it: a gRPC query at another height fails, and a store query at another height gets the SDK
store's answer for a version it does not hold (code 0, no value, a log). Any path prefix before
the method is ignored, as a provider's key would be.
"""

import base64
import datetime
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BLOCK_NS = 2_870_000_000
# The tally of a version nobody signalled: voting_power absent (0), the threshold and the total.
NO_TALLY = "EMj0pLgBGNbYkt0B"
CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def address(raw, prefix="celestiavaloper"):
    """BIP 173 bech32 of raw bytes."""
    acc = bits = 0
    data = []
    for b in raw:
        acc = (acc << 8) | b
        bits += 8
        while bits >= 5:
            bits -= 5
            data.append((acc >> bits) & 31)
    if bits:
        data.append((acc << (5 - bits)) & 31)
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in [ord(c) >> 5 for c in prefix] + [0] + [ord(c) & 31 for c in prefix] + data + [0] * 6:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            if (top >> i) & 1:
                chk ^= gen[i]
    chk ^= 1
    return prefix + "1" + "".join(CHARSET[d] for d in data + [(chk >> 5 * (5 - i)) & 31 for i in range(6)])


def block_time(state, height):
    ns = (int(time.time()) // 60 * 60 - state.get("age", 0)) * 10**9 - (state["height"] - height) * BLOCK_NS
    t = datetime.datetime.fromtimestamp(ns // 10**9, datetime.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S") + f".{ns % 10**9:09d}Z"


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        data = json.dumps({"jsonrpc": "2.0", "id": -1, **body}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def query(self, state, path, data):
        if path == "/celestia.signal.v1.Query/VersionTally":
            # The request is {1: version}: one key byte, then the varint.
            version, shift = 0, 0
            for b in data[1:]:
                version |= (b & 0x7F) << shift
                shift += 7
            return state["tally"].get(str(version), NO_TALLY)
        if path == "/celestia.signal.v1.Query/GetUpgrade":
            return state["upgrade"] or None
        if path == "/store/signal/key":
            version = state["signals"].get(data.hex())
            return base64.b64encode(version.to_bytes(8, "big")).decode() if version is not None else None
        raise KeyError(path)

    def do_GET(self):
        url = urlparse(self.path)
        method = url.path.rstrip("/").rsplit("/", 1)[-1]
        args = {k: v[0] for k, v in parse_qs(url.query).items()}
        if method == "health":
            return self.reply(200, {"result": {}})
        with open(sys.argv[2]) as f:
            state = json.load(f)
        height = state["height"]
        if method in state.get("refuse", []):
            return self.reply(403, {"error": {"code": -32601, "message": "Forbidden", "data": f"{method} is not served here"}})
        if method == "status":
            return self.reply(200, {"result": {
                "node_info": {"network": state["network"], "version": "0.38.17"},
                "sync_info": {"latest_block_height": str(height), "latest_block_time": block_time(state, height),
                              "earliest_block_height": str(state["lowest"]), "catching_up": state.get("catching_up", False)}}})
        if method == "abci_info":
            return self.reply(200, {"result": {"response": {
                "data": "celestia-app", "version": state["version"], "app_version": str(state["app_version"]),
                "last_block_height": str(height - state.get("abci_behind", 0)), "last_block_app_hash": "AAAA"}}})
        if method == "blockchain":
            # CometBFT's bounds: at most 20 blocks, none above the last or below the lowest kept.
            hi = min(int(args.get("maxHeight", height)), height)
            lo = max(int(args.get("minHeight", 1)), state["lowest"], hi - 19)
            if lo > hi:
                return self.reply(500, {"error": {"code": -32603, "message": "Internal error",
                                                  "data": f"min height {lo} can't be greater than max height {hi}"}})
            return self.reply(200, {"result": {"last_height": str(height), "block_metas": [
                {"header": {"height": str(h), "time": block_time(state, h)}} for h in range(hi, lo - 1, -1)]}})
        if method == "abci_query":
            path = json.loads(args["path"])
            data = bytes.fromhex(args["data"][2:]) if args.get("data", "").startswith("0x") else b""
            if path.startswith("/store/") and (args.get("height") != str(height) or state.get("no_version")):
                # cosmos-sdk store iavl/store.go, Query "/key": a version the store does not hold.
                return self.reply(200, {"result": {"response": {
                    "code": 0, "log": "version does not exist", "height": args.get("height")}}})
            if args.get("height") != str(height):
                return self.reply(200, {"result": {"response": {
                    "code": 26, "log": f"the stand-in answers at height {height} only, asked {args.get('height')}", "height": "0"}}})
            try:
                value = self.query(state, path, data)
            except KeyError:
                return self.reply(200, {"result": {"response": {"code": 6, "log": f"unknown query path {path}", "height": str(height)}}})
            return self.reply(200, {"result": {"response": {"code": 0, "log": "", "value": value, "height": str(height)}}})
        return self.reply(404, {"error": {"code": -32601, "message": "Method not found"}})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if sys.argv[1] == "address":
        print(address(bytes.fromhex(sys.argv[2])))
    else:
        ThreadingHTTPServer((sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
