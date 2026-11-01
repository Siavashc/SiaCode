"""Anthropic-API normalising proxy: Claude Code -> this -> molab llama-server.

Why: llama.cpp's Anthropic endpoint feeds messages straight into the Qwen chat
template, which raises "System message must be at the beginning" when Claude Code
puts a system/tool turn mid-conversation. This proxy rewrites the request bodies
into a shape the template accepts and logs everything it sees.

Default: listen 127.0.0.1:8011, upstream from siacode.env next to this script.
Usage: python molab_proxy.py [--port 8011] [--upstream URL] [--no-rewrite] [--log DIR]
"""
import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "siacode.env")
LOGDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_log")


def env_conf():
    base = key = None
    try:
        for line in open(ENV, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line.startswith("MOLAB_BASE_URL="):
                base = line.split("=", 1)[1].strip()
            elif line.startswith("MOLAB_API_KEY="):
                key = line.split("=", 1)[1].strip()
    except Exception:
        pass
    return base, key


def sanitize_content(msgs, notes):
    """Qwen's chat template only understands text blocks in a content list; anything else
    (thinking, redacted_thinking, image, ...) raises 'Unexpected item type in content'.
    Claude Code echoes the model's thinking blocks back in history, so strip them."""
    out = []
    for m in msgs:
        c = m.get("content")
        if not isinstance(c, list):
            out.append(m)
            continue
        new = []
        for b in c:
            if not isinstance(b, dict):
                new.append({"type": "text", "text": str(b)})
                continue
            t = b.get("type")
            if t in ("thinking", "redacted_thinking"):
                notes.append("dropped thinking block")
                continue
            if t == "text":
                new.append({"type": "text", "text": b.get("text", "")})
            elif t == "tool_result":
                cc = b.get("content")
                if isinstance(cc, list):
                    b = dict(b)
                    b["content"] = "".join(x.get("text", "") for x in cc
                                           if isinstance(x, dict) and x.get("type") == "text")
                    notes.append("flattened tool_result")
                new.append(b)
            elif t == "tool_use":
                new.append(b)
            elif t == "image":
                new.append({"type": "text", "text": "[image omitted]"})
                notes.append("dropped image")
            else:
                new.append({"type": "text", "text": json.dumps(b)[:4000]})
                notes.append(f"coerced {t} block to text")
        m = dict(m)
        m["content"] = new or [{"type": "text", "text": ""}]
        out.append(m)
    return out


def fix_anthropic_body(body):
    """Return (new_body, notes). Merges system into the first user turn, hoists
    mid-conversation system messages, and strips unsupported content block types."""
    notes = []
    if not isinstance(body, dict) or "messages" not in body:
        return body, notes
    msgs = body.get("messages") or []
    sys_field = body.get("system")
    sys_texts = []
    if isinstance(sys_field, str) and sys_field.strip():
        sys_texts.append(sys_field)
    elif isinstance(sys_field, list):
        for blk in sys_field:
            if isinstance(blk, dict) and blk.get("type") == "text":
                sys_texts.append(blk.get("text", ""))
            elif isinstance(blk, str):
                sys_texts.append(blk)

    def text_of(content):
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(b.get("text", "") for b in content
                           if isinstance(b, dict) and b.get("type") == "text")
        return ""

    out = []
    for i, m in enumerate(msgs):
        role = m.get("role")
        if role == "system":
            sys_texts.append(text_of(m.get("content")))
            notes.append(f"hoisted mid-system @{i}")
            continue
        out.append(m)

    # a system message must be the very first thing: prepend it to the first user turn
    if sys_texts and out:
        joined = "\n\n".join(t for t in sys_texts if t.strip())
        first = out[0]
        if first.get("role") == "user":
            fc = first.get("content")
            if isinstance(fc, str):
                first["content"] = joined + "\n\n" + fc
            elif isinstance(fc, list):
                first["content"] = [{"type": "text", "text": joined}] + [
                    b for b in fc if not (isinstance(b, dict) and b.get("type") == "text")]
                first["content"].insert(1, {"type": "text", "text": text_of(fc)})
            notes.append("system prepended to first user turn")
        else:
            out.insert(0, {"role": "user", "content": joined})
            notes.append("system inserted as leading user turn")
        body["messages"] = out
        body.pop("system", None)
    elif sys_texts:
        body.pop("system", None)
        body["messages"] = [{"role": "user", "content": "\n\n".join(sys_texts)}]
        notes.append("system as only message")
    else:
        body["messages"] = out
    body["messages"] = sanitize_content(body["messages"], notes)
    return body, notes


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = None
    key = None
    rewrite = True
    logdir = LOGDIR
    seq = 0

    def log_message(self, fmt, *args):
        pass

    def _dump(self, kind, data):
        try:
            os.makedirs(self.logdir, exist_ok=True)
            H.seq += 1
            p = os.path.join(self.logdir, f"{time.strftime('%H%M%S')}_{H.seq:03d}_{kind}.txt")
            with open(p, "w", encoding="utf-8", errors="ignore") as f:
                f.write(data if isinstance(data, str) else data.decode("utf-8", "ignore"))
            # keep the dir small
            files = sorted(os.listdir(self.logdir))
            for old in files[:-60]:
                try:
                    os.remove(os.path.join(self.logdir, old))
                except Exception:
                    pass
            return p
        except Exception:
            return ""

    def _forward(self, method):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b""
        body = None
        if raw:
            try:
                body = json.loads(raw)
            except Exception:
                body = None
        if body is not None and self.rewrite and "/v1/messages" in self.path:
            body, notes = fix_anthropic_body(body)
            if notes:
                print("rewrote:", notes, flush=True)
                self._dump("req", json.dumps(body)[:200000])
        elif body is not None and "/v1/messages" in self.path:
            self._dump("req", json.dumps(body)[:200000])
        data = json.dumps(body).encode() if body is not None else raw
        hdrs = {k: v for k, v in self.headers.items()
                if k.lower() not in ("host", "content-length", "accept-encoding")}
        hdrs["authorization"] = "Bearer " + (H.key or "")
        hdrs["ngrok-skip-browser-warning"] = "1"
        url = H.upstream.rstrip("/") + self.path
        try:
            r = requests.request(method, url, data=data, headers=hdrs, stream=True, timeout=600)
        except Exception as e:
            msg = json.dumps({"type": "error", "error": {"type": "api_error",
                                                         "message": "proxy upstream: " + str(e)[:200]}})
            self.send_response(502)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg.encode())
            return
        ctype = r.headers.get("content-type", "application/json")
        self.send_response(r.status_code)
        self.send_header("content-type", ctype)
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        acc = []
        try:
            for chunk in r.iter_content(chunk_size=512):
                if not chunk:
                    continue
                acc.append(chunk)
                self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception as e:
            print("stream-err", str(e)[:120], flush=True)
        if r.status_code >= 400:
            txt = b"".join(acc)[:4000]
            print("UPSTREAM", r.status_code, txt[:400], flush=True)
            self._dump("err", txt)

    def do_POST(self):
        self._forward("POST")

    def do_GET(self):
        self._forward("GET")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--upstream")
    ap.add_argument("--no-rewrite", action="store_true")
    ap.add_argument("--log", default=LOGDIR)
    a = ap.parse_args()
    base, key = env_conf()
    H.upstream = (a.upstream or base or "").strip()
    H.key = key
    H.rewrite = not a.no_rewrite
    H.logdir = a.log
    if not H.upstream:
        raise SystemExit("no upstream (fill siacode.env next to this script, or pass --upstream)")
    print(f"proxy 127.0.0.1:{a.port} -> {H.upstream} (rewrite={H.rewrite})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
