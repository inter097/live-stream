# live-stream

Self-hosted live streaming. Captura tu pantalla → 2 calidades HLS → sírvelo local o por Cloudflare tunnel. Sin OBS, sin Docker, sin VPS, sin compilar. Un solo archivo.

```
python3 stream.py
  │
  ├── HTTP server (:8080, Python stdlib)
  ├── cloudflared tunnel → your-domain.com (HTTPS, opcional)
  └── ffmpeg (avfoundation / x11grab / gdigrab)
        split=2 → 1080p / 480p
        HLS → /tmp/hls-screen/
```

- **2 calidades:** 480p (1400k) / 1080p (4500k)
- **Latencia:** ~10–15s
- **Costo:** $0
- **Dependencias:** ffmpeg + python3
- **CPU:** ~15–25% en Apple Silicon (2 transcodes libx264 ultrafast)
- **Plataformas:** macOS / Linux / Windows
- **Un solo archivo:** `stream.py` (orquestador + HTTP server + player HTML)

---

## Quick start (local only)

```bash
git clone https://github.com/<your-user>/live-stream.git
cd live-stream
python3 stream.py
```

Abrir **http://localhost:8080/** — elegir calidad. `Ctrl+C` para parar.

### Prerequisites

| Dep | macOS | Linux (Debian) | Windows |
|---|---|---|---|
| ffmpeg | `brew install ffmpeg` | `sudo apt install ffmpeg` | [ffmpeg.org](https://ffmpeg.org/download.html) |
| Python3 | `brew install python3` | `sudo apt install python3` | [python.org](https://python.org/) |

> **macOS first run:** System Preferences → Privacy & Security → Screen Recording → add ffmpeg. If screen is black, this is why.

---

## With Cloudflare tunnel (HTTPS, no ports exposed)

`stream.py` auto-detects `cloudflared` and starts the tunnel if config exists.

### 1. Install cloudflared

```bash
brew install cloudflared          # macOS
sudo apt install cloudflared      # Linux
```

### 2. Login + create tunnel

```bash
cloudflared tunnel login          # opens browser, authorize
cloudflared tunnel create live    # outputs tunnel UUID
```

### 3. Create config

```bash
mkdir -p ~/.cloudflared
cat > ~/.cloudflared/config.yml << 'EOF'
tunnel: <YOUR-TUNNEL-UUID>
credentials-file: <YOUR-HOME>/.cloudflared/<YOUR-TUNNEL-UUID>.json
ingress:
  - hostname: stream.example.com
    service: http://127.0.0.1:8080
  - service: http_status:404
EOF
```

### 4. Create DNS route

```bash
cloudflared tunnel route dns live stream.example.com
```

### 5. Stream

```bash
python3 stream.py
```

Open **https://stream.example.com** — HTTPS, no ports exposed, DDoS protected.

> Without cloudflared or config, `stream.py` runs local-only at `http://localhost:8080/`.

---

## How it works

```
┌──────────────────────────────────────────────────────┐
│  stream.py (single file)                             │
│                                                      │
│  1. Dep check: ffmpeg, python3                       │
│  2. HTTP server on :8080 (ThreadingHTTPServer)       │
│  3. Cloudflare tunnel (if cloudflared + config found)│
│  4. Symlinks: serve/screen/{1080,480} → hls dir      │
│  5. ffmpeg: screen capture → split=2 → HLS output    │
│  6. Health check: waits for tv.m3u8 before announcing│
│  7. Reconnect: restarts ffmpeg with 2s cooldown      │
│  8. Cleanup: SIGINT/SIGTERM kills only what it       │
│     started (no orphaned processes)                  │
└──────────────────────────────────────────────────────┘
```

**Screen capture by OS:**
- macOS: `avfoundation` (auto-detects screen index)
- Linux: `x11grab` (`:0.0`)
- Windows: `gdigrab` (`desktop`)

**HTTP server** is Python stdlib (`http.server.ThreadingHTTPServer`) with cache headers:
- `.m3u8` → no-cache (playlist changes constantly)
- `.ts` → 1h cache (immutable by filename)
- CORS: `Access-Control-Allow-Origin: *`

**Player** is HTML inline in `stream.py` — 2 `<a>` links to `.m3u8` files + vanilla JS status badge (EN VIVO / OFFLINE). No hls.js, no frameworks, no external files.

---

## Technical decisions

| Decision | Why |
|---|---|
| Single file (`stream.py`) | Orquestador + HTTP server + player HTML en un archivo. Clonas, lees, entiendes. |
| ffmpeg screen capture, not OBS | One dependency instead of two. No GUI config, no plugins. Direct frame buffer access. |
| `libx264 -preset ultrafast`, not VideoToolbox | VideoToolbox produces non-monotonic DTS with avfoundation screen capture. HLS segments never generate. |
| Direct `.m3u8` links, not hls.js | Safari plays HLS natively. VLC opens `.m3u8` directly. Zero JS bugs. Maximum simplicity. |
| Python stdlib http.server, not Go/nginx | Zero extra deps. No compile step. ThreadingHTTPServer handles 1-3 viewers fine. Cache headers in 10 lines. |
| HTML inline in Python string | No `player/` directory, no symlinks, no file I/O. HTML served from memory. |
| Python, not bash | Cross-platform (Windows has no native bash). Robust signal handling. Clean finally-block cleanup. |
| No Makefile / Docker / launchd | `python3 stream.py` = one command. Nothing to build, nothing to install, nothing to configure. |
| `split=2` for 2 qualities | Single input → 2 outputs in one ffmpeg process. Consistent GOP across renditions. |
| GOP 60 frames (2s @ 30fps) | HLS segment = 4s = 2 GOPs. No scene change detection = no B-frames = no buffer holes. |

---

## Troubleshooting

### Screen is black (macOS)

macOS requires Screen Recording permission for ffmpeg:

1. System Preferences → Privacy & Security → Screen Recording
2. Add `/opt/homebrew/bin/ffmpeg`
3. Restart terminal, run `python3 stream.py` again

### Port 8080 already in use

```bash
lsof -i :8080
# kill the process or change HTTP_PORT in stream.py
```

### Missing dependencies

```bash
stream.py checks for ffmpeg + python3 at startup.
Install missing ones (see Prerequisites table above).
```

### Tunnel not working

```bash
# Check config exists
cat ~/.cloudflared/config.yml

# Check tunnel is running
cloudflared tunnel info live

# Without tunnel, stream.py still works at localhost:8080
```

### ffmpeg crashes and restarts

This is the reconnect loop working. Check `/tmp/stream.log` for details. Common causes:
- Screen Recording permission revoked
- Display disconnected
- ffmpeg killed by OS (OOM)

### Logs

```bash
tail -f /tmp/stream.log
```

---

## Project structure

```
.
├── stream.py       ← orquestador + HTTP server + player HTML (single file)
├── .gitignore
└── LICENSE         MIT
```

That's it. 3 files, 0 directories.

---

## Limitations

- **No audio:** video only. System audio via BlackHole is future work.
- **No auth:** anyone with the URL can watch (Cloudflare proxy doesn't authenticate).
- **CPU-bound:** 2 simultaneous libx264 transcodes. ~15-25% CPU on M-series.
- **Single screen:** captures screen 0 only. Multi-monitor not supported.
- **TCC permission:** macOS requires manual Screen Recording permission for ffmpeg.

---

## License

MIT — see [LICENSE](LICENSE).
