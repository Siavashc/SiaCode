# siacode-kit 🦐

Run **your own Claude Code** powered by **MiniMax-M2.7 (230B MoE, 196k context)** on
**molab's free GPU** — no API key, no subscription, no Anthropic account. Claude Code's
real UI, tools, streaming and thinking on a fully local chain you control.

> This is the exact setup behind the **SiaCode** reels. Scripts are fully automated:
> `siacode` checks itself at every launch and rebuilds the whole chain if anything died.

---

## What you get

```
You: siacode
   -> gate check (~10s, self-heals anything broken)
   -> Claude Code (real UI) -> local proxy :8011 -> ngrok tunnel
   -> molab sandbox llama-server :8010 -> MiniMax-M2.7 (108 GB, GPU+CPU expert split)
```

- **Model:** `unsloth/MiniMax-M2.7-GGUF` quant **UD-IQ4_XS** (~108 GB in 4 shards)
- **Engine:** llama.cpp build `b12032` (CUDA 12.8) — the build that ships the `minimax-m2` arch
- **Window:** 196 608 tokens of context (the model's real trained length), one slot, full width
- **Speed on molab's free RTX PRO 6000:** ~24 tok/s steady decode, ~530 tok/s prefill
  (instant on cache hits). The model **thinks before every answer**, so small questions take
  ~25–40s wall — that is normal, not slowness.
- **12-hour wall:** molab kills the sandbox every ~12 h — `siacode` respawns and rebuilds
  everything automatically on your next launch (budget: 108 GB re-download, ~20–30 min).

## Requirements

1. Windows 10/11 + PowerShell
2. Python 3.10+ (64-bit) — `python.org`
3. Claude Code CLI installed (`claude` works in your terminal)
4. A free **molab** account: [molab.marimo.io](https://molab.marimo.io) (Google sign-in)
5. A free **ngrok** authtoken: [dashboard.ngrok.com](https://dashboard.ngrok.com)
6. VPN note (Iran): sign-up pages generally need it; the sandbox itself reaches
   HF/GitHub/ngrok on its own, and your PC reaches the tunnel from most networks.

## Setup (one-time, ~30 min of mostly waiting)

```powershell
cd siacode-kit
powershell -ExecutionPolicy Bypass -File .\install.ps1   # deps + siacode command
python link_molab.py                                     # connect molab + ngrok token
```

`link_molab.py` opens a Chrome window: sign into molab with Google, click **+ New Notebook**,
and the script detects it automatically. Then:

```powershell
siacode          # first run: downloads the 108 GB model (~20-30 min), then opens
```

Done. Every later `siacode` call re-checks the chain in ~10 s and self-heals anything dead.

## Files

| file | what it does |
|---|---|
| `install.ps1` | python deps + installs the `siacode` PowerShell command |
| `link_molab.py` | one-time link: ngrok token + your molab notebook → `config.json` |
| `siacode_up.py` | the brain: `--check` gate + full self-heal (respawn, install, restart) |
| `molab_api.py` | molab auth token + file API client |
| `molab_run.py` | runs arbitrary python inside the sandbox kernel over HTTP |
| `molab_keeper.py` | keeps a PC-owned kernel session alive (edit rights) |
| `molab_proxy.py` | normalises Anthropic API bodies for llama.cpp, listens on :8011 |
| `molab_payload_setup.py` | sandbox job: download MiniMax GGUF (+llama.cpp if missing) |
| `molab_payload_llamabin.py` | sandbox job: pinned llama.cpp b12032 install |
| `molab_payload_serve3.py` | sandbox job: llama-server with expert-offload config |
| `molab_payload_serve2.py` | optional: switch to the old Qwen3.5-122B uncensored lane |
| `molab_payload_ngrok2.py` | sandbox job: ngrok tunnel |

`config.json` / `siacode.env` / `molab_creds.json` are **your own** machine's state —
never share them, they contain your tokens.

---

<details>
<summary><b>فارسی / راهنمای فارسی</b></summary>

# سیاکد-کیت 🦐

کلود کد واقعی (رابط و ابزارهای خودش) ولی با مدل **MiniMax-M2.7 (230 پارامتر، MoE، کانتکست ۱۹۶ هزار توکن)**
روی **GPU رایگانِ molab** — بدون کلید API، بدون اشتراک، بدون حساب آنتروپیک.

## چیزهایی که لازم داری

1. ویندوز ۱۰/۱۱ + پایتون ۳.۱۰ به بالا (۶۴ بیتی) از python.org
2. کلود کد نصب باشه (`claude` توی ترمینال کار کنه)
3. حساب رایگان molab: مولاب مریمو با گوگل لاگین میشه
4. توکن رایگان ngrok از dashboard.ngrok.com
5. نکته ایران: برای ثبت‌نام‌ها VPN لازمه؛ بعدش خودِ سرور مستقیم دانلود می‌کنه و کانکشن
   شما به ngrok معمولاً از شبکه‌های ایران هم جواب میده.

## نصب (یک بار برای همیشه، حدود ۳۰ دقیقه که بیشترش زمان دانلوده)

```powershell
cd siacode-kit
powershell -ExecutionPolicy Bypass -File .\install.ps1   # پکیج‌ها + دستور siacode
python link_molab.py                                     # اتصال مولاب + توکن ngrok
```

اسکریپت دوم یک کروم باز می‌کنه: با گوگل توی مولاب لاگین کن، **+ New Notebook** بزن؛
اسکریپت خودش پیداش می‌کنه. بعدش:

```powershell
siacode          # بار اول مدل ۱۰۸ گیگابایتی دانلود میشه (~۲۰ تا ۳۰ دقیقه)، بعدش باز میشه
```

## نکات مهم

- مولاب هر **~۱۲ ساعت** سندبوکس رو می‌کشه؛ بار بعدی که `siacode` رو بزنی خودش همه چیز رو
  از نو می‌سازه (۲۰-۳۰ دقیقه دانلود). مدل روی دیسک سندبوکس می‌مونه و دوباره دانلود نمیشه.
- این مدل **قبل از هر جواب فکر می‌کنه (thinking)**؛ یعنی سوال‌های ساده هم ۲۵-۴۰ ثانیه طول
  می‌کشن. کاملاً نرماله. سرعت واقعی خروجی ~۲۴ توکن در ثانیه.
- `config.json` و `siacode.env` توکن‌های خودت هستن — **هیچ‌وقت با کسی به اشتراک نذار.**

</details>

---

License: MIT. Everything here is user-space scripts; the model runs server-side on molab
with its own terms. Be a good citizen: don't hammer infra you got for free.