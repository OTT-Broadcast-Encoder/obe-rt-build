# Webpanel 0.4 — contratto

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
  "version": {"panel": "0.4.0", "encoder": null}
}
```

`phase`: idle/probing/starting/running/reconnecting/stopping/error.
`signal_state`: unknown/receiving/reconnecting/stopped.
`signal`, quando noto: `{width,height,scan:"i"|"p",fps:"25/1",audio:string|null}`.
`encoding_started` solo dopo conferma CLI; `running` non certifica una consegna
in onda. `signal_state` usa gli eventi reali del lettore. CPU indica percentuale
di un core del solo encoder (può superare 100); memoria RSS. I bitrate misurati
provengono dai contatori del motore. Eventi ordinati, limitati a 200, sanificati.
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

## Statistiche SRT e monitoraggio (0.4)

`GET /api/state` aggiunge `srt` e `monitoring`. Campi esistenti e configurazione
rimangono compatibili. `srt.status`: `not_applicable` per ingressi diversi da SRT,
`idle` senza ricevitore avviato, `waiting` nei primi 5 s, `unavailable` senza
campioni, `live` con dati validi, `stale` dopo 5 s. Tutte le letture sono `null`
fuori dallo stato `live`, salvo `age_seconds` quando è noto l'ultimo campione.

`srt` contiene `connection` (crescente nel processo), `sample_us` (clock del
ricevitore), `age_seconds`, `rtt_us`/`rtt_ms`, `received_packets`, `received_bytes`,
`lost_packets`, `retransmitted_packets`, `dropped_packets`, `buffer_bytes`,
`buffer_ms`, `buffer_available_bytes`, `latency_ms`, `receive_bitrate_kbps` e
`interval_seconds`. I rate richiedono due campioni della stessa connessione.
I campioni duplicati non rinnovano la freshness; contatori regressivi o schema
non valido vengono ignorati. Le righe `OBE_SRT` schema 1 non entrano nei log.
`lost_packets` include perdite poi recuperate; `dropped_packets` conta gli scarti
tardivi. Non vengono derivati jitter, perdita residua o latenza end-to-end.

`monitoring`: `{status: "live"|"unavailable", age_seconds, sample_interval_seconds: 1,
sample_limit: 1800, history_available, samples, active_alarms, rules}`.
`status` riguarda il campionatore; `history_available` riguarda separatamente il
disco. Gli allarmi hanno `id`, `time` UTC, `code`, `state`, `reason`, `run_id`,
`session_id`, `label`, `level`, `guide`, `trigger_seconds`, `clear_seconds`.
`state`: `active` o `resolved`; `reason`: `threshold`, `recovered`,
`encoder_stopped`, `process_changed`, `monitor_restarted`. La chiusura per fine
osservazione non prova il recupero del segnale. CPU/RSS, storico e allarmi sono
campionati dal servizio anche senza client HTTP; i GET non consumano i contatori.

### Storico ed export

- `GET /api/history?limit=120`: JSON `{schema:1, generated_at, sample_limit,
  alarm_limit:200, total_samples, samples:[], alarms:[]}`, in ordine cronologico.
- `limit`: intero 1–1800, default 120; applicato ai campioni. `alarms` contiene
  sempre fino a 200 transizioni, anche di sessioni precedenti.
- `format=json` aggiunge un download `obe-history.json`; `format=csv` restituisce
  le misure in `text/csv`, BOM UTF-8, con nome `obe-history.csv`.
- Parametri sconosciuti, duplicati o fuori range: 422 `invalid_history_query`.
  Archivio non disponibile: 503 `history_unavailable`; nessun effetto sul processo.
- Campione: `{time, run_id, session_id, revision, phase, process_alive,
  encoding_started, signal_state, source_fps, context, version, metrics,
  telemetry, srt, active_alarms:[code...]}`. `context` include solo nome, protocollo
  di ingresso e parametri video/audio. `version` conserva le versioni esposte dal
  pannello e dal binario. Nessun URL, log grezzo, PID riutilizzabile o credenziale.
- Archivio SQLite locale limitato a 1800 campioni e 200 transizioni; i periodi
  senza processo non consumano campioni salvo cambi di stato/profilo. Le pagine
  vengono riutilizzate e il database principale ha un limite di 16 MiB.
- Risposte `no-store`, stessi controlli Host/Origin delle altre API; testi CSV
  potenzialmente interpretabili come formula preceduti da un apostrofo.

Soglie e runbook: [guida del pannello](../docs/webpanel.md#statistiche-srt-storico-e-allarmi).
