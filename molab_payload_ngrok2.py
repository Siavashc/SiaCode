"""Install + run ngrok in the sandbox, publishing llama-server :8010. Writes /root/relay_url.txt"""
import json
import os
import re
import subprocess
import time
import urllib.request

TOKEN = "__NGROK_TOKEN__"   # substituted on the PC side from config.json
NG = "/root/ngrok/ngrok"


def sh(cmd, t=180):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return (r.stdout + r.stderr).strip()


os.makedirs("/root/ngrok", exist_ok=True)
if not os.path.exists(NG):
    print("downloading ngrok...", flush=True)
    try:
        urllib.request.urlretrieve(
            "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz",
            "/root/ngrok/n.tgz")
        print("tgz size", os.path.getsize("/root/ngrok/n.tgz"), flush=True)
        print(sh("tar xzf /root/ngrok/n.tgz -C /root/ngrok"), flush=True)
    except Exception as e:
        print("DL-FAIL", str(e)[:200], flush=True)
print("bin present:", os.path.exists(NG), flush=True)
if not os.path.exists(NG):
    raise SystemExit("no ngrok binary")

os.chmod(NG, 0o755)
print(sh(NG + " version"), flush=True)
print(sh(NG + " config add-authtoken " + TOKEN), flush=True)
print("kill old:", sh("pkill -f 'ngrok http' ; sleep 1 ; true"), flush=True)

log = open("/root/ngrok.log", "w")
p = subprocess.Popen([NG, "http", "8010", "--log", "stdout", "--log-format", "json"],
                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
print("ngrok pid", p.pid, flush=True)

url = ""
for i in range(30):
    time.sleep(2)
    try:
        txt = open("/root/ngrok.log").read()
    except Exception:
        txt = ""
    m = re.search(r'https://[a-z0-9-]+\.ngrok-free\.(?:app|dev)', txt)
    if m:
        url = m.group(0)
        break
    if "authentication failed" in txt.lower() or "ERR_NGROK" in txt:
        break
if url:
    open("/root/relay_url.txt", "w").write(url)
    print("NGROK-OK>>", url, flush=True)
else:
    print("NGROK-FAIL>>", txt[-1500:], flush=True)
print("DONE", flush=True)
