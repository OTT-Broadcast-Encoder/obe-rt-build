# Webpanel OBE

Console scura in italiano per configurare e controllare **fino a quattro encoder locali**.
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
4. Avviare. Il pannello mostra la conferma CLI, lo stato di ricezione o
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
dopo l'azione: reinserirla dopo un probe per avviare un nuovo processo. URL con
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
La preview è opzionale; livelli audio e statistiche SRT non disponibili restano esclusi.

Il campionamento avviene una volta al secondo e il browser si aggiorna ogni due.
I rate richiedono due campioni; i valori mancanti restano vuoti. Dopo cinque
secondi senza campioni validi, le misure scadono e vengono oscurate. Un vecchio
binario senza `show telemetry` continua a codificare e indica telemetria del
flusso non disponibile. Per abilitarla serve ricompilare anche `obe-rt` dal
branch `feat/encoder-telemetry`; la sola modifica del pannello non basta.
Il primo commit del core che espone questi contatori è `ee5f5a5`.

I log sono limitati e mantenuti in memoria; la riconnessione automatica del
lettore OBE resta operativa, ma un crash dell'encoder richiede un nuovo avvio.

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
Tutti i 15 test sono passati il 21 ottobre 2026. Il test standalone del core
`obe-rt/tests/statistics.c` verifica incrementi concorrenti, range e reset;
compilato con ASan/UBSan e superato.

Con il pannello attivo e l'encoder fermo, il test reale si esegue nel container:

```sh
docker exec obe-webpanel python3 tests/webpanel-live.py
docker exec obe-webpanel python3 tests/webpanel-live.py --format 1080i50
```

Il test usa sorgente sintetica e porte UDP su loopback, ripristinando il profilo
iniziale al termine. Probe, start, ricezione, metriche, interruzione/riconnessione,
stop e decodifica AVC/MP2 sono passati. Il campione PAL rilevato era 720×576i,
25 frame/s, audio stereo 48 kHz; l'uscita acquisita era di 7.663.068 byte.
Artefatti iniziali: `tests/.build/webpanel-live-o_jul3gd/`, esclusi da Git.

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

Restano fuori da questa versione livelli audio, statistiche SRT,
account/ruoli e il collaudo prolungato. Contratto API:
[webpanel/CONTRACT.md](../webpanel/CONTRACT.md).

### Multicanale

Il selettore **Canale** mostra nome e stato; **Nuovo canale** crea un profilo
indipendente, fino a quattro. Stop, configurazione, log e misure riguardano il
canale selezionato. Cambiare scheda non ferma gli altri encoder; allo spegnimento
del servizio vengono arrestati tutti i processi posseduti.
Gli ingressi UDP/RTP e le destinazioni devono usare porte distinte fra i canali.
Dimensionare CPU e memoria con il carico reale prima dell'uso prolungato.

`python3 tests/webpanel.py` include isolamento, persistenza, limite e conflitti.
`docker exec obe-webpanel-next python3 tests/webpanel-multichannel-live.py`
verifica due encoder reali contemporanei PAL/1080i, stop indipendente e decodifica.
Usare esclusivamente un servizio di collaudo fermo: il test salva e ripristina
i profili e crea il secondo canale se manca.

### Preview dell'uscita

**Attiva preview** avvia FFmpeg solo per il canale selezionato. Riceve l'uscita
UDP già configurata, deinterlaccia e produce JPEG larghi 640 px, senza audio,
al massimo un'immagine/s; il browser aggiorna ogni 2 s. Non cambia il bitrate o
le uscite OBE. Sono ammessi loopback e multicast IP; per unicast remoto serve
un monitor a destinazione. La porta unicast locale deve essere libera: la
preview non abilita il riuso del socket, per non sottrarre pacchetti a un ricevitore.

Memoria limitata all'ultimo JPEG (massimo 1 MiB), nessuna registrazione su disco.
Immagini più vecchie di 3 s, sorgente in riconnessione, crash o stop non appaiono
come live. Uno stop della preview lascia attivo l'encoder; stop/crash dell'encoder
e shutdown fermano anche FFmpeg. Il processo preview consuma CPU aggiuntiva,
non inclusa nella metrica **CPU encoder**. Un errore richiede riattivazione manuale.
Non sostituisce controllo dei campi, ascolto audio o analisi qualità.

Test: `tests/webpanel-preview-unit.py`, lifecycle API in `tests/webpanel.py` e
`docker exec obe-webpanel-next python3 tests/webpanel-preview-live.py`.
Quest'ultimo usa solo HD sintetico e porte locali; ripristina il profilo iniziale.

### Banco di questo incremento

Il checkout `obe-rt-build-panel-next` usa `feat/panel-multichannel` e il core
`fix/url-mid-gop`. Il container `obe-webpanel-next` espone solo
`127.0.0.1:18089`; il precedente container di collaudo è conservato fermo come
`obe-webpanel-next-baseline`. Dipendenze e SDK già presenti sono montati in sola
lettura dal checkout di build; nessuna dipendenza nuova è installata sull'host.
Il binario candidato è nel nuovo checkout e lo stato in `webpanel/.state/`.
Il servizio originale 18088 e il suo checkout restano indipendenti.

Per il browser remoto: `ssh -N -L 18089:127.0.0.1:18089 encoder-server`.
Per fermare il solo candidato: `docker stop --timeout 15 obe-webpanel-next`;
per riavviarlo: `docker start obe-webpanel-next` (gli encoder rimangono fermi).

Vedi anche [gateway MPEG-TS remoto](ssh-contribution-gateway.md) e
[collaudo HD con CSV/JSON](hd-pilot.md). UI verificata in Chromium a 390 e
1440 px, cambio/creazione canale, fallback per URL inesistente e JPEG HD reale,
senza overflow o errori console. Le immagini vengono inserite solo dopo il
caricamento: risolta la segnalazione del controllo design, senza soppressioni.
