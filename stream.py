#!/usr/bin/env python3
"""stream.py — All-in-one WebRTC screen streaming (single file).

Serves static HTML, proxies HLS, runs MediaMTX + Cloudflare tunnel.
Everything embedded: no .html or .yml files needed.
"""

import http.server
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

MEDIAMTX_BIN = "/opt/homebrew/bin/mediamtx"
MEDIAMTX_RUNTIME_CONFIG = Path(__file__).parent / ".mediamtx.yml"
MEDIAMTX_LOG = "/tmp/mediamtx.log"
MEDIAMTX_HLS = "http://127.0.0.1:8888"
TUNNEL_CONFIG = os.path.expanduser("~/.cloudflared/config.yml")
TUNNEL_NAME = "live"
LOG_FILE = "/tmp/stream.log"
PUBLISH_PORT = 8080

C_RED = "\033[91m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_BLUE = "\033[94m"
C_BOLD = "\033[1m"
C_RESET = "\033[0m"

PUBLISH_URL = f"http://localhost:{PUBLISH_PORT}/publish.html"

MEDIAMTX_YAML = """\
logLevel: info
api: yes
apiAddress: :9997

webrtcAddress: :8889
webrtcEncryption: no
webrtcAdditionalHosts: [127.0.0.1, localhost, tv.eliuth.dev]

hlsAddress: :8888
hlsEncryption: no
hlsAlwaysRemux: yes
hlsSegmentDuration: 6s
hlsPartDuration: 200ms
hlsMuxerCloseAfter: 60000ms

paths:
  live:
    source: publisher
"""

INDEX_HTML = """\
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>tv.eliuth.dev</title>
  <style>
    body { background: #0f172a; color: #e2e8f0; font-family: system-ui, sans-serif;
           display: flex; flex-direction: column; align-items: center;
           justify-content: center; min-height: 100vh; gap: 1rem; margin: 0; padding: 1rem; }
    h1 { margin: 0; font-size: 1.2rem; font-weight: 600; color: #94a3b8; }
    video { max-width: 95%; max-height: 85vh; border-radius: 8px; background: #000; }
    .offline { color: #64748b; }
  </style>
</head>
<body>
  <h1 id="status">tv.eliuth.dev</h1>
  <video id="player" controls autoplay muted playsinline></video>
  <script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.13"></script>
  <script>
    const v = document.getElementById('player');
    const status = document.getElementById('status');
    const src = '/live/index.m3u8';

    function setup() {
      if (v.canPlayType('application/vnd.apple.mpegurl')) {
        v.src = src;
      } else if (Hls.isSupported()) {
        const h = new Hls();
        h.loadSource(src);
        h.attachMedia(v);
      }
    }

    async function check() {
      try {
        const r = await fetch(src, { method: 'HEAD' });
        if (r.ok) {
          status.textContent = 'tv.eliuth.dev — EN VIVO';
          status.className = '';
          if (!v.src && !v.srcObject) setup();
        } else {
          status.textContent = 'tv.eliuth.dev — OFFLINE (publisher no conectado)';
          status.className = 'offline';
        }
      } catch {
        status.textContent = 'tv.eliuth.dev — OFFLINE';
        status.className = 'offline';
      }
    }

    check();
    setInterval(check, 5000);
    setup();
  </script>
</body>
</html>
"""

PUBLISH_HTML = """\
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Stream Publisher</title>
  <style>
    body { background: #0f172a; color: #e2e8f0; font-family: system-ui, sans-serif;
           display: flex; flex-direction: column; align-items: center;
           justify-content: center; min-height: 100vh; gap: 1rem; margin: 0; padding: 1rem; }
    h1 { margin: 0; }
    button { padding: 1rem 2rem; font-size: 1.2rem; font-weight: 700;
             background: #dc2626; color: white; border: none; border-radius: 8px;
             cursor: pointer; }
    button.stop { background: #475569; }
    button:disabled { background: #334155; cursor: not-allowed; }
    #local { max-width: 80%; max-height: 50vh; border-radius: 8px; background: #000; }
    #status { font-size: 0.9rem; padding: 0.4rem 1rem; border-radius: 6px;
              background: #334155; color: #94a3b8; }
    #status.live { background: #16a34a; color: white; }
    .hint { font-size: 0.8rem; color: #64748b; max-width: 600px; text-align: center; }
  </style>
</head>
<body>
  <h1>🎥 Stream Publisher</h1>
  <div id="status">OFFLINE</div>
  <button id="start">Start Stream</button>
  <video id="local" autoplay muted playsinline></video>
  <p class="hint">En el picker de macOS, selecciona tu pantalla y marca ☑ <b>Share audio</b></p>

  <script>
    const btn = document.getElementById('start');
    const status = document.getElementById('status');
    const local = document.getElementById('local');
    let pc = null;
    let stream = null;

    btn.onclick = async () => {
      if (pc) {
        pc.close();
        pc = null;
        if (stream) stream.getTracks().forEach(t => t.stop());
        stream = null;
        local.srcObject = null;
        status.textContent = 'OFFLINE';
        status.className = '';
        btn.textContent = 'Start Stream';
        btn.className = '';
        return;
      }
      btn.disabled = true;
      try {
        stream = await navigator.mediaDevices.getDisplayMedia({
          video: { frameRate: 24, width: { ideal: 1920 }, height: { ideal: 1080 } },
          audio: true
        });
      } catch (e) {
        alert('Permiso denegado o cancelado');
        btn.disabled = false;
        return;
      }
      local.srcObject = stream;

      stream.getVideoTracks()[0].onended = () => {
        if (pc) { pc.close(); pc = null; }
        status.textContent = 'OFFLINE';
        status.className = '';
        btn.textContent = 'Start Stream';
        btn.className = '';
        btn.disabled = false;
      };

      pc = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
      });
      stream.getTracks().forEach(t => pc.addTrack(t, stream));

      const videoTransceivers = pc.getTransceivers().filter(t =>
        t.sender && t.sender.track && t.sender.track.kind === 'video'
      );
      if (videoTransceivers.length > 0) {
        const caps = RTCRtpSender.getCapabilities('video');
        const h264 = caps.codecs.filter(c =>
          c.mimeType === 'video/H264' &&
          (c.sdpFmtpLine || '').includes('level-asymmetry-allowed=1')
        );
        if (h264.length > 0) {
          videoTransceivers[0].setCodecPreferences(h264);
        }
      }

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      const res = await fetch('http://localhost:8889/live/whip', {
        method: 'POST',
        body: offer.sdp,
        headers: { 'Content-Type': 'application/sdp' }
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => '');
        alert('MediaMTX WHIP failed: ' + res.status + ' - ' + txt);
        btn.disabled = false;
        return;
      }
      const answer = await res.text();
      await pc.setRemoteDescription({ type: 'answer', sdp: answer });

      status.textContent = 'EN VIVO';
      status.className = 'live';
      btn.textContent = 'Stop Stream';
      btn.className = 'stop';
      btn.disabled = false;
    };
  </script>
</body>
</html>
"""

started = []


def log(msg, color=C_RESET):
    line = f"{color}{msg}{C_RESET}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(msg + "\n")
    except OSError:
        pass


class StreamHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle(send_body=True)

    def do_HEAD(self):
        self._handle(send_body=False)

    def _handle(self, send_body):
        if self.path == "/" or self.path == "/index.html":
            self._serve_html(INDEX_HTML, send_body)
        elif self.path == "/publish.html":
            self._serve_html(PUBLISH_HTML, send_body)
        elif self.path.startswith("/live/") or self.path == "/live":
            self._proxy_to_mediamtx(send_body)
        else:
            self.send_error(404)

    def _serve_html(self, body, send_body=True):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        body_bytes = body.encode("utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        if send_body:
            self.wfile.write(body_bytes)

    def _proxy_to_mediamtx(self, send_body=True):
        url = f"{MEDIAMTX_HLS}{self.path}"
        try:
            req = urllib.request.Request(url, method="HEAD" if not send_body else "GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.send_response(resp.status)
                for header, value in resp.headers.items():
                    if header.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(header, value)
                self.end_headers()
                if send_body:
                    self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            if send_body:
                self.wfile.write(e.read())
        except Exception as e:
            self.send_error(502, f"Proxy error: {e}")

    def log_message(self, fmt, *args):
        pass


class ReusableHTTPServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True


def kill_previous_instance():
    """Kill any leftover stream.py / mediamtx / cloudflared from prior runs."""
    for pattern in ("stream.py", "mediamtx", "cloudflared"):
        try:
            subprocess.run(["pkill", "-9", "-f", pattern],
                           capture_output=True, timeout=2)
        except subprocess.TimeoutExpired:
            pass
    for port in (8080, 8888, 8889, 9997):
        try:
            r = subprocess.run(["lsof", f"-ti:{port}"],
                               capture_output=True, text=True, timeout=2)
            for pid in r.stdout.strip().split():
                try:
                    os.kill(int(pid), 9)
                except (ProcessLookupError, PermissionError):
                    pass
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    time.sleep(0.5)


def start_publish_server():
    kill_previous_instance()
    server = ReusableHTTPServer(("127.0.0.1", PUBLISH_PORT), StreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def write_mediamtx_config():
    MEDIAMTX_RUNTIME_CONFIG.write_text(MEDIAMTX_YAML)


def cleanup(*_):
    log("\n  Stopping...", C_YELLOW)
    for p in started:
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            p.kill()
    try:
        MEDIAMTX_RUNTIME_CONFIG.unlink(missing_ok=True)
    except Exception:
        pass
    log(f"  Done. Logs at {LOG_FILE} | {MEDIAMTX_LOG}\n", C_GREEN)
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        with open(LOG_FILE, "w") as f:
            f.write(f"--- stream.py started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    except OSError:
        pass

    log(f"\n{C_BOLD}  stream.py — WebRTC screen streaming{C_RESET}\n")

    if not Path(MEDIAMTX_BIN).exists():
        log(f"  [FAIL] mediamtx not found at {MEDIAMTX_BIN}", C_RED)
        log(f"         Install: brew install mediamtx", C_RED)
        sys.exit(1)

    log(f"  [1/3] Starting publish server on :{PUBLISH_PORT} (with HLS proxy)...", C_BLUE)
    start_publish_server()
    log(f"  [OK] Publish page: {PUBLISH_URL}", C_GREEN)
    log(f"  [OK] HLS proxy:    /live/* → MediaMTX :8888", C_GREEN)

    log("  [2/3] Starting MediaMTX...", C_BLUE)
    write_mediamtx_config()
    mtx_log = open(MEDIAMTX_LOG, "w")
    mtx = subprocess.Popen(
        [MEDIAMTX_BIN, str(MEDIAMTX_RUNTIME_CONFIG)],
        stdout=mtx_log,
        stderr=subprocess.STDOUT,
    )
    started.append(mtx)
    time.sleep(2)
    if mtx.poll() is not None:
        log(f"  [FAIL] MediaMTX failed. Check {MEDIAMTX_LOG}", C_RED)
        sys.exit(1)
    log(f"  [OK] MediaMTX running (HLS :8888, WebRTC :8889, API :9997)", C_GREEN)

    log("  [3/3] Starting Cloudflare tunnel...", C_BLUE)
    if os.path.isfile(TUNNEL_CONFIG):
        t = subprocess.Popen(
            ["cloudflared", "tunnel", "--config", TUNNEL_CONFIG, "run", TUNNEL_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started.append(t)
        time.sleep(3)
        log(f"  [OK] Tunnel started", C_GREEN)
    else:
        log(f"  [WARN] Tunnel config not found, local only", C_YELLOW)

    log("  Opening publish page in browser...", C_BLUE)
    try:
        subprocess.Popen(["open", "http://localhost:8080/publish.html"])
        log(f"  [OK] Browser opened → {PUBLISH_URL}", C_GREEN)
    except Exception as e:
        log(f"  [WARN] Could not open browser: {e}", C_YELLOW)
        log(f"         Open manually: {PUBLISH_URL}", C_YELLOW)

    log("\n  ─────────────────────────────────────────", C_BOLD)
    log(f"  {C_BOLD}1. Open in browser:{C_RESET}  {C_GREEN}{PUBLISH_URL}{C_RESET}")
    log(f"  {C_BOLD}2. Click 'Start Stream' → pick screen + ☑ Share audio{C_RESET}")
    log(f"  {C_BOLD}3. Public stream:{C_RESET}    {C_GREEN}https://tv.eliuth.dev/{C_RESET}")
    log("  ─────────────────────────────────────────\n", C_BOLD)
    log(f"  {C_BOLD}Ctrl+C to stop.{C_RESET}\n")

    try:
        mtx.wait()
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
