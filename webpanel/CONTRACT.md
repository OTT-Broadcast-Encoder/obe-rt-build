# Webpanel 0.3 — contratto

Console italiana, scura, per un processo OBE gestito dal pannello. Python stdlib
e HTML/CSS/JS, font IBM Plex locali. Nessuna dipendenza nuova. Il servizio resta
locale, accessibile da remoto mediante tunnel SSH; nessun intervento sui router.

## API

- `GET /api/state`: stato completo descritto sotto; polling ogni 2 secondi.
- `PUT /api/config`: `{revision, config}`; salva atomicamente il profilo validato
  solo a encoder fermo. Revision discordante: 409. Nessuna passphrase persistita.
- `POST /api/probe`: `{revision, passphrase?: string}`; verifica asincrona del
  profilo salvato usando OBE; torna 202. Non avvia l'encoding.
- `POST /api/start`: `{revision, passphrase?: string, auto_restart?: boolean}`;
  probe e avvio asincrono, 202. Recupero disabilitato se il campo è omesso.
  Configurazione applicata solo al processo figlio posseduto dal pannello.
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
facoltativa 10–79 byte, conservata in memoria durante la sessione e passata
ai processi OBE soltanto via ambiente, compresi i riavvii della stessa sessione.

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
  "version": {"panel": "0.3.0", "encoder": null}
}
```

`phase`: idle/probing/starting/running/reconnecting/restart_wait/stopping/error.
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

## Recupero dopo crash e diagnostica

Il recupero è una scelta per il singolo avvio, senza modifiche al profilo salvato.
Si arma dopo la prima conferma `Encoding started`. Un'uscita imprevista (anche
con codice 0) consente al massimo tre riavvii, con attese di **2, 4 e 8 secondi**.
Il budget non si azzera quando un retry riesce: serve un nuovo avvio manuale.
Ogni retry esegue nuovamente probe e configurazione, usando lo stesso profilo e
la stessa chiave SRT in memoria. I contatori di telemetria ripartono da zero.

`restart_wait` indica un riavvio in attesa senza processo vivo. Start, probe e
salvataggio sono bloccati con 409 anche in questa fase. Stop cancella sessione e
timer, compreso un retry concorrente alla creazione del processo. Spegnere o
riavviare il pannello lascia l'encoder fermo; non c'è recupero delle sessioni da disco.

Probe, errori iniziali, mancata conferma entro 20 secondi, ingresso non supportato
e cambio formato non attivano il recupero. Dopo il primo avvio riuscito, un crash
durante il probe di un retry consuma il budget restante; un timeout esplicito
del probe o dell'avvio è terminale. Una sorgente temporaneamente assente usa la
riconnessione interna di OBE, senza cambiare PID né consumare tentativi.
Stop, errore terminale ed esaurimento budget rimuovono la chiave dalla sessione.

`GET /api/state` aggiunge `recovery`:

```json
{
  "enabled": true, "active": true,
  "attempts": 1, "max_attempts": 3,
  "next_retry_seconds": 3.2, "exhausted": false,
  "last_exit": {
    "time": "2026-10-21T20:00:00Z", "pid": 123, "action": "start",
    "returncode": -9, "signal": "SIGKILL", "expected": false,
    "reason": "process_signal", "uptime_seconds": 12.5, "restart_attempt": 1
  },
  "history": [{
    "time": "2026-10-21T20:00:00Z", "pid": 123, "action": "start",
    "returncode": -9, "signal": "SIGKILL", "expected": false,
    "reason": "process_signal", "uptime_seconds": 12.5, "restart_attempt": 1
  }]
}
```

`active` indica una sessione di encoding attiva, anche con recupero disabilitato;
`enabled` descrive la policy dell'ultima sessione. `attempts` conta i retry già
iniziati; `next_retry_seconds` è `null` quando non c'è un timer. `exhausted`
resta vero dopo l'esaurimento, fino alla prossima azione probe/start.

`history` contiene le ultime **10 uscite**, dalla più vecchia alla più recente;
`last_exit` è l'ultima o `null`. Il tentativo 0 è l'avvio manuale. `expected`
indica un arresto richiesto dal pannello, anche per un timeout. `returncode` è
negativo per un segnale; `signal` è il nome POSIX o `null`. Per un avvio fallito
senza processo, PID e codice sono `null` e la durata è zero.
Cause: `process_signal`, `process_exit`, `operator_stop`, `panel_shutdown`,
`probe_complete`, `probe_timeout`, `start_timeout`, `unsupported_input`,
`operation_failed`, `spawn_failed`, `encoder_unavailable`.
Lo storico è solo in RAM, senza URL, ambiente o passphrase, e si svuota al riavvio
del servizio. Non sostituisce un crash dump o un registro persistente.

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
