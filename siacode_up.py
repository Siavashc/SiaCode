"""siacode_up.py - one command to bring the whole chain back up.

Chain: claude -> 127.0.0.1:8011 (molab_proxy, normalises Anthropic bodies)
             -> ngrok https://<id>.ngrok-free.app (from the sandbox)
             -> llama-server :8010 (MiniMax-M2.7 UD-IQ4_XS, expert-offloaded)
             -> kernel exec via molab HTTP API (molab_keeper holds the session)

Two model lanes share this chain:
  * MiniMax-M2.7 (default) - 196608 ctx, -ncmoe expert split, needs llama.cpp b12032
  * Qwen3.5-122B abliterix (RE/uncensored, kept on disk) - serve2 payload, b12032 also runs it

Steps: creds -> keeper -> sandbox state -> gguf/llama -> ngrok -> siacode.env
       -> local proxy -> end-to-end check.

Usage: python siacode_up.py [--check]   (--check: report only, change nothing)
"""
import json
import os
import re
import socket
import subprocess
import sys
import time

import requests

import molab_api as m
import molab_run

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, "siacode.env")
PROXY_PORT = 8011
GGUF = "/root/gguf/minimax/UD-IQ4_XS/MiniMax-M2.7-UD-IQ4_XS-00001-of-00004.gguf"
CHECK_ONLY = "--check" in sys.argv


def say(msg):
    print(msg, flush=True)


def port_open(host, port, t=1.5):
    s = socket.socket()
    s.settimeout(t)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


# ---------------------------------------------------------------- keeper
CDP = "127.0.0.1", 9222
CHROME_PROFILE = os.path.expandvars(r"%LOCALAPPDATA%\molab-chrome-profile")


def chrome_up():
    return port_open("127.0.0.1", CDP[1])


def launch_chrome():
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(p):
            say("chrome: launching debuggable instance (:9222)")
            subprocess.Popen([p, "--remote-debugging-port=%d" % CDP[1],
                              "--user-data-dir=" + CHROME_PROFILE,
                              "--no-first-run", "--window-size=1200,900"])
            for _ in range(20):
                time.sleep(1)
                if chrome_up():
                    return True
            return False
    say("! chrome.exe not found in Program Files")
    return False


def keeper_alive():
    try:
        h = json.load(open(os.path.join(HERE, "molab_keeper.health")))
        return time.time() - h.get("t", 0) < 90
    except Exception:
        return False


def start_keeper():
    say("keeper: starting")
    subprocess.Popen([sys.executable, os.path.join(HERE, "molab_keeper.py")],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(2)
        if keeper_alive():
            return True
    return False


# ---------------------------------------------------------------- sandbox
PROBE = r'''
import json, socket, os, subprocess
def listen(p):
    s = socket.socket(); s.settimeout(1)
    try:
        s.connect(("127.0.0.1", p)); return True
    except Exception:
        return False
    finally:
        s.close()
print(json.dumps({
    "gguf": os.path.exists(%r),
    "llama": listen(8010),
    "ngrok": subprocess.run("pgrep -f 'ngrok http'", shell=True,
                            capture_output=True).returncode == 0,
    "url": (open("/root/relay_url.txt").read().strip()
            if os.path.exists("/root/relay_url.txt") else ""),
}))
'''


def sandbox_state():
    rc, txt = molab_run.run_inline(PROBE % GGUF, timeout=60, quiet=True)
    for line in reversed(txt.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                pass
    say("! could not read sandbox state:\n" + txt[-600:])
    return None


BINCHK = r'''
import glob, os, subprocess
srv = sorted(glob.glob("/root/llama.cpp-bin/**/llama-server", recursive=True))
vram = subprocess.run("nvidia-smi --query-gpu=memory.used --format=csv,noheader",
                      shell=True, capture_output=True, text=True).stdout.strip()
pgrep = subprocess.run("pgrep -f 'llama-server --' || true", shell=True,
                       capture_output=True, text=True).stdout.strip().splitlines()
print("%r|%s|%d" % (srv, vram, len([p for p in pgrep if p.isdigit()])))
'''


VRAMCHK = r'''
import socket, subprocess
def listen(p):
    s = socket.socket(); s.settimeout(1)
    try:
        s.connect(("127.0.0.1", p)); return True
    except Exception:
        return False
    finally:
        s.close()
port = listen(8010)
out = subprocess.run("nvidia-smi --query-gpu=memory.used --format=csv,noheader",
                     shell=True, capture_output=True, text=True).stdout.strip()
vram = 0 if not out else "".join(ch for ch in out.splitlines()[0] if ch.isdigit())
procs = len([p for p in subprocess.run("pgrep -f 'llama-server --' || true", shell=True,
                                       capture_output=True, text=True).stdout.split()
             if p.isdigit()])
print("%d|%d|%d" % (port, int(vram), procs))
'''


def llama_health():
    """(vram_mib, proc_count) from the sandbox; vram=0/procs=0 if unreachable."""
    rc, txt = molab_run.run_inline(VRAMCHK, timeout=60, quiet=True)
    for line in reversed(txt.splitlines()):
        line = line.strip()
        parts = line.split("|")
        if len(parts) == 3:
            try:
                port, vram, procs = (int(x) for x in parts)
                return vram, procs
            except ValueError:
                continue
    return 0, 0


def kill_llama():
    molab_run.run_inline("import subprocess, time; subprocess.run(['pkill', '-9', '-f', 'llama-server', ], capture_output=True); time.sleep(2)",
                         timeout=60, quiet=True)


def sandbox_bin_present():
    """True when a llama-server binary exists (and reports its VRAM/pgrep for logs)."""
    rc, txt = molab_run.run_inline(BINCHK, timeout=60, quiet=True)
    for line in reversed(txt.splitlines()):
        line = line.strip()
        if line.startswith("['"):
            try:
                bins_repr, vram, procs = line.rsplit("|", 2)
                bins = eval(bins_repr)   # our own probe's repr of a path list
            except Exception:
                continue
            say("  bin=%s vram=%s procs=%s" % (bins[-1][-40:] if bins else "-",
                                               vram, procs))
            return bool(bins)
    return False


def run_bg_file(name, wait_hint=20):
    src = open(os.path.join(HERE, name), encoding="utf-8").read()
    if "__NGROK_TOKEN__" in src:
        tok = json.load(open(os.path.join(HERE, "config.json"))).get("ngrok_token", "")
        if not tok:
            say("! config.json has no ngrok_token - run: python link_molab.py")
            return None
        src = src.replace("__NGROK_TOKEN__", tok)
    out = molab_run.run_bg(src, quiet=True)
    nonce = out[0] if isinstance(out, tuple) and out else None
    if not nonce:
        say("%s: BG-FAIL (no nonce)" % name)
        return None
    say("%s: launched (nonce %s)" % (name, nonce))
    return nonce


def tail_log(nonce, n=20):
    try:
        d = m.grab_creds()
        r = m.get(d, f"/root/_bg_{nonce}.log")
        if r.status_code == 200:
            return "\n".join(r.text.splitlines()[-n:])
    except Exception:
        pass
    return ""


def wait_llama(timeout=600):   # MiniMax load takes ~3.5 min (-lm none); 240s was too tight
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = sandbox_state()
        if st and st.get("llama"):
            return True
        time.sleep(6)
    return False


def wait_url(timeout=180):
    t0 = time.time()
    d = m.grab_creds()
    while time.time() - t0 < timeout:
        r = m.get(d, "/root/relay_url.txt")
        if r.status_code == 200 and r.text.strip().startswith("http"):
            return r.text.strip()
        time.sleep(4)
    return ""


def tunnel_ok(url):
    if not url:
        return False
    try:
        r = requests.get(url + "/health", headers={"Authorization": "Bearer " + API_KEY,
                                                  "ngrok-skip-browser-warning": "1"}, timeout=20)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------- proxy
def proxy_alive():
    try:
        requests.get(f"http://127.0.0.1:{PROXY_PORT}/", timeout=3)
        return True
    except Exception:
        pass
    return port_open("127.0.0.1", PROXY_PORT)


def kill_proxy():
    try:
        out = subprocess.run("netstat -ano | findstr :%d" % PROXY_PORT, shell=True,
                             capture_output=True, text=True).stdout
        pids = {l.split()[-1] for l in out.splitlines() if "LISTENING" in l}
        for pid in pids:
            subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
    except Exception:
        pass


def start_proxy():
    kill_proxy()
    time.sleep(1)
    subprocess.Popen([sys.executable, os.path.join(HERE, "molab_proxy.py"),
                      "--port", str(PROXY_PORT)],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(1)
        if port_open("127.0.0.1", PROXY_PORT):
            return True
    return False


def write_env(url):
    with open(ENV, "w", encoding="utf-8") as f:
        f.write("# siacode - molab MiniMax-M2.7 (IQ4_XS + expert offload) settings\n")
        f.write("# refill with: python siacode_up.py in this kit folder\n")
        f.write(f"# updated {time.strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"MOLAB_BASE_URL={url}\n")
        f.write(f"MOLAB_API_KEY={API_KEY}\n")


def e2e():
    try:
        r = requests.post(f"http://127.0.0.1:{PROXY_PORT}/v1/messages",
                          json={"model": "minimax-m2.7", "max_tokens": 24,
                                "messages": [{"role": "user", "content": "Say OK"}]},
                          headers={"authorization": "Bearer x", "anthropic-version": "2023-06-01"},
                          timeout=180)
        ok = r.status_code == 200 and "content" in r.text
        return ok, f"{r.status_code} {r.text[:160]}"
    except Exception as e:
        return False, "ERR " + str(e)[:160]


API_KEY = ""


def main():
    global API_KEY
    ck = 0
    say("== siacode_up ==")
    try:
        d = m.grab_creds()
    except SystemExit:
        say("! kit not linked yet - run:  python link_molab.py")
        return 2
    say(f"sandbox host: {d['host']}")
    for line in open(ENV, encoding="utf-8", errors="ignore"):
        if line.startswith("MOLAB_API_KEY="):
            API_KEY = line.split("=", 1)[1].strip()

    if not keeper_alive():
        if CHECK_ONLY:
            say("keeper: DOWN")
            ck = 1
        elif not start_keeper():
            say("! keeper failed to start")
            return 1
    say("keeper: alive")

    # fresh sandbox may have regenerated the llama key: sandbox copy wins
    try:
        r = m.get(d, "/root/llama_api_key.txt")
        if r.status_code == 200 and r.text.strip():
            API_KEY = r.text.strip()
    except Exception:
        pass

    st = sandbox_state()
    if not st:
        if CHECK_ONLY:
            say("sandbox: DEAD (410 / unreachable)")
            return 1
        say("sandbox: dead -> respinning via notebook page")
        if not chrome_up():
            launch_chrome()
        try:
            d = m.grab_creds(force=True)   # visit spins a fresh sandbox + refreshes cache
            say(f"new sandbox host: {d['host']}")
        except SystemExit as e:
            say("! sandbox respawn failed: %s" % e)
            return 1
        # keeper takes a moment to hop onto the new sandbox before run_inline works
        for _ in range(30):
            time.sleep(2)
            st = sandbox_state()
            if st:
                break
        if not st:
            say("! sandbox unreachable after respawn")
            return 1
    say(f"sandbox: gguf={st['gguf']} llama={st['llama']} ngrok={st['ngrok']} url={st['url']}")

    if not st["gguf"]:
        if CHECK_ONLY:
            say("! GGUF missing on sandbox - full setup (~12 min) required")
            ck = 1
        else:
            say("gguf missing -> full setup payload (MiniMax IQ4_XS 108GB, ~20-30 min)")
            run_bg_file("molab_payload_setup.py")
            say("  waiting for gguf ...")
            t0 = time.time()
            while time.time() - t0 < 2700:
                time.sleep(20)
                s2 = sandbox_state()
                if s2 and s2.get("gguf"):
                    say("  gguf ready")
                    break

    # llama.cpp-bin does not survive a respawn; GitHub ships .tar.gz now (setup
    # payload's zip path fails) so install the pinned build ourselves
    if not sandbox_bin_present():
        if CHECK_ONLY:
            say("! llama.cpp bin missing on sandbox")
            ck = 1
        else:
            say("llama.cpp bin missing -> pinned install (b12032 cuda-12.8, ~3 min)")
            run_bg_file("molab_payload_llamabin.py")
            t0 = time.time()
            while time.time() - t0 < 420:
                time.sleep(10)
                if sandbox_bin_present():
                    say("  llama.cpp bin ready")
                    break
            if not sandbox_bin_present():
                say("! llama.cpp bin install failed")
                return 1
    listening = st["llama"]
    if not listening:
        if CHECK_ONLY:
            say("llama-server: DOWN")
            ck = 1
        else:
            say("llama-server: down -> restarting (serve3: MiniMax + -ncmoe hybrid)")
            run_bg_file("molab_payload_serve3.py")
            if not wait_llama():
                say("! llama-server did not come up")
                return 1
            listening = True
    if listening:
        # CPU-sick guard: a server whose LD path missed the backend runs CPU-only
        # (~6 tok/s: a "hi" takes 5 min) while health still says ok. Verify by VRAM.
        vram, nproc = llama_health()
        say(f"llama-server: up (:8010) vram={vram}MiB procs={nproc}")
        if vram < 40000:
            if CHECK_ONLY:
                say("! llama-server looks CPU-bound - run a repair to restart it")
                ck = 1
            else:
                say("llama-server: CPU-bound -> killing + restarting")
                kill_llama()
                run_bg_file("molab_payload_serve3.py")
                if not wait_llama():
                    say("! llama-server did not come up after CPU-sick restart")
                    return 1
                say("llama-server: up (:8010) again with GPU offload")

    url = st["url"]
    if not (url and tunnel_ok(url)):
        if CHECK_ONLY:
            say(f"tunnel: DEAD ({url or 'no url'})")
        else:
            say("tunnel: down -> (re)starting ngrok")
            run_bg_file("molab_payload_ngrok2.py")
            url = wait_url()
            say(f"tunnel url: {url}")
    if not url:
        say("! no tunnel url")
        return 1
    if not tunnel_ok(url):
        say(f"! tunnel {url} not answering /health from this PC (VPN?)")
        return 1
    say("tunnel: reachable from PC")

    if not API_KEY:
        r = m.get(d, "/root/llama_api_key.txt")
        API_KEY = r.text.strip() if r.status_code == 200 else ""

    if not CHECK_ONLY:
        write_env(url)
        say("siacode.env: written")
        # the proxy reads siacode.env ONLY at startup: always restart it in heal
        # mode so it never keeps pointing at the previous tunnel/key
        say("proxy: restarting (it caches the env at startup)")
        start_proxy()
    else:
        say("siacode.env: (check only, not written)"); say("proxy: (check only)")

    say(f"proxy: {'up' if proxy_alive() else 'DOWN'} on :{PROXY_PORT}")

    if CHECK_ONLY:
        return ck
    ok, info = e2e()
    say(("E2E: OK  " if ok else "E2E: FAIL  ") + info)
    if ok:
        say("\nrun:  siacode")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
