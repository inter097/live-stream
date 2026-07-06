# live-stream

Stream de pantalla vía WebRTC + HLS. El browser captura screen + audio, MediaMTX convierte a HLS, Cloudflare tunnel lo expone públicamente en `tv.eliuth.dev`.

## Quick start

```bash
python3 stream.py
```

Eso es todo. El script:

1. Arranca publish server (`:8080`)
2. Inicia MediaMTX (`:8888` HLS, `:8889` WebRTC)
3. Conecta tunnel Cloudflare
4. **Abre el browser solo** en `http://localhost:8080/publish.html`

En el browser:

1. Click **"Start Stream"**
2. Selecciona tu pantalla + marca **☑ Share audio**
3. Listo — el stream está en `https://tv.eliuth.dev/`

## Detener

`Ctrl+C` en la terminal donde corre `stream.py`.

## URLs

| Servicio | URL |
|---|---|
| Capture (local) | http://localhost:8080/publish.html |
| Player (local) | http://localhost:8080/ |
| Player (público) | https://tv.eliuth.dev/ |

## Requirements

- macOS
- Python 3
- `mediamtx` (`brew install mediamtx`)
- `cloudflared` con tunnel `live` configurado
- Chrome / Edge / Safari (WebRTC `getDisplayMedia`)

## Por qué WebRTC y no ffmpeg

macOS no expone loopback nativo de audio. WebRTC `getDisplayMedia({audio: true})` usa ScreenCaptureKit + CoreAudio internamente — captura screen + audio simultáneamente, sin driver virtual, sin processing.

## Files

- `stream.py` — **orchestrator todo-en-uno** (HTMLs + YAML embebidos)
- `LICENSE` — MIT

`auto.crt`, `auto.key`, `.mediamtx.yml` se generan en runtime (gitignored).
