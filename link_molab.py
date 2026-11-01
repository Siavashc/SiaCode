"""link_molab.py - ONE-TIME first-run step of the siacode kit.

Wires the kit to YOUR molab account. Three inputs:
  1. a free ngrok authtoken (dashboard.ngrok.com -> Your Authtoken)  -> config.json
  2. your molab account (Google sign-in in the Chrome window that opens)
  3. one new notebook (just click "+ New Notebook")

Everything after this is automatic: `siacode` will set up and heal the whole
chain (model download, llama.cpp, tunnel, proxy) by itself.

Usage:
  python link_molab.py                  interactive
  python link_molab.py --ngrok TOKEN    (skips the token prompt)
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
CDP = "127.0.0.1", 9222
PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "molab-chrome-profile")


def port_open(port):
    import socket
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def tabs():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json" % CDP[1], timeout=5) as r:
            return json.load(r)
    except Exception:
        return []


def launch_chrome():
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(p):
            subprocess.Popen([p, "--remote-debugging-port=%d" % CDP[1],
                              "--user-data-dir=" + PROFILE,
                              "--no-first-run", "--window-size=1200,900"])
            for _ in range(20):
                time.sleep(1)
                if port_open(CDP[1]):
                    return True
    print("Chrome not found in Program Files - install Chrome or edit link_molab.py")
    return False


def save(cfg):
    json.dump(cfg, open(CONFIG, "w"), indent=2)


def main():
    cfg = {}
    if os.path.exists(CONFIG):
        cfg = json.load(open(CONFIG))

    # ---- 1. ngrok authtoken -------------------------------------------------
    tok = ""
    if "--ngrok" in sys.argv:
        tok = sys.argv[sys.argv.index("--ngrok") + 1]
    tok = tok.strip()
    if not tok and not cfg.get("ngrok_token"):
        print()
        print("*" * 62)
        print("STEP 1/3 - free ngrok authtoken")
        print("  1. open https://dashboard.ngrok.com  (free signup, email is enough)")
        print("  2. left menu -> 'Your Authtoken'  ->  copy it")
        print("  (if you need a VPN for ngrok, turn it on before signing up)")
        print("*" * 62)
        tok = input("paste your ngrok authtoken: ").strip()
    if tok:
        cfg["ngrok_token"] = tok
        save(cfg)

    if not cfg.get("ngrok_token"):
        print("no authtoken - cannot continue")
        return 1

    # ---- 2. connect the molab notebook -------------------------------------
    if not cfg.get("notebook"):
        if not port_open(CDP[1]):
            print("launching Chrome (molab needs a normal Chrome for Google sign-in)...")
            if not launch_chrome():
                return 1
        print()
        print("*" * 62)
        print("STEP 2/3 - molab account   (in the Chrome window that just opened)")
        print("  1. go to molab.marimo.io and sign in with your Google account")
        print("     (Google blocks sign-in in automated browsers - use that Chrome)")
        print("STEP 3/3 - one notebook")
        print("  2. click '+ New Notebook' and let it boot")
        print("  This script waits and detects it automatically.")
        print("*" * 62)

    nb = ""
    for i in range(600):   # up to ~10 min of patient waiting
        for t in tabs():
            url = t.get("url", "")
            if "molab.marimo.io/notebooks/nb_" in url:
                nb = url.split("/notebooks/")[-1].split("/")[0].split("?")[0]
                if nb.startswith("nb_"):
                    break
        if nb:
            break
        if cfg.get("notebook"):
            nb = cfg["notebook"]
            break
        time.sleep(2)
    if not nb:
        print("no molab notebook found after 10 min - sign in / create it and retry")
        return 1

    if nb != cfg.get("notebook"):
        cfg["notebook"] = nb
        save(cfg)
        print("notebook detected:", nb)
        print("config.json written ->", CONFIG)

    # ---- 3. verify exec channel --------------------------------------------
    print("verifying the chain...")
    os.chdir(HERE)
    try:
        import molab_run as mr  # noqa: E402
    except ImportError:
        print("molab_run.py missing - install the kit fully")
        return 1
    keeper_h = os.path.join(HERE, "molab_keeper.health")
    fresh = False
    try:
        fresh = time.time() - os.path.getmtime(keeper_h) < 60
    except OSError:
        pass
    if not fresh:
        # the exec channel needs a session: start the keeper (stable PC-owned sid)
        subprocess.Popen([sys.executable, os.path.join(HERE, "molab_keeper.py")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            time.sleep(2)
            try:
                fresh = time.time() - os.path.getmtime(keeper_h) < 60
            except OSError:
                fresh = False
            if fresh:
                break
    rc, txt = mr.run_inline("print('KIT-LINK-OK>> from sandbox kernel')", timeout=60)
    ok = "KIT-LINK-OK" in txt
    print("\n".join(txt.splitlines()[-3:]))
    print()
    if ok:
        print("LINKED ✔  next: type  siacode  in a PowerShell window and wait for it")
        print("to build everything (first run downloads a 74 GB model, ~12-20 min).")
        return 0
    print("link NOT verified - rerun this script once Chrome has the notebook open.")
    return 1


if __name__ == "__main__":
    sys.exit(main())