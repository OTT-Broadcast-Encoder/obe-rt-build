# Attività — banco di prova encoding e input IP

Riferimento: [roadmap](plan.md). T03 completato; T04/T05/T06 parziali con URL e SRT cifrato collaudati localmente. T07 supera il nativo 1080i50 TFF/BFF; conversioni e durata restano aperte. T02 dispone di misure SSH/TCP, mentre UDP/SRT Internet attende l'inoltro sul gateway. Priorità confermata: qualità/compressione, 1080i e ingresso URL per test senza SDI. I file sono riferimenti per le misure; il lettore file diretto è opzionale. Non sono state aperte issue da questo documento.

Primo incremento: T04 → T05 su URL UDP/RTP locale, usando le dipendenze già disponibili. T03 abilita SRT, necessario insieme a T05 per T06. I campioni sintetici consentono di iniziare senza attendere il collegamento al playout; T01 e T02 completano i riferimenti e le condizioni reali.

S = 1–2 file; M = 3–5 file. I percorsi indicati per nuovi moduli e strumenti sono proposte, da confermare con le convenzioni della repository. Suddividere ulteriormente una voce se richiede più di cinque file o più interventi indipendenti.

## T01 — Definire riferimento e corpus [M0]

Raccogliere il profilo PAL effettivo e sorgenti native PAL, 1080i50 e 1080p50, con materiale rappresentativo e pattern temporali.
- [ ] Manifest con hash, formato, cadenza/campi, colore e audio; file multimediali conservati fuori da Git.
- [ ] Profilo attuale confrontato con gli header e con i campioni decodificati; vincoli HD del ricevitore e budget di bitrate/latency/risorse registrati, con i campi ancora da misurare espliciti.
- Verifica: analisi del bitstream, pattern di movimento e confronto con il report della redazione; nessuna equivalenza dedotta dalla sola etichetta PAL.
- Dipendenze: campioni del playout e configurazione della consegna. Dimensione: S.
- File probabili, obe-rt-build: `tests/encoding/corpus.json`, `docs/encoding-baseline.md`.

## T02 — Misurare la contribuzione Internet [M0]

Determinare capacità utile e condizioni del percorso Uno4 → sviluppo, poi scegliere il primo codec/bitrate di contribuzione e il verso caller/listener.
- [x] Rilievo preliminare: RTT medio 30,039 ms; payload SSH/TCP 18,16 Mbit/s su prova limitata a 20 Mbit/s; multicast PAL ricevuto a 1,3018 Mbit/s. Metodo e limiti in [contribution-link.md](../docs/contribution-link.md).
- [ ] Rilevati capacità utile con margine, RTT, jitter, perdite e raggiungibilità; prova concordata senza saturare il servizio in onda.
- [ ] Sorgente SRT nativa o gateway identificati; bitrate e latenza scelti distinguendo overhead e ritrasmissioni; riferimento prima della contribuzione disponibile per stimarne il danno.
- Verifica: confronto fra file sorgente e contribuzione ricevuta, con statistiche di rete e carico del mittente/ricevitore.
- Dipendenze: T01, endpoint disponibili; il prototipo di trasporto di T03 può supportare la misura.
- Dimensione: S. File probabili, obe-rt-build: `docs/contribution-link.md`, `tests/encoding/contribution-profile.json`.

### Checkpoint M0
- [ ] Corpus e riferimento verificati; assunzioni su sorgente, rete e macchina obiettivo esplicite. Definire le soglie mancanti prima di accettare il pilota.

## T03 — Abilitare SRT nella build isolata [M1]

Stato: **completato**. SRT 1.5.7 fissata a commit, collegata a FFmpeg/OBE con OpenSSL nell'immagine isolata.
- [x] Versione e opzioni riproducibili; protocollo SRT disponibile nell'ambiente isolato e smoke test preesistenti ancora validi.
- [x] Prova locale cifrata fra due endpoint con errore riconoscibile per credenziale errata, senza esporla nei log.
- Verifica: `tests/input-srt.py`, caller/listener cifrati attraverso OBE e chiave errata dopo controllo positivo; `tests/smoke-current.sh`, unit test ASan/UBSan e redazione del segreto negli errori FFmpeg.
- Dipendenze: libssl-dev installata soltanto nell'immagine Docker autorizzata. Dimensione: M.
- File: `build.sh`, `docker/obe-builder/Dockerfile.current`, `sources-manifest.txt`, `tests/input-srt.py`, [guida SRT](../docs/input-srt.md).

## T04 — Implementare probe e decode video da URL [M1]

Stato: **parziale**. Modulo `lavf_input` implementato con le API FFmpeg correnti; collaudo MPEG-TS locale su UDP/RTP superato (PAL, 1080i50 e ingresso AVC/AAC). [Uso e limiti](../docs/input-url.md).
- [x] Apertura URL e decode video collegati a probe e avvio; selezione automatica di un video e formati accettati esplicitamente limitati.
- [ ] Apertura dell'URL, selezione esplicita dello stream e decode video; pixel format, dimensioni, cadenza, timestamp, campi e colore rilevati senza inventare valori.
- [ ] Ciclo di vita dei frame verificato, compresi stride non compatti, errori di decode e arresto del mittente; buffer limitati e letture interrompibili.
- Verifica: `tests/input-url.py` attraversa la pipeline OBE con stream noto generato localmente; completare i controlli di ciclo di vita, memoria e matrice dei formati prima di chiudere la voce.
- Dipendenze: contratto di formato di T01, inizialmente verificabile con pattern sintetici; nessuna dipendenza da SRT o dal collegamento Internet. Dimensione: M.
- File attuali: obe-rt `input/lavf/lavf.c`; obe-rt-build `tests/input-url.py`, `docs/input-url.md`.

## T05 — Collegare input, audio e clock a OBE [M1]

Stato: **parziale**. Modulo registrato in probe e avvio, audio opzionale stereo a 48 kHz e origine temporale comune implementati; collaudo locale superato; restano la matrice completa, la rete reale e il test prolungato.
- [x] Dispatch URL attivato; comportamento esplicito per timeout, cambi di formato, timestamp invalidi e assenza di audio. Riconnessione automatica collaudata su UDP con riavvii del mittente, timestamp azzerati e perdita della sola traccia audio o video; selezione manuale delle tracce rinviata.
- [ ] Flusso URL audio/video codificabile da OBE con selezione delle tracce; timebase, origine comune e conversione verso il clock OBE rispettano la relazione fra audio e video.
- [ ] Avvio, ritmo di ingresso, arresto e discontinuità hanno comportamento definito; caso senza audio ed eventuali campi mancanti sono gestiti o rifiutati esplicitamente.
- Verifica: stream locale con sincronismo noto, durata audio/video, timestamp di uscita e arresti ripetuti. Verificare la pipeline OBE reale.
- Dipendenze: T04. Dimensione: M.
- File attuali: obe-rt `input/lavf/lavf.c`, `input/input.h`, `obe/obe.c`, `obe/Makefile.am`; obe-rt-build `tests/input-url.py`, `tests/input-url-reconnect.py`.

### Checkpoint URL senza SDI
- [x] OBE codifica il flusso UDP/RTP locale con audio e timestamp verificati; il test è ripetibile e non richiede hardware di acquisizione né lettura diretta da file.

## T06 — Ricevere SRT con risorse limitate [M1]

Portare il lettore URL già collaudato su SRT, poi sul collegamento Internet, e misurare il costo di ingresso nella pipeline.
- [x] Caller/listener cifrati: riavvio mittente e PTS azzerati recuperati; timestamp A/V continui; `quit` senza sorgente in 0,03 s. Chiave errata respinta in 3,02 s dopo controllo positivo.
- [ ] Timeout interrompibili, arresto rapido, code limitate e gestione dichiarata di perdita, riconnessione e cambio formato.
- [ ] Misure distinte di decode, conversioni, copie, CPU e memoria; una sola conversione verso il formato richiesto quando possibile e ownership dei buffer verificata.
- Verifica: stream noto in laboratorio, perdita/ritardo, sorgente ferma, formato inatteso e ripartenza; poi campione live con il profilo di T02. Nessuna crescita indefinita della memoria.
- Dipendenze: T03 e T05; T02 per il collegamento reale. Dimensione: M.
- File probabili: obe-rt `input/lavf/lavf.c`, `input/lavf/lavf.h`; obe-rt-build `tests/input-network.sh`, `docs/input-support-matrix.md`.

## T07 — Verificare il percorso 1080i50 [M2]

Stato: **parziale**. HD interlacciato nativo collaudato; conversioni da 1080p50 e PAL ancora aperte.
- [x] Ordine TFF/BFF, 300 campi consecutivi a 50 Hz per caso, conversione 4:2:2→4:2:0, BT.709/range limitato e SAR 1:1 verificati dopo decode. Scarto A/V massimo 10,69 ms, soglia 20 ms; controlli negativi superati.
- [ ] Conversioni esplicite e identiche fra prove; risultati HD nativi separati dall'upscaling PAL e sincronismo audio/video preservato.
- Verifica: conteggio dei campi/istanti temporali e controllo visivo di ticker, diagonali e movimento; il flag interlaced da solo non soddisfa la verifica.
- Dipendenze: T05 e corpus di T01; T06 per ripetere i casi live. Dimensione: M.
- File: `tests/input-1080i.py`, [metodo e risultati](../docs/1080i-validation.md). Nessun difetto video riprodotto, nessuna patch video necessaria.

### Checkpoint input e formato
- [x] In laboratorio, ingressi URL UDP/RTP e SRT senza SDI; formato nativo 1080i50 corretto prima di valutare i preset sulla qualità. Restano il percorso Internet SRT, conversioni e prova prolungata.

## T08 — Automatizzare le misure di qualità [M3]

Produrre risultati ripetibili dalla pipeline OBE, trasmettendo una sequenza nota al lettore URL e confrontandola con riferimenti allineati. Misurare separatamente eventuali alterazioni introdotte dal generatore o dalla contribuzione.
- [ ] Runner con configurazione effettiva, revisioni, sorgente, bitrate video/TS, stuffing, CPU, memoria, code e latenza; output CSV e rapporto leggibile.
- [ ] PSNR/SSIM e revisione visiva con trattamento dichiarato dei campi; VMAF eventualmente aggiunto in un'immagine di analisi separata e con modello fissato.
- Verifica: un riferimento senza alterazioni e una degradazione nota devono distinguersi; frame mancanti o disallineati rendono la prova non valida invece di produrre score fuorvianti.
- Dipendenze: T01, T05 e T07. Dimensione: M.
- File probabili, obe-rt-build: `tests/encoding/run`, `tests/encoding/measure`, `tests/encoding/report`, `tests/encoding/results-schema.json`, `docs/encoding-method.md`.

## T09 — Confrontare e selezionare i preset AVC [M3]

Eseguire sweep piccoli e progressivi sul corpus, a bitrate confrontabili e entro il budget della macchina obiettivo.
- [ ] Almeno quattro bitrate per configurazione candidata, registrando preset, GOP/B-frame/reference, AQ/psy, lookahead, VBV, speedcontrol e parametri applicati dopo gli adattamenti di OBE.
- [ ] Finalisti ripetuti a carico comparabile; grafici qualità/bitrate e motivazione della scelta, con candidati fuori dai limiti real time esclusi dall'uso operativo.
- Verifica: confronto con il riferimento sullo stesso hardware e stessa pipeline; controllare scene difficili, burst/VBV, ritardo e andamento delle code, oltre allo score medio.
- Dipendenze: T08; hardware obiettivo e soglie definite in T01. Dimensione: M.
- File probabili, obe-rt-build: `tests/encoding/sweep`, `tests/encoding/configurations.json`, `profiles/1080i-reference.json`, `docs/encoding-results.md`.

### Checkpoint qualità/compressione
- [ ] Miglioramento e costo misurati; riferimento, score, visione dei campioni e comportamento in tempo reale sostengono la scelta del preset.

## T10 — Correggere i colli di bottiglia dimostrati [M3, condizionale]

Aprire una patch per ogni limite riprodotto nel lettore, nelle conversioni o nel controllo dell'encoder, usando il benchmark come prova.
- [ ] Profilo del carico o caso difettoso identifica il limite; accettazione fissata prima della patch.
- [ ] Confronto prima/dopo a qualità e condizioni equivalenti, senza regressioni di campi, audio, clock o memoria.
- Verifica: riproduttore mirato e casi pertinenti del corpus; ripetere le prove aggiuntive soltanto se la modifica le coinvolge.
- Dipendenze: T06 e T09. Dimensione: S per patch.
- File: massimo due in obe-rt o x264 per intervento, individuati dal profiling; nessun refactor preventivo. Se non emerge un difetto o un vantaggio misurabile, documentare il risultato senza una patch.

## T11 — Collaudare il profilo HD sul percorso reale [M4]

Provare il profilo scelto sul server obiettivo e sui ricevitori esistenti, in una finestra concordata.
- [ ] Prima prova di 24 ore con risorse e tempi entro le soglie; interruzioni e recupero verificati, con formato audio/video, PID, PSI/SI e PCR/PTS controllati alla consegna.
- [ ] Preset/revisioni congelati e ritorno al PAL verificato; nessuna modifica in onda implicita nel completamento del benchmark.
- Verifica: misure sul server reale e riscontro del ricevitore, distinguendo qualità dell'encoding, danno della contribuzione ed errori di trasporto.
- Dipendenze: T02, T06, T07, T09 e correzioni T10 necessarie; limiti numerici completati in T01 e finestra disponibile.
- Dimensione: S per documentazione. File probabili, obe-rt-build: `docs/1080i-pilot.md`, `docs/recovery-runbook.md`.

### Checkpoint M4
- [ ] Profilo HD collaudato e limiti residui registrati; preset PAL e procedura di ripristino disponibili.
- [ ] Rivalutare il backlog di API/dashboard, altri protocolli, ridondanza e GPU solo dopo questo risultato. Il piano guida lo sviluppo; il rilascio operativo richiede il collaudo concordato.
