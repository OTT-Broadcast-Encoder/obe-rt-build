# Gateway ONVServer → NDI → SRT → OBE

Il gateway riceve `PLAYOUT (PO_Uno4)` dal runtime NDI già presente sul PC
Windows e offre il programma direttamente su una porta UDP tramite SRT
cifrato. Usa Python stdlib, runtime NDI e FFmpeg già installati. ONVServer,
CaptureStreamer e SDI mantengono la configurazione della messa in onda.
SSH serve solo per amministrare il PC: il trasporto video e la durata del
processo non dipendono da una sessione SSH.

```text
Windows: ONVServer → NDI PAL → gateway H.264/AAC → SRT listener UDP 20879
                                                             │ router/NAT
OBE:                                        ingresso URL SRT ←┘
```

## Avvio

Sul PC Windows, da PowerShell amministratore dell'utente del playout,
con `install-srt.ps1` e `ndi_sender.py` nella stessa directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-srt.ps1 -Action Install
```

L'installazione copia il gateway in `C:\ProgramData\OBE-NDI-Gateway`, genera
una chiave SRT casuale in `srt-passphrase.txt` con accesso riservato all'utente,
SYSTEM e amministratori, crea una sola regola firewall Windows UDP 20879 per
FFmpeg e avvia l'attività `OBE-NDI-SRT-Gateway`. Non installa dipendenze.
La deroga alla execution policy vale solo per quel processo PowerShell.
L'attività riparte al login dell'utente del playout; **richiede una sessione
Windows di quell'utente**, non una connessione SSH. Non è un servizio pre-login.

Per controllo e arresto usare lo stesso script installato con
`-Action Status`, `-Action Stop` o `-Action Start`. Stop scrive un marcatore
locale e lascia al gateway la chiusura dei propri processi; non ferma il playout.
Il log `gateway.log` ruota a 2 MiB con due copie. La chiave non compare nei log
o nell'URL; è comunque accessibile agli amministratori locali, anche negli
argomenti del processo FFmpeg.

Nel webpanel impostare, dopo l'inoltro sul router:
**`srt://77.39.212.14:20879?mode=caller&latency=200000`**.
Inserire la chiave nel campo SRT separato per probe e avvio. Per una macchina
nella LAN del playout usare `192.168.144.35` al posto dell'IP pubblico.
Su mini-server una copia riservata della chiave è in
`webpanel/.state/ndi-srt-passphrase.txt` (permessi 0600, esclusa da Git).
Il gateway serve **un client alla volta**; OBE può effettuare il probe,
chiudere la connessione e riaprirla per l'encoding. Chiudere eventuali player
di prova prima di avviare OBE. Il precedente URL UDP locale non è più il
percorso operativo. Il gateway non avvia né riconfigura OBE.

## Prompt per l'agent del router

> Configura sul router del playout l'inoltro UDP 20879 sull'IP pubblico
> 77.39.212.14 verso 192.168.144.35:20879 per il gateway video SRT. Verifica
> che l'IP LAN del PC Windows resti stabile e aggiungi la sola regola firewall
> necessaria. Mantieni le altre configurazioni, compreso SSH sulla porta
> 31222. Non serve aprire TCP né abilitare DMZ o UPnP. Conferma la
> configurazione applicata e verifica una sessione SRT cifrata dall'esterno
> con il ricevitore autorizzato: un controllo TCP non verifica questa porta.

Il NAT e la prova attraverso Internet devono essere completati dal router;
l'ascolto locale da solo non certifica la raggiungibilità pubblica.

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

Il muxer FIFO di FFmpeg mantiene al massimo 60 pacchetti codificati e scarta
il backlog quando non c'è un client. SRT usa AES-256, latenza iniziale 200 ms,
timeout di ascolto di 2 s e timeout I/O di 3 s; il muxer riapre il listener
dopo 1 s e riprende da un keyframe. Il solo traffico media attraversa UDP.
Durante l'attesa senza client, timeout di ascolto e scarti della FIFO nei log
sono previsti: il gateway mantiene il programma corrente senza accumularlo.
Assenza di audio/video per tre secondi, timestamp invalidi o una coda raw piena
riavviano la cattura NDI dopo due secondi. La cattura non attende heartbeat SSH.
La coda raw è limitata a 64 blocchi e 32 MiB, per assorbire brevi pause senza
conservare il backlog indefinitamente.
La chiusura interessa esclusivamente il ricevitore NDI e FFmpeg del gateway.

Una chiave errata impedisce il collegamento; la cifratura è obbligatoria.
PID MPEG-TS stabili e header ripetuti permettono a OBE di effettuare il suo
probe, riaprire l'ingresso all'avvio e riconnettersi dopo una nuova sessione SRT.
Un cambio permanente di formato richiede comunque un nuovo probe in OBE.

SRT ritrasmette i pacchetti persi entro il budget di latenza configurato;
non può recuperare qualsiasi guasto di rete. Il recupero non è senza perdita;
non vengono generati nero e
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

Questi risultati del 21 ottobre riguardano il trasporto SSH precedente.
La suite aggiuntiva `tests/ndi-gateway-srt.py`, eseguibile con lo stesso
ambiente Python/FFmpeg della suite locale, verifica il listener SRT reale:
cifratura, rifiuto della chiave errata, attesa senza client, recupero fra due
client e assenza della chiave nei log. L'implementazione SSH è conservata per
compatibilità e regressione, ma non serve al funzionamento del listener SRT.

Il 22 ottobre 2026 il collaudo sul PC Windows è passato: due connessioni SRT
cifrate successive, chiave errata rifiutata, AVC 720×576i25 TFF 16:9 e AAC
stereo 48 kHz, chiave assente dai log. Stop/Start dell'attività chiude e ricrea
solo il proprio FFmpeg; ONVServer, CaptureStreamer e PlayOutNet restano attivi.
Artefatto: `tests/.build/ndi-gateway-srt/windows-result.json`. La prova
dall'esterno non ha stabilito la connessione: NAT pubblico ancora da verificare
con l'agent del router (`public-result.json`).
