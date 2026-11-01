"""Run arbitrary Python in the molab kernel over plain HTTP (no browser/editor/WS).

Channel: POST https://<sbhost>/api/kernel/run  {"cellIds":[id],"codes":[code]}
         + authorization: Bearer <auth_token>  + Marimo-Session-Id: <sid>
Inline mode captures stdout/stderr into /root/_exec_<nonce>.txt and reads it back.

NEVER run a long/blocking payload inline - it blocks the kernel (and then every
later run queues). Use --bg, which writes the payload to /root/_bg_<nonce>.py,
spawns it detached (new session), and returns at once; then --tail the log.

Usage:
  python molab_run.py payload.py [--timeout 300] [--quiet]
  python molab_run.py -c "print(1+1)"
  python molab_run.py --bg payload.py            -> prints NONCE=<n> PID=<p>
  python molab_run.py --tail <nonce> [-f]        -> prints/follows /root/_bg_<n>.log
  python molab_run.py --interrupt                -> POST /api/kernel/interrupt
  python molab_run.py --kstate                   -> GET /api/kernel/status
"""
import argparse
import base64
import json
import os
import sys
import time

import requests

import molab_api as m

# IMPORTANT: marimo's static analyser rejects a cell that DEFINES a name another
# cell already defines (marimo-error "multiple-defs") and the body never runs.
# So the whole wrapper lives inside a uniquely-named function and the payload is
# exec'd against a PRIVATE globals dict -> only one fresh top-level name per run.
WRAP = r'''
def _molab_run_%(nonce)s():
    import io, contextlib, traceback, glob, os, time, builtins
    _payload = %(payload)r
    _out = %(out)r
    _buf = io.StringIO()
    _status = "OK"
    _t0 = time.time()
    _g = {"__name__": "__main__", "__builtins__": builtins, "__file__": "<molab-payload>"}
    try:
        _src = compile(_payload, "<molab-payload>", "exec")
        with contextlib.redirect_stdout(_buf), contextlib.redirect_stderr(_buf):
            exec(_src, _g)
    except SystemExit:
        pass
    except BaseException:
        _status = "FAIL"
        traceback.print_exc(file=_buf)
    try:
        open(_out, "w").write(_status + " " + str(round(time.time() - _t0, 1)) + "s\n" + _buf.getvalue())
    except BaseException:
        pass
    for _f in glob.glob("/root/_exec_*.txt"):
        if _f != _out and os.path.getmtime(_f) < time.time() - 3600:
            try:
                os.remove(_f)
            except Exception:
                pass
_molab_run_%(nonce)s()
'''

BG = r'''
def _molab_bg_%(nonce)s():
    import base64, subprocess
    _src = base64.b64decode(%(b64)r).decode("utf-8")
    _py = %(py)r
    _log = %(log)r
    open(_py, "w").write(_src)
    _f = open(_log, "a")
    _p = subprocess.Popen(["python3", "-u", _py], stdout=_f, stderr=subprocess.STDOUT,
                          start_new_session=True)
    open(%(pidf)r, "w").write(str(_p.pid))
    print("BG-STARTED pid=" + str(_p.pid) + " log=" + _log)
_molab_bg_%(nonce)s()
'''


KEEPER_SID = "s_pc001"
HEART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "molab_keeper.health")


def current_sid(d):
    """Prefer the PC-owned keeper session (browser-free, stable); else the browser's."""
    try:
        h = json.load(open(HEART))
        if h.get("sid") == KEEPER_SID and time.time() - h.get("t", 0) < 60:
            return KEEPER_SID
    except Exception:
        pass
    return d.get("sid")


def _post(code, timeout=60):
    d = m.grab_creds()
    d["sid"] = current_sid(d)
    if not d.get("sid"):
        raise SystemExit("no session id - start molab_keeper.py (or run molab_sess.py)")
    nonce = "n" + str(int(time.time() * 1000))[-9:]
    h = dict(m.H(d))
    h["content-type"] = "application/json"
    h["Marimo-Session-Id"] = d["sid"]
    body = {"cellIds": ["exec_" + nonce], "codes": [code]}
    r = requests.post("https://" + d["host"] + "/api/kernel/run", headers=h,
                      json=body, timeout=timeout)
    if r.status_code == 403:
        # edit rights were taken by another consumer - grab them back and retry once
        try:
            requests.post("https://" + d["host"] + "/api/kernel/takeover", headers=h,
                          json={}, timeout=30)
            r = requests.post("https://" + d["host"] + "/api/kernel/run", headers=h,
                              json=body, timeout=timeout)
        except Exception:
            pass
    return d, nonce, r


def run_inline(code, timeout=300, quiet=False):
    d = m.grab_creds()
    nonce = "n" + str(int(time.time() * 1000))[-9:]
    out_path = f"/root/_exec_{nonce}.txt"
    t0 = time.time()
    d, nonce, r = _post(WRAP % {"nonce": nonce, "payload": code, "out": out_path}, timeout=timeout)
    if r.status_code != 200:
        print("RUN-FAIL", r.status_code, r.text[:300])
        return 1, ""
    deadline = t0 + timeout
    while time.time() < deadline:
        g = m.get(d, out_path)
        if g.status_code == 200:
            txt = g.text
            if not quiet:
                head, _, rest = txt.partition("\n")
                print(f"--- {time.time()-t0:.1f}s {head}")
                sys.stdout.write(rest)
            return (0 if txt.startswith("OK") else 1), txt
        time.sleep(1.2)
    print("TIMEOUT waiting for", out_path, "- if the kernel is stuck: --interrupt")
    return 2, ""


def run_bg(code, quiet=False):
    d = m.grab_creds()
    nonce = "n" + str(int(time.time() * 1000))[-9:]
    py = f"/root/_bg_{nonce}.py"
    log = f"/root/_bg_{nonce}.log"
    pidf = f"/root/_bg_{nonce}.pid"
    payload = BG % {"nonce": nonce, "b64": base64.b64encode(code.encode()).decode(),
                    "py": py, "log": log, "pidf": pidf}
    d, _, r = _post(payload, timeout=60)
    if r.status_code != 200:
        print("BG-FAIL", r.status_code, r.text[:300])
        return None
    for _ in range(20):
        g = m.get(d, pidf)
        if g.status_code == 200:
            pid = g.text.strip()
            if not quiet:
                print(f"NONCE={nonce} PID={pid}")
            return nonce, pid
        time.sleep(0.7)
    print(f"NONCE={nonce} (pid not confirmed yet)")
    return nonce, "?"


def tail(nonce, follow=False):
    d = m.grab_creds()
    log = f"/root/_bg_{nonce}.log"
    pidf = f"/root/_bg_{nonce}.pid"
    seen = 0
    while True:
        g = m.get(d, log)
        if g.status_code == 200:
            body = g.text
            if len(body) > seen:
                sys.stdout.write(body[seen:])
                sys.stdout.flush()
                seen = len(body)
        pid = m.get(d, pidf)
        alive = False
        if pid.status_code == 200:
            p = pid.text.strip()
            chk = m.get(d, f"/proc/{p}/status").status_code == 200
            alive = chk
            if not chk and not follow:
                print(f"\n[pid {p} ended]")
                return
            if not chk:
                print(f"\n[pid {p} ended]")
                return
        if not follow:
            return
        time.sleep(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("payload", nargs="?")
    ap.add_argument("-c", dest="inline")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--bg", action="store_true")
    ap.add_argument("--tail")
    ap.add_argument("-f", dest="follow", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--interrupt", action="store_true")
    ap.add_argument("--kstate", action="store_true")
    a = ap.parse_args()

    if a.interrupt or a.kstate:
        d = m.grab_creds()
        h = dict(m.H(d))
        h["Marimo-Session-Id"] = current_sid(d)
        ep = "/api/kernel/interrupt" if a.interrupt else "/api/kernel/status"
        r = requests.request("POST" if a.interrupt else "GET", "https://" + d["host"] + ep,
                             headers=h, json={} if a.interrupt else None, timeout=30)
        print(ep, r.status_code, r.text[:200])
        return
    if a.tail:
        tail(a.tail, a.follow)
        return
    code = a.inline if a.inline else (open(a.payload, "rb").read().lstrip(b"\xef\xbb\xbf").decode("utf-8")
                                      if a.payload else sys.stdin.read())
    if a.bg:
        r = run_bg(code, a.quiet)
        sys.exit(0 if r else 1)
    rc, _ = run_inline(code, a.timeout, a.quiet)
    sys.exit(rc)


if __name__ == "__main__":
    main()
