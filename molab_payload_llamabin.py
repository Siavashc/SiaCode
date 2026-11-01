"""Install llama.cpp bin into /root/llama.cpp-bin on the sandbox kernel.

PINNED RELEASE = b12032 (ubuntu-cuda-12.8 x86_64).
  * b12032 is REQUIRED for MiniMax-M2.7 (arch `minimax-m2`); b12023 lacks the arch
    entirely and fails with "unknown model architecture".
  * cuda-12.8 (NOT 13.2) - the 13.2 build produces gibberish for M2.7.
  * same build also runs the Qwen3.5-122B fallback lane, so one build serves both.
Idempotent: skips if a llama-server already exists. Writes /root/llamabin_status.json
and, for compatibility with older callers, the same logic to /root/_llamabin_b12032.py.
"""
import glob
import json
import os
import subprocess
import urllib.request

LLAMA_DIR = "/root/llama.cpp-bin"
STATUS = "/root/llamabin_status.json"
TAG = "b12032"
TARS = ["llama-%s-bin-ubuntu-cuda-12.8-x64.tar.gz" % TAG,
        "cudart-%s-bin-ubuntu-cuda-12.8-x64.tar.gz" % TAG]
BASE = "https://github.com/ggml-org/llama.cpp/releases/download/%s/" % TAG

# skip if this exact build is already installed (do NOT accept an older one:
# b12023 cannot load MiniMax and the "already running" trap is how that hides)
def have():
    return sorted(glob.glob(LLAMA_DIR + "/**/llama-server", recursive=True))


have_ok = [b for b in have() if TAG in b]
if have_ok:
    json.dump({"stage": "already", "bin": have_ok[0]}, open(STATUS, "w"))
    print("ALREADY>>", have_ok[0])
    raise SystemExit(0)

print("downloading", TAG, "->", LLAMA_DIR, flush=True)
os.makedirs(LLAMA_DIR, exist_ok=True)
for t in TARS:
    tgt = os.path.join(LLAMA_DIR, t)
    if not os.path.exists(tgt):
        urllib.request.urlretrieve(BASE + t, tgt)
    print("extract", tgt, flush=True)
    subprocess.run(["tar", "-xzf", tgt, "-C", LLAMA_DIR], check=True)

binp = sorted(glob.glob(LLAMA_DIR + "/**/llama-server", recursive=True))
for f in binp:
    os.chmod(f, 0o755)
print("bin:", binp[:2], flush=True)
if not binp:
    json.dump({"stage": "no-binary"}, open(STATUS, "w"))
    raise SystemExit(1)
json.dump({"stage": "installed", "tag": TAG, "bin": binp[-1]}, open(STATUS, "w"))
print("LLAMABIN-OK>>", binp[-1])
