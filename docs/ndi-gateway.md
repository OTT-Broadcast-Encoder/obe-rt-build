# Gateway ONVServer → NDI → SSH → OBE

Il gateway riceve `PLAYOUT (PO_Uno4)` dal runtime NDI già presente sul PC
Windows e porta il programma nel container OBE attraverso SSH. Non cambia
ONVServer, CaptureStreamer, SDI o le regole di rete. Usa Python stdlib e
FFmpeg esistenti; il codice del mittente viene eseguito in memoria via SSH.

```text
Windows: ONVServer → NDI PAL → ricevitore separato → FFmpeg H.264/AAC → MPEG-TS
                                                                        │ SSH
mini-server:                         OBE ← UDP loopback ← relay container ←┘
```

## Avvio

Con `obe-webpanel` già attivo, dalla radice del repository su mini-server:

```sh
python3 gateway/ssh_relay.py \
  --host HOST_PLAYOUT --ssh-port 31222 --user UTENTE_SSH \
  --source 'PLAYOUT (PO_Uno4)' --container obe-webpanel --ask-password
```

Sostituire host e utente con quelli del playout. La chiave host SSH deve già
essere verificata: il relay usa `StrictHostKeyChecking=yes`. Omettere
`--ask-password` per usare una chiave SSH o un agent. La modalità password
richiede `sshpass` sul lato Linux, già disponibile nell'ambiente collaudato;
la password passa tramite un descrittore anonimo e resta solo in memoria.
Non inserirla nella riga di comando, nei profili o negli script.

Nel webpanel impostare **`udp://127.0.0.1:12000`**, quindi verificare l'ingresso.
È il loopback **del container**, non quello del PC Windows o del browser.
Il profilo PAL è una base per il bitrate di uscita; il gateway non avvia OBE.
Senza `--container`, il relay pubblica invece sul loopback del sistema locale.
`--udp-port` cambia la porta. Un lock impedisce a due relay sullo stesso host
di mescolare i flussi verso la stessa destinazione.

Ctrl+C arresta il relay e il suo mittente. `SIGTERM` ha lo stesso effetto.
`--seconds 60` limita una prova a circa un minuto, più il tempo di chiusura.
L'avvio non installa servizi o attività pianificate: dopo un riavvio dell'host
il comando deve essere rilanciato. Non è necessario aprire nuove porte UDP
pubbliche: tutta la contribuzione attraversa la connessione SSH esistente.

## Formato e sincronismo

- Il PAL misurato è 720×576 interlacciato, 25 frame/s, 16:9. L'uscita AVC
  conserva 4:2:2 a 8 bit e SAR 64:45; non viene ingrandita a 1080.
- Audio NDI stereo float a 96 kHz ricampionato a 48 kHz, AAC 256 kb/s.
  L'ingresso URL OBE lo decodifica e può ricodificarlo in MP2.
- Timestamp NDI di audio e video conservati in un contenitore intermedio
  con origine comune. Non vengono ricreati due clock indipendenti da pipe raw.
  L'audio mantiene il proprio scarto iniziale; il ricampionatore compensa
  piccoli scostamenti e aggiunge silenzio iniziale quando richiesto dai PTS.
- Contribuzione x264 `veryfast`, CRF 16, VBV massimo 15 Mbit/s, GOP circa
  mezzo secondo con header ripetuti, nessun B-frame, due thread. È una compressione lossy;
  la qualità della contribuzione va inclusa nei confronti sull'encoder finale.
  `--max-mbps` e `--threads` permettono di variare questi limiti.
- `--field-order tff` è la scelta iniziale; `bff` è disponibile. I flag TFF
  sono stati controllati nel bitstream. L'ordine temporale reale dei campi
  del playout e il sincronismo labiale richiedono ancora materiale di verifica.

La cattura accetta frame NDI completi UYVY, interlacciati o progressivi,
entro 1920×1080 e 60 frame/s, con audio stereo 32–192 kHz. Il collaudo reale
riguarda il PAL SDR osservato, non tutta questa matrice. Campi NDI separati,
altri layout video/audio o cambi di formato provocano un errore esplicito.

## Recupero e limiti

Il relay riprova la connessione dopo 2, 4 e poi 5 secondi. Un controllo di
presenza ogni due secondi ferma il mittente se per dieci secondi non arrivano
messaggi dal relay. EOF, assenza di audio/video per tre secondi, timestamp
invalidi o una coda piena chiudono la sessione; il relay la ricrea.
La coda raw è limitata a 64 blocchi e 32 MiB, per assorbire brevi pause senza
conservare il backlog indefinitamente.
La chiusura interessa esclusivamente il ricevitore NDI e FFmpeg del gateway.

Gli errori di autenticazione o verifica host fermano i tentativi, per evitare
blocchi dell'account. Se il container sparisce, il relay attende che ritorni.
PID MPEG-TS stabili e header ripetuti permettono a OBE di effettuare il suo
probe, riaprire l'ingresso all'avvio e riconnettersi dopo una nuova sessione SSH.
Un cambio permanente di formato richiede comunque un nuovo probe in OBE.

SSH usa TCP: protegge il contenuto ma può accumulare ritardo durante perdita
o congestione. Il recupero non è senza perdita; non vengono generati nero e
silenzio per riempire l'interruzione del programma. Per esercizio continuativo
restano da collaudare lunga durata, risorse, ritardo e qualità sul materiale reale.

## Verifiche

Suite locale, senza accessi al playout; il test media richiede FFmpeg e ffprobe:

```sh
docker exec -e PYTHONDONTWRITEBYTECODE=1 \
  -e PATH=/work/target-root/usr/local/bin:/usr/bin:/bin \
  -e LD_LIBRARY_PATH=/work/target-root/usr/local/lib \
  obe-webpanel python3 tests/ndi-gateway.py
```

I 12 test sono passati: mux reale/decodifica, offset audio dopo ricampionamento,
campi, aspect ratio, intervallo dei keyframe, timestamp non validi, stride
video/audio, limiti della coda, confini dei pacchetti MPEG-TS, esclusione del
doppio relay e argomenti per il comando Windows.

`tests/ndi-gateway-live.py` prova invece il gateway già attivo con un nuovo
processo OBE. Va eseguito in un container di test separato, indicando una
directory per gli artefatti. Con `--reconnect` attende una sola interruzione
volontaria della connessione SSH del gateway di test; il file
`ready-to-interrupt` segnala quando effettuarla. Non fermare il playout NDI.

Il 21 ottobre 2026 la prova completa è passata: 10.537.212 byte di uscita
AVC/MP2 decodificabile, PAL 16:9, audio stereo 48 kHz non silenzioso,
riconnessione SSH/OBE e chiusura dei processi. Nell'intervallo misurato il
passo video resta 40 ms; il recupero presenta un passo audio massimo di
176 ms. La differenza fra i primi PTS audio/video è 85,333 ms: è una misura
dell'inizio delle tracce acquisite, non una certificazione del sincronismo labiale.
Artefatti esclusi da Git: `tests/.build/ndi-gateway/live-obe/`.

Verificati anche la chiusura del mittente Windows alla scadenza del controllo
di presenza e il recupero dopo il ritorno del container. Il probe finale
dell'URL nel container conferma AVC PAL TFF 16:9 e AAC stereo 48 kHz con
un secondo di analisi e 2 MB di probe. I risultati sono in
`tests/.build/ndi-gateway/heartbeat-result.json` e `final-url-probe.json`.
