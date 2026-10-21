# Webpanel 0.1 — contratto

Console italiana, scura, per un processo OBE gestito dal pannello. Python stdlib
e HTML/CSS/JS, font IBM Plex locali. Nessuna dipendenza nuova. Il servizio resta
locale, accessibile da remoto mediante tunnel SSH; nessun intervento sui router.

## API

- `GET /api/state`: stato completo descritto sotto; polling ogni 2 secondi.
- `PUT /api/config`: `{revision, config}`; salva atomicamente il profilo validato
  solo a encoder fermo. Revision discordante: 409. Nessuna passphrase persistita.
- `POST /api/probe`: `{revision, passphrase?: string}`; verifica asincrona del
  profilo salvato usando OBE; torna 202. Non avvia l'encoding.
- `POST /api/start`: stesso corpo; probe e avvio asincrono, 202. Configurazione
  applicata solo al processo figlio posseduto dal pannello.
- `POST /api/stop`: `{}`; arresto del solo processo gestito, idempotente.
- Errori: `{error: {code, message, fields?: object}}`, con 400/403/409/422/500/503.
  Durante lo spegnimento, probe/start già accettati sono rifiutati con 503.
- Le risposte di successo mutative restituiscono lo stesso stato di GET.
- Corpo JSON massimo 16 KiB; Host solo localhost/127.0.0.1/[::1], Origin
  coerente se presente, no richieste cross-site, no CORS. Asset serviti da
  whitelist. Bind loopback di default; nel container bind 0.0.0.0 e pubblicazione
  Docker solo su 127.0.0.1. Non è un servizio pubblico o multiutente.

## Configurazione completa

```json
{
  "name": "Banco 1080i",
  "input_url": "udp://127.0.0.1:12000",
  "output_url": "udp://127.0.0.1:13000",
  "video_bitrate_kbps": 8000,
  "mux_bitrate_kbps": 9500,
  "vbv_buffer_kbits": 8000,
  "preset": "veryfast",
  "gop_frames": 50,
  "bframes": 2,
  "lookahead_frames": 20,
  "threads": 4,
  "audio_bitrate_kbps": 192
}
```

Video AVC; formato, colore e campi ereditati dalla sorgente, senza scaling.
Se rilevato, audio stereo 48 kHz convertito in MP2; altrimenti solo video.
Output MPEG-TS UDP CBR. Video 128–50000 kb/s; mux fino a 100000 kb/s e almeno
video+audio+128 kb/s; VBV 128–100000 kbit; GOP 1–300; B-frame 0–8;
lookahead 0–100; thread 1–32; audio 64/96/128/160/192/224/256/320/384 kb/s.
Preset x264 ultrafast/superfast/veryfast/faster/fast/medium/slow/slower/veryslow.
URL solo protocolli di rete accettati da OBE, output solo UDP. Rifiutati
credenziali, separatori CLI, controlli e query crypto/credenziali. Chiave SRT
facoltativa 10–79 byte, solo per l'azione e solo via ambiente OBE.

## Stato

```json
{
  "phase": "idle",
  "message": "Encoder fermo",
  "revision": 1,
  "config": {},
  "profiles": [{"id": "hd", "label": "HD · 9,5 Mbit/s", "config": {}}],
  "process_alive": false,
  "encoding_started": false,
  "pid": null,
  "signal_state": "unknown",
  "signal": null,
  "encrypted": false,
  "metrics": {"cpu_percent": null, "memory_mb": null, "uptime_seconds": 0},
  "events": [{"id": 1, "time": "2026-10-21T20:00:00Z", "level": "info", "message": "Pannello pronto"}],
  "version": {"panel": "0.1.0", "encoder": null}
}
```

`phase`: idle/probing/starting/running/reconnecting/stopping/error.
`signal_state`: unknown/receiving/reconnecting/stopped.
`signal`, quando noto: `{width,height,scan:"i"|"p",fps:"25/1",audio:string|null}`.
`encoding_started` solo dopo conferma CLI; `running` non certifica una consegna
in onda. `signal_state` usa gli eventi reali del lettore. CPU indica percentuale
di un core del solo encoder (può superare 100); memoria RSS. Nessun bitrate
misurato o grafico di rete inventato. Eventi ordinati, limitati a 200, sanificati.
Le modifiche non salvate restano nel form e non sono sovrascritte dal polling.
Prima di probe/start richiedere salvataggio della bozza; passphrase mai in storage,
URL, log o export e cancellata dal form dopo l'azione. Errori e disconnessione API
devono essere visibili; nessun avvio al caricamento o riavvio automatico del server.
