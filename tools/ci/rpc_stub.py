"""A stand-in for a celestia-appd node's CometBFT RPC, as celestia-mon-signal reads it, in CI.

  python3 tools/ci/rpc_stub.py PORT STATE_FILE [ADDRESS]  serve the RPC on ADDRESS:PORT (127.0.0.1 by default)
  python3 tools/ci/rpc_stub.py address HEX [PREFIX]        print the bech32 address of these bytes
                                                            (celestiavaloper by default)

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
  redirect     a URL that every method answers with a redirect (302) to
  oversize     true for a /status answer larger than 1 MiB
  nested       {method: HTTP status}: the methods the node answers with that status and JSON
               nested 500000 deep (valid, under 1 MiB, far deeper than Python's parser goes)
  gov          the proposals, [{id, start, end, title, voters}]: start and end in Unix seconds,
               voters the celestia1 accounts with a vote; and optionally status (2, the voting
               period, by default), nanos (of the end) and expedited. Proposals answers those
               with the status asked, and with a voter asked those it lists; none by default
  gov_error    true for a node that fails the Proposals query (code 6)
  gov_broken   a Proposals answer that does not read: "end" leaves out each proposal's voting end
               time, "voted" lists with a voter also the proposals out of the status asked
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
# The nested answer: 1000040 bytes. Written out, as json.dumps cannot nest that deep either.
NESTED = b'{"jsonrpc": "2.0", "id": -1, "result": ' + b"[" * 500_000 + b"]" * 500_000 + b"}"


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


def varint(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def field(number, value):
    """A protobuf field: a varint for an int (and a bool), length-delimited for bytes."""
    if isinstance(value, int):
        return varint(number << 3) + varint(int(value))
    return varint(number << 3 | 2) + varint(len(value)) + value


def request(data):
    """The fields of a request, {number: value}: varints as int, strings as str."""
    out, i = {}, 0
    while i < len(data):
        key, i = read_varint(data, i)
        if key & 7 == 0:
            out[key >> 3], i = read_varint(data, i)
        elif key & 7 == 2:
            n, i = read_varint(data, i)
            out[key >> 3], i = data[i : i + n].decode(), i + n
        else:
            raise ValueError(f"wire type {key & 7} in a request")
    return out


def read_varint(data, i):
    value = shift = 0
    while True:
        b = data[i]
        i += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, i
        shift += 7


def proposal(p, end=True):
    """A cosmos.gov.v1.Proposal (gov.proto) with every field a node sends for one in its voting
    period: id 1, messages 2 (one Any with its type URL only), status 3, final_tally_result 4 (all
    counts "0" until the tally), submit_time 5, deposit_end_time 6, total_deposit 7 (two Coins),
    voting_start_time 8, voting_end_time 9 (left out with end false), metadata 10, title 11,
    summary 12, proposer 13 and expedited 14."""
    tally = b"".join(field(n, b"0") for n in (1, 2, 3, 4))
    deposit = field(7, field(1, b"utia") + field(2, b"9000000000")) + field(7, field(1, b"utia") + field(2, b"1000000000"))
    return (field(1, p["id"]) + field(2, field(1, b"/cosmos.gov.v1.MsgExecLegacyContent")) + field(3, p.get("status", 2))
            + field(4, tally) + field(5, field(1, p["start"] - 3600)) + field(6, field(1, p["start"] + 86400)) + deposit
            + field(8, field(1, p["start"]))
            + (field(9, field(1, p["end"]) + field(2, p.get("nanos", 0))) if end else b"")
            + field(10, b"ipfs://bafkreigdummy") + field(11, p.get("title", "").encode())
            + field(12, f"The summary of proposal {p['id']}.".encode())
            + field(13, b"celestia1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq") + field(14, p.get("expedited", False)))


def block_time(state, height):
    ns = (int(time.time()) // 60 * 60 - state.get("age", 0)) * 10**9 - (state["height"] - height) * BLOCK_NS
    t = datetime.datetime.fromtimestamp(ns // 10**9, datetime.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S") + f".{ns % 10**9:09d}Z"


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        self.send_body(code, json.dumps({"jsonrpc": "2.0", "id": -1, **body}).encode())

    def send_body(self, code, data):
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
        if path == "/cosmos.gov.v1.Query/Proposals" and not state.get("gov_error"):
            # QueryProposalsRequest: proposal_status 1, voter 2 (x/gov/keeper/grpc_query.go applies both).
            req = request(data)
            broken = state.get("gov_broken")
            found = [p for p in state.get("gov", []) if (p.get("status", 2) == req.get(1, 0) or broken == "voted" and 2 in req)
                     and (2 not in req or req[2] in p.get("voters", []))]
            # QueryProposalsResponse: proposals 1, pagination 2 (its total, field 2, when there is one; no
            # next_key, field 1, which a node sets only past the 100 proposals of a page).
            page = field(2, len(found)) if found else b""
            return base64.b64encode(b"".join(field(1, proposal(p, broken != "end")) for p in found) + field(2, page)).decode()
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
        if state.get("redirect"):
            self.send_response(302)
            self.send_header("Location", state["redirect"])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if state.get("oversize") and method == "status":
            return self.reply(200, {"result": {"padding": "x" * (2 << 20)}})
        if method in state.get("nested", {}):
            return self.send_body(state["nested"][method], NESTED)
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
        print(address(bytes.fromhex(sys.argv[2]), *sys.argv[3:4]))
    else:
        ThreadingHTTPServer((sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
