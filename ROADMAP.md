# Roadmap feature OBE

Aggiornata al 21 ottobre 2026. `[x]` = implementato e verificato nell'ambito
indicato; `[ ]` = ancora da completare. Il completamento in laboratorio non
equivale alla validazione sul percorso di trasmissione reale.

**Telemetria, affidabilità e qualità/compressione sono tutte priorità P0.**
Il primo incremento di telemetria è completato. I prossimi incrementi sono
il recupero controllato dopo crash e il benchmark del motore OBE; la raccolta
dei riferimenti può procedere in parallelo. Le date dipendono dalle misure.

## P0 — Telemetria e diagnostica

- [x] CPU, memoria RSS e tempo di attività del processo encoder.
- [x] Bitrate AVC e UDP misurati, frame/s, frame codificati, coda video ed errori locali di invio.
- [x] Campioni mancanti o scaduti espliciti; nessuna misura ricavata dal bitrate configurato.
- [x] Telemetria collegata al webpanel e verificata su PAL e 1080i50 con riconnessione.
- [ ] Storico limitato ed esportazione delle misure con profilo e revisioni software.
- [ ] Statistiche SRT: RTT, perdite, ritrasmissioni e occupazione dei buffer.
- [ ] Misura della latenza end-to-end e allarmi per soglie, stallo e crescita delle code.

Il bitrate UDP indica payload accettato dal socket locale, non ricezione remota.
Su 1080i50 il contatore misura 25 frame interi/s, corrispondenti a 50 semiquadri/s.

## P0 — Affidabilità del backend

- [x] Riconnessione automatica dell'ingresso URL, collaudata con interruzioni e ripartenza del mittente.
- [x] Avvio/arresto del solo processo gestito e blocco degli avvii durante lo spegnimento del pannello.
- [ ] Riavvio opzionale dopo crash, con attesa progressiva e numero massimo di tentativi.
- [ ] Stop che cancella anche i riavvii in attesa; nessun ciclo infinito o autoavvio dopo reboot.
- [ ] Diagnostica dell'uscita: codice, segnale, causa e storico limitato dei tentativi.
- [ ] Test di concorrenza tra start/stop/riavvio e isolamento dei dati tra processi successivi.
- [ ] Collaudo prolungato con memoria, code, interruzioni e tempi di recupero sotto controllo.

La riconnessione della sorgente è disponibile; il recupero dopo crash dell'encoder
richiede ancora l'avvio manuale.

## P0 — Qualità e compressione del motore OBE

- [ ] Corpus riproducibile: studio, movimento, ticker, rumore, cambi scena e pattern temporali.
- [ ] Riferimento PAL misurato e budget HD di bitrate, latenza e risorse sulla macchina obiettivo.
- [ ] Benchmark sulla pipeline OBE con ingresso URL e riferimento allineato frame/campi.
- [ ] PSNR/SSIM e confronto visivo, separando il danno della contribuzione da quello dell'encoding.
- [ ] Confronto dei preset x264 a più bitrate, includendo GOP, B-frame, lookahead, AQ/psy e VBV.
- [ ] Rapporto qualità/bitrate con CPU, memoria, code e verifica del funzionamento in tempo reale.
- [ ] Scelta motivata dei profili PAL e 1080i50; correzioni del motore solo su limiti dimostrati.

## P0 — Correttezza video e audio

- [x] 1080i50 nativo: movimento dei campi TFF/BFF, crominanza, BT.709 e sincronismo A/V verificati sui pattern.
- [ ] Conversione 1080p50 → 1080i50 preservando i 50 istanti temporali.
- [ ] Conversione PAL → 1080i50 con scaling e colorimetria dichiarati, valutata separatamente dall'HD nativo.
- [ ] Matrice completa di formati e selezione esplicita delle tracce video/audio.
- [ ] Integrazione del playout reale, compreso l'adattamento dell'audio al formato stereo 48 kHz richiesto.

## P1 — Contribuzione remota e pilota HD

- [x] Ingresso URL UDP/RTP e SRT nella build isolata, con prove locali senza SDI.
- [x] SRT cifrato: chiave errata, riconnessione e recupero di perdite/ritardi verificati in laboratorio.
- [x] Prime misure Uno4 → sviluppo su SSH/TCP e ricezione del contributo PAL.
- [ ] Contribuzione dal playout via SRT o gateway sul percorso remoto raggiungibile concordato.
- [ ] Misure Internet di banda utile, RTT, jitter, perdite e recupero SRT.
- [ ] Pilota 1080i50 di almeno 24 ore sul server obiettivo e sui ricevitori reali.
- [ ] Controllo MPEG-TS, PCR/PTS, continuità A/V e procedura di ritorno al PAL verificata.

Le prove SRT Internet restano aperte. Nessuna modifica ai router o agli inoltri
di porta è prevista: l'utente ha chiesto di non procedere con quell'intervento.

## P1 — Webpanel e operatività

- [x] Console scura responsive con panoramica, configurazione e registro eventi.
- [x] Profilo persistente, preset iniziali, verifica ingresso, avvio/arresto e validazione delle richieste.
- [x] Accesso locale o tramite tunnel SSH; passphrase SRT effimera e log limitati.
- [ ] Libreria di profili utente con import/export e versioni ripristinabili.
- [ ] Anteprima video, livelli audio e indicatori di assenza segnale.
- [ ] Account, ruoli e accesso HTTPS prima di un'eventuale esposizione condivisa.

## P2 — Estensioni successive

- [ ] Gestione di più encoder/canali dal pannello.
- [ ] Ridondanza MAIN/BACKUP e commutazione controllata.
- [ ] Valutazione HEVC, GPU e UHD con benchmark separati.
- [ ] Ulteriori trasporti e aggiornamenti degli SDK proprietari secondo necessità.

Dettagli e criteri di accettazione: [attività tecniche](tasks/todo.md),
[piano di misura](tasks/plan.md), [webpanel e risultati](docs/webpanel.md).
