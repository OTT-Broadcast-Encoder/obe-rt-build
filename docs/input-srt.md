# Ingresso SRT e riconnessione

Il candidato abilita MPEG-TS su `srt://` attraverso FFmpeg e libSRT 1.5.7, con cifratura OpenSSL. Il 21 ottobre 2026 sono passati i test locali di ricezione cifrata come caller e listener, riconnessione e rifiuto della chiave errata. Restano validi i formati e i vincoli del [lettore URL](input-url.md): video obbligatorio, audio stereo a 48 kHz, stesso formato e stesse tracce dopo la riconnessione. SRT trasporta il flusso compresso del playout; la capacità nominale di 1 Gbit/s non determina da sola bitrate, latenza e margine necessari sulla tratta Internet.

## Collegamento e cifratura

OBE può aprire una connessione come `caller` oppure attendere il mittente come `listener`. Per esempio, l'opzione CLI di ingresso è:

```text
set input url
set input opts location=srt://127.0.0.1:10000?mode=caller&transtype=live&latency=200000
```

L'altro estremo deve usare `mode=listener`; invertire i due ruoli per fare attendere OBE. `latency` è espressa in microsecondi: 200000 corrisponde a 200 ms ed è soltanto un valore iniziale per il banco di prova. Il listener deve essere raggiungibile sulla porta UDP scelta; in presenza di NAT occorrono inoltro della porta o una rete privata. Il loopback raggiunge solo lo stesso host/container. Tra container separati usare la rete appropriata, per esempio `--network host` in un laboratorio Linux controllato.

Per cifrare il collegamento, impostare **solo nell'ambiente del processo OBE** `OBE_SRT_PASSPHRASE`, con una passphrase da 10 a 79 byte, identica a quella configurata sul mittente. OBE usa AES-256 e richiede l'accordo di cifratura tra i due estremi. Non mettere la passphrase nell'URL OBE, nella configurazione CLI o nella cronologia: le opzioni URL `passphrase`, `pbkeylen` ed `enforced_encryption` vengono rifiutate. Senza la variabile, l'ingresso è in chiaro e il mittente deve essere configurato coerentemente.

La passphrase può essere letta senza eco in Bash prima di avviare OBE:

```sh
read -r -s -p 'Passphrase SRT: ' OBE_SRT_PASSPHRASE
printf '\n'
export OBE_SRT_PASSPHRASE
./run.sh -c tests/.build/srt-demo.cfg
unset OBE_SRT_PASSPHRASE
```

La configurazione completa di encoder e mux è quella dell'[esempio URL](input-url.md), sostituendo soltanto l'URL di ingresso. Se OBE viene avviato nel container, passare la variabile già esportata con `docker run -e OBE_SRT_PASSPHRASE`, senza aggiungerne il valore alla riga di comando. Non inserire segreti nei comandi copiati o nei log del mittente. L'ambiente e gli argomenti di un processo restano accessibili a processi privilegiati sullo stesso sistema.

## Probe, interruzione e ripresa

Il probe apre una connessione SRT, rileva le tracce e la chiude; `start` ne apre una nuova. Il mittente deve quindi saper riaprire la connessione anche tra probe e avvio: un singolo processo FFmpeg che termina alla disconnessione deve essere gestito da un supervisore. Il probe iniziale è limitato nel tempo e non riprova indefinitamente: deve trovare il mittente disponibile.

Durante l'encoding la perdita della connessione usa la stessa riconnessione automatica del lettore URL, con attese di 1, 2, 4 e poi 5 secondi. Il mittente può ripartire da timestamp azzerati mantenendo PID e formato. Durante l'assenza dei dati non vengono generati nero o silenzio; alla ripresa il clock media prosegue. `quit` interrompe anche l'attesa del collegamento. Un cambiamento permanente di formato o tracce richiede un nuovo probe.

## Test ripetibile

Eseguire dalla radice del candidato, dopo aver costruito FFmpeg con libSRT e OBE:

```sh
docker run --rm --cpus=6 --memory=8g \
    --mount "type=bind,source=$PWD,target=/work" -w /work \
    obe-rt-build:srt-2026-10-21 python3 tests/input-srt.py
```

Non sono richieste nuove dipendenze Python. Il test importa gli helper di `input-url.py` e `input-url-reconnect.py`, usa sorgenti locali PAL interlacciate e la pipeline completa `SRT cifrato → OBE AVC/MP2 → MPEG-TS`. Verifica entrambe le modalità (`--case caller`, `--case listener`), riavvio del mittente con PTS azzerati, identità delle tracce, timestamp continui, decodifica, audio non silenzioso e `quit` durante l'assenza del mittente. `--case wrong-key` verifica prima un probe con chiave corretta sullo stesso endpoint, poi il rifiuto di una chiave errata entro 12 secondi, evitando di scambiare una sorgente assente per una verifica crittografica riuscita.

Il supervisore riavvia realmente FFmpeg dopo la chiusura del probe. Ogni esecuzione genera chiavi effimere: la configurazione OBE rimane in memoria, la chiave passa solo nell'ambiente OBE e stderr del mittente viene scartato. Prima di scrivere i log, il test elimina eventuali credenziali e fallisce se OBE ha esposto la chiave. Nessuna chiave viene conservata negli artefatti `tests/.build/input-srt-*`. Le righe `PASS UDP recovery` provengono dall'helper di controllo riutilizzato: in questa suite analizzano la registrazione dell'ingresso SRT.

Esiti rilevati il 21 ottobre 2026 nell'immagine `obe-rt-build:srt-2026-10-21`:

| Caso | Esito |
| --- | --- |
| Caller e listener cifrati | PASS: PAL AVC/MP2, stesso formato e stesse tracce dopo il riavvio del mittente. |
| Ripresa con PTS azzerati | PASS: PTS/DTS continui, audio/video decodificabili, audio non silenzioso; scarto tra pacchetti A/V al confine pari a 2,02 ms. |
| `quit` senza mittente | PASS: 0,03 s in entrambe le modalità. È una misura locale, non un limite garantito sulla WAN. |
| Passphrase errata | PASS: rifiuto e uscita in 3,02 s, dopo un probe riuscito con la chiave corretta sullo stesso endpoint. |

Gli artefatti caller/listener sono in `tests/.build/input-srt-0qkbhcqh`; il risultato del caso con chiave errata è in `tests/.build/srt-wrong-key.log`, con i suoi artefatti in `tests/.build/input-srt-mxnaw8xj`. La build precedente, senza SRT, falliva esplicitamente al controllo iniziale dei protocolli FFmpeg.

**Nessuna prova SRT sulla WAN è riuscita finora.** La raggiungibilità del listener attraverso il NAT/gateway resta da risolvere e verificare: i risultati sopra provengono dal loopback nello stesso container. Restano da misurare perdita/jitter, latenza e margine di bitrate sulla tratta reale, oltre alla qualità su contenuti reali e alla stabilità prolungata. Il nativo 1080i50 ha un [collaudo locale dedicato](1080i-validation.md). I file TS live possono terminare con un PES incompleto: il controllo analizza l'intervallo audio/video completo dopo la ripresa.
