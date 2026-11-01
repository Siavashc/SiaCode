"""Keep a PC-owned marimo kernel session alive (no browser).

molab only lets a *connected consumer* run code: a session created by our WS is
read-only until POST /api/kernel/takeover, and it is reaped when the WS closes.
So: hold the WS open forever, take over once per (re)connect, heartbeat to a file.

The session id is stable (SID below), so molab_run.py can post runs to it any time
the keeper is alive. Auto-reconnects.

Run detached:  pythonw molab_keeper.py   (or start /b python molab_keeper.py)
"""
import asyncio
import json
import os
import time

import requests
import websockets

import molab_api as m

SID = "s_pc001"
HERE = os.path.dirname(os.path.abspath(__file__))
HEART = os.path.join(HERE, "molab_keeper.health")
LOG = os.path.join(HERE, "molab_keeper.log")


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def heartbeat(state):
    try:
        with open(HEART, "w", encoding="utf-8") as f:
            json.dump({"sid": SID, "state": state, "t": time.time()}, f)
    except Exception:
        pass


def takeover(host, tok):
    h = {"authorization": "Bearer " + tok,
         "x-runtime-url": "https://" + host + "/",
         "content-type": "application/json",
         "Marimo-Session-Id": SID}
    try:
        r = requests.post("https://" + host + "/api/kernel/takeover", headers=h, json={}, timeout=30)
        log(f"takeover {r.status_code} {r.text[:80]}")
        return r.status_code == 200
    except Exception as e:
        log("takeover-err " + str(e)[:100])
        return False


async def keep():
    while True:
        d = m.grab_creds()
        host, tok = d["host"], d["tok"]
        url = f"wss://{host}/ws?session_id={SID}&access_token={tok}"
        try:
            async with websockets.connect(url, max_size=None, open_timeout=25,
                                          ping_interval=20, ping_timeout=20) as ws:
                log("ws open " + SID)
                took = False
                last_take = 0.0
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        if time.time() - last_take > 45:
                            took = takeover(host, tok)
                            last_take = time.time()
                        heartbeat("open")
                        continue
                    try:
                        op = json.loads(msg).get("op")
                    except Exception:
                        op = "?"
                    if op == "kernel-ready" and not took:
                        took = takeover(host, tok)
                        last_take = time.time()
                        heartbeat("ready" if took else "readonly")
                    elif time.time() - last_take > 45:
                        took = takeover(host, tok)
                        last_take = time.time()
                        heartbeat("ready" if took else "open")
                    else:
                        heartbeat("open")
        except Exception as e:
            log("ws-err " + str(e)[:120])
            heartbeat("dead")
            await asyncio.sleep(3)


if __name__ == "__main__":
    log("keeper start sid=" + SID)
    asyncio.run(keep())
