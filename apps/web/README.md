# Web client

Serve this directory from an HTTPS origin. The client signs in through `/auth/login`, authenticates the voice socket with its first JSON frame, captures mono PCM16 through an AudioWorklet, reconnects with backoff and displays citations/audio.

Set `globalThis.JARVIS_API_URL` before loading `src/main.js` when the API is not at the current hostname on port 8000. Use HTTPS/WSS outside localhost.
