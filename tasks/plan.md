# Roadmap feature — qualità di codifica e passaggio a 1080i

Data: 21 ottobre 2026. Priorità aggiornata dalle indicazioni dell'utente: testing, migliore qualità a parità di bitrate, migliore compressione a qualità confrontabile, passaggio da PAL a 1080i e ingresso di rete per lavorare senza SDI. Il primo incremento dell'ingresso URL è implementato; T04/T05 sono parziali e il primo collaudo locale UDP/RTP è superato. Le altre attività restano pianificate.

## Obiettivo della prima versione

Ricevere una sorgente tramite URL nella pipeline OBE, misurare il risultato e scegliere un profilo AVC 1080i sostenibile sul server di codifica. L'utente dà priorità all'ingresso di rete: il primo risultato utilizzabile deve essere URL → decode audio/video → encoder e mux OBE. I file restano riferimenti per le misure; la lettura diretta da file in OBE è un'estensione opzionale. Il risultato atteso è un confronto ripetibile tra configurazioni, con video decodificato, bitrate effettivo, occupazione delle code, CPU, memoria e latenza.

Il primo target proposto è **1080i50: 1920×1080, 25 fotogrammi e 50 semiquadri al secondo**. Cadenza e ordine dei campi devono essere verificati nella sorgente e nel ricevitore. L'aumento di risoluzione non implica poter mantenere il bitrate PAL: il budget HD sarà scelto dalle misure e dai vincoli della consegna.

## Dati disponibili

| Elemento | Stato e conseguenza |
| --- | --- |
| Base software | Build isolata e smoke test completati; 25 commit pubblicati in sette repository, sui branch di lavoro. Vedere [LIBRARIES.md](../LIBRARIES.md). |
| Profilo attuale | I rilievi del pannello della redazione riportano PAL, AVC 1.020 kb/s, MP2 128 kb/s e mux CBR 1.300 kb/s. Sono valori osservati nella configurazione, da verificare sul bitstream. |
| Interlacciamento attuale | Nel report le caselle Interlaced/TFF non sono selezionate. La modalità di acquisizione PAL non prova la scansione effettiva dell'uscita: va misurata prima di replicare o correggere il profilo. |
| Sorgenti disponibili | L'utente indica disponibilità di PAL, 1080i50, 1080p50 e uscite SDI, NDI, MPEG-TS. La presenza di un'uscita SRT nativa va verificata; è possibile prevedere un gateway lato playout. |
| Rete | Uno4: 1 Gbit/s simmetrico. Sviluppo: 2,5 Gbit/s download, 1 Gbit/s upload. Sono accessi Internet, non un collegamento Ethernet diretto. Da Uno4 allo sviluppo il limite nominale è l'upload a 1 Gbit/s; capacità utile, RTT, jitter e perdite restano da misurare. |
| Ingresso generico | `lavf_input` è collegato a probe e avvio: primo percorso URL → decode → encoder/mux OBE con selezione automatica di un video e audio opzionale stereo 48 kHz. Collaudo locale superato: PAL UDP/RTP, 1080i50 UDP e AVC con B-frame/AAC UDP; vedere la [guida](../docs/input-url.md). SRT e il percorso Internet restano da implementare/collaudare. |
| Librerie disponibili | FFmpeg ha demux/decoder e protocolli UDP/RTP, RTSP e HTTP; libSRT è disabilitata. PSNR/SSIM sono disponibili, libvmaf è disabilitata. |
| Controlli encoder | OBE espone preset, bitrate, VBV, GOP, lookahead, interlacciamento e TFF; il fork x264 include speedcontrol. Il benchmark deve registrare i parametri effettivi dopo gli adattamenti di OBE. |

Il report indica un i5-8500 a sei CPU nel server della redazione: va confermato come macchina obiettivo. Una configurazione che funziona sulla macchina di sviluppo deve essere misurata anche sulla destinazione.

## Milestone e criteri di uscita

| Milestone | Priorità | Consegna | Criterio verificabile |
| --- | --- | --- | --- |
| M0 — Riferimento | P0 | Corpus PAL/HD, profilo attuale, budget di bitrate e latenza, inventario hardware/rete | Sorgenti identificate e ripetibili; formato effettivo e limiti del ricevitore registrati; soglie del collaudo definite |
| M1 — Input URL senza SDI | P0 | Lettore URL basato su libavformat/libavcodec, collaudo locale UDP/RTP e ingresso SRT per Internet | OBE codifica audio/video dalla sorgente URL, con timestamp e metadati corretti, memoria limitata, arresto e gestione delle interruzioni verificati |
| M2 — 1080i corretto | P0 | Percorso 1080i50 verificato, con conversioni esplicite per le altre sorgenti | 50 istanti temporali quando presenti alla sorgente, ordine dei campi, crominanza, colore, aspect ratio e sincronismo verificati sul video decodificato |
| M3 — Qualità e compressione | P0 | Benchmark automatico, curve qualità/bitrate e preset AVC selezionati | Miglioramento documentato rispetto al riferimento a condizioni uguali; candidati entro budget di CPU/latency/VBV e senza accumulo delle code |
| M4 — Pilota HD | P1 | Prova prolungata sul server obiettivo e verifica della consegna | Prima prova di 24 ore entro le soglie concordate, compatibilità dei ricevitori e ripristino del profilo PAL verificati |
| Successivo — Gestione e ampliamenti | P2 | Dashboard/API, allarmi, ridondanza, multicanale, GPU/UHD e altri trasporti | Attività da dettagliare dopo il risultato dell'encoding, per esigenza concreta |

Ordine di consegna: M0 → M1 → M2 → M3 → M4. Preparazione del corpus e strumenti di misura possono procedere durante M1; la scelta del preset finale richiede la validazione di M2. Il primo incremento T04/T05 funziona su URL UDP/RTP locale usando le librerie già presenti; T03 abilita SRT e T06 completa il percorso Internet. La lettura diretta da file non è un prerequisito.

## Ingresso di test e uso della rete

**Percorso proposto:** sorgente del playout → eventuale codifica di contribuzione/gateway SRT → Internet → modulo URL di OBE → decode → conversione necessaria → encoder e mux OBE. Per ripetere gli esperimenti, un generatore locale trasmette una sequenza nota allo stesso ingresso URL; il relativo file di riferimento serve al confronto. SRT è il trasporto; codec e bitrate della contribuzione sono una scelta distinta.

| Modalità | Uso nella roadmap | Verifica richiesta |
| --- | --- | --- |
| File di riferimento lossless o non compresso | Riferimento delle misure e sorgente di un generatore di rete ripetibile; lettura diretta in OBE opzionale | Hash, formato, frame/campi e audio noti; verificare il contributo del generatore e temporizzare la trasmissione per valutare il funzionamento in tempo reale |
| SRT con contribuzione AVC ad alta qualità | Prima modalità live su Internet; punto di partenza compatibile con l'obiettivo interlacciato | Bitrate scelto con margine per gli altri servizi e le ritrasmissioni; latenza e CPU di codifica/decodifica misurate; confronto con il riferimento prima della contribuzione |
| UDP/RTP | Primo collaudo locale del lettore URL con MPEG-TS e librerie già presenti | Audio/video e timestamp verificati sul flusso generato; perdite e arresto del mittente rilevati |
| RTSP, HTTP e altri protocolli | Estensioni dello stesso lettore URL, secondo una matrice esplicita di protocolli, contenitori e codec | Test dedicati per ciascuna combinazione; supporto dichiarato in base alla build FFmpeg effettiva |
| NDI | Percorso già presente da confrontare se utile e compatibile con SDK v4 e collegamento effettivo | Trasporto attraverso Internet, compatibilità e impatto sulla qualità/CPU da verificare; la presenza del codice non prova l'interoperabilità |

A 1080i50, il solo video 4:2:2 non compresso richiede circa **829 Mbit/s a 8 bit** o **1,037 Gbit/s a 10 bit**, prima di audio, overhead e padding. Il calcolo è 1920×1080×25×16 oppure ×20 bit. Su un accesso nominale da 1 Gbit/s si parte quindi da una contribuzione compressa di qualità misurata. La capacità utile si rileva senza saturare un collegamento in uso per la trasmissione.

L'efficienza dell'input va misurata per fase: demux, decode, conversioni, copie, code ed encoder. Riutilizzare buffer e riferimenti quando il ciclo di vita lo consente, effettuare soltanto le conversioni necessarie e imporre limiti alle code. Il timeout e l'arresto devono interrompere anche una lettura di rete bloccata. EOF, perdita della sorgente, riconnessione e cambio formato richiedono comportamenti espliciti; per il primo rilascio un cambio formato non supportato può fermare il canale con un errore chiaro.

## Metodo per misurare qualità e compressione

1. **Riferimenti rappresentativi:** studio/volti, movimento rapido, panoramiche, titoli e ticker, rumore/grana e cambi scena; includere pattern con movimento tra campi e sincronismo audio/video. Conservare i file fuori da Git, con manifest, hash e provenienza. Distinguere HD nativo, PAL nativo e PAL ingrandito.
2. **Percorso 1080i:** conservare il 1080i nativo; per 1080p50 generare campi da istanti consecutivi; valutare separatamente la conversione dal PAL. L'ingrandimento del PAL non aggiunge dettaglio HD. Verificare TFF/BFF, cadenza, SAR/DAR, range e conversione SD/HD della colorimetria, evitando scaling e sottocampionamento della crominanza che mescolino istanti diversi dei campi.
3. **Misure del video decodificato:** iniziare con PSNR/SSIM e ispezione visiva di movimento, bordi e testi. Per l'interlacciato usare misure per campo o una conversione comune e dichiarata a 50p su riferimento e risultato. Valutare VMAF in un'immagine di analisi separata, con versione del modello registrata: lo score non sostituisce il controllo dei campi. Confronti tra risoluzioni richiedono lo stesso trattamento di visualizzazione, dichiarato nel rapporto.
4. **Curve qualità/bitrate:** almeno quattro bitrate per ogni configurazione candidata sullo stesso corpus. Tenere costanti sorgente, filtri, audio e regole del mux; registrare bitrate video reale, bitrate TS e stuffing, dimensione dei file, CPU, memoria, latenza e code. Confrontare sia qualità a parità di bitrate sia bitrate a qualità confrontabile. Non promettere una percentuale di risparmio prima delle misure.
5. **Tuning nella pipeline OBE:** sweep limitati di preset, GOP/B-frame/reference, lookahead, AQ/psy e VBV; annotare speedcontrol e parametri effettivi. Ripetere le configurazioni finaliste a carico comparabile, distinguendo esecuzione in tempo reale e misura della capacità massima. FFmpeg da solo serve come controllo, non sostituisce il test di OBE e del suo fork x264.
6. **Sorgente live:** separare il danno introdotto dalla contribuzione da quello del nuovo encoding. Usare sia il riferimento precedente alla contribuzione per il risultato complessivo sia i frame decodificati ricevuti come riferimento dell'encoder. Non attribuire all'encoder un limite già presente nel flusso in ingresso.

Restano da scegliere con le misure: bitrate massimo video/TS HD accettabile, latenza operativa, headroom CPU, limiti di deriva e recupero. Prima del pilota tali campi devono avere valori numerici; “video visibile” non è un criterio sufficiente. Mantenere inizialmente AVC e il formato audio compatibile con la consegna; HEVC e GPU hanno un collaudo separato se richiesti.

## Ambito e lavoro successivo

- **obe-rt:** modulo `lavf_input`, integrazione probe/avvio, timestamp audio/video, metadati, conversioni e statistiche necessarie alle misure.
- **obe-rt-build:** dipendenza SRT fissata e collegamento FFmpeg, immagini di analisi, corpus/manifest, benchmark, risultati e preset riproducibili.
- **x264:** tuning e correzioni motivate dal profilo del carico o da difetti riprodotti; preservare le estensioni OBE e provare ogni variazione sul corpus.
- **libtstools/libmpegts:** interventi soltanto per problemi misurati di trasporto, mux o compatibilità del TS. ONID/LCN/SI dipendono dalle responsabilità effettive del remux, descritte nel [confronto di compatibilità](../docs/confronto-inaria.md).
- **Backlog successivo:** preset gestiti da API/dashboard, allarmi, MAIN/BACKUP e failover, anteprime, più processi per più canali, GPU/UHD, RIST e bonding. Gli aggiornamenti degli SDK proprietari restano separati.

Conservare la build PAL precedente. I pin e le patch attuali sono riferiti alle basi upstream: usare i nuovi commit dei fork richiede evitare di riapplicare modifiche già incluse. Nessuna nuova dipendenza viene installata durante la pianificazione.

Le [attività verificabili](todo.md) definiscono il prossimo lavoro e distinguono il primo incremento URL dal collaudo ancora necessario. Le scadenze si assegnano dopo la prima misura della sorgente e del server; le altre scelte architetturali restano proposte da validare.
