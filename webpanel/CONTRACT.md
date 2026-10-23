# Webpanel 0.9.1 — contratto

Console italiana, scura, per processi OBE gestiti per canale. Python stdlib
e HTML/CSS/JS, font IBM Plex locali. Nessuna dipendenza nuova. Il servizio resta
locale, accessibile mediante tunnel SSH o LAN fidata esplicitamente abilitata;
nessun intervento sui router.

## API

- `GET /api/delivery`: configurazione pubblica e stato del monitoraggio della
  consegna; la stessa struttura è inclusa in `GET /api/state` sotto `delivery`.
- `PUT /api/delivery`: `{instance_id,revision,config:{enabled,source_url,receiver_url},tokens?:{source?,receiver?}}`.
  Salvataggio separato dal profilo encoder, con revisione propria, persistente
  e senza stop/start. I token omessi sono conservati soltanto per lo stesso
  endpoint. Endpoint HTTP su `127.0.0.1` tramite inoltri SSH; token mai restituiti.
- `POST /api/delivery-control`: `{instance_id,revision,mode:"auto"|"main"|"backup"}`.
  Richiede un selettore remoto verificato e fresco. Un osservatore in sola
  lettura non accetta commutazioni. Istanza/revisione del ricevitore vengono
  verificate anche dal componente remoto; i timeout non vengono ritentati
  automaticamente. Stessi controlli di sessione e Host/Origin delle altre API.
  Le rotte sono disponibili anche sotto `/api/channels/:id/`.
  Confini di misura, configurazione e limiti: [consegna e failover](../docs/delivery-monitoring.md).

- `GET /api/state`: stato completo descritto sotto; polling ogni 2 secondi.
- `POST /api/reveal-passphrase`: `{revision, instance_id}`; con login attivo e
  sessione autenticata restituisce la passphrase SRT salvata per il canale
  selezionato. Richiede revisione e istanza correnti; risposta senza cache.
  Lo stato espone `capabilities.passphrase_reveal`: il pannello usa l'endpoint
  per compilare il campo SRT dopo il login, senza includere il segreto nello stato.
- `PUT /api/config`: `{revision, config, credentials?: object}`; salva atomicamente
  profilo e credenziali validati, solo a encoder fermo. Revision discordante: 409.
- `POST /api/apply`: `{revision, instance_id, config, credentials?: object}`;
  salva e riavvia il canale con una sessione di encoding attiva (anche in attesa
  di recupero). Risponde 202 dopo il salvataggio, prima del completamento del
  riavvio; seguire `GET /api/state`. Da fermo salva soltanto e risponde 200.
  Mantiene la scelta del recupero automatico, con un nuovo budget di tentativi,
  e le credenziali omesse secondo le stesse regole di `PUT /api/config`.
  Un override SRT della sessione resta valido se l'origine non cambia e
  `credentials.passphrase` non viene aggiornato o rimosso.
  Validazione, revisione/istanza, conflitti fra canali e scrittura precedono
  l'arresto: un rifiuto lascia il processo corrente invariato. Durante apply,
  probe/start/save/delete e un altro apply sono rifiutati con 409; Stop annulla
  il riavvio conservando il nuovo profilo. Un probe senza encoding va completato
  o arrestato prima di applicare. Le vecchie e nuove porte restano riservate
  fino all'arresto del processo precedente. La preview viene fermata.
  Se il riavvio fallisce, il profilo resta salvato e stato/eventi segnalano
  l'errore; nessun ripristino o avvio aggiuntivo implicito. Una risposta persa
  va risolta rileggendo lo stato: non ripetere automaticamente la richiesta
  con una nuova revisione. Ripetere la vecchia revisione restituisce 409.
- `POST /api/probe`: `{revision, passphrase?: string}`; verifica asincrona del
  profilo salvato usando OBE; torna 202. Non avvia l'encoding.
- `POST /api/start`: `{revision, passphrase?: string, auto_restart?: boolean}`;
  probe e avvio asincrono, 202. Recupero disabilitato se il campo è omesso.
  Configurazione applicata solo al processo figlio posseduto dal pannello.
  Probe/start riusano le credenziali salvate. Il vecchio campo `passphrase`, se
  presente, resta un override per la singola azione e non modifica il salvataggio.
- `POST /api/stop`: `{}`; arresto del solo processo gestito, idempotente.
- `POST /api/vapoursynth-validate`: `{script: string}`; controllo sintattico Python
  senza esecuzione, salvataggio o riavvio. Risponde `{valid: true}` oppure 422 con
  `fields.vapoursynth_script`, riga e colonna. Stessi controlli di autenticazione
  e origine delle altre API, anche su `/api/channels/:id/vapoursynth-validate`.
- `POST /api/vapoursynth-test`: `{script: string}`; esegue la bozza con VapourSynth
  e renderizza cinque frame sintetici in un processo separato, senza salvataggio
  o riavvio. Risponde `{valid, message, log, duration_ms}`; gli errori di runtime
  tornano con `valid: false` e traceback nel log. Tipo e lunghezza non validi
  tornano con 422; un test già in corso con 409 (`test_busy`). Usa risoluzione e
  fps rilevati, altrimenti il formato DeckLink o 1080i50; formato pixel YUV420P8
  e durata virtuale del bridge OBE. Verifica output 0, formato, dimensioni, fps,
  durata e rendering dei frame 0, 1, 2, 12 e 24. Limiti: 60 secondi complessivi
  (inizializzazione, render e chiusura), due CPU, 120 secondi CPU cumulativi,
  3 GiB di spazio di indirizzamento e log restituito di circa 16 KiB, con le
  redazioni dei segreti del pannello. Non è una sandbox né una prova del flusso
  live. Il browser attende fino a 75 secondi per ricevere anche l'esito di timeout.
  Stessi controlli di autenticazione e origine delle altre API, anche su
  `/api/channels/:id/vapoursynth-test`.
- Errori: `{error: {code, message, fields?: object}}`, con 400/403/409/422/500/503.
  Durante lo spegnimento, probe/start già accettati sono rifiutati con 503.
- Le risposte di successo mutative restituiscono lo stesso stato di GET.
- Corpo JSON massimo 64 KiB; Host localhost/127.0.0.1/[::1] e IP aggiunti con
  `--allow-host`, Origin
  coerente se presente, no richieste cross-site, no CORS. Asset serviti da
  whitelist. Bind loopback di default; nel container bind 0.0.0.0 e pubblicazione
  Docker su 127.0.0.1 e, facoltativamente, `OBE_PANEL_LAN_IP`.
  `OBE_PANEL_NETWORK_MODE=host` abilita multicast sulla LAN condividendo la rete
  dell'host: porta `OBE_PANEL_PORT`, bind loopback oppure 0.0.0.0 se LAN abilitata.
  I nuovi canali usano `239.255.0.1` con TTL 1; i profili salvati non cambiano.
  Non è un servizio pubblico o multiutente.

## Configurazione minima compatibile

Le opzioni x264 aggiuntive hanno default in `configuration.X264_DEFAULTS` e non
emettono override CLI quando restano automatiche o vuote. La modalità
`x264_speedcontrol="fixed"` disattiva gli adattamenti per frame del fork;
`motion_estimation`, `motion_range`, `subme`, `trellis`, `mixed_refs`,
`partitions`, `psy_rd` e `psy_trellis` richiedono questa modalità.
Gli offset deblocking e le due intensità psy si impostano a coppie.
`weighted_pred` attivo richiede video progressivo. I profili precedenti
mantengono la modalità adattiva.

Campi additivi VapourSynth: `vapoursynth_enabled` (booleano, default `false`) e
`vapoursynth_script` (stringa UTF-8, massimo 8192 byte). Il default è un passthrough
`import vapoursynth as vs\n\nclip.set_output()\n`. Spazi e righe sono conservati.
I profili precedenti restano validi. Quando il filtro è abilitato, la validazione
sintattica precede salvataggio e arresto; da disabilitato sono ammesse bozze
incomplete. Script e flag partecipano a copia, import/export e ripristino.

All'avvio il backend crea un file privato (0600, directory 0700) per il singolo
processo e passa solo il percorso controllato in `vs-script`. Il file resta
immutato fino all'arresto ed è eliminato all'uscita; il probe non lo crea.
`capabilities.vapoursynth_editor` indica il supporto di queste API;
`capabilities.vapoursynth_test` abilita il test di esecuzione della bozza;
`vapoursynth.status` è `stopped`, `disabled`, `waiting`, `active` o `error`,
ricavato dal processo e dai messaggi del filtro, non dal solo flag salvato.

OBE fornisce `clip`; lo script deve registrare l'output video 0 mantenendo
formato pixel, risoluzione, fps e durata. Sono supportati YUV420P8, YUV420P10
e YUV422P10; il filtro introduce circa un secondo di buffer a 25 fps.
Plugin e output sono verificati dal motore all'avvio. Se la catena non si
inizializza, lo stato segnala errore e il motore continua senza filtro.
La verifica sintattica non certifica prestazioni o correttezza del video.
Python gira con i permessi dell'encoder, senza sandbox: gli script importati
devono essere fidati. L'export comprende il testo integrale dello script,
inclusi eventuali valori letterali; le credenziali di connessione rimangono separate.

```json
{
  "name": "Banco 1080i",
  "input_url": "udp://127.0.0.1:12000",
  "output_url": "udp://239.255.0.1:13000",
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
Se rilevato, audio stereo 48 kHz: MP2 per default, AAC/AC3/EAC3 configurabili.
Output MPEG-TS CBR, UDP per default. Video 128–50000 kb/s; mux fino a 100000 kb/s e almeno
video+audio+128 kb/s; VBV 128–100000 kbit; GOP 1–300; B-frame 0–8;
lookahead 0–100; thread 1–32; audio 64/96/128/160/192/224/256/320/384 kb/s.
Preset x264 ultrafast/superfast/veryfast/faster/fast/medium/slow/slower/veryslow.
URL solo protocolli di rete accettati da OBE, output solo UDP. Rifiutati
credenziali incorporate, separatori CLI, controlli e query crypto/credenziali:
per autenticarsi usare i campi dedicati descritti sotto.

## Credenziali persistenti per canale

`credentials` accetta aggiornamenti parziali di stringhe: `passphrase` (SRT,
10–79 byte), `username` (massimo 256 byte), `password` (1024 byte) e `query`
(2048 byte, formato `token=valore&chiave=valore`). Gli ultimi tre campi sono
per HTTP/HTTPS/RTSP. Nessun carattere di controllo; password richiede username,
senza `:`; la query non può duplicare parametri tra loro o dell'URL pubblico.
Una chiave omessa conserva il valore; `""` lo cancella, `null` non è accettato.
Cambiare protocollo, host o porta della sorgente elimina i valori precedenti,
prima di applicare eventuali credenziali fornite nello stesso salvataggio.

Il file locale `config.json` contiene `{revision, config, credentials}`, scritto
atomicamente con permessi 0600 in una directory 0700 esclusa da Git. I vecchi
profili senza `credentials` sono caricati senza modifiche. Lo storage locale
non è cifrato; deve essere protetto insieme ai backup. Stato/API restituiscono
solo quattro booleani di presenza, mai i valori. Stop, crash e riavvio del
pannello non cancellano il salvataggio e non avviano automaticamente l'encoder.

SRT passa tramite `OBE_SRT_PASSPHRASE`. L'URL HTTP/RTSP autenticato è costruito
solo al lancio e passato tramite `OBE_INPUT_URL`, supportato dal core aggiornato.
La CLI riceve l'URL pubblico, evitando credenziali nella history o negli argomenti.
Eventi ed export oscurano i segreti; il browser non usa localStorage/sessionStorage
per conservarli e svuota i campi dopo un salvataggio riuscito.

## Stato

```json
{
  "phase": "idle",
  "message": "Encoder fermo",
  "revision": 1,
  "instance_id": "2f38c627c4184275a53126f46b843740",
  "applying": false,
  "config": {},
  "credentials": {"passphrase": false, "username": false, "password": false, "query": false},
  "profiles": [{"id": "hd", "label": "HD · 9,5 Mbit/s", "config": {}}],
  "process_alive": false,
  "encoding_started": false,
  "pid": null,
  "signal_state": "unknown",
  "signal": null,
  "encrypted": false,
  "metrics": {"cpu_percent": null, "memory_mb": null, "uptime_seconds": 0},
  "events": [{"id": 1, "time": "2026-10-21T20:00:00Z", "level": "info", "message": "Pannello pronto"}],
  "version": {"panel": "0.9.1", "encoder": null}
}
```

`phase`: idle/probing/starting/running/reconnecting/restart_wait/stopping/error.
`applying`: vero durante la sostituzione del processo per una configurazione
salvata; torna falso quando comincia il probe della nuova sessione oppure
quando l'operazione termina per errore o annullamento. Il solo 202 non prova
che l'encoding sia ripartito: attendere `running` e `encoding_started`.
`signal_state`: unknown/receiving/reconnecting/stopped.
`signal`, quando noto: `{width,height,scan:"i"|"p",fps:"25/1",audio:string|null}`.
`encoding_started` solo dopo conferma CLI; `running` non certifica una consegna
in onda. `signal_state` usa gli eventi reali del lettore. CPU indica percentuale
di un core del solo encoder (può superare 100); memoria RSS. I bitrate misurati
provengono dai contatori del motore. Eventi ordinati, limitati a 200, sanificati.
Le modifiche non salvate restano nel form e non sono sovrascritte dal polling.
Prima di probe/start richiedere salvataggio della bozza, incluse le credenziali.
Nessuna credenziale in stato, log o export. Errori e disconnessione API
devono essere visibili; nessun avvio al caricamento o riavvio automatico del server.

## Recupero dopo crash e diagnostica

Il recupero è salvato nel profilo come `auto_restart`, disabilitato per default.
`POST /api/start` usa il valore salvato; il parametro opzionale `auto_restart`
lo sovrascrive solo per quella sessione. Apply adotta la nuova modalità se il
valore salvato cambia, altrimenti conserva l'eventuale override della sessione.
Si arma dopo la prima conferma `Encoding started`. Un'uscita imprevista (anche
con codice 0) consente al massimo tre riavvii, con attese di **2, 4 e 8 secondi**.
Il budget non si azzera quando un retry riesce: serve un nuovo avvio manuale.
Ogni retry esegue nuovamente probe e configurazione, usando lo stesso profilo e
la stessa chiave SRT in memoria. I contatori di telemetria ripartono da zero.

`restart_wait` indica un riavvio in attesa senza processo vivo. Start, probe e
`PUT /api/config` sono bloccati con 409 anche in questa fase; Apply salva e
riavvia con la nuova configurazione. Stop cancella sessione e timer, compreso
un retry concorrente alla creazione del processo. Il riavvio del pannello
riprende il canale solo se `resume_on_restart` è abilitato e lo stato desiderato
registrava una codifica attiva.

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
Cause: `process_signal`, `process_exit`, `operator_stop`, `panel_shutdown`, `config_apply`, `apply_stop_failed`,
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
Il core risponde con `OBE_TELEMETRY {JSON}` schema 2 e clock `monotonic_ns`;
lo schema 1 resta accettato. Lo schema 2 aggiunge `video_qp_aq`, QP medio
dopo AQ dell’ultimo frame AVC emesso (0–51, `null` se non disponibile).
È una misura della quantizzazione, non un voto percettivo. La lettura scade
insieme agli altri valori e non viene calcolata dai contatori di bitrate.
`video_pvmaf` è la stima sperimentale 0–100, `null` quando spenta o non
disponibile; richiede build esplicita e input 1080p 8 bit 4:2:0. Non è
validata per interlacciato/scaling né una certificazione di qualità TV;
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
  telemetry, srt, system?, active_alarms:[code...]}`. `context` include solo nome, protocollo
  di ingresso e parametri video/audio. `version` conserva le versioni esposte dal
  pannello e dal binario. Nessun URL, log grezzo, PID riutilizzabile o credenziale.
- `system` è additivo: stato del campionatore, `instance_id` del pannello,
  CPU (utilizzo e load 1/5/15 min), memoria (byte usati/totali e percentuale),
  rete (nome interfaccia e bitrate RX/TX). Include solo misure e nomi delle
  interfacce; fuori dallo stato `live` non conserva valori delle risorse.
  I vecchi campioni senza `system` restano validi. L'export CSV aggiunge CPU,
  load e memoria; le serie per interfaccia sono disponibili nell'export JSON.
- Archivio SQLite locale limitato a 1800 campioni e 200 transizioni; il
  campionamento continua anche senza processo encoder. Le pagine
  vengono riutilizzate e il database principale ha un limite di 16 MiB.
- Risposte `no-store`, stessi controlli Host/Origin delle altre API; testi CSV
  potenzialmente interpretabili come formula preceduti da un apostrofo.

Soglie e runbook: [guida del pannello](../docs/webpanel.md#statistiche-srt-storico-e-allarmi).

## Canali

Fino a quattro canali con processo, profilo, revisione, eventi e telemetria
indipendenti. `main` conserva stato e rotte esistenti; nessun autoavvio.

- `GET /api/channels`: `{limit:4,channels:[{id,name,phase,process_alive}]}`.
- `POST /api/channels`: `{name}` → 201 `{id,state}`; 409 se il limite è raggiunto.
- `PATCH /api/channels/{id}`: `{name,revision,instance_id}` → 200 stato del
  canale. Rinomina anche `main` senza fermare la codifica; modifica solo il nome
  e la revisione, preservando credenziali e backup precedente. Un nome non valido
  restituisce 422; revisione o istanza obsolete restituiscono 409.
- `DELETE /api/channels/{id}`: `{revision,instance_id}` → 200
  `{deleted,limit,channels}`. Elimina profilo, credenziali e storico del solo
  canale secondario fermo. `main`, encoder occupato o recupero attivo: 409;
  canale assente: 404. Revisione o istanza non corrispondenti: 409.
  `instance_id` è un token opaco dello stato, diverso dopo riavvio del servizio
  o ricreazione di uno slot; la UI conserva quello mostrato alla conferma.
  Gli slot eliminati sono riutilizzabili con un profilo nuovo.
  Ogni nuovo canale parte da una revisione intera casuale (entro 52 bit), poi
  incrementata a ogni salvataggio, per rifiutare anche salvataggi/avvii obsoleti
  dei client che inviano solo `revision`.
  Un errore di rimozione restituisce 500; `channel_cleanup_failed` indica che
  il canale è stato rimosso dalla lista ma restano dati sul disco in una directory
  `.deleted-*`, che non viene caricata al riavvio.
- `/api/channels/{id}/state|config|apply|probe|start|stop|history`: stessi metodi e corpi
  delle rotte originali, che restano alias di `main`.
- Identificatori assegnati dal server: `main`, `channel-2`…`channel-4`.
- Profili aggiuntivi in `.state/channels/{id}/config.json`; porte iniziali distinte.
- Ogni canale avvia il proprio campionatore e conserva lo storico in
  `.state/channels/{id}/monitoring.sqlite3` (`main` usa `.state/monitoring.sqlite3`).
  Il cambio di canale svuota i dati visualizzati e ignora le risposte storico
  del canale precedente. JSON e CSV esportano il solo canale selezionato.
- Ingressi UDP/RTP/SRT listener con porte sovrapposte e destinazioni UDP già usate da un
  canale attivo, anche in attesa di riavvio, sono rifiutati prima dell'avvio (409). Il limite di quattro
  canali non certifica la capacità CPU della macchina.
- La selezione UI non arresta la codifica; una bozza va salvata o ripristinata
  prima del cambio o dell'eliminazione. La UI richiede conferma e torna a `main`
  dopo l'eliminazione. Un collegamento a un canale assente torna a `main`, salvo
  una bozza aperta da conservare: in quel caso viene segnalato l'errore.

## Preview per canale

- `POST /api/channels/{id}/preview`, corpo `{}`: attiva la preview JPEG dell'uscita;
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
sono rifiutate. La porta locale `0` sceglie una porta disponibile e non viene
riservata tra i canali. Gli schemi URL vengono salvati in minuscolo. Per l'uscita gli alias loopback localhost/127.0.0.1/::1 sono
considerati la stessa destinazione. Gli alias DNS personalizzati non vengono
risolti dal controllo: assegnare destinazioni esplicitamente distinte.

## Estensioni 0.9

Campi aggiuntivi facoltativi, con default in `configuration.EXTENDED_DEFAULTS`,
`inputs.INPUT_DEFAULTS` e `recovery.DEFAULTS`; `outputs` ha default `[]`.
Il server restituisce la configurazione normalizzata completa. Le vecchie
richieste con i soli campi base restano accettate. MP2 conserva i bitrate
precedenti; AAC/AC3/EAC3 hanno limiti specifici validati dal modello.

`PUT config` e `POST apply` accettano anche `output_credentials`, mappa
id uscita → passphrase (stringa vuota per rimuovere). Chiavi e credenziali
sono persistite nello stesso file atomico della configurazione; lo stato
pubblico espone solo booleani. `outputs` contiene fino a otto oggetti:
`{id,name,enabled,protocol,url,srt_mode,latency_ms,srt_key_length,
srt_max_bandwidth_kbps,srt_connect_timeout_ms,srt_send_timeout_ms,
srt_drop_late_packets}`. Protocollo UDP/RTP/SRT; modo SRT caller/listener;
latenza 20–8000 ms. I nuovi campi sono facoltativi nei profili esistenti:
default rispettivi 32 byte AES, 0 kb/s (banda automatica), 1000 ms,
1000 ms e true. Chiave AES: 16/24/32 byte; banda: 0 oppure
320–1000000 kb/s; timeout: 100–60000 ms. Default enabled=true,
srt_mode=caller, latency_ms=200. URL protocollo://host:porta senza query.
`capabilities.outputs.srt_parameters` descrive tipo, default, scelte o limiti
dei campi SRT; `allow_zero` indica il valore automatico oltre all'intervallo
ammesso. La definizione comune è `srt_options.py`, usata anche dalla build C.
Il frontend usa questi metadati e mantiene un fallback per i backend che
espongono soltanto `srt_options`. I campi SRT nascosti sono disabilitati nella
validazione HTML. Per UDP/RTP il payload conserva i parametri SRT salvati o
i default; non include valori SRT non validi rimasti nella bozza. L'API
continua a validare tutti i campi pubblici, anche quando il protocollo non è SRT.
La banda massima è un limite SRT che include le ritrasmissioni; il TS
proviene direttamente dal canale e usa blocchi fissi di 1316 byte.
`POST reveal-output-passphrase` accetta `{revision,instance_id,output_id}` e
restituisce la passphrase soltanto a una sessione autenticata del canale
corrente; la risposta usa `Cache-Control: no-store`.
Le destinazioni disabilitate conservano il profilo ma non trasmettono.

Lo stato include `capabilities.inputs/outputs`, `transport.outputs`,
`system` (cache diagnostica con status e age_seconds), `signal_details`
(misure note/null, verifiche false quando non effettuate) e `profile_backup`.
`transport` misura invii e recupero di ogni destinazione; UDP non garantisce
ricezione remota. Un arresto del gateway incompleto impedisce un nuovo avvio.

`capabilities.outputs.live_update=true` indica la gestione delle destinazioni
indipendente dalla codifica. Il TS passa sempre dal distributore locale, anche
con `outputs=[]`; l'UDP primario è una destinazione del distributore e ammette
il solo parametro URL `ttl` (0–255, predefinito multicast 1).
`POST apply` che cambia soltanto `outputs`, `output_url` o `output_credentials`
restituisce 200 con `apply_mode:"outputs"` e `applying:false`: PID encoder,
sessione di recupero, ingresso locale e worker delle destinazioni invariate
sono conservati. Una rinomina conserva anche il worker della destinazione.
Un cambio di endpoint, parametri o chiave sostituisce solo quel worker.
Validazione, verifica dei conflitti e preparazione dei worker precedono il
salvataggio atomico; un errore lascia attive le uscite precedenti.
Le modifiche a ingresso/codifica/mux seguono lo stop/start esistente (202).
Durante l'attesa di recupero, un aggiornamento delle sole uscite aggiorna il
profilo della sessione senza annullarne il timer.
Con `capabilities.outputs.independent_process=true`, Stop arresta solo l'encoder:
il servizio delle uscite conserva ingresso, connessioni e contatori, anche durante
recupero, Apply e riavvio del pannello. `transport.service_available`,
`service_pid`, `service_instance`, `input_status` (`waiting/live/stale/unknown`)
e `input_age_seconds` distinguono servizio e disponibilità del media.
Le modifiche alle uscite sono applicabili anche da fermo; eliminare un canale
rilascia le sue uscite prima di cancellarne i dati. Le porte SRT listener restano
riservate quando l'encoder è fermo. La modalità incorporata, usata dai vecchi
avvii diretti senza `--transport-socket`, mantiene il lifecycle precedente.
I contatori OBE `output_*` misurano il TS consegnato al loopback; i contatori
di `transport.outputs` misurano gli invii delle singole destinazioni.

Il servizio `transport_service.py` espone solo un socket Unix privato (0600),
con protocollo JSON versione 1 e operazioni `state/configure/remove` per i quattro
ID canale ammessi. La riconfigurazione prepara i worker e il file temporaneo,
risponde `ready`, attende il salvataggio del profilo e `commit`, quindi pubblica
il nuovo stato. Disconnessione/errore prima del commit annullano la preparazione.
Se manca la conferma dopo il salvataggio del profilo, l'API segnala esplicitamente
la necessità di verificare e riapplicare: non garantisce rollback di due processi.
`outputs.json` (0600) conserva configurazioni applicate, chiavi e porte loopback;
il servizio le ripristina al proprio riavvio senza richiedere un riavvio di OBE
quando condividono la rete host. Un secondo servizio sulla stessa directory è
rifiutato tramite lock esclusivo. Il flusso TS non attraversa il socket di controllo.

API profili, anche in `/api/channels/{id}/...`:
- `GET profile`: attachment `{format:"obe-webpanel-profile",version:1,config}`.
- `POST profile-validate`: documento export, restituisce `{config}` normalizzata;
  non scrive e non arresta il processo.
- `POST restore`: `{revision,instance_id}`, recupera lo snapshot privato precedente
  e passa attraverso Apply, risposta 200 da fermo / 202 se richiede riavvio.

Recupero persistente: `auto_restart=false`, `resume_on_restart=false`, `restart_attempts=3` (1–20),
`restart_delay_seconds=2` (1–60). Ritardo esponenziale limitato a 60 secondi.
`desired.json` locale (0600) registra intenzione e policy; Stop la annulla,
shutdown del servizio la conserva. Nessun autoavvio dei profili precedenti.

API globali per autenticazione:
- `GET /api/session` → `{auth_required,authenticated,secure}`.
- `POST /api/login` → `{username,password}`; imposta cookie obe_session.
- `POST /api/logout` → `{}`; revoca e cancella il cookie.

Tutte le API operative richiedono autenticazione se `auth.json` esiste.
Cookie HttpOnly/SameSite=Strict, durata sette giorni, Secure su TLS; token
memorizzati solo come hash. Rate limit con 429; assenza di sessione 401;
configurazione auth non utilizzabile 503. Nessuna password predefinita.
`--auth-file`, `--tls-cert`, `--tls-key` configurano account e TLS nativo.
Host/Origin sono verificati anche per login/logout e per lo schema HTTPS.

## Preview video/audio 0.9.1

`POST preview` accetta `{mode:"video"}` oppure `{mode:"snapshot"}`; il corpo
vuoto resta equivalente a snapshot. `POST preview-stop {}` chiude entrambi.
Lo stato preview espone `mode`; per video aggiunge session, mime, first_sequence
e last_sequence. `GET preview-init?session=...` restituisce ftyp+moov,
`GET preview-segment?session=...&sequence=N` restituisce un frammento MP4.
204 se il prossimo segmento non è pronto, 409 se sessione o segmento sono
scaduti, 422 per parametri non validi. Endpoint per canale con gli stessi controlli
auth/Host/Origin. Cache limitata a 8 segmenti/8 MiB; dati obsoleti non sono serviti.
Il browser utilizza MSE con buffer limitato, autoplay muted e controlli audio.
CSP autorizza media locali e URL blob; nessuno script remoto. Il monitor
ricodificato non dichiara precisione broadcast o latenza WebRTC.

## Selezione delle tracce audio

`capabilities.audio_tracks: 8` indica il supporto alla configurazione multitraccia.
`config.audio_tracks` è `null` nei profili precedenti (prima traccia automatica,
metadati dai campi `audio_pid`, `audio_language`, `audio_type`), oppure un array
con al massimo 8 oggetti:

```json
{"source":1,"enabled":true,"pid":991,"language":"ita","audio_type":"undefined"}
```

`source` è l’ordinale audio rilevato, da 1 a 8; `pid` è 0 automatico o 32–8190;
`language` è vuota o ISO 639 a tre lettere, normalizzata in minuscolo; `audio_type`
è `undefined`, `clean-effects`, `hearing-impaired` o `visual-impaired`. I tipi
specifici richiedono una lingua. Le sorgenti e i PID espliciti delle tracce abilitate
sono univoci; i PID non possono sovrapporsi a PMT, video o PCR. I campi omessi
usano i valori dell’esempio salvo PID 0 e lingua vuota; campi sconosciuti e tipi
JSON errati restituiscono 422 con percorso `audio_tracks.N.campo`.

Con un array esplicito, i metadati audio scalari precedenti non determinano le
tracce; codec, bitrate, gain e offset rimangono comuni. Array vuoto o tutte le
tracce disabilitate escludono l’audio. La validazione del mux somma la banda di
tutte le tracce abilitate. Import, copia, salvataggio, rollback e apply conservano
l’array. La selezione di una sorgente assente fallisce all’avvio senza sostituzioni.

`signal.audio_tracks` espone tutti gli audio del probe come
`{id,description,sdi_pair}`; `signal.audio` conserva la descrizione della prima
traccia per compatibilità. I PID di ingresso vengono mantenuti dalla sessione
URL durante le riconnessioni; un cambio permanente richiede un nuovo probe.


## Audio: elaborazione e misuratori

`capabilities.audio_processing: true` abilita i controlli comuni:
`audio_normalize` booleano (default false), `audio_loudness_lufs` −70…−5 (−23),
`audio_true_peak_dbtp` −9…0 (−1), `audio_loudness_range_lu` 1…50 (7).
I numeri devono essere finiti; valori non validi restituiscono 422 sul campo.
Import, copia e apply conservano i controlli; i profili precedenti usano il bypass.

La CLI emette telemetria schema 3, con i campi dello schema 2 e un array `audio`
(max 8). Ogni elemento contiene `output_id` 1…8, `input_id` intero non negativo,
`peak_dbfs` e `rms_dbfs` (due numeri −120…0), `clipped` (due booleani),
`age_ms` intero non negativo e `normalizing` booleano. −120 è il fondo scala
per il silenzio. Il backend continua ad accettare gli schemi 1 e 2 senza misuratori.

`telemetry.audio` è null se non disponibile o a encoder fermo, [] se non sono
ancora disponibili campioni audio. Ai misuratori viene aggiunto `status` live/stale
con l’età aggiornata anche fra due interrogazioni della CLI. Oltre 1500 ms,
`peak_dbfs`, `rms_dbfs` e `clipped` sono null; i campioni non vengono presentati
come nuovi. La scadenza globale della telemetria resta cinque secondi.
I valori sono PCM dopo elaborazione, prima della codifica; non rappresentano
il loudness integrato del programma né il true peak del flusso compresso.
