"""A local OpenAI proxy that removes schemas, to test the non-schema rungs.

Many OpenAI-compatible gateways accept `response_format` and quietly ignore it,
or refuse it outright. Both behaviours are hard to exercise against the real
vendor endpoint, which honours schemas perfectly. This proxy stands in for such
a gateway: point `--api_base` at it and the translator has to reach its
delimiter / plain-prompt rungs while still talking to a real paid model.

    python tools/schema_drop_proxy.py --port 8765 [--upstream https://api.openai.com]
                                      [--mode drop|reject]

`drop`   deletes `response_format` from the request body and forwards the rest.
`reject` answers 400 with an OpenAI-shaped error when it sees one.

Nothing but the request line is logged: the Authorization header carries a paid
key and the bodies carry the book.
"""

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
}

REJECT_BODY = {
    "error": {
        "message": "Unsupported parameter: response_format",
        "type": "invalid_request_error",
        "param": "response_format",
        "code": None,
    }
}


def log(*parts):
    print(" ".join(str(p) for p in parts), file=sys.stderr, flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence BaseHTTPRequestHandler's own log
        pass

    def do_GET(self):
        self._forward(b"", "-")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        note = "no-body"
        if body:
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                note = "unparsed"
            else:
                present = isinstance(payload, dict) and "response_format" in payload
                if not present:
                    note = "absent"
                elif self.server.mode == "reject":
                    log("POST", self.path, "response_format=present->REJECTED", 400)
                    self._respond(
                        400, json.dumps(REJECT_BODY).encode(), "application/json"
                    )
                    return
                else:
                    payload.pop("response_format")
                    body = json.dumps(payload).encode()
                    note = "present->dropped"
        self._forward(body, note)

    def _forward(self, body, note):
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in HOP_BY_HOP and k.lower() != "host"
        }
        url = self.server.upstream.rstrip("/") + self.path
        try:
            r = httpx.request(
                self.command, url, headers=headers, content=body, timeout=600.0
            )
        except Exception as e:
            log(
                self.command,
                self.path,
                f"response_format={note}",
                f"UPSTREAM-ERROR {type(e).__name__}",
            )
            self._respond(
                502,
                json.dumps(
                    {"error": {"message": f"proxy upstream error: {e}"}}
                ).encode(),
                "application/json",
            )
            return
        log(self.command, self.path, f"response_format={note}", r.status_code)
        self._respond(
            r.status_code,
            r.content,
            r.headers.get("content-type", "application/json"),
            r.headers,
        )

    def _respond(self, status, body, content_type, upstream_headers=None):
        self.send_response(status)
        for k, v in (upstream_headers or {}).items():
            if k.lower() not in HOP_BY_HOP and k.lower() != "content-type":
                self.send_header(k, v)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--upstream", default="https://api.openai.com")
    p.add_argument("--mode", choices=("drop", "reject"), default="drop")
    a = p.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    server.upstream = a.upstream
    server.mode = a.mode
    log(f"schema_drop_proxy mode={a.mode} port={a.port} upstream={a.upstream}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
