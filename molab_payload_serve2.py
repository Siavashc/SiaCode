"""Start/restart llama-server on :8010, self-contained (no dependency on /root/qwen_serve.py).
Tries ctx 131072 first, falls back to 65536 if the GPU cannot hold it.
Writes /root/ctx_used.txt and the key to /root/llama_api_key.txt."""
import glob
import os
import secrets
import subprocess
import time
import urllib.request

BINS = sorted(glob.glob("/root/llama.cpp-bin/**/llama-server", recursive=True))
GGUFS = sorted(glob.glob("/root/gguf/*.gguf"))
if not BINS or not GGUFS:
    print("MISSING bin=%s gguf=%s" % (BINS, GGUFS), flush=True)
    raise SystemExit(1)
BIN, GGUF = BINS[0], GGUFS[0]
KEYFILE = "/root/llama_api_key.txt"
KEY = open(KEYFILE).read().strip() if os.path.exists(KEYFILE) else ""
if not KEY:
    KEY = "sk-molab-" + secrets.token_urlsafe(24)
    open(KEYFILE, "w").write(KEY)
LIBS = ":".join(d for d in glob.glob("/root/llama.cpp-bin/**/lib*", recursive=True) if os.path.isdir(d))
# tarball layout puts every .so flat in the binary's own dir and cudart in its own
# non-"lib*" subdir - every dir that contains a .so must be on the path or the
# dynamic CUDA backend never loads (libcudart.so.12 not found -> silent CPU fallback)
BINDIR = os.path.dirname(BIN)
SODIRS = {BINDIR}
for f in glob.glob("/root/llama.cpp-bin/**/*.so*", recursive=True):
    SODIRS.add(os.path.dirname(f))
env_extra = ":".join(sorted(SODIRS)) + (":" + LIBS if LIBS else "")


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


def launch(ctx):
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = env_extra + ":" + env.get("LD_LIBRARY_PATH", "")
    log = open("/root/llama_server.log", "w")
    p = subprocess.Popen([BIN, "-m", GGUF, "-ngl", "99", "-c", str(ctx), "-np", "1", "--flash-attn", "on",
                          "-t", "8", "--api-key", KEY, "--host", "0.0.0.0", "--port", "8010"],
                         stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    return p.pid


print("bin:", BIN, flush=True)
print("gguf:", GGUF, flush=True)
h = health(2)
print("already up?", h, "| vram:", vram(), flush=True)
if h.startswith("{"):
    print("SERVE-OK>> already running", flush=True)
    raise SystemExit(0)

subprocess.run("pkill -f llama-server ; sleep 3 ; true", shell=True, capture_output=True)
for ctx in (131072, 65536):
    print("launching ctx=%d" % ctx, flush=True)
    pid = launch(ctx)
    ok = False
    t0 = time.time()
    while time.time() - t0 < 300:
        if health().startswith("{"):
            ok = True
            break
        time.sleep(4)
    print("pid", pid, "| health:", health(), "| vram:", vram(), flush=True)
    if ok:
        open("/root/ctx_used.txt", "w").write(str(ctx))
        print("SERVE-OK>> ctx=%d" % ctx, flush=True)
        break
    print("ctx %d failed, falling back" % ctx, flush=True)
print("DONE", flush=True)
