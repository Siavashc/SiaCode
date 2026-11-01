"""Start/restart llama-server :8010 for MiniMax-M2.7-UD-IQ4_XS + expert offload.
Model: unsloth MiniMax-M2.7 (230B-A10B), weights 108GB > 97.9GB VRAM:
  - MoE expert weights split: first N layers' experts stay in CPU RAM (-ncmoe N;
    NOTE: never combine with -ot exps=CPU, that forces ALL experts to CPU),
    the rest + all attention/KV on GPU. N comes from $N_CMOE (default 26).
  - KV q8_0 both K and V (flash-attn on) so the window fits with room for experts.
  - Context 196608 = the GGUF's real n_ctx_train (NOT 200K/204800 - would exceed it),
    -np 1 MANDATORY (slots split -c).
TUNING: reads /root/serve_cfg.json {"ncmoe":N,"ctx":M} if present (kernel env vars do
NOT propagate here); falls back to N_CMOE/CTX env then defaults. Tune = write that file
+ rerun with SERVE_FORCE=1.
Also works for the old Qwen model if minimax gguf is absent (plain config).
Writes /root/ctx_used.txt; key stays /root/llama_api_key.txt."""
import glob
import json
import os
import secrets
import subprocess
import time
import urllib.request

_CFG = {}
if os.path.exists("/root/serve_cfg.json"):
    try:
        _CFG = json.load(open("/root/serve_cfg.json"))
    except Exception:
        _CFG = {}
# Baked-in defaults = the measured-best config (2026-11-01). These MUST match the tuned
# values, not the first-guess ones: /root/serve_cfg.json does not survive a sandbox
# respawn, so after a heal the defaults are what actually runs.
N_CMOE = int(_CFG.get("ncmoe", os.environ.get("N_CMOE", "26")))
CTX = int(_CFG.get("ctx", os.environ.get("CTX", "196608")))
THREADS = int(_CFG.get("threads", os.environ.get("THREADS", "16")))
NBATCH = int(_CFG.get("batch", "0")) or None
NUBATCH = int(_CFG.get("ubatch", "0")) or None
LOADMODE = _CFG.get("loadmode", os.environ.get("LOADMODE", "none"))

# prefer the newest pinned build (llama-b12032 > llama-b12023 ...)
_all_bins = sorted(glob.glob("/root/llama.cpp-bin/**/llama-server", recursive=True))
BIN = _all_bins[-1] if _all_bins else None
MM = sorted(glob.glob("/root/gguf/minimax/**/*UD-IQ4_XS*00001-of-*.gguf", recursive=True))
if MM:
    GGUF = MM[0]
    EXTRA = ["-ncmoe", str(N_CMOE), "-ctk", "q8_0", "-ctv", "q8_0"]
else:  # legacy Qwen setup
    GGUF = sorted(glob.glob("/root/gguf/*.gguf"))[0]
    EXTRA = []

KEYFILE = "/root/llama_api_key.txt"
KEY = open(KEYFILE).read().strip() if os.path.exists(KEYFILE) else ""
if not KEY:
    KEY = "sk-molab-" + secrets.token_urlsafe(24)
    open(KEYFILE, "w").write(KEY)

# every dir holding a .so must be on LD_LIBRARY_PATH or CUDA backend silently
# fails its libcudart.so.12 dep -> llama-server falls back to CPU (the 2.5h trap)
SODIRS = {os.path.dirname(BIN)}
for f in glob.glob("/root/llama.cpp-bin/**/*.so*", recursive=True):
    SODIRS.add(os.path.dirname(f))
env_extra = ":".join(sorted(SODIRS))


def vram():
    return subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                           "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()


def health(timeout=5):
    try:
        req = urllib.request.Request("http://127.0.0.1:8010/health",
                                     headers={"Authorization": "Bearer " + KEY})
        return urllib.request.urlopen(req, timeout=timeout).read().decode()[:60]
    except Exception as e:
        return "ERR " + str(e)[:50]


print("bin:", BIN, flush=True)
print("gguf:", GGUF, flush=True)
print("ncmoe:", N_CMOE, "ctx:", CTX, flush=True)
h = health(2)
print("already up?", h, "| vram:", vram(), flush=True)
if h.startswith("{") and not (os.environ.get("SERVE_FORCE") or _CFG.get("force")):
    print("SERVE-OK>> already running", flush=True)
    raise SystemExit(0)

subprocess.run("pkill -f llama-server ; sleep 3 ; true", shell=True, capture_output=True)

env = os.environ.copy()
env["LD_LIBRARY_PATH"] = env_extra + ":" + env.get("LD_LIBRARY_PATH", "")
log = open("/root/llama_server.log", "w")
cmd = [BIN, "-m", GGUF, "-ngl", "99", "-c", str(CTX), "-np", "1", "--flash-attn", "on",
       "-t", str(min(THREADS, os.cpu_count() or THREADS)), "--api-key", KEY,
       "--host", "0.0.0.0", "--port", "8010"] + EXTRA
if NBATCH:
    cmd += ["-b", str(NBATCH)]
if NUBATCH:
    cmd += ["-ub", str(NUBATCH)]
if LOADMODE:
    cmd += ["-lm", str(LOADMODE)]
print("launching:", " ".join(cmd), flush=True)
p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)

ok = False
t0 = time.time()
while time.time() - t0 < 900:  # 108GB page-in can take a few minutes
    if health().startswith("{"):
        ok = True
        break
    time.sleep(4)
tail = subprocess.run("tail -c 1500 /root/llama_server.log", shell=True,
                      capture_output=True, text=True).stdout
print(tail, flush=True)
print("pid", p.pid, "| health:", health(), "| vram:", vram(), flush=True)
if ok:
    open("/root/ctx_used.txt", "w").write(str(CTX))
    print("SERVE-OK>> ctx=%d ncmoe=%d" % (CTX, N_CMOE), flush=True)
else:
    print("SERVE-FAIL", flush=True)
print("DONE", flush=True)