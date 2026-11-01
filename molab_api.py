"""Direct PC -> molab sandbox API client (siacode-kit edition).

Reads the notebook id from config.json (created by link_molab.py), grabs the
auth_token + sandbox host from the notebook page opened in the debug Chrome
(CDP :9222), then talks to https://<host>/api/... with plain requests.

CLI (debug helpers):
  python molab_api.py read <path> [path...]
"""
import base64
import json
import os
import sys

import requests
from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9222"
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
CACHE = os.path.join(HERE, "molab_creds.json")


def load_config():
    try:
        return json.load(open(CONFIG))
    except Exception:
        return {}


def notebook_id():
    nb = load_config().get("notebook", "")
    if not nb:
        raise SystemExit("config.json has no notebook id - run: python link_molab.py")
    return nb


def grab_creds(force=False):
    """Drive the debug Chrome to the notebook page, wait for the sandbox frame,
    decode auth_token from its URL, cache and return {"host", "tok", "sbt"}."""
    try:
        if not force:
            d = json.load(open(CACHE))
            if d.get("host") and d.get("tok"):
                return d
    except Exception:
        pass
    nb = notebook_id()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP, timeout=30000)
        ctx = browser.contexts[0]
        page = None
        for pg in ctx.pages:
            if "molab.marimo.io" in pg.url:
                page = pg
                break
        if page is None:
            page = ctx.new_page()
        page.goto("https://molab.marimo.io/notebooks/" + nb, timeout=120000,
                  wait_until="domcontentloaded")
        for _ in range(40):   # the visit itself spins a sandbox - can take a while
            if any("/session/" in f.url and "sb-" in f.url for f in page.frames):
                break
            page.wait_for_timeout(3000)
        tok = sbt = None
        for fr in page.frames:
            if "/session/" in fr.url and "sb-" in fr.url:
                rest = fr.url.split("/session/", 1)[1].split("?")[0]
                sbt = fr.url.split("/session/", 1)[0].split("//", 1)[1].split("/", 1)[0]
                for cut in range(len(rest), 10, -1):
                    try:
                        pad = "=" * ((4 - len(rest[:cut]) % 4) % 4)
                        raw = base64.urlsafe_b64decode(rest[:cut] + pad).decode("utf-8", errors="ignore")
                        if raw.startswith("{"):
                            tok = json.loads(raw[: raw.rfind("}") + 1])["auth_token"]
                            break
                    except Exception:
                        continue
                break
        if not tok:
            raise SystemExit("NO-TOKEN - is the debug Chrome still logged into molab? "
                             "Run: python link_molab.py")
        base = sbt.split("-session")[0].split("-preview")[0]
        host = base if "molab.run" in base else base + ".sb.molab.run"
        d = {"host": host, "tok": str(tok).strip(), "sbt": sbt}
        json.dump(d, open(CACHE, "w"))
        return d


def H(d):
    return {"authorization": "Bearer " + d["tok"], "x-runtime-url": "https://" + d["host"] + "/"}


def get(d, path, extra=None):
    h = H(d)
    if extra:
        h.update(extra)
    url = f"https://{d['host']}/api/files/download?path=" + path
    r = requests.get(url, headers=h, timeout=60)
    return r


def main():
    d = grab_creds(force=("-f" in sys.argv))
    print("HOST:", d["host"])
    paths = sys.argv[2:] if len(sys.argv) > 2 and sys.argv[1] == "read" else sys.argv[1:]
    if not paths:
        paths = ["/root/qwen_setup_status.json"]
    for path in paths:
        r = get(d, path)
        body = r.text
        if len(body) > 1500:
            body = body[:800] + " ...[cut]... " + body[-600:]
        print(f"=== {path} [{r.status_code}]\n{body}")


if __name__ == "__main__":
    main()