# Webpanel OBE

Console scura in italiano per configurare e controllare **fino a quattro encoder locali**.
Usa la build OBE già compilata e aggiunge un servizio Python senza nuove
dipendenze. Non modifica il server remoto, i router o un encoder avviato altrove.

## Vincolo di compatibilità per la diffusione TV

Il flusso è destinato alla televisione: la compatibilità dei ricevitori è un
requisito per ogni scelta di encoding, comprese le opzioni x264 non esposte e
il filtraggio VapourSynth. Una riduzione del blocking non giustifica una
regressione di decodifica, aggancio del canale o sincronismo audio/video.

Per le prove 1080i, il riferimento attuale è AVC High 4.1, 1920×1080 a 25 frame
e 50 semiquadri al secondo, TFF, 8 bit e 4:2:0. A questa risoluzione il DPB
di livello 4.1 ammette al massimo quattro frame, inclusi i B di riferimento.
Profilo e livello devono rispettare la specifica della rete: questo riferimento
va validato sui destinatari e non garantisce compatibilità con ogni televisore.
Il vincolo di banda resta 1.300 kb/s per l'intero MPEG-TS, audio e overhead di
multiplex inclusi; header di rete e ritrasmissioni SRT sono aggiuntivi.

Prima di promuovere un profilo, verificare VBV/HRD, GOP e punti di accesso,
B-pyramid e open GOP, ordine e movimento dei campi, BT.709, range e rapporto
d'aspetto, PCR e jitter, PTS/DTS, continuità TS e PAT/PMT.
Codec/profilo audio, incapsulamento e segnalazione
PSI/SI del mux finale devono rispettare la specifica della rete TV di destinazione.
Nel fork attuale OBE imposta `fakevbr`: calcola la temporizzazione interna ma
non segnala i parametri NAL-HRD nel SPS. Né il solo VBV configurato né la
correzione sperimentale del ratecontrol provano conformità HRD/DVB.
La sola decodifica FFmpeg o la preview del pannello non certificano compatibilità:
servono prove sui decoder TV destinatari, con aggancio da punti diversi del
flusso, riaggancio dopo interruzioni e riproduzione prolungata. Fino ad allora
i risultati restano qualificati come verifiche software o sperimentali.

## Funzioni broadcast della versione 0.9.1

I profili precedenti restano compatibili. Le opzioni aggiuntive sono raccolte nei
pannelli avanzati: profilo/livello/tune AVC, massimo VBV, reference frame,
B-pyramid, ordine campi e rapporto d'aspetto; codec e profili AAC, guadagno,
offset e lingua audio; TSID, programma, PID video/audio/PMT/PCR, periodi PCR/PAT,
nome servizio e provider. I PID in collisione e le combinazioni non valide sono
rifiutati prima di arrestare un encoder attivo. TFF/BFF imposta l'ordine dei
campi di una sorgente già interlacciata: non crea 50 istanti temporali diversi.

L'ingresso può essere URL, DeckLink (SDI/HDMI e connessioni supportate dalla
scheda) oppure NDI. Supporto della build e disponibilità dell'hardware sono
indicazioni distinte; selezionare una scheda non ne dimostra il funzionamento.
La stessa scheda DeckLink non può essere riservata da due canali attivi.

In **Impostazioni → Filtri → VapourSynth** si può abilitare e modificare lo
script Python del canale. **Esegui test** esegue la bozza con VapourSynth e i plugin
installati, senza salvare o interrompere il canale. Renderizza cinque frame
sintetici (0, 1, 2, 12 e 24) e mostra output ed errori Python nel log compatto
sotto l'editor. Verifica anche che l'output video 0 rispetti il contratto OBE.
**Salva** la conserva e **Applica e riavvia** sostituisce lo script del canale
attivo. Un errore sintattico impedisce il riavvio. Lo script riceve `clip` e deve
chiamare `clip.set_output()` mantenendo formato pixel, altezza, fps e durata.
Il filtro supporta YUV420 a 8/10 bit e YUV422 a 10 bit, con circa un secondo di
buffer a 25 fps. I plugin vengono caricati nel processo encoder, senza sandbox.
Il pannello mostra lo stato effettivo del filtro; se lo script non inizializza
la catena, OBE segnala errore e interrompe la codifica. Import, export e ripristino
comprendono il testo dello script (massimo 8 KiB); usare solo script fidati.

OBE fornisce anche queste variabili globali Python a ogni caricamento dello script,
incluso il ricaricamento. Descrivono l'ingresso prima dei filtri e del resize:

| Variabile | Significato |
| --- | --- |
| `OBE_INTERLACED` | `1` per ingresso interlacciato, `0` per progressivo. |
| `OBE_TFF`, `OBE_BFF` | Ordine dei campi, come flag `0/1`; entrambi `0` sul progressivo. |
| `OBE_FIELD_BASED` | Convenzione VapourSynth: `0` progressivo, `1` BFF, `2` TFF. |
| `OBE_WIDTH`, `OBE_HEIGHT` | Dimensioni del video consegnato al filtro. |
| `OBE_FPS_NUM`, `OBE_FPS_DEN` | Cadenza dei fotogrammi come frazione, non dei semiquadri. |
| `OBE_BIT_DEPTH` | Profondità del formato pixel ricevuto dal filtro: 8 oppure 10 bit. |

Le variabili provengono dai metadati dell'ingresso OBE, non dall'ordine scelto
per la codifica in uscita. Non occorre leggere un frame per conoscere la parità:

```python
if OBE_INTERLACED:
    fields = clip.std.SeparateFields(tff=OBE_TFF)
    # Applicare qui eventuali filtri spaziali ai campi.
    clip = fields.std.DoubleWeave(tff=OBE_TFF).std.SelectEvery(cycle=2, offsets=0)
clip.set_output()
```

Per i filtri temporali, elaborare separatamente i campi della stessa parità.

L'esempio [1080i-jetpack.vpy](../examples/1080i-jetpack.vpy) può essere incollato
integralmente nell'editor. Parte da deblock moderato, RemoveGrain e riduzione
della grana nelle zone chiare, sulla sola luminanza e con variazione massima di
4 livelli in scala 8 bit. Impostare `FIELD_ORDER` sulla parità reale della
sorgente. `DENOISER` seleziona una sola alternativa; le modalità temporali
elaborano separatamente i campi della stessa parità e ricompongono i 25 fps.
Dering e deband sono opzionali, senza grana aggiunta. DPIR richiede modelli e
backend MLRT disponibili; BM3D CUDA richiede la GPU. Prima dell'uso live eseguire
il test del pannello e misurare qualità, bitrate e velocità con il segnale reale.
Con JET 2.2.6 e mvutensils 10, `mc_degrain` passa un argomento non più accettato:
lo script riconosce questo errore e usa il MVTools classico già installato.
Le prove automatiche coprono le modalità CPU; DPIR e CUDA restano da verificare
sull'hardware e con i modelli scelti.

Il ridimensionamento orizzontale si scrive direttamente nello script VapourSynth,
prima dell'output, per esempio:

```python
clip = clip.resize.Spline36(width=1440)
clip.set_output()
```

OBE ricava la risoluzione dal primo frame filtrato prima di aprire x264: non
occorre selezionare separatamente larghezza o scaler. Il resize usa VapourSynth/zimg
e conserva altezza, cadenza e formato pixel. Il rapporto pixel viene letto dalle
props dell’output: zimg lo adegua nel resize, quindi da 1920×1080 con SAR 1:1
a 1440×1080 produce SAR 4:3, mantenendo 16:9 sul televisore. Se la sorgente non
fornisce un SAR valido, OBE non presume pixel quadrati.
È possibile scegliere un altro kernel nello script, ad esempio
`clip.resize.Lanczos(width=1440, filter_param_a=3)`.
Sono ammessi downscale orizzontali con larghezza pari; il resize verticale non è
supportato da questo percorso. Un cambio di geometria richiede il riavvio del
canale. Errori di script o formato interrompono la codifica senza inoltrare frame
non filtrati. Disabilitando lo script si torna alla risoluzione originale.

All’ingresso OBE imposta le proprietà del frame prima di eseguire i filtri:
`_FieldBased` (0 progressivo, 1 BFF, 2 TFF), `_DurationNum`/`_DurationDen` e
`_AbsoluteTime`; quando disponibili anche `_SARNum`/`_SARDen`, `_Matrix`,
`_Primaries`, `_Transfer`, range e `_ChromaLocation`. VapourSynth 4.2 usa
`_Range` (0 limitato, 1 completo); il bridge accetta anche il precedente
`_ColorRange` (1 limitato, 0 completo). La crominanza usa la convenzione VS:
0 sinistra, 1 centro, 2 alto-sinistra, 3 alto, 4 basso-sinistra, 5 basso.
I dati sconosciuti restano assenti, senza dedurre colore o SAR dalla risoluzione.

OBE legge le props del primo frame filtrato per configurare campi, SAR e colore
in x264. Devono poi rimanere costanti: un cambio richiede il riavvio. Le proprietà
note all’ingresso non possono sparire; durata e timestamp devono corrispondere
al frame sorgente. Il SAR deve essere rappresentabile in H.264, con numeratore
e denominatore ridotti entro 65535. Primarie 22 e transfer HDR PQ/HLG sono
rifiutati perché la catena attuale non li supporta completamente. Impostare una
prop modifica la segnalazione: una conversione colore o deinterlacciamento
richiede anche il filtro che trasforma i pixel.

Il test mostra **Ingresso / Uscita**: dimensioni, cadenza, scansione, formato e
profondità pixel, SAR/DAR, colore, range e crominanza; le props effettive sono nei
dettagli tecnici. Usa i metadati del primo frame OBE rilevato, compreso il formato
YUV420 a 8/10 bit o YUV422 a 10 bit. In assenza di quel rilevamento usa geometria
e fps disponibili, poi il formato configurato o 1920×1080 a 25 fps. Il formato
pixel di ripiego è YUV420P8; scansione e parità simulate sono indicate, con TFF
quando l’ordine è automatico. Non vengono inventati colori o SAR mancanti.
`_AbsoluteTime` nel test parte da zero e avanza con la cadenza simulata.
La clip conserva la durata virtuale del bridge e riceve le stesse variabili
`OBE_*`. I metadati mostrati in uscita sono letti dai frame prodotti dallo script,
con gli stessi controlli del percorso live; cinque campioni non garantiscono
che uno script variabile mantenga il contratto sui frame successivi.
Il test è eseguito in un processo separato, uno alla volta, con limite di
60 secondi complessivi per inizializzazione, rendering e chiusura, due CPU
(120 secondi CPU cumulativi) e 3 GiB di
spazio di indirizzamento. Il log restituito è limitato a 16 KiB. Questi limiti
non costituiscono una sandbox: usare solo script fidati. I cinque frame
sintetici verificano inizializzazione e rendering, ma non certificano qualità,
prestazioni o comportamento sul flusso reale. Per questa richiesta il browser
attende fino a 75 secondi, consentendo al backend di restituire l'esito del test.

Per verificare l'integrazione nel container di build già disponibile:

```sh
python3 tests/webpanel-vapoursynth.py
python3 tests/vapoursynth-low-bitrate.py
bash tests/vapoursynth-filter.sh
python3 tests/webpanel-vapoursynth-live.py --video-format 1080i
python3 tests/webpanel-vapoursynth-resize-live.py --artifacts tests/.build/vs-resize-live
```

Fino a otto destinazioni aggiuntive condividono lo stesso MPEG-TS, senza una
nuova codifica. Ogni uscita ha coda limitata, stato e contatori indipendenti.
L'encoder invia sempre il TS a un distributore locale, anche quando è configurata
solo l'uscita UDP primaria. Se cambi soltanto le uscite o le relative passphrase,
**Applica uscite** aggiunge, rimuove o sostituisce le destinazioni senza riavviare
la codifica. Le uscite invariate conservano connessioni e contatori; solo una
destinazione modificata si riconnette. Una nuova destinazione inizia a decodificare
quando riceve le tabelle TS e un keyframe. Ingresso, codec e mux richiedono invece
**Applica e riavvia**. Se cambia l'UDP primario, l'anteprima attiva viene riaperta
sul nuovo indirizzo.
SRT offre caller/listener, latenza, chiave AES da 16/24/32 byte (32 per i profili
esistenti), banda massima facoltativa, timeout e scarto dei pacchetti tardivi;
si riconnette quando il collegamento cade. UDP/RTP riportano
l'invio locale, senza confermare la ricezione remota. MAIN/BACKUP sono nomi delle
uscite: due uscite abilitate trasmettono contemporaneamente, non costituiscono
un cluster HA. L'output UDP storico resta disponibile per il monitor locale.
Le nuove destinazioni accettano URL senza query; le chiavi SRT si salvano in
campi separati. Il cambio endpoint rimuove la chiave precedente.
Con login attivo, la passphrase salvata compare direttamente nel campo SRT
dell'uscita; svuotarlo e applicare rimuove la chiave. Il TS sorgente è già quello
codificato dal canale: non occorre indicare un gruppo multicast sorgente né la
dimensione dei blocchi di lettura di un trasmettitore esterno.
L'URL UDP primario ammette `?ttl=0…255`; il TTL multicast predefinito resta 1.

Passando da SRT a UDP/RTP, i campi SRT nascosti non bloccano il salvataggio:
si conservano i valori SRT già salvati, oppure i predefiniti per una nuova
destinazione. Tornando a SRT prima di salvare si ritrova la bozza, che deve
essere valida per poter essere applicata.
Default, intervalli e scelte delle uscite SRT sono definiti in
`webpanel/srt_options.py`: il backend li espone all'interfaccia e la build
li usa per generare le verifiche del gateway C. Il frontend conserva il
contratto precedente soltanto come compatibilità con servizi non aggiornati.

Il gateway SRT usa esclusivamente le librerie già presenti nella build:

```sh
sh webpanel/build-srt-output.sh
```

Il binario locale `webpanel/bin/obe-srt-output` è escluso da Git. Non vengono
aperte porte Docker o modificate regole di rete automaticamente.

Le verifiche mirate sono:

```sh
node tests/webpanel-output-settings.js
python3 -B tests/webpanel-transport.py
python3 -B tests/webpanel-profiles.py
python3 -B tests/webpanel-parity.py ParityTests.test_output_parameter_contract_and_protocol_switch ParityTests.test_output_passphrase_reveal_is_authenticated_and_bound_to_output
```

Coprono il cambio protocollo con una bozza SRT non valida, il caricamento
asincrono della passphrase, AES-128/192/256 in caller/listener e la compatibilità
del gateway precedente. Le prove native confrontano la validazione con l'API,
misurano l'effetto di banda massima e timeout e verificano il payload ricevuto.
Usano esclusivamente loopback e binari temporanei: non certificano la tratta
WAN, il funzionamento prolungato o la qualità della codifica.

**Copia da canale** e **Importa** preparano una bozza da controllare e salvare o
applicare. L'export JSON versione 1 contiene solo la configurazione pubblica,
senza credenziali. Prima di ogni salvataggio viene conservato uno snapshot
privato `previous.json` (0600); **Ripristina precedente** ripristina anche le
credenziali e usa lo stesso ciclo di validazione e stop/start di Applica.

La diagnostica aggiunge CPU, memoria, disco, limiti del container e contatori
rete. Le misure scadute sono segnalate, non presentate come attuali. I 50
semiquadri nominali di 1080i25 non provano il movimento di 50 immagini distinte;
ordine campi, colore e sincronismo non misurati restano sconosciuti. La preview video/audio ricodificata resta un controllo visivo, non una misura
di qualità o sincronismo. Nei browser compatibili usa H.264/AAC, fino a
640 pixel di larghezza e 50 fps, con audio inizialmente disattivato; il player permette
di attivarlo. Le immagini JPEG rimangono disponibili come fallback.

Il budget e il ritardo iniziale del recupero sono configurabili. La ripresa
all'avvio del pannello è disabilitata per default: quando abilitata, riprende
solo un canale precedentemente avviato e non arrestato dall'operatore, usando
le credenziali salvate. Stop annulla la ripresa; lo spegnimento del servizio
conserva l'intenzione. Un override temporaneo della passphrase non è salvato.
Lo stato desiderato è locale: non effettua failover fra macchine.

## Account locale e HTTPS

Creare un account con una password richiesta dal terminale:

```sh
python3 webpanel/setup-auth.py --auth-file webpanel/.state/auth.json --username operatore
```

L'account viene rilevato dal servizio anche senza riavvio. `--replace` cambia
l'account e revoca le sessioni. Le password sono derivate con PBKDF2-SHA256;
le sessioni durano sette giorni, persistono ai riavvii e si revocano con Logout.
Le API richiedono la sessione quando l'account è configurato. Un file account
corrotto o con permessi errati blocca l'accesso. Il login non cifra HTTP.

Per HTTPS diretto, conservare certificato e chiave nella directory privata e
passare i percorsi visibili nel container:

```sh
OBE_PANEL_TLS_CERT=/work/webpanel/.state/tls/cert.pem \
OBE_PANEL_TLS_KEY=/work/webpanel/.state/tls/key.pem ./webpanel/run.sh
```

Il server accetta TLS 1.2 o superiore; in HTTPS il cookie è anche Secure.
Certificato, DNS e fiducia del browser vanno configurati per l'indirizzo usato.
Non vengono generati account/password predefiniti o certificati automatici.

## Verifiche della versione 0.9

Test unitari e HTTP coprono validazione, segreti, copia/import/export/rollback,
recupero persistente, autenticazione, diagnostica e lifecycle del trasporto.
I test SRT loopback verificano AES256, chiave errata, sostituzione del peer e
isolamento di una destinazione guasta. `tests/webpanel-broadcast-live.py` usa
una fixture isolata per otto combinazioni PAL/1080i e MP2/AAC/AC3/EAC3:
verifica TFF/BFF nel bitstream, PID, PCR, PMT, TSID, servizio, lingua,
continuità TS e decodifica rigorosa, rilevando eventuali perdite della cattura.
Queste prove non sostituiscono collaudi SDI/NDI, confronto qualità a parità di
bitrate, misure WAN o failover HA con secondo nodo e fencing.

## Avvio isolato

### Distribuzione in un servizio indipendente

`webpanel/run.sh` avvia `obe-transport` in un container distinto da `obe-webpanel`.
Stop, cambio configurazione, crash dell'encoder e riavvio del pannello lasciano
attivi il distributore, la sua porta UDP loopback e le connessioni delle uscite
invariate. Il pannello si ricollega attraverso un socket Unix privato.
Le uscite restano configurabili anche con encoder fermo; eliminare un canale
ne chiude le uscite. Le destinazioni SRT listener continuano a riservare la porta.

Senza TS in ingresso il servizio resta in attesa, senza generare immagini,
ripetere pacchetti o accumulare media vecchi. Un riavvio dell'encoder comporta
quindi un'interruzione del contenuto e una nuova acquisizione di tabelle/keyframe,
anche quando la connessione SRT e la sequenza RTP restano aperte.
Nel pannello lo stato del trasporto è distinto da quello del processo encoder.

Il container delle uscite ha riavvio Docker `unless-stopped`; conserva porte e
configurazioni applicate in `webpanel/.state/transport/outputs.json` (0600),
incluse le chiavi SRT. La directory (0700) resta esclusa da Git e dagli export
dei profili. Dopo un crash del distributore si riaprono le destinazioni salvate;
connessioni e contatori ripartono. Non è una garanzia di consegna senza perdita.
Gli helper SRT su Linux terminano anche se il processo distributore viene ucciso.

Con rete `host`, già usata sul server, encoder e distributore condividono il
loopback anche dopo il riavvio di uno dei due container. In modalità `bridge`
il pannello condivide la rete del container `obe-transport`: dopo il riavvio
di quest'ultimo occorre riavviare anche il pannello per riallineare la rete.
Un cambio di rete/porte del launcher richiede una riconfigurazione pianificata
del container delle uscite; il launcher non lo sostituisce automaticamente.
Fermare soltanto `obe-webpanel.service` lascia `obe-transport` in esecuzione.

Per avvii diretti senza Docker, avviare prima `webpanel/transport_service.py`
con `--root` e `--state-dir`, poi passare il suo `control.sock` a
`webpanel/server.py --transport-socket`. Senza questa opzione resta disponibile
la modalità incorporata precedente, usata dai test legacy.

`python3 tests/webpanel-transport-service.py` verifica processi reali separati,
stop/start/crash con encoder simulato, Apply, riavvio del pannello, recupero del
distributore sulla stessa porta, sequenza RTP e connessione SRT cifrata
caller/listener durante una pausa della sorgente. Non contatta reti di produzione.

Dalla radice del candidato, con l'immagine SRT e `obecli` già disponibili:

```sh
./webpanel/run.sh
```

Aprire `http://127.0.0.1:18088`. Il container pubblica soltanto questa porta su
loopback. `OBE_PANEL_PORT` cambia la porta locale; `OBE_PANEL_IMAGE` seleziona
un'immagine builder compatibile.

Per consentire anche l'accesso dalla LAN fidata, indicare un IPv4 assegnato al
server, per esempio `OBE_PANEL_LAN_IP=192.168.1.10 ./webpanel/run.sh`.
Il pannello risponde anche su `http://192.168.1.10:18088`, mantenendo loopback.
Solo l'indirizzo indicato viene aggiunto ai controlli Host; i controlli Origin
restano attivi. Il login è opzionale e si configura come descritto sotto;
senza account, la LAN deve essere fidata.

Per trasmettere multicast sulla LAN, avviare il container con la rete dell'host:

```sh
OBE_PANEL_NETWORK_MODE=host OBE_PANEL_LAN_IP=192.168.1.10 ./webpanel/run.sh
```

Il bridge Docker predefinito non inoltra il multicast verso la scheda fisica.
In modalità `host` l'encoder usa le interfacce e le rotte del server; non servono
mappature Docker delle porte UDP. Il pannello usa direttamente `OBE_PANEL_PORT`
sull'host: bind loopback senza LAN abilitata, `0.0.0.0` con LAN abilitata;
autenticazione e controlli Host/Origin restano attivi. La porta deve essere libera.
Il cambio modalità richiede il riavvio del container e interrompe i canali attivi.

I nuovi canali usano l'uscita UDP multicast locale `udp://239.255.0.1:13000`
(porte `13020`, `13030`, `13040` per i canali aggiunti). Il pannello usa TTL 1
se il profilo non specifica un TTL. Su un PC della stessa LAN, aprire in VLC
`udp://@239.255.0.1:13000`; il firewall del PC deve consentire la ricezione UDP.
I profili già salvati mantengono il proprio URL di uscita finché non viene cambiato.
Il multicast non attraversa Internet o altre sottoreti senza una rete predisposta.
Lo stato UDP del pannello conferma l'invio locale, non la ricezione del player.

Per arrestare il servizio da un altro terminale:

```sh
docker stop --timeout 15 obe-webpanel
```

Il servizio arresta anche il proprio encoder. Il profilo salvato rimane in
`webpanel/.state/`, ignorata da Git. Di default, riavviare il servizio non avvia l'encoding; la ripresa automatica
richiede l'opzione esplicita nel profilo.

Per usare il browser del proprio PC verso il server encoder, aprire un tunnel SSH
con l'alias già configurato:

```sh
ssh -N -L 18088:127.0.0.1:18088 encoder-server
```

Poi visitare lo stesso URL locale. L'accesso si basa sulla macchina locale,
sull'autenticazione SSH o sulla LAN fidata abilitata esplicitamente:
**non pubblicare il servizio su Internet**. Non dispone
di ruoli multiutente; login locale e TLS sono opzionali. I controlli Host/Origin e le richieste
JSON impediscono alle normali pagine di altri siti di inviare comandi al pannello;
non sostituiscono l'autenticazione per un servizio pubblico.

## Flusso operativo

Il pannello ha tre sezioni: **Controllo** per stato e anteprima,
**Impostazioni** per connessioni, codifica, verifica ingresso e avvio/arresto,
**Diagnostica** per allarmi,
storico e registro eventi. In Impostazioni, le schede **Ingresso**, **Codifica**,
**Audio**, **TS**, **Uscite** e **Gestione** riducono lo scorrimento; i comandi
di salvataggio e avvio restano comuni a tutte. Un errore di validazione apre
automaticamente la scheda interessata.
In **Codifica** si regolano GOP, AQ, curva del bitrate, limiti QP, crominanza,
predizione, deblocking e analisi x264. **Automatico**, **Dal preset** o un campo
vuoto lasciano il valore del motore; 0 è un override esplicito. I valori del
rapporto P/B richiedono MBtree disattivato. Gli offset deblocking e le intensità
psy vanno inseriti a coppie. L’ingresso interlacciato non supporta la predizione
pesata P. La modalità **Qualità fissa** applica ricerca del movimento, Subme,
Trellis, partizioni e intensità psy selezionate; nella modalità adattiva
speedcontrol può cambiarle durante la codifica. Qualità fissa può far crescere
la coda se la CPU non tiene il passo. La correzione HRD del fork va valutata
separatamente prima di interpretare variazioni di qualità e conformità VBV.
L'anteprima resta visibile in Controllo, accanto allo stato su desktop e subito
sotto sugli schermi piccoli; **Attiva anteprima** la abilita durante la codifica.
I comandi **Verifica ingresso**, **Avvia encoder** e **Arresta encoder** sono
in alto in Impostazioni; il comando di arresto appare mentre il canale è attivo,
anche durante un recupero automatico.
Il selettore del canale resta sempre disponibile; creazione ed eliminazione
sono in **Gestisci**.

1. Impostare URL di ingresso, destinazione UDP e parametri di codifica nelle
   rispettive schede di Impostazioni.
2. Salvare la configurazione. Da fermo, **Salva** conserva il profilo senza
   avviare l'encoder.
3. Verificare l'ingresso: OBE rileva risoluzione, scansione, cadenza e audio.
   Il probe non avvia l'encoding e richiede una sorgente disponibile.
4. Scegliere in **Impostazioni → Gestione → Recupero automatico** se abilitare il recupero automatico
   e salvare la scelta per il canale; il valore predefinito è manuale. Avviare. Il pannello mostra la conferma CLI, lo stato di ricezione o
   riconnessione e i log del processo gestito.
5. Durante la codifica, le modifiche alle sole destinazioni si applicano con
   **Applica uscite**, mantenendo attive la codifica e le uscite invariate.
   Per ingresso, codifica o mux, **Applica e riavvia** salva, arresta e riavvia automaticamente
   il solo canale selezionato. Il flusso subisce una breve interruzione;
   recupero automatico e credenziali salvate vengono mantenuti. La preview
   va riattivata dopo il riavvio. **Arresta encoder** annulla anche il riavvio
   in corso, mantenendo il nuovo profilo salvato.

La validazione e il salvataggio precedono l'arresto: errori nei parametri,
conflitti di porte con altri canali o un disco non scrivibile lasciano la
codifica attuale in esecuzione e la bozza disponibile nel form. Se invece
fallisce il nuovo avvio, la configurazione resta salvata e il pannello
segnala l'errore: correggerla e riprovare con **Avvia encoder**.

Input: protocolli di rete supportati da OBE, inclusi UDP/RTP e SRT. Output:
MPEG-TS CBR su UDP, RTP e SRT, AVC configurabile e audio MP2/AAC/AC3/EAC3
quando la sorgente contiene audio.
Formato, campi e colore provengono dalla sorgente: nessuno scaling o conversione
PAL→1080i è implicito. I limiti del [lettore URL](input-url.md) restano validi.

Gli indirizzi `127.0.0.1` di ingresso e uscita sono interni al container.
Per un contributo esterno usare un URL raggiungibile dal container. Un ingresso
UDP listener dalla LAN richiede una pubblicazione UDP Docker esplicita; non è
attivata dal launcher. Nessun inoltro sul router viene predisposto.

Per l'ingresso di rete il pannello compone l'URL da protocollo, host e porta.
Mostra percorso per HTTP(S), RTSP e RTMP, porta locale per UDP/RTP, modalità e
latenza per SRT. «URL e opzioni avanzate» conserva gli indirizzi già salvati e
permette di modificarli direttamente. La porta locale `0` lascia scegliere una
porta disponibile al sistema; gli altri parametri non devono ripetere modalità,
latenza o porta locale, che hanno campi dedicati. Per RTMP indicare sia app sia
nome del flusso nel percorso, ad esempio `/live/stream`.

Le credenziali si salvano insieme alla configurazione, separatamente per ogni
canale. Per SRT usare il campo passphrase; per HTTP/HTTPS/RTSP usare utente,
password e gli eventuali parametri URL riservati, ad esempio `token=valore`.
Per HTTP/HTTPS/RTSP, lasciare un campo vuoto mantiene il valore salvato; la casella
di rimozione lo cancella al salvataggio. Con login attivo, la passphrase SRT
salvata appare direttamente nel suo campo: modificarla o svuotarla e salvare
la aggiorna o rimuove. Cambiando
protocollo, host o porta della sorgente, le
vecchie credenziali vengono rimosse per non inviarle a una destinazione diversa.

Probe, avvio, Stop, crash e riavvio del pannello riusano il salvataggio: non serve
reinserire password o passphrase per ogni sessione. Di default l'encoder rimane fermo dopo un riavvio del pannello. I segreti sono nel file locale del profilo con permessi
0600, in una directory 0700 esclusa da Git; **non sono cifrati sul disco**.
Proteggere anche i backup e l'accesso al pannello; login e TLS sono configurabili.

SRT usa l'ambiente `OBE_SRT_PASSPHRASE`; l'URL autenticato usa `OBE_INPUT_URL`.
Il core deve includere il supporto a quest'ultima variabile. Credenziali e token
non compaiono nella cronologia CLI, negli eventi o negli export. Gli URL dei
campi principali restano senza credenziali: inserire i valori nei campi dedicati.
Le credenziali delle vecchie sessioni, mai salvate, vanno inserite una prima volta.

## Stato e misure

Il [monitoraggio della consegna](delivery-monitoring.md) aggiunge il ritorno
decodificato, la misura passiva di latenza video fra playout e ricevitore e
il controllo del selettore MAIN/BACKUP. Si configura separatamente nelle Uscite;
un ricevitore configurato ma non verificato impedisce lo stato «Flusso regolare».

La pagina **Controllo** riunisce stato del flusso, anteprima, statistiche delle
uscite e ultimi problemi. Il riepilogo distingue canale fermo, recupero, errore
e misure scadute: «Flusso regolare» richiede ingresso presente, frame in
avanzamento alla cadenza attesa, uscite abilitate in trasmissione e allarmi
aggiornati senza segnalazioni. Non conferma la ricezione UDP sul destinatario.
Un filtro VapourSynth configurato ma non attivo impedisce l'indicazione regolare.

Sei grafici mostrano bitrate AVC/UDP, fps, CPU sistema, RAM, carico a 1/5/15 minuti
e RX/TX dell'interfaccia selezionata. Intervallo di 2 o 30 minuti, aggiornamento
automatico e pulsante Aggiorna. Le risorse del sistema sono viste dal contenitore;
CPU e RAM del processo encoder sono separate dai valori di sistema. Il grafico
di rete comprende tutto il traffico dell'interfaccia, non soltanto il canale.
Le interruzioni delle misure e i cambi di processo spezzano le linee; i valori
mancanti restano sconosciuti. Errori e scarti nella tabella uscite sono cumulativi.

Lo storico conserva fino a 1800 campioni per canale anche a encoder fermo, circa
30 minuti con campionamento regolare. Dopo l'aggiornamento i grafici delle risorse
si popolano con i nuovi campioni; quelli precedenti non sono ricostruiti.

Le misure CPU e RSS riguardano il processo OBE; 100% CPU equivale a un core.
Un processo appena avviato può non avere ancora campioni disponibili. Il tempo
indica la durata del processo, non una certificazione di continuità del servizio.
La sezione **Telemetria del flusso** mostra bitrate AVC e UDP misurati, frame/s,
frame codificati, coda video ed errori locali di invio. Questi valori provengono
da contatori del motore OBE e sono distinti dai bitrate configurati. Su 1080i50,
25 frame/s corrispondono ai 50 semiquadri/s. Il bitrate UDP riguarda il payload
MPEG-TS accettato dal socket locale: non certifica la ricezione a destinazione.
Le statistiche SRT hanno una sezione dedicata e la preview video è disponibile
su richiesta. I livelli audio restano fuori da questa versione.

Il campionamento avviene una volta al secondo e il browser si aggiorna ogni due.
I rate richiedono due campioni; i valori mancanti restano vuoti. Dopo cinque
secondi senza campioni validi, le misure scadono e vengono oscurate. Un vecchio
binario senza `show telemetry` continua a codificare e indica telemetria del
flusso non disponibile. Per abilitarla serve ricompilare anche `obe-rt` dal
commit fissato in `build.sh`; la sola modifica del pannello non basta.
Il primo commit del core che espone questi contatori è `ee5f5a5`.
Ogni canale dispone di processo, recupero, monitoraggio e database SQLite
indipendenti. Storico, allarmi ed esportazioni seguono il canale selezionato;
un canale in attesa di riavvio mantiene riservate le proprie porte.

I log sono limitati e mantenuti in memoria. La riconnessione della sorgente nel
lettore OBE mantiene lo stesso processo ed è distinta dal recupero dopo crash.

## Recupero dopo crash

L'opzione **Recupero automatico** si salva nella configurazione del canale con
**Salva**, oppure con **Applica e riavvia** se il canale è attivo. La scelta resta
disponibile dopo il ricaricamento del pannello e ai successivi avvii.
Consente per default tre riavvii con attese di 2, 4 e 8
secondi. Si attiva solo dopo la prima conferma di codifica: gli errori iniziali
restano visibili e richiedono una correzione. Il budget vale per tutta la sessione,
anche se un tentativo riesce, e si azzera con un nuovo avvio manuale.

Durante l'attesa la panoramica mostra il prossimo tentativo e consente **Arresta
encoder**, che cancella i retry anche senza processo vivo. Probe e start
rimangono bloccati fino allo Stop o alla fine del recupero; **Applica e riavvia**
consente di salvare modifiche e sostituire il tentativo in attesa. Al riavvio del
pannello, il canale riparte soltanto se era attivo ed è abilitata la relativa
opzione di ripresa.

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

Recupero, monitoraggio, multicanale e preview sono integrati su `master`.
Il servizio unico è `obe-webpanel`, accessibile localmente su porta **18088**.
Il collaudo di 24 ore e il riaggancio dei ricevitori reali restano da completare.

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

Restano da implementare i ruoli multiutente; i misuratori audio sono disponibili nella versione attuale;
il funzionamento prolungato richiede un collaudo dedicato. Preview video, account locale e più encoder sono ora disponibili. Contratto API:
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

La Diagnostica mostra subito stato, allarmi e grafici di bitrate, frame/s e CPU;
con una sorgente SRT mostra anche l'andamento RTT. Per i grafici storici si può
scegliere fra 2 e 30 minuti. Tabella, contatori e cronologia restano nei dettagli
tecnici; la tabella mostra gli ultimi 20 campioni. I download
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

Per rimuovere un canale secondario, selezionarlo e premere **Stop** se è attivo
o in recupero. Salvare o ripristinare eventuali modifiche, quindi premere
**Gestisci → Elimina canale** e confermare: vengono eliminati configurazione, credenziali
salvate e storico del canale. Il pannello torna al canale principale, che non
può essere eliminato. Lo slot liberato è riutilizzabile con **Nuovo canale**.
Se un altro operatore modifica o ricrea il canale, la conferma scade e va riaperta.
**Gestisci → Rinomina canale** aggiorna il nome del canale selezionato, incluso
quello principale, senza fermare la codifica. Salvare o ripristinare prima una
bozza eventualmente aperta; il nome viene aggiornato anche nel profilo.

`python3 tests/webpanel.py` include isolamento, persistenza, limite e conflitti.
`docker exec obe-webpanel python3 tests/webpanel-multichannel-live.py`
verifica due encoder reali contemporanei PAL/1080i, stop indipendente e decodifica.
Usare esclusivamente un servizio di collaudo fermo: il test salva e ripristina
i profili e crea il secondo canale se manca.

### Preview JPEG di compatibilità

La modalità snapshot dell’anteprima avvia FFmpeg solo per il canale selezionato. Riceve l'uscita
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
`docker exec obe-webpanel python3 tests/webpanel-preview-live.py`.
Quest'ultimo usa solo HD sintetico e porte locali; ripristina il profilo iniziale.

### Pannello unificato

Il checkout `master` usa le revisioni del core e delle librerie fissate in
`build.sh` e `sources-manifest.txt`. Il container `obe-webpanel` espone di default
`127.0.0.1:18088`, con accesso LAN opzionale tramite `OBE_PANEL_LAN_IP`;
profili e storico sono in `webpanel/.state/`, esclusa da Git.
Nessun encoder viene avviato automaticamente dopo il riavvio del servizio.

Per il browser remoto: `ssh -N -L 18088:127.0.0.1:18088 encoder-server`.
Per fermare il pannello: `docker stop --timeout 15 obe-webpanel`;
per avviarlo usare `./webpanel/run.sh` dalla radice del checkout.

La revisione integrata 0.5 aggiunge prove per lo storico separato dei canali,
la sua riapertura dopo un riavvio e le porte riservate durante il recupero.
Il 22 ottobre 2026 sono passati i 35 test API/processo e i 17 test del monitor,
la prova simultanea PAL/1080i50 e il recupero reale dopo SIGKILL in HD.
La prova SRT cifrata locale ha recuperato 412/412 pacchetti scartati dal proxy,
senza scarti tardivi, con storico e rientro degli allarmi verificati.
Queste prove non sostituiscono il collaudo prolungato o quello SRT su Internet.

Vedi anche [gateway MPEG-TS remoto](ssh-contribution-gateway.md) e
[collaudo HD con CSV/JSON](hd-pilot.md). UI verificata in Chromium a 390 e
1440 px, cambio/creazione canale, fallback per URL inesistente e JPEG HD reale,
senza overflow o errori console. Le immagini vengono inserite solo dopo il
caricamento: risolta la segnalazione del controllo design, senza soppressioni.

### Preview video/audio 0.9.1

Il pulsante Attiva anteprima usa Media Source Extensions quando il browser
supporta AVC/AAC; altrimenti usa le immagini JPEG. Un processo FFmpeg separato
deinterlaccia il monitor a 50 fps e produce frammenti MP4 da circa mezzo secondo.
La cache server conserva al massimo otto frammenti / 8 MiB; il browser conserva
pochi secondi e si riallinea al live dopo un ritardo. Nessun client lento blocca
l'encoder. Arrestare la preview lascia il processo OBE in esecuzione; Stop o
Applica chiudono entrambi i monitor. La preview consuma CPU e banda aggiuntive
(circa 1 Mbit/s per browser), non è WebRTC e non sostituisce l'analisi del TS.

`tests/webpanel-live-preview.py` verifica su una sorgente sintetica 1080i il
risultato 640×360p50 con AAC 48 kHz stereo, la decodifica, l'indipendenza dell'encoder,
lo stop, il ritorno al JPEG e la chiusura della preview allo stop del canale.

### Tracce audio

La scheda Audio permette di aggiungere fino a 8 tracce con uscita stereo a 48 kHz, scegliere
la sorgente rilevata e impostare PID di uscita, lingua ISO 639 (tre lettere) e tipo
audio per ciascuna. Ogni traccia può essere disabilitata o rimossa; nessuna traccia
abilitata produce un’uscita solo video. Codec, bitrate, guadagno e offset sono comuni.
Il mux deve coprire massimo VBV + somma dei bitrate audio abilitati + 128 kb/s.
PID automatici evitano quelli espliciti di video, PCR, PMT e altre tracce.

L’ingresso URL decodifica separatamente le tracce selezionate, mantenendo i PID
rilevati durante le riconnessioni anche se cambia l’ordine di discovery. Una
sorgente esplicitamente selezionata ma assente impedisce l’avvio. I profili
precedenti conservano la selezione automatica della prima traccia e l’ingresso
senza audio resta supportato. Su SDI le sorgenti corrispondono alle coppie rilevate;
il collaudo hardware resta distinto dalle prove di rete.

Verifica isolata: `python3 tests/webpanel-audio.py`; nel builder esistente,
`python3 tests/webpanel-audio-live.py --artifacts tests/.build/audio-live-NOME`.
La prova reale controlla toni indipendenti, PID, lingue, timestamp, esclusione delle
tracce e ripresa dopo interruzione con ordine dei PID in ingresso invertito.

### Misuratori, conversione e normalizzazione audio

Controllo e Impostazioni → Audio mostrano per ciascuna traccia abilitata le barre
L/R, picco e RMS in dBFS e l’indicatore CLIP. Le misure provengono dal PCM dopo
il guadagno e l’eventuale normalizzazione, prima dell’encoder audio. Il picco è
il massimo della finestra fra interrogazioni (circa un secondo), l’RMS è calcolato
sugli stessi campioni. Sono sample peak, non misure true peak né LUFS. I dati
più vecchi di 1,5 secondi, lo stop e la disconnessione mostrano valori assenti.

Gli ingressi URL accettano mono, stereo e 5.1 a 8–192 kHz: il lettore converte
automaticamente in stereo 48 kHz. Il downmix Lo/Ro include centro e surround
a −3 dB, esclude LFE e normalizza i coefficienti per lasciare margine. Il layout
5.1 deve essere dichiarato; SDI continua a usare le coppie PCM rilevate.

La normalizzazione, disattivata per default, usa `loudnorm` di FFmpeg in modalità
dinamica EBU R128. Controlli comuni: obiettivo −70…−5 LUFS (default −23), limite
true peak −9…0 dBTP (default −1) e dinamica obiettivo 1…50 LU (default 7).
Ogni traccia mantiene uno stato di analisi indipendente con gli stessi parametri.
Il guadagno manuale, quando presente, precede la normalizzazione in virgola mobile.
La catena aggiunge circa tre secondi di buffer; conserva i timestamp dei campioni,
non aggiunge automaticamente un offset audio. I profili precedenti restano in bypass.
La normalizzazione è adattiva: il livello istantaneo non coincide sempre col target.
Il codec può introdurre overshoot dopo il limitatore: usare ulteriore margine se
richiesto dalla rete TV e misurare anche il flusso finale.

Prove: `tests/audio-meters.c`, `tests/audio-loudness.c`,
`python3 tests/webpanel-audio-processing.py` e, nel builder già presente,
`python3 tests/webpanel-audio-processing-live.py --artifacts tests/.build/audio-processing-NOME`.
Il collaudo reale include mono 44,1 kHz, stereo 96 kHz, downmix 5.1 a 44,1 kHz
con toni distinti, normalizzazione misurata sul flusso codificato, clipping e
riconnessione di due tracce normalizzate con ordine dei PID invertito.
Il collaudo SDI fisico e la validazione prolungata sui ricevitori TV restano aperti.
