# Pilota 1080i50

`webpanel/pilot.py` osserva un canale già in codifica mediante le sole GET API.
Non avvia, arresta o riconfigura encoder, preview e playout. CSV e rapporto JSON
contengono tempi reali, soglie, profilo senza URL, revisione, versioni e hash
dell'osservatore. I file sono scritti progressivamente; il rapporto è aggiornato
atomicamente. Massimo 24 ore, intervallo minimo 1 s, aggregati in memoria e
primi 20 errori conservati nel rapporto. Interrompere con Ctrl-C conserva i dati.

## Procedura

1. Preparare un canale di prova distinto da quello in onda, con un ingresso
   **1920×1080i, 25 frame/s, stereo 48 kHz**. Il preset HD non converte il PAL.
2. Verificare la sorgente e avviare dal pannello. Attendere telemetria stabile,
   almeno 10 s dopo la conferma di avvio. Verificare la ricezione dell'uscita
   usando uno strumento separato. Nessun intervento sui router è necessario.
3. Eseguire prima una prova breve su una directory nuova:

   ```sh
   python3 webpanel/pilot.py --panel http://127.0.0.1:18089 --channel main \
     --seconds 60 --output tests/.build/pilot-hd-breve
   ```

4. Se la prova breve passa, osservare per 24 ore sul server obiettivo:

   ```sh
   python3 webpanel/pilot.py --panel http://127.0.0.1:18089 --channel main \
     --seconds 86400 --interval 5 --output tests/.build/pilot-hd-24h
   ```

Per il pannello remoto usare un tunnel SSH e il suo indirizzo loopback.
`--max-cpu`, `--max-rss-mb`, `--max-queue` rendono espliciti i limiti della macchina.
Il comando termina con exit 1 per anomalie, 130 se interrotto, 0 per una prova
senza anomalie. La directory deve essere nuova: nessun rapporto viene sovrascritto.

## Criteri della telemetria

- Processo e revisione invariati, sorgente in ricezione, metadati 1080i50 e audio stereo 48 kHz.
- Telemetria live, età massima 5 s, contatori di frame e byte in avanzamento.
- Cadenza osservata fra 22 e 28 frame/s; bitrate mux entro ±20% del configurato.
- Zero errori locali UDP; coda al massimo 50 frame; RSS al massimo 2048 MB.
- CPU encoder al massimo 600% (sei core). Queste soglie sono iniziali e
  vanno adeguate al budget effettivo prima dell'accettazione operativa.

Ogni violazione campionata rende il rapporto `fail`; un intervallo mancante non
viene interpolato. Le medie sono medie dei campioni, non una misura end-to-end.
Le misure CPU/RSS riguardano OBE; sommare il costo di gateway, preview e sistema
nel dimensionamento. Un'interruzione dell'osservatore non interrompe l'encoder.

`short_pass` indica una prova inferiore a 24 ore; `telemetry_pass` almeno 24 ore
di sola osservazione. **Nessuno dei due qualifica il servizio in produzione.**
Restano da verificare sui ricevitori: decodifica, PCR/PTS, continuità MPEG-TS,
ordine e movimento dei campi, colore, sincronismo A/V e ritorno al PAL.
Non basta il flag interlacciato del probe per verificare i 50 istanti temporali.

## Evidenza iniziale

21 ottobre 2026: 30 s di HD sintetico con encoder reale e preview, 7 campioni,
`short_pass`, nessuna anomalia campionata. RSS 222,7–226 MB, coda 0–2,
24,99–26,99 frame/s, UDP 9,134–9,914 Mbit/s su mux configurato 9,5 Mbit/s.
Artefatti: `tests/.build/preview-g5b2s4s6/pilot/`, esclusi da Git.
La sorgente remota nota è PAL: pilota HD remoto di 24 ore ancora da eseguire.

Test di classificazione: `python3 tests/webpanel-pilot.py`.
Smoke completo in container isolato: `python3 tests/webpanel-preview-live.py --pilot-seconds 30`.
