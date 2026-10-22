# Audit upstream LTN — 21 ottobre 2026

Il candidato integra gli aggiornamenti compatibili con le librerie moderne e con
l'ingresso URL già funzionante. Il confronto usa i riferimenti Git `ltn-audit`
e `upstream` acquisiti nei checkout originali, insieme a
[`sources-manifest.txt`](../sources-manifest.txt) e [`LIBRARIES.md`](../LIBRARIES.md).

Il lavoro è isolato in `obe-rt-build-ltn`, sul branch
`chore/ltn-upstream-2026-10-21`. I checkout di `obe-rt-build-current` restano
inalterati; il branch `backup/pre-ltn-2026-10-21` conserva il punto di partenza
delle repository interessate.

| Componente | Riscontro upstream | Decisione nel candidato |
| --- | --- | --- |
| Ambiente di build | [`9b7589b`](https://github.com/LTNGlobal-opensource/obe-build-environment/commit/9b7589b9040dd7a0d8628d3b3efcecf2352b7514), 3 settembre 2026; 32 commit mancanti | Conservare la build Ubuntu isolata e aggiornata. Le ricette LTN comprendono ancora versioni vecchie, percorsi CentOS/macOS, SRT 1.4.4 e modifiche all'ABI `pic_timing` da valutare separatamente. |
| Encoder OBE | Branch attivo `3.0.0`, [`7c62443`](https://github.com/LTNGlobal-opensource/obe-rt/commit/7c62443c8e8da1eb1e579e8627c3e9e1a95be12f), 3 settembre 2026 | Integrare selettivamente le correzioni compatibili. `master`, fermo a marzo 2022, è già incluso nel nostro fork. |
| libltntstools | [`f639963`](https://github.com/LTNGlobal-opensource/libltntstools/commit/f6399630de4d29496c94256f1fec727efa10d1df), 2 ottobre 2026 | Rebase dei due fix locali di `segmentwriter`; correggere inoltre l'accesso all'oggetto dopo l'accodamento in `write` e `object_write`. |
| libklscte35 | [`9df34b2`](https://github.com/LTNGlobal-opensource/libklscte35/commit/9df34b200b71485fcd757f9da6b3aeb492e559b6), 1 settembre 2026 | Aggiornare: cinque commit con correzioni di sicurezza della memoria, test API/regressioni e documentazione SCTE-35. |
| libklvanc | `b409fc2` | Già all'ultima revisione verificata; conservare. |
| libmpegts-obe | `1ee8519`, branch `hevc-dev` | Già all'ultima revisione verificata; conservare. |
| x264 | Il nostro fork include l'upstream ufficiale `0480cb0`; il `master` LTN è `0052f6d` del 2013 | Conservare x264 moderno e le estensioni OBE, incluso lo speed control. |

La divergenza dell'encoder è di **31 commit locali e 162 commit LTN**. Il rebase
completo, tentato esclusivamente nel candidato, è stato interrotto al primo
conflitto nel Makefile: introduceva dipendenze `libltnsdi`, curl e JSON e
aspettative diverse sull'interfaccia timecode di x264. L'integrazione selettiva
mantiene verificabile la compatibilità con FFmpeg e x264 già aggiornati.

Nell'encoder sono stati applicati otto commit selettivi: rilascio dei filtri
video, corruzione WST/PES dopo circa 35 giorni, limiti dei buffer VANC e degli
stream DeckLink, errori e cleanup DeckLink, allocazioni dei metadati e puntatori
nulli nei cleanup AAC/AC-3. Restano esclusi i cambiamenti di clock/locking
multischeda, riuso dei buffer, SRT, interfaccia e timecode. Le modifiche hardware
richiedono una prova con le schede reali; gli SDK proprietari restano invariati.

La verifica Git conferma revisioni, conteggi e inclusione del vecchio `master`
OBE. Il test delle allocazioni dei metadati è passato con AddressSanitizer e
UndefinedBehaviorSanitizer, inclusi payload di un byte, fallimenti delle due
allocazioni e clone parziale: ha individuato e verificato la correzione di un
overflow preesistente nell'allocazione della struttura.

Il rebase di `libltntstools` conserva i path letterali (`28194d6`) e adatta il
conteggio dei byte alla nuova gestione della coda (`47a023c`), con un test
dedicato (`9cb2af6`). Il test obbliga il consumer a liberare l'oggetto prima che
l'enqueue ritorni: AddressSanitizer rileva l'uso dopo il rilascio in entrambe
le API originali, mentre quelle corrette superano i controlli su nome, contenuto
e conteggio dei byte. La verifica disabilita il rilevamento dei leak, perché
il lifecycle upstream del writer presenta leak preesistenti non risolti qui.

Nel container Ubuntu isolato sono passati i **209 test SCTE-35**, la build e
l'installazione delle due librerie, il nuovo link dell'encoder e
`tests/smoke-current.sh`. Sono state riutilizzate le altre librerie già compilate;
non è stata rieseguita l'intera build di FFmpeg, x264 e delle dipendenze invariate.
Il test audio usa anche lo stub della rimozione dalla coda senza lock richiesto
dal nuovo controllo sui frame nulli.

Sono inoltre passati, sul binario ricollegato alle nuove librerie:

```sh
python3 tests/input-url.py --case udp-pal --case udp-1080i50
python3 tests/input-url-reconnect.py --case repeat --case track-loss --case video-loss
```

Il primo verifica H.264/MP2, segnalazione interlacciata, decodifica e timestamp
A/V. Il secondo verifica tre ripartenze del mittente con reset dei timestamp,
perdita della sola traccia audio o video e recupero con identità degli stream
invariata; l'uscita durante assenza del mittente avviene in meno di due secondi.
Queste prove usano loopback UDP, non il collegamento Internet di contribuzione.
I log locali sono in `tests/.build/ltn-smoke.log`, `ltn-input-url.log`,
`ltn-input-reconnect.log` e `scte35-test.log`; quelli del writer in
`libltntstools/test/.build/`.

Le patch `0008`/`0011`/`0012`/`0013` applicate alla base OBE fissata nel build
ricostruiscono esattamente tutti i **183 file** del candidato `cf57ee0`.
La patch `0010` sulla nuova base `f639963` ricostruisce tutti i **164 file** di
`libltntstools` a `9cb2af6`. Gli hash delle modifiche locali preesistenti e gli
HEAD dei checkout originali sono rimasti identici allo snapshot iniziale.

Questa integrazione migliora robustezza e gestione degli errori: non costituisce
una misura di miglioramento della qualità o compressione a 1080i. Restano da
valutare separatamente timecode, percorsi multischeda e nuovi servizi LTN, con
test hardware e benchmark di qualità prima di un'adozione più ampia.
