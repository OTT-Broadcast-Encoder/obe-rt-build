# Gateway MPEG-TS via SSH

`gateway/ts_ssh.py` legge un ingresso UDP esistente su un server remoto mediante
`/usr/bin/srt-live-transmit … file://con` e inoltra MPEG-TS all'encoder di prova
locale. Non modifica il playout, non installa programmi sul server, non pubblica
porte Docker e non modifica router. La tratta Internet usa **SSH su TCP**;
non è una prova di SRT Internet e non permette di misurare perdite o jitter UDP.

## Configurazione

Serve SSH già autenticato con una chiave o con una connessione multiplex,
con chiave host verificata. Il gateway usa BatchMode e StrictHostKeyChecking:
non chiede né memorizza password. `--known-hosts` può indicare il file di chiavi
host già verificato; `--control-socket` una sessione SSH già autenticata.

Per il canale **Contribuzione remota** nel container di test impostare:

- Ingresso `udp://0.0.0.0:12030` (ascolto nel container).
- Uscita `udp://127.0.0.1:13030` per la preview locale.
- Profilo SD se il contributo è PAL; il profilo HD non effettua upscaling.

Avviare sullo stesso host Docker:

```sh
python3 gateway/ts_ssh.py --host operator@contribution.example \
  --source udp://239.255.0.1:5000 --container obe-webpanel \
  --udp-port 12030 --seconds 60 --max-mbps 20 \
  --report tests/.build/remote-contribution.json
```

Se si usa un socket esistente, aggiungere `--control-socket /percorso/socket`.
Il gateway legge l'IP del container mediante `docker inspect`; sono accettati
solo container locali con un unico indirizzo IPv4 privato di bridge. Non apre
alcuna porta verso Internet. Senza `--container` invia soltanto su loopback host.
Il nome del container e l'URL UDP sono validati; nessuna shell locale è usata.

Con il relay attivo: **Verifica ingresso**, poi **Avvia encoder** sul canale
salvato. La sorgente deve essere presente per entrambe le operazioni. La preview
è facoltativa. Gli altri canali rimangono indipendenti.

## Durata, recupero e limiti

Durata predefinita 60 s, massima 24 ore; Ctrl-C o SIGTERM fermano anche SSH.
Un EOF SSH o 10 s senza dati provocano una nuova connessione, con attese 2, 4,
8…30 s, massimo cinque tentativi complessivi (`--retries`, massimo 10).
Il limite `--max-mbps` interrompe se la media ricevuta in una finestra di almeno
5 s supera il budget; **non è uno shaper**. Scegliere prima un contributo con
bitrate compatibile. Il relay conserva solo un buffer limitato, ricompone
pacchetti TS da 188 byte e invia datagrammi da 1316 byte.

Il rapporto distingue byte ricevuti/inoltrati, retry e byte scartati per
riallineamento. Gli scarti di framing non misurano la perdita Internet.
TCP può ritardare il video durante le ritrasmissioni: non garantisce la latenza
limitata di SRT. Il relay non modifica codec, risoluzione, audio o timestamp;
un audio incompatibile richiede un gateway di adattamento separato.

Test locale: `python3 tests/ts-ssh-gateway.py` (letture parziali, riallineamento,
forward UDP, retry limitati). Il collaudo remoto è esplicito e richiede un
canale di test già creato e fermo:

```sh
python3 tests/webpanel-remote-live.py --host operator@contribution.example \
  --source udp://239.255.0.1:5000 --control-socket /percorso/socket
```

Usa il servizio di test 18088, canale `channel-3` e container
`obe-webpanel`, con durata 60 s. Ripristina il profilo iniziale e ferma
soltanto encoder e preview del canale di prova. Non eseguirlo su un canale in onda.

## Risultato e requisito del core

Il 21 ottobre 2026: relay di 60,001 s, 9.735.768 byte inoltrati (1,2981 Mbit/s),
7.398 datagrammi, nessun retry o scarto di framing. Sorgente PAL 720×576i25,
MP2 stereo 48 kHz; encoding e preview dell'uscita verificati. Il campione di
telemetria dopo l'avvio mostra 24,99 frame/s, mux 6,504 Mbit/s e zero errori UDP.
Artefatti: `tests/.build/remote-panel-bzour1u9/`.

È servita la correzione core `d318854` (`fix/url-mid-gop`), inclusa nelle nuove
build da `patches/0018-obe-url-mid-gop.patch`: l'ingresso live può iniziare prima
dei parametri H.264 SPS/PPS. OBE ora scarta i pacchetti non decodificabili
all'inizio, entro i timeout esistenti, invece di fallire subito il probe.
Dopo il primo frame rimane attivo il normale percorso di errore/riconnessione.

Regressione riproducibile: `docker exec obe-webpanel python3 tests/input-mid-gop.py`.
Il TS sintetico viene inoltrato senza remux partendo nel GOP; il test verifica
che manchino le intestazioni iniziali, poi controlla probe, encoding e decodifica.
Fallimento sulla build precedente: `tests/.build/mid-gop-68yfz6x2/`;
passaggio sul candidato: `tests/.build/mid-gop-yjljjv63/`.
