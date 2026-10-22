# Ingresso video da URL

Il modulo `lavf_input` riceve una sorgente di rete, decodifica video e audio tramite FFmpeg e li inoltra all'encoder e al mux MPEG-TS di OBE. Non richiede SDI né un file di ingresso. Questo primo incremento serve al banco di prova locale; il percorso Internet con SRT resta da implementare e collaudare.

## Ambito attuale

| Elemento | Comportamento |
| --- | --- |
| Trasporto di riferimento | MPEG-TS su `udp://` oppure MPEG-TS incapsulato in RTP su `rtp://`; i casi automatici sono descritti sotto. RTP non significa supporto già verificato di ogni payload RTP. |
| Altri URL ammessi | `http://`, `https://`, `rtsp://`, `tcp://`; richiedono protocolli/demuxer presenti nella build FFmpeg e un collaudo specifico. Nessuna dichiarazione di interoperabilità con tutti i server o flussi. |
| SRT e file | SRT non è abilitato nella build attuale. Percorsi locali e `file://` non sono accettati dal modulo. |
| Selezione | Un video e al massimo un audio, scelti automaticamente da FFmpeg; selezione manuale di programma/PID/traccia non ancora esposta. Il video è obbligatorio, l'audio opzionale. |
| Video | SDR YUV planare 4:2:0 o 4:2:2 a 8/10 bit; PAL interlacciato, NTSC interlacciato, 1080i50/59,94/60 e progressivo fino a 1920×1080 e 60 fotogrammi/s. HDR PQ/HLG e altri formati vengono rifiutati. Questa è la matrice accettata dal codice, non una certificazione di tutti i casi. |
| Audio | Se presente, deve essere stereo a 48 kHz; conversione interna in PCM planare. Non sono implementati ricampionamento da altre frequenze e downmix multicanale. |
| Timestamp e metadati | Origine temporale comune audio/video nel clock OBE; cadenza, SAR, ordine dei campi e colorimetria trasmessi alla pipeline. PTS assenti, non crescenti o con salti oltre due secondi durante la ricezione fanno riaprire la sorgente. |
| Interruzioni | Riconnessione automatica durante l'esecuzione dopo EOF, timeout, errori di rete/decode o timestamp invalidi. Attese progressive di 1, 2, 4 e poi 5 secondi, senza limite di tentativi. Cambi di formato, esaurimento memoria, code oltre soglia ed errori permanenti di configurazione fermano l'ingresso con un messaggio. |

Il probe iniziale non ritenta: la sorgente deve essere disponibile per rilevare il formato e configurare la pipeline. Il probe apre il flusso e l'avvio lo apre nuovamente. Dopo un probe riuscito, anche gli errori transitori di apertura all'avvio vengono ritentati. Non cambiare formato tra probe e avvio.

Alla ripresa sono richiesti gli stessi PID/tracce e lo stesso formato rilevati dal probe; una traccia temporaneamente assente viene cercata nei tentativi successivi. Un cambio permanente dei PID richiede una nuova configurazione. Un cambio di risoluzione, cadenza, campi, colore o formato audio richiede un nuovo probe: il lettore si ferma, mentre la CLI rimane disponibile per `quit` e un nuovo avvio.

Durante l'assenza del segnale l'uscita si sospende dopo i dati già accodati; non vengono generati nero o silenzio. Alla riconnessione il video prosegue dal fotogramma successivo sulla timeline media; l'audio usa lo stesso offset, scartando eventuali blocchi sovrapposti a quelli già consegnati. Funziona anche se il mittente azzera i timestamp. Non è una commutazione senza perdita: al confine possono mancare campioni e fotogrammi. L'attesa torna a un secondo dopo la ricezione di tutte le tracce e almeno un secondo di media.

Ogni apertura ha un limite di cinque secondi e ogni lettura di due; il lettore controlla anche che una traccia non sparisca mentre l'altra continua per oltre due secondi. Dopo una riconnessione, il primo audio ha fino a cinque secondi per superare eventuali blocchi già consegnati prima dell'interruzione. Se sparisce soltanto l'audio, alla ripresa è ammesso il salto necessario a riallinearlo al video proseguito nel frattempo. `quit` interrompe anche l'attesa tra i tentativi. I log distinguono perdita della sorgente, numero e attesa del tentativo, ripresa effettiva di audio/video ed errore terminale.

I limiti di arresto sono verificati con indirizzi IP numerici. La risoluzione DNS sincrona di FFmpeg può superare i timeout indicati. `quit` interrompe la pipeline live senza svuotare tutti i buffer del mux: il file registrato può terminare con un PES incompleto e una coda video più lunga di quella audio. Le misure di sincronismo e qualità vanno eseguite sull'intervallo comune completo.

## Prova locale senza file di ingresso

Eseguire i comandi dalla radice del repository, dopo la build, in due terminali Linux. I container usano `--network host`, quindi `127.0.0.1` identifica la stessa rete dell'host e raggiunge anche il mittente nell'altro container. Senza questa opzione, ogni container ha il proprio loopback: `127.0.0.1` non raggiunge l'altro container o il playout remoto.

Nel primo terminale, avviare una sorgente sintetica PAL con campi TFF da istanti distinti a 50 Hz e tono stereo a 48 kHz:

```sh
docker run --rm -it --cpus=6 --memory=8g --network host \
    --mount "type=bind,source=$PWD,target=/work" \
    -e LD_LIBRARY_PATH=/work/target-root/usr/local/lib:/work/NDI/sdk/lib/x86_64-linux-gnu \
    obe-rt-build:current-2026-10-21 \
    /work/target-root/usr/local/bin/ffmpeg -hide_banner \
    -re -f lavfi -i 'testsrc2=size=720x576:rate=50' \
    -re -f lavfi -i 'sine=frequency=997:sample_rate=48000' \
    -vf 'interlace=scan=tff:lowpass=0,setsar=16/15' \
    -c:v mpeg2video -q:v 4 -g 12 -bf 0 -threads 2 \
    -flags +ilme+ildct -pix_fmt yuv420p \
    -color_primaries smpte170m -color_trc smpte170m -colorspace smpte170m \
    -c:a mp2 -b:a 192k -ac 2 \
    -f mpegts 'udp://127.0.0.1:10000?pkt_size=1316'
```

Nel secondo terminale creare la configurazione di OBE. Il file di uscita è un artefatto da analizzare; l'ingresso resta l'URL live.

```sh
mkdir -p tests/.build
cat > tests/.build/url-demo.cfg <<'EOF'
set obe opts system-type=generic,max-probe-time=8
set input url
set input opts location=udp://127.0.0.1:10000
probe input
set stream opts 0:format=avc,preset=ultrafast,tune=zerolatency,bitrate=5000,vbv-maxrate=5000,vbv-bufsize=5000,thread=2,bframes=0,keyint=25,rc-lookahead=0
set stream opts 1:format=mp2,bitrate=192,channel-map=stereo
set muxer mpegts
set muxer opts ts-muxrate=6500000,cbr=1,ts-type=dvb
set outputs 1
set output opts 0:type=filets,target=/work/tests/.build/url-demo.ts
start
EOF
docker run --rm -it --cpus=6 --memory=8g --network host \
    --mount "type=bind,source=$PWD,target=/work" \
    obe-rt-build:current-2026-10-21 ./run.sh -c tests/.build/url-demo.cfg
```

Scrivere `quit` nella CLI per terminare e fermare il generatore con Ctrl+C. La configurazione usa gli ID 0 e 1 del primo probe in un processo nuovo, come i test automatici. Per sorgenti senza audio eliminare la riga `set stream opts 1:...`. `thread=2` è il nome dell'opzione della CLI OBE; `threads` è invece un'opzione di FFmpeg.

Per generare 1080i50, sostituire nel mittente `720x576` con `1920x1080`, `setsar=16/15` con `setsar=1/1` e i tre valori `smpte170m` con `bt709`. OBE rileva il formato al probe. Per MPEG-TS su RTP, usare `-f rtp_mpegts` nel mittente e sostituire `udp://` con `rtp://` nei due URL; prevedere anche la porta successiva per RTCP.

Preset `ultrafast`, bitrate e muxrate dell'esempio servono alla verifica funzionale. Non sono il profilo finale di qualità/compressione né un dimensionamento della contribuzione Internet.

## Test ripetibile

Il test usa un generatore locale e la pipeline completa `URL → OBE AVC/MP2 → MPEG-TS`, conserva configurazione, sorgente, uscita e log in `tests/.build/input-url-*` e non installa dipendenze:

```sh
docker run --rm --cpus=6 --memory=8g \
    --mount "type=bind,source=$PWD,target=/work" \
    obe-rt-build:current-2026-10-21 python3 tests/input-url.py
```

Mittente e ricevitore del test sono nello stesso container: qui non serve `--network host`. Per un solo caso aggiungere, per esempio, `--case udp-1080i50`.

I casi superati il 21 ottobre 2026 sono PAL su UDP e RTP, 1080i50 su UDP e AVC/AAC su UDP, incluso riordino dei B-frame. Il test controlla decodificabilità dell'uscita, formato, SAR, colorimetria, flag TFF, audio non silenzioso, cadenza PTS/DTS e allineamento iniziale audio/video sull’intervallo comune, arresto dopo la chiusura del mittente e probe di un URL senza dati. Un esito `PASS` non sostituisce la verifica visiva e temporale dei singoli campi, il benchmark di qualità o un collaudo prolungato sul playout.

La riconnessione ha un test dedicato: interrompe il mittente UDP, lo riavvia sullo stesso URL con timestamp azzerati e verifica ripresa di entrambe le tracce, PTS/DTS crescenti, identità degli stream, decodificabilità e audio non silenzioso. Controlla anche `quit` durante l'assenza del segnale e durante l'attesa tra i tentativi:

```sh
docker run --rm --cpus=6 --memory=8g \
    --mount "type=bind,source=$PWD,target=/work" \
    obe-rt-build:current-2026-10-21 python3 tests/input-url-reconnect.py
```

Gli artefatti sono in `tests/.build/url-reconnect-*`; `--case repeat` verifica tre ripartenze nello stesso processo e la continuità dei timestamp, `--case resume` una ripartenza, `--case silent` e `--case backoff` l'arresto senza sorgente e durante l'attesa. I casi `--case track-loss` e `--case video-loss` filtrano per 3,2 secondi soltanto il PID audio o video, lasciando proseguire l'altra traccia, e controllano il recupero di entrambe.

Rimangono da collaudare il collegamento reale, perdita/jitter prolungati, matrice completa dei formati e gli altri protocolli. Per Internet, l'accesso a 1 Gbit/s non equivale a una LAN: la prossima fase è abilitare e provare SRT con contribuzione compressa e margine misurato per rete e CPU.

Il buffer di ricezione richiesto di default è 4 MiB: in RTP evita la perdita del burst iniziale mentre i pacchetti del probe vengono consumati al ritmo video. Il kernel può applicare un limite inferiore; nessun parametro dell’host viene modificato automaticamente. Nei test su IP numerico, `quit` durante assenza o riconnessione ha richiesto meno di cinque secondi; il probe UDP senza dati circa due secondi con `max-probe-time=2`.

La build include `patches/0011-obe-network-input.patch` e `patches/0012-obe-url-reconnect.patch`, applicate dopo la patch `0008` sulla revisione OBE fissata. `tests/smoke-current.sh` verifica anche blocchi audio URL da 2048/4096 campioni e PTS senza compensazioni hardware. Sono stati corretti l’inizializzazione delle statistiche del mux con libltntstools corrente e il rilascio del mutex della coda in caso di allocazione fallita.
