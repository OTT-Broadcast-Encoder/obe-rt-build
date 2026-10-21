# Verifica temporale 1080i50

`tests/input-1080i.py` verifica i campi **decodificati** della pipeline
`MPEG-TS UDP → input URL OBE → AVC/MP2 → MPEG-TS`, sia TFF sia BFF.
Controlla sei secondi completi di ciascuna uscita: 300 campi provenienti da
300 istanti consecutivi a 50 Hz. I flag del bitstream sono verificati insieme
ai pixel, non usati come prova sufficiente dell'interlacciamento.

## Metodo e soglie

La sorgente è costruita in Python come 1920×1080 YUV 4:2:2 a 8 bit. Ogni campo
porta un contatore binario a 12 bit; righe pari e dispari contengono identità
consecutive, assegnate nell'ordine temporale TFF o BFF. Il decoder MPEG-2 deve
prima restituire correttamente la sorgente: un errore nel generatore interrompe
il test prima dell'avvio di OBE.

Nella zona colore, i due campi hanno U/V distinti, 64/192 e 192/64. Questo
rende visibile un'eventuale media tra campi durante il passaggio 4:2:2 → 4:2:0.
Due zone di luminanza controllano nero 16 e bianco 235. La fixture segnala
BT.709, range limitato e SAR 1:1 anche sui frame tramite `setparams`: con
questa versione FFmpeg le sole opzioni dell'encoder MPEG-2 lasciavano
primarie e trasferimento non specificati.

Un flash di un campo e un impulso sinusoidale di 997 Hz lungo 20 ms hanno lo
stesso istante iniziale, una volta al secondo. Il test rileva il flash nei
pixel e l'impulso nei campioni PCM decodificati; usa i PTS dei rispettivi frame
per misurare lo scarto, senza dedurlo dal solo primo timestamp delle tracce.

| Controllo | Soglia |
| --- | --- |
| Identità dei campi | Incremento esatto di uno, nessuno scambio/duplicato/salto; primo campo del frame con identità pari |
| Cadenza | 20 ms per campo, errore massimo 0,1 ms sui timestamp |
| Ordine | TFF/BFF coerente nei pixel e nei flag di ogni frame analizzato |
| Luminanza | Nero 16 e bianco 235, tolleranza di 6 codici |
| Crominanza | U/V attesi per ciascun campo, tolleranza di 10 codici |
| Segnalazione | BT.709 per primarie, trasferimento e matrice; range `tv`; SAR 1:1; 1920×1080 a 25 frame/s |
| Sincronismo | Almeno quattro coppie flash/impulso; scarto massimo 20 ms, cioè un campo |

I controlli negativi alterano un frame realmente decodificato scambiando le
righe dei campi, duplicando un campo o sostituendo la crominanza con la media
128. Un quarto controllo sposta di 100 ms gli istanti dei marker audio
rilevati. Ognuna delle quattro alterazioni deve essere rifiutata dal relativo
controllo. Sono prove del rilevatore sugli artefatti decodificati, non una
simulazione di guasti sul trasporto UDP.

## Esecuzione e risultati

Dalla radice del candidato, nell'immagine già disponibile:

```sh
docker run --rm --cpus=6 --memory=8g \
  --mount "type=bind,source=$PWD,target=/work" -w /work \
  obe-rt-build:srt-2026-10-21 python3 tests/input-1080i.py
```

`--case tff` o `--case bff` limita la prova; `--fixture-only` verifica soltanto
il generatore. `--analyse-dir tests/.build/input-1080i-NOME` rianalizza i media
già acquisiti senza avviare OBE. Il test conserva sorgenti, configurazioni,
uscite, log e risultati JSON, con hash SHA-256 dei media.

Il 21 ottobre 2026 entrambe le pipeline sono state eseguite con x264
`ultrafast`, 8 Mbit/s video, MP2 stereo 192 kbit/s e mux CBR 9,5 Mbit/s.
Questi valori servono alla verifica funzionale, non costituiscono il profilo
HD consigliato. Prova ripetuta dopo la build SRT; acquisizione:
`tests/.build/input-1080i-b38z0r3b/`.

| Caso | Campi decodificati | Identità temporali | Scarto A/V massimo | Errore crominanza massimo |
| --- | --- | --- | --- | --- |
| Sorgente TFF/BFF | 300 per caso | 0–299 | 0,67 ms | 0 codici |
| OBE TFF | 300 | 432–731 | 10,69 ms | 0,75 codici |
| OBE BFF | 300 | 432–731 | 10,69 ms | 0,75 codici |

Il controllo comprende la conversione 4:2:2 → 4:2:0 e l'allineamento temporale
sul tratto comune completo, prima della coda interrotta del mux. Non certifica
input a 10 bit, range completo, altri formati o protocolli, SDI/NDI, ricevitori
reali o funzionamento prolungato. Il pattern verifica la conservazione dei
campi e dei livelli; qualità percettiva e compressione su contenuti reali
richiedono il corpus e i benchmark previsti nella roadmap.
