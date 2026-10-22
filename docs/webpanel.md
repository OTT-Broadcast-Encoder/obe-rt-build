# Webpanel OBE

Console scura in italiano per configurare e controllare **un encoder locale**.
Usa la build URL/SRT già compilata e aggiunge un servizio Python senza nuove
dipendenze. Non modifica il server remoto, i router o un encoder avviato altrove.

## Avvio isolato

Dalla radice del candidato, con l'immagine SRT e `obecli` già disponibili:

```sh
./webpanel/run.sh
```

Aprire `http://127.0.0.1:18088`. Il container pubblica soltanto questa porta su
loopback. `OBE_PANEL_PORT` cambia la porta locale; `OBE_PANEL_IMAGE` seleziona
un'immagine builder compatibile. Per arrestare il servizio da un altro terminale:

```sh
docker stop --timeout 15 obe-webpanel
```

Il servizio arresta anche il proprio encoder. Il profilo salvato rimane in
`webpanel/.state/`, ignorata da Git. Riavviare il servizio non avvia l'encoding.

Per usare il browser del proprio PC verso il server encoder, aprire un tunnel SSH
con l'alias già configurato:

```sh
ssh -N -L 18088:127.0.0.1:18088 encoder-server
```

Poi visitare lo stesso URL locale. L'accesso si basa sulla macchina locale o
sull'autenticazione SSH: **non pubblicare il servizio su Internet**. Non dispone
di login multiutente, ruoli o TLS propri. I controlli Host/Origin e le richieste
JSON impediscono alle normali pagine di altri siti di inviare comandi al pannello;
non sostituiscono l'autenticazione per un servizio pubblico.

## Flusso operativo

1. Impostare URL di ingresso e destinazione UDP, oppure caricare un profilo
   iniziale PAL/HD. I profili propongono parametri, non convertono la risoluzione.
2. Salvare la configurazione. Le modifiche nel form non cambiano un processo
   già avviato; il salvataggio è bloccato durante le operazioni dell'encoder.
3. Verificare l'ingresso: OBE rileva risoluzione, scansione, cadenza e audio.
   Il probe non avvia l'encoding e richiede una sorgente disponibile.
4. Scegliere in **Recupero del processo** se abilitare il recupero automatico
   per l'avvio; il valore predefinito è manuale. Avviare. Il pannello mostra la conferma CLI, lo stato di ricezione o
   riconnessione e i log del processo gestito. Arrestare prima di riconfigurare.

Input: protocolli di rete supportati da OBE, inclusi UDP/RTP e SRT. Output:
MPEG-TS UDP CBR, AVC con bitrate/VBV/preset/GOP/B-frame/lookahead/thread
configurabili; MP2 stereo 48 kHz quando la sorgente contiene audio.
Formato, campi e colore provengono dalla sorgente: nessuno scaling o conversione
PAL→1080i è implicito. I limiti del [lettore URL](input-url.md) restano validi.

Gli indirizzi `127.0.0.1` di ingresso e uscita sono interni al container.
Per un contributo esterno usare un URL raggiungibile dal container. Un ingresso
UDP listener dalla LAN richiede una pubblicazione UDP Docker esplicita; non è
attivata dal launcher. Nessun inoltro sul router viene predisposto.

Per SRT, inserire facoltativamente la passphrase nell'apposito campo prima
dell'azione. È passata soltanto nell'ambiente del processo e non viene salvata
nel profilo, restituita dall'API o conservata dal browser. Il campo viene svuotato
dopo l'azione: reinserirla dopo un probe o uno Stop per avviare una nuova sessione.
Durante il recupero automatico resta in memoria per i retry della stessa sessione;
viene rimossa dalla sessione allo Stop, all'errore terminale o all'esaurimento dei tentativi. URL con
credenziali o parametri crypto sono rifiutati. Questo primo pannello non gestisce
URL HTTP con token o autenticazione incorporata.

## Stato e misure

Le misure CPU e RSS riguardano il processo OBE; 100% CPU equivale a un core.
Un processo appena avviato può non avere ancora campioni disponibili. Il tempo
indica la durata del processo, non una certificazione di continuità del servizio.
La sezione **Telemetria del flusso** mostra bitrate AVC e UDP misurati, frame/s,
frame codificati, coda video ed errori locali di invio. Questi valori provengono
da contatori del motore OBE e sono distinti dai bitrate configurati. Su 1080i50,
25 frame/s corrispondono ai 50 semiquadri/s. Il bitrate UDP riguarda il payload
MPEG-TS accettato dal socket locale: non certifica la ricezione a destinazione.
Le statistiche SRT hanno una sezione dedicata; anteprima video e livelli audio
restano fuori da questa versione.

Il campionamento avviene una volta al secondo e il browser si aggiorna ogni due.
I rate richiedono due campioni; i valori mancanti restano vuoti. Dopo cinque
secondi senza campioni validi, le misure scadono e vengono oscurate. Un vecchio
binario senza `show telemetry` continua a codificare e indica telemetria del
flusso non disponibile. Per abilitarla serve ricompilare anche `obe-rt` dal
branch `feat/encoder-telemetry`; la sola modifica del pannello non basta.
Il primo commit del core che espone questi contatori è `ee5f5a5`.

I log sono limitati e mantenuti in memoria. La riconnessione della sorgente nel
lettore OBE mantiene lo stesso processo ed è distinta dal recupero dopo crash.

## Recupero dopo crash

L'opzione **Automatico · 3 tentativi** consente tre riavvii con attese di 2, 4 e 8
secondi. Si attiva solo dopo la prima conferma di codifica: gli errori iniziali
restano visibili e richiedono una correzione. Il budget vale per tutta la sessione,
anche se un tentativo riesce, e si azzera con un nuovo avvio manuale.

Durante l'attesa la panoramica mostra il prossimo tentativo e consente **Arresta
encoder**, che cancella i retry anche senza processo vivo. Configurazione, probe
e start rimangono bloccati fino allo Stop o alla fine del recupero. Riavviare il
pannello non avvia l'encoder e non ripristina la policy dalla sessione precedente.

La voce **Diagnostica · ultime 10 uscite** mostra orario, PID, causa, codice o
segnale, durata e numero del tentativo. Le uscite sono conservate solo nella RAM
del pannello, senza segreti, e si perdono al riavvio del servizio. Un cambio formato
o un timeout di probe/avvio interrompe il recupero e richiede un intervento manuale.

Il nuovo processo ripete il probe (circa 8 secondi) e azzera i contatori. Il
recupero interrompe la trasmissione e non garantisce continuità di PCR/PTS o
riaggancio su ogni ricevitore; il crash può troncare l'ultimo PES audio/video.
Un processo bloccato ma vivo richiederà un controllo di stallo separato.

Il vecchio listener web OBE rimane disabilitato: il pannello non usa i suoi
campi fissi come telemetria. Non abilita la vecchia scrittura periodica di
statistiche su `/tmp`, che creerebbe log senza limite di dimensione.

## Collaudo

`python3 tests/webpanel.py` esercita le API con un processo OBE simulato:
validazione, revisione del profilo, persistenza, Host/Origin, gestione del processo,
stop durante probe, crash, mancata conferma di avvio, riconnessione e protezione
della passphrase, incluso il blocco degli avvii durante lo spegnimento del servizio.
La suite include anche rate, scadenza dei campioni, contatori non validi,
compatibilità con binari precedenti e isolamento tra processi.
La suite con recupero include 26 test, passati il 21 ottobre 2026: riavvio opt-in,
budget 2/4/8 secondi, errori iniziali e timeout, Stop durante backoff o spawn,
completamenti tardivi, isolamento della chiave SRT, storico limitato e nessun
autoavvio al riavvio del servizio. Il test standalone del core
`obe-rt/tests/statistics.c` verifica incrementi concorrenti, range e reset;
compilato con ASan/UBSan e superato.

Con il pannello attivo e l'encoder fermo, il test reale si esegue nel container:

```sh
docker exec obe-webpanel python3 tests/webpanel-live.py
docker exec obe-webpanel python3 tests/webpanel-live.py --format 1080i50
docker exec obe-webpanel python3 tests/webpanel-live.py --recovery
docker exec obe-webpanel python3 tests/webpanel-live.py --format 1080i50 --recovery
```

Il test usa sorgente sintetica e porte UDP su loopback, ripristinando il profilo
iniziale al termine. Probe, start, ricezione, metriche, interruzione/riconnessione,
stop e decodifica AVC/MP2 sono passati. Il campione PAL rilevato era 720×576i,
25 frame/s, audio stereo 48 kHz; l'uscita acquisita era di 7.663.068 byte.
Artefatti iniziali: `tests/.build/webpanel-live-o_jul3gd/`, esclusi da Git.

Con `--recovery` il test invia SIGKILL soltanto al proprio encoder di collaudo,
verifica nuovo PID, stesso profilo e ripresa della telemetria, e decodifica
separatamente l'AVC/MP2 ricevuto dopo il riavvio. L'acquisizione termina con Stop
regolare per evitare che un secondo crash tronchi l'ultimo PES del campione.
Una sessione successiva verifica Stop durante l'attesa del riavvio. Log del
mittente, stato in caso di errore e risultati rimangono in `tests/.build/`.

Il collaudo crash PAL e 1080i50 è passato il 21 ottobre 2026, con ripresa di
processo e telemetria rispettivamente in **15,31 s e 14,12 s**, inclusi attesa e
nuovo probe. I campioni ripresi, di 5.250.840 e 7.164.304 byte, hanno superato
la decodifica AVC/MP2 di due secondi. Risultati: `tests/.build/webpanel-live-onpax4a8/` (PAL) e
`tests/.build/webpanel-live-gfp47ru2/` (HD). Il primo tentativo PAL ha incontrato
un timeout della sorgente dopo il riavvio; la successiva prova completa è passata.
Questi tempi sono osservazioni di laboratorio, non una garanzia sotto carico.

La revisione del recupero è isolata nel branch `feat/webpanel-recovery` e nel
container `obe-webpanel-recovery`, accessibile localmente su porta **18090**.
Usa gli artefatti compilati esistenti in sola lettura e un proprio profilo.
Per accedervi dal PC: `ssh -N -L 18090:127.0.0.1:18090 encoder-server`, poi aprire
`http://127.0.0.1:18090`. Il collaudo di 24 ore e il riaggancio dei ricevitori
reali restano da completare.

La revisione con telemetria è stata collaudata anche sul vero encoder PAL e
1080i50, includendo interruzione/ripresa sorgente e decodifica AVC/MP2.
Questi sono gli intervalli osservati in tre campioni di circa un secondo:

| Sorgente | AVC misurato | UDP misurato | Frame/s | Coda video | Errori UDP |
| --- | --- | --- | --- | --- | --- |
| PAL | 2,924–3,234 Mbit/s | 6,493–6,595 Mbit/s | 23,99–25,98 | 0 | 0 |
| 1080i50 | 4,924–5,159 Mbit/s | 9,145–9,851 Mbit/s | 23,98–24,99 | 0–2 | 0 |

I profili di test usano ultrafast, 2 thread e sorgenti sintetiche: queste misure
verificano la telemetria, non certificano qualità visiva o prestazioni sostenute.
I contatori aumentano dopo la riconnessione; allo stop tutte le letture diventano
nulle. Risultati e output: `tests/.build/webpanel-live-499b05t8/` (PAL) e
`tests/.build/webpanel-live-zrq3pspz/` (HD), ignorati da Git.

In Chromium sono stati provati navigazione, bozza durante polling, validazione
del mux, profili/salvataggio, probe/avvio sul binario reale, annullamento e
conferma dell'arresto, disconnessione del servizio e ripresa, filtri degli eventi
e navigazione da tastiera. Layout controllato a 1440, 768 e 390 px senza overflow
orizzontale. Snapshot Lighthouse della panoramica:
accessibilità 100/100, best practices 100/100; non è una certificazione completa
di accessibilità. Nessuna richiesta a servizi esterni; console pulita dopo il
ricaricamento con il servizio disponibile.

Restano fuori da questa versione preview video, livelli audio,
account/ruoli, più encoder e il collaudo prolungato. Contratto API:
[webpanel/CONTRACT.md](../webpanel/CONTRACT.md).

## Statistiche SRT, storico e allarmi

La versione 0.4 aggiunge statistiche **del ricevitore libSRT**, lette dal thread
di ingresso senza modificare il trasporto. Servono la patch FFmpeg
`patches/0016-ffmpeg-srt-statistics.patch` (applicata da `build.sh` nei nuovi clone)
e il core aggiornato in `input/lavf/lavf.c`. Per checkout FFmpeg già esistenti,
applicare la patch una volta e ricompilare libavformat e OBE. Un vecchio binario
continua a codificare ma espone statistiche SRT non disponibili.

La panoramica mostra RTT, bitrate ricevuto, pacchetti ricevuti, perdite rilevate,
ritrasmessi ricevuti, scarti tardivi, buffer occupato/disponibile, durata del
buffer e latenza negoziata. `srt_bstats(clear=0)` preserva i contatori; i totali
ripartono con ogni connessione. Le perdite rilevate possono essere recuperate,
quindi **non sono perdita residua** e da sole non attivano un allarme di scarto.
Il bitrate è calcolato da `byteRecvTotal` di libSRT, comprese le ritrasmissioni;
non equivale al bitrate video utile. RTT e latenza negoziata non misurano jitter
né latenza end-to-end. I campioni scadono dopo 5 s senza dati nuovi: rileggere
lo stesso snapshot non ne rinnova la validità.

**Storico e allarmi** raccoglie nominalmente un campione al secondo anche senza
browser. Gli ultimi **1800 campioni** (circa 30 minuti durante la codifica) e
**200 transizioni** restano in `webpanel/.state/monitoring.sqlite3`, con permessi
0600 e limite di 16 MiB per il database principale. SQLite riutilizza le pagine
liberate; il journal transitorio resta separato. A encoder fermo si registrano
solo cambi di stato/profilo. Un disco pieno o occupato interrompe la scrittura,
segnala l'indisponibilità e viene ritentato dopo 10 s; l'encoder e gli allarmi
in memoria continuano. I campioni mancanti non sono ricostruiti.

Il grafico mostra gli ultimi 120 campioni, la tabella gli ultimi 20. I download
JSON/CSV includono fino a 1800 campioni; JSON include anche le transizioni.
Ogni campione porta data UTC, sessione, processo, revisione del profilo,
parametri video/audio e versioni pannello/encoder. **URL, passphrase e log grezzi
sono esclusi**. Le celle testuali CSV sono protette dall'interpretazione come
formule. I file restano locali; non vengono inviate notifiche esterne.

| Allarme | Attivazione | Rientro |
| --- | --- | --- |
| Errore encoder | Stato errore o attesa riavvio | 3 s senza errore |
| Sorgente assente | 3 s in riconnessione | 3 s fuori riconnessione |
| Telemetria encoder/SRT assente | 3 s di misure scadute/indisponibili | 3 s con misure disponibili |
| Errori UDP / scarti SRT | Incremento del relativo contatore | 10 s senza nuovi incrementi, poi 3 s di conferma |
| RTT SRT elevato | RTT oltre 80% della latenza negoziata per 5 s | Sotto 60% per 5 s |
| Coda video elevata | Oltre 2 s di frame alla cadenza della sorgente per 5 s | Sotto 1 s per 5 s |
| Codifica senza avanzamento | Nessun frame nuovo da 5 s, con sorgente in ricezione e telemetria valida | 3 s dopo la ripresa |

Le soglie sono prudenziali e fisse, consultabili nel pannello e nell'API; vanno
tarate sul percorso reale. Le misure mancanti non dimostrano il rientro di un
allarme di soglia già attivo. Stop, cambio processo e riavvio del pannello
chiudono esplicitamente gli incidenti del processo precedente, distinguendoli
da un recupero del segnale. Non ci sono riconoscimento manuale o notifiche esterne.

Verifiche del 21 ottobre 2026: 29 test API/processo/parser e 17 test specifici
SRT/storico/allarmi. Coprono schema e monotonia, scadenza, reset connessione,
deduplica/isteresi, retention, persistenza, protezione CSV e degrado del disco.
Il collaudo reale si esegue a encoder fermo nel container:

```sh
docker exec obe-webpanel python3 tests/webpanel-srt-live.py
```

PAL cifrato con proxy locale: scarto singolo di 1 pacchetto su 23 e ritardo di
80 ms su 1 su 17, latenza SRT 400 ms. **479 pacchetti scartati dal proxy e 479
recuperati**, 621 ritardati, zero scarti tardivi SRT nei campioni verificati.
RTT osservato nei due campioni riportati: 0,323 / 0,391 ms. Verificati allarme
sorgente e rientro, connessione 1→2, oscuramento dei dati scaduti, storico
autonomo, export senza credenziali e decodifica AVC/MP2 (14.019.348 byte acquisiti).
Artefatti: `tests/.build/webpanel-srt-de2wmx0b/`, esclusi da Git. È una prova locale,
non una misura su un percorso Internet reale. Nessuna modifica ai router.

Con il nuovo campionatore è passato anche il test reale UDP **1080i50**:
probe, codifica, interruzione/ripresa, contatori, arresto e decodifica AVC/MP2.
Nei tre campioni: 22,99–25,99 frame/s, coda 1–3 frame, zero errori UDP;
10.801.728 byte acquisiti in `tests/.build/webpanel-live-lvkuiz0s/`.
Chromium: storico, export e layout verificati a 1440 e 390 px, senza overflow
orizzontale del documento e senza errori console dopo il ricaricamento.
