#!/usr/bin/env python3
"""
stream.py — Universal screen capture streaming pipeline.

One command captures your screen, transcodes to 3 HLS qualities
(720p/480p/360p), and serves them via Python http.server + Cloudflare tunnel.

Usage:
    python3 stream.py

Ctrl+C stops capture. Processes started by this script are cleaned up on exit.
"""

import http.server
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

HLS_DIR = "/tmp/hls-screen"
SERVE_DIR = "/tmp/streaming-serve"
HTTP_PORT = 8080
TUNNEL_CONFIG = os.path.expanduser("~/.cloudflared/config.yml")
TUNNEL_NAME = "live"
LOG_FILE = "/tmp/stream.log"

C_BLUE = "\033[94m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_BOLD = "\033[1m"
C_RESET = "\033[0m"

PLAYER_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Live Stream</title>
  <style>
    body {
      background: #0f172a; color: #e2e8f0;
      font-family: system-ui, sans-serif;
      min-height: 100vh; display: flex; flex-direction: column;
      align-items: center; justify-content: center; gap: 1.5rem;
    }
    a {
      font-size: 1.5rem; font-weight: 700; color: #38bdf8;
      text-decoration: none; padding: 0.5rem 2rem;
      border: 2px solid #334155; border-radius: 8px;
      transition: all 0.15s;
    }
    a:hover { border-color: #38bdf8; background: #1e293b; }
    .status {
      font-size: 1rem; font-weight: 600; letter-spacing: 0.05em;
      padding: 0.4rem 1.2rem; border-radius: 6px;
    }
    .live { background: #dc2626; color: #fff; }
    .offline { background: #334155; color: #94a3b8; }
    .dot {
      display: inline-block; width: 8px; height: 8px;
      border-radius: 50%; margin-right: 6px;
      animation: pulse 1.5s infinite;
    }
    .live .dot { background: #fff; }
    .offline .dot { background: #64748b; animation: none; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
  </style>
</head>
<body>
  <div id="status" class="status offline"><span class="dot"></span>OFFLINE</div>
  <p><a href="/screen/720/tv.m3u8">720p</a></p>
  <p><a href="/screen/480/tv.m3u8">480p</a></p>
  <p><a href="/screen/360/tv.m3u8">360p</a></p>
  <script>
    async function checkStatus() {
      const el = document.getElementById('status');
      try {
        const r = await fetch('/screen/720/tv.m3u8', { method: 'HEAD' });
        if (r.ok) {
          el.className = 'status live';
          el.innerHTML = '<span class="dot"></span>EN VIVO';
        } else {
          el.className = 'status offline';
          el.innerHTML = '<span class="dot"></span>OFFLINE';
        }
      } catch {
        el.className = 'status offline';
        el.innerHTML = '<span class="dot"></span>OFFLINE';
      }
    }
    checkStatus();
    setInterval(checkStatus, 5000);
  </script>
</body>
</html>"""

started_procs = []
we_started = {"tunnel": False}


def log(msg, color=C_RESET):
    line = f"{color}{msg}{C_RESET}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(re.sub(r"\033\[[0-9;]*m", "", line) + "\n")
    except OSError:
        pass


def port_open(port, host="127.0.0.1"):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect((host, port))
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


def proc_running(pattern):
    try:
        r = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True,
        )
        return r.returncode == 0
    except FileNotFoundError:
        return False


def check_dependencies():
    missing = []
    for dep in ("ffmpeg", "python3"):
        if not shutil.which(dep):
            missing.append(dep)
    if missing:
        log(f"  [FAIL] Missing dependencies: {', '.join(missing)}", C_RED)
        log(f"         Install with: brew install {' '.join(missing)}", C_RED)
        sys.exit(1)
    log("  [OK] Dependencies: ffmpeg, python3", C_GREEN)


class CacheHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SERVE_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(PLAYER_HTML.encode("utf-8"))
            return
        super().do_GET()

    def end_headers(self):
        path = self.path.split("?")[0]
        self.send_header("Access-Control-Allow-Origin", "*")
        if path.endswith(".m3u8"):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        elif path.endswith(".ts"):
            self.send_header("Cache-Control", "public, max-age=3600")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


def start_http_server():
    if port_open(HTTP_PORT):
        log(f"  [OK] HTTP server already running on :{HTTP_PORT}", C_GREEN)
        return

    os.makedirs(SERVE_DIR, exist_ok=True)
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", HTTP_PORT), CacheHandler
    )
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    for _ in range(10):
        if port_open(HTTP_PORT):
            log(f"  [OK] HTTP server started on :{HTTP_PORT}", C_GREEN)
            return
        time.sleep(0.5)
    log("  [FAIL] HTTP server did not start", C_RED)
    sys.exit(1)


def ensure_tunnel():
    if proc_running("cloudflared.*tunnel.*run.*live"):
        log("  [OK] Cloudflare tunnel already running", C_GREEN)
        return

    if not shutil.which("cloudflared"):
        log("  [WARN] cloudflared not found — local only", C_YELLOW)
        log(f"         Stream at http://localhost:{HTTP_PORT}/", C_YELLOW)
        return

    if not os.path.isfile(TUNNEL_CONFIG):
        log(f"  [WARN] Tunnel config not found ({TUNNEL_CONFIG})", C_YELLOW)
        log(f"         Stream at http://localhost:{HTTP_PORT}/", C_YELLOW)
        return

    log("  [..] Starting Cloudflare tunnel...", C_YELLOW)
    p = subprocess.Popen(
        ["cloudflared", "tunnel", "--config", TUNNEL_CONFIG, "run", TUNNEL_NAME],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    started_procs.append(p)
    we_started["tunnel"] = True
    time.sleep(3)
    if p.poll() is None:
        log(f"  [OK] Cloudflare tunnel started (pid={p.pid})", C_GREEN)
    else:
        log("  [WARN] Tunnel failed — local only", C_YELLOW)
        log(f"         Stream at http://localhost:{HTTP_PORT}/", C_YELLOW)


def setup_symlinks():
    os.makedirs(SERVE_DIR, exist_ok=True)

    if os.path.exists(HLS_DIR):
        shutil.rmtree(HLS_DIR)
    for q in ("360", "480", "720"):
        os.makedirs(os.path.join(HLS_DIR, q))

    screen_dir = os.path.join(SERVE_DIR, "screen")
    os.makedirs(screen_dir, exist_ok=True)

    for q in ("360", "480", "720"):
        link = os.path.join(screen_dir, q)
        if os.path.islink(link) or os.path.exists(link):
            os.unlink(link)
        os.symlink(os.path.join(HLS_DIR, q), link)

    log(f"  [OK] Symlinks ready ({SERVE_DIR}/screen/ -> {HLS_DIR}/)", C_GREEN)


def cleanup_symlinks():
    screen_dir = os.path.join(SERVE_DIR, "screen")
    for q in ("360", "480", "720"):
        link = os.path.join(screen_dir, q)
        try:
            if os.path.islink(link):
                os.unlink(link)
        except OSError:
            pass
    try:
        if os.path.isdir(screen_dir) and not os.listdir(screen_dir):
            os.rmdir(screen_dir)
    except OSError:
        pass


def cleanup_started_procs():
    for p in started_procs:
        if p.poll() is None:
            try:
                p.send_signal(signal.SIGINT)
                p.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                p.kill()
                p.wait()
    cleanup_symlinks()


def detect_screen_index_macos():
    try:
        r = subprocess.run(
            ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True, text=True, timeout=5,
        )
        for line in r.stderr.splitlines():
            if "capture screen" in line.lower():
                m = re.search(r"\[(\d+)\]", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return "2"


def build_ffmpeg_cmd():
    os_name = sys.platform

    filter_complex = (
        "[0:v]split=3[v1][v2][v3];"
        "[v1]fps=30,scale=1280:720[v720];"
        "[v2]fps=30,scale=854:480[v480];"
        "[v3]fps=30,scale=640:360[v360]"
    )

    if os_name == "darwin":
        idx = detect_screen_index_macos()
        encoder = "libx264"
        extra_enc = ["-preset", "ultrafast"]
        input_args = [
            "-f", "avfoundation",
            "-framerate", "30",
            "-capture_cursor", "1",
            "-capture_mouse_clicks", "1",
            "-i", f"{idx}:none",
        ]
    elif os_name.startswith("linux"):
        encoder = "libx264"
        extra_enc = ["-preset", "ultrafast"]
        input_args = [
            "-f", "x11grab",
            "-framerate", "25",
            "-i", ":0.0",
        ]
    elif os_name in ("win32", "cygwin"):
        encoder = "libx264"
        extra_enc = ["-preset", "ultrafast"]
        input_args = [
            "-f", "gdigrab",
            "-framerate", "30",
            "-i", "desktop",
        ]
    else:
        log(f"  [FAIL] Unsupported OS: {os_name}", C_RED)
        sys.exit(1)

    qualities = [
        ("720", "2500k", "2800k", "5000k"),
        ("480", "1400k", "1600k", "2800k"),
        ("360", "800k",  "900k",  "1600k"),
    ]

    cmd = ["ffmpeg"]
    cmd.extend(input_args)
    cmd.extend(["-filter_complex", filter_complex])

    for res, bv, maxrate, bufsize in qualities:
        cmd.extend([
            "-map", f"[v{res}]",
            "-c:v", encoder,
        ])
        cmd.extend(extra_enc)
        cmd.extend([
            "-pix_fmt", "yuv420p",
            "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
            "-b:v", bv, "-maxrate", maxrate, "-bufsize", bufsize,
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "6",
            "-hls_flags", "delete_segments",
            "-hls_segment_filename", f"{HLS_DIR}/{res}/tv_%03d.ts",
            f"{HLS_DIR}/{res}/tv.m3u8",
        ])

    return cmd


def wait_for_hls(timeout=10):
    m3u8 = os.path.join(HLS_DIR, "720", "tv.m3u8")
    for _ in range(timeout * 2):
        if os.path.isfile(m3u8) and os.path.getsize(m3u8) > 0:
            return True
        time.sleep(0.5)
    return False


def _sigterm_handler(signum, frame):
    raise KeyboardInterrupt


def main():
    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        with open(LOG_FILE, "w") as f:
            f.write(f"--- stream.py started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    except OSError:
        pass

    log(f"\n{C_BOLD}  stream.py — screen capture pipeline{C_RESET}\n")

    log("  [1/4] Checking dependencies...", C_BLUE)
    check_dependencies()

    log("  [2/4] Starting HTTP server...", C_BLUE)
    start_http_server()

    log("  [3/4] Checking Cloudflare tunnel...", C_BLUE)
    ensure_tunnel()

    log("  [4/4] Setting up symlinks + launching ffmpeg...", C_BLUE)
    setup_symlinks()

    if sys.platform == "darwin":
        log(f"\n  {C_YELLOW}[WARN] If screen is black:{C_RESET}", C_YELLOW)
        log(f"  {C_YELLOW}  System Preferences > Privacy & Security > Screen Recording{C_RESET}", C_YELLOW)
        log(f"  {C_YELLOW}  -> Add /opt/homebrew/bin/ffmpeg{C_RESET}\n", C_YELLOW)

    cmd = build_ffmpeg_cmd()

    ffmpeg_proc = None
    try:
        while True:
            ffmpeg_proc = subprocess.Popen(cmd)
            started_procs.append(ffmpeg_proc)

            if not wait_for_hls():
                log("  [WARN] HLS not generating — check ffmpeg permissions", C_YELLOW)
            else:
                log(f"\n{C_BOLD}  Streaming at:{C_RESET}")
                log(f"  {C_GREEN}http://localhost:{HTTP_PORT}/{C_RESET}")
                if we_started["tunnel"]:
                    log(f"  {C_GREEN}(tunnel active — your domain is live){C_RESET}")
                log(f"\n{C_BOLD}  Ctrl+C to stop.{C_RESET}\n")

            exit_code = ffmpeg_proc.wait()
            log(f"  [WARN] ffmpeg exited (code={exit_code}) — restarting in 2s (Ctrl+C to stop)", C_YELLOW)
            time.sleep(2)

    except KeyboardInterrupt:
        log(f"\n  {C_YELLOW}Stopping...{C_RESET}")
        if ffmpeg_proc and ffmpeg_proc.poll() is None:
            ffmpeg_proc.send_signal(signal.SIGINT)
            try:
                ffmpeg_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ffmpeg_proc.kill()
                ffmpeg_proc.wait()
    finally:
        cleanup_started_procs()
        if we_started["tunnel"]:
            log(f"  {C_GREEN}Stopped: tunnel + ffmpeg.{C_RESET}")
        else:
            log(f"  {C_GREEN}Done. Tunnel was pre-existing, still running.{C_RESET}")
        log(f"  {C_GREEN}Logs at {LOG_FILE}{C_RESET}\n")


if __name__ == "__main__":
    main()
