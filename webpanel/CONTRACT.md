# Webpanel 0.3 — contratto

Console italiana, scura, per processi OBE gestiti per canale. Python stdlib
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
  "version": {"panel": "0.2.0", "encoder": null}
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

## Telemetria del flusso

`GET /api/state` aggiunge `telemetry`, indipendente dalle metriche CPU/RSS.
È una modifica additiva: configurazione e richieste esistenti restano valide.

- `status`: `idle` senza processo, `waiting` prima dei campioni, `live` con un
  campione recente, `stale` dopo 5 secondi, `unavailable` senza risposta CLI.
- `age_seconds`: età locale dell'ultimo campione valido; `interval_seconds`:
  intervallo monotono del motore usato per calcolare i rate.
- `video_frames`, `video_bytes`: frame interi e byte AVC prodotti da x264.
- `output_bytes`, `output_datagrams`, `output_errors`: payload UDP accettato dal
  socket, datagrammi inviati ed errori locali di invio, cumulativi nel processo.
- `video_queue_frames`: frame ancora nella coda degli encoder AVC.
- `video_fps`, `video_bitrate_kbps`, `output_bitrate_kbps`: differenze dei
  contatori divise per l'intervallo; kb/s decimali. Il primo campione non ha rate.

Le letture assenti, non supportate, scadute o di un processo terminato sono
`null`, mai ricavate dal bitrate configurato. Il contatore frame misura 25 frame/s
su 1080i50, non i 50 semiquadri/s. L'uscita UDP misura payload MPEG-TS nel pannello:
nessuna misura di overhead IP, perdita remota, latenza o conferma di ricezione.

Il backend invia `show telemetry` su stdin ogni secondo, con una sola richiesta
in corso. Dopo 5 secondi senza risposta sospende il campionamento per quel
processo e segnala l'indisponibilità senza arrestare la codifica. Una build
precedente continua quindi a funzionare con la sola telemetria CPU/RSS.
Il core risponde con `OBE_TELEMETRY {JSON}` schema 1 e clock `monotonic_ns`;
i contatori sono atomici e azzerati prima dell'avvio dei thread OBE.
Schema, tipi, clock e monotonia dei contatori sono validati; campioni malformati
non sostituiscono quelli validi. Le righe di misura non riempiono il registro
eventi e non creano file di statistiche. I processi precedenti non possono
aggiornare la telemetria del successivo.

## Canali

Fino a quattro canali con processo, profilo, revisione, eventi e telemetria
indipendenti. `main` conserva stato e rotte esistenti; nessun autoavvio.

- `GET /api/channels`: `{limit:4,channels:[{id,name,phase,process_alive}]}`.
- `POST /api/channels`: `{name}` → 201 `{id,state}`; 409 se il limite è raggiunto.
- `/api/channels/{id}/state|config|probe|start|stop`: stessi metodi e corpi
  delle rotte originali, che restano alias di `main`.
- Identificatori assegnati dal server: `main`, `channel-2`…`channel-4`.
- Profili aggiuntivi in `.state/channels/{id}/config.json`; porte iniziali distinte.
- Ingressi UDP/RTP/SRT listener con porte sovrapposte e destinazioni UDP già usate da un
  canale attivo sono rifiutati prima dell'avvio (409). Il limite di quattro
  canali non certifica la capacità CPU della macchina.
- La selezione UI non arresta la codifica; una bozza va salvata o ripristinata
  prima del cambio. Un collegamento a un canale assente torna a `main`.

## Preview per canale

- `POST /api/channels/{id}/preview`, corpo `{}`: attiva la preview dell'uscita;
  richiede codifica avviata (409), destinazione locale/multicast (422), FFmpeg (503).
- `POST /api/channels/{id}/preview-stop`, `{}`: ferma solo la preview.
- `GET /api/channels/{id}/preview-frame`: JPEG non memorizzabile in cache;
  503 se assente/scaduto o il canale non è in codifica. Nessun URL fornito dal client.
- Stato aggiuntivo `preview: {status, age_seconds, frame_id, pid}`; status
  `idle|waiting|live|stale|error`. Età massima 3 s, JPEG massimo 1 MiB.
- Stessi endpoint senza `/channels/{id}` come alias di `main`.
- Avvio esplicito, nessun autoavvio e nessuna modifica alle uscite OBE.

Il controllo delle porte considera `localport`, `localrtpport` e
`localrtcpport` per i protocolli che li supportano; le query con chiavi duplicate
sono rifiutate. Per l'uscita gli alias loopback localhost/127.0.0.1/::1 sono
considerati la stessa destinazione. Gli alias DNS personalizzati non vengono
risolti dal controllo: assegnare destinazioni esplicitamente distinte.
