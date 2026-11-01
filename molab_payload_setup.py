"""Sandbox respawn payload: fetch MiniMax-M2.7-UD-IQ4_XS GGUF (+ llama.cpp bin if needed).
Idempotent. Runs detached on the kernel; stage log -> /root/minimax_setup_status.json.
NOTE: 2026-11-01 primary model switched from Qwen3.5-122B-abliterix (70GB, fully in VRAM)
to MiniMax-M2.7 (108GB > VRAM) -> needs the llama-b12032 build + -ncmoe expert split."""
import glob
import json
import os
import subprocess

STATUS = "/root/minimax_setup_status.json"
REPO = "unsloth/MiniMax-M2.7-GGUF"
GGUF_DIR = "/root/gguf/minimax"
LLAMA_DIR = "/root/llama.cpp-bin"


def put(k, v):
    d = {}
    try:
        d = json.load(open(STATUS))
    except Exception:
        pass
    d[k] = v
    json.dump(d, open(STATUS, "w"))


def have_minimax():
    return sorted(glob.glob(GGUF_DIR + "/**/*UD-IQ4_XS*00001-of-*.gguf", recursive=True))


def have_bin():
    return sorted(glob.glob(LLAMA_DIR + "/**/llama-server", recursive=True))


mm, bins = have_minimax(), have_bin()
if mm and bins:
    put("stage", "already")
    print("ALREADY>> gguf+bin present")
    raise SystemExit(0)

os.makedirs(GGUF_DIR, exist_ok=True)
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

if not mm:
    put("stage", "gguf-download")
    r = subprocess.run(["hf", "download", REPO, "--include", "UD-IQ4_XS/*",
                        "--local-dir", GGUF_DIR], capture_output=True, text=True, timeout=3600)
    put("hf_out", (r.stdout or "")[-800:])
    put("hf_err", (r.stderr or "")[-800:])
    mm = have_minimax()
    if not mm:
        put("stage", "gguf-failed")
        raise SystemExit(1)
    put("gguf", mm)

if not bins:
    # self-contained: do NOT depend on /root/_llamabin_b12032.py existing, kernel
    # files die with the sandbox (old gap - setup used to fail on a fresh respawn).
    # b12032 + cuda-12.8 are the only combination that loads MiniMax-M2.7.
    put("stage", "llamabin")
    import urllib.request
    TAG = "b12032"
    BASE = "https://github.com/ggml-org/llama.cpp/releases/download/%s/" % TAG
    os.makedirs(LLAMA_DIR, exist_ok=True)
    try:
        for t in ("llama-%s-bin-ubuntu-cuda-12.8-x64.tar.gz" % TAG,
                  "cudart-%s-bin-ubuntu-cuda-12.8-x64.tar.gz" % TAG):
            tgt = os.path.join(LLAMA_DIR, t)
            if not os.path.exists(tgt):
                urllib.request.urlretrieve(BASE + t, tgt)
            subprocess.run(["tar", "-xzf", tgt, "-C", LLAMA_DIR], check=True)
        for f in glob.glob(LLAMA_DIR + "/**/llama-server", recursive=True):
            os.chmod(f, 0o755)
    except Exception as e:
        put("llamabin_err", repr(e)[:400])
    bins = have_bin()
    if not bins:
        put("stage", "llamabin-failed")
        raise SystemExit(1)
put("stage", "DONE-SPAWNED")
print("MINIMAX-SETUP-OK>>", mm[0], "| bin:", bins[-1])