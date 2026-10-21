# Playout Windows: formato del programma e percorso verso HD

Verifica del 21 ottobre 2026, eseguita via SSH sul PC Windows indicato
dall'utente. Ispezione in sola lettura: nessun cambio di profilo, riavvio delle
applicazioni, installazione o intervento su router e firewall. Un ricevitore NDI
temporaneo ha letto 20 frame alla massima qualità, poi è stato chiuso.

## Esito operativo

**Il programma attualmente disponibile da ONVServer è PAL, non HD.**
Il materiale sorgente può essere HD: tutti e quattro i file recenti campionati
sono AVC 1920×1080 progressivi a 25 fps. Il ridimensionamento avviene quindi
prima dell'uscita NDI del programma. Ingrandire questa uscita a 1080 non
recupera il dettaglio perso.

Si può iniziare il lavoro con il PAL già disponibile. Per ottenere il programma
completo in HD, con grafica e continuità del palinsesto, serve una catena HD
separata oppure un cambio pianificato del formato principale di ONVServer,
verificando anche l'uscita SDI e l'eventuale conversione verso la distribuzione PAL.
Nessuna di queste modifiche è stata applicata durante l'ispezione.

## Catena osservata

```text
PlayOutNet (palinsesto e controllo)
  └─ TCP locale 21100 → ONVServerSG (lettura media, grafica, rendering PAL)
       ├─ uscita configurata: DeckLink Mini Monitor
       └─ NDI: PLAYOUT (PO_Uno4), 720×576 interlacciato
            └─ CaptureStreamer → MServer/Medialooks → streaming RTMP
```

Le connessioni TCP tra PlayOutNet e ONVServer sono attive. CaptureStreamer
ha connessioni locali al sender NDI di ONVServer sulla porta 5961; il processo
MServer ha una connessione RTMP attiva. Il percorso fisico SDI fino all'encoder
di distribuzione non è stato seguito durante questa verifica.

Versioni osservate: ONVServerSG 3.6.0.3, CaptureStreamer 2.3.5.5,
PlayOutNet 1.0.0.1; librerie Medialooks MFormats e runtime NDI 6.0.0.0.
Host Windows 10 Pro, Ryzen 5 3600 (6 core/12 thread), GTX 1650 e
DeckLink Mini Monitor. La presenza di componenti HD non implica che il
programma attivo venga renderizzato in HD.

## Evidenze del formato

| Punto | Evidenza | Interpretazione |
|---|---|---|
| ONVServer, `VideoManager/VideoFormat` | `5`, risolto nell'enum installato come `eMVF_PAL_16x9` | Formato principale PAL 16:9 |
| ONVServer, `Renderer/NDIResizeRatio` | `1` | Il probe sottostante misura comunque l'uscita effettiva |
| NDI `PLAYOUT (PO_Uno4)` | 20 frame: 720×576, 25/1 fps, UYVY 4:2:2, DAR 16:9, `frame_format_type=interleaved` | Programma SD interlacciato, ricevuto con `bandwidth=highest`, non anteprima a bassa risoluzione |
| Audio NDI | 2 canali, 96.000 Hz, 3.840 campioni per blocco | Per il lettore URL OBE occorre ricampionare a 48 kHz nel gateway |
| CaptureStreamer, `MainClass/VideoFormat` | `17`, enum `eMVF_HD1080_50p`; input NDI `PO_Uno4` | Impostazione HD a valle di una sorgente PAL; non prova di programma HD nativo |
| Quattro file recenti presenti nei log | AVC 1920×1080p25; AAC stereo 44,1 kHz | Esiste materiale HD a monte; campione limitato, non censimento dell'archivio |

Il probe NDI legge il flusso mediante il runtime già installato, senza cambiare
il sender o inviare comandi di playout. Risoluzione, cadenza e audio sono
misurati; ordine TFF/BFF e movimento dei 50 campi non sono stati certificati.
I quattro originali sono 25p: inserirli in 1080i50 non crea 50 istanti di
movimento distinti. Il profilo RTMP è configurato con audio AAC a 48 kHz,
ma il bitstream RTMP non è stato campionato in questa verifica.

## Come procedere

1. **Banco PAL immediato:** usare il contributo MPEG-TS già collaudato via SSH
   nel [rapporto di rete](contribution-link.md), oppure predisporre un ricevitore
   NDI separato sul PC Windows, con ricampionamento audio 96→48 kHz e
   contribuzione compressa verso OBE. Il successivo [gateway NDI/SSH](ndi-gateway.md)
   implementa e collauda questo percorso senza cambiare il playout.
2. **Banco HD:** partire dagli originali HD in un percorso separato e verificare
   risoluzione, grafica, formato audio e cadenza prima di intervenire sulla
   messa in onda. Un'altra istanza del playout richiede verifica di licenza,
   risorse e dispositivi, non è stata presunta utilizzabile.
3. **Programma HD operativo:** valutare il formato principale 1080i50 di
   ONVServer e la conservazione del ramo PAL. Le modalità HD1080_50i e
   HD1080_50p esistono nell'enum installato, ma non sono state applicate né
   collaudate sulle uscite fisiche.

FFmpeg e Python sono già installati sul PC. La build FFmpeg interrogata non
espone un dispositivo NDI: un gateway NDI deve usare il runtime disponibile o
un'applicazione compatibile, non si può presumere funzionante `ffmpeg -f libndi_newtek`.
Per Internet resta valida la necessità di una contribuzione compressa; nessun
nuovo inoltro di porte è stato predisposto.

## Limiti e dati sensibili

L'ispezione non certifica tutto l'archivio, la resa della grafica in HD,
l'uscita SDI, la capacità residua del PC o il comportamento su lunghe durate.
Nei profili legacy sono presenti credenziali di streaming; una chiave è emersa
nell'output diagnostico e va ruotata se ancora valida. Il filtro è stato
corretto. Nessuna credenziale, URL con chiave o copia completa dei profili
è inclusa in questo rapporto o nei file versionati.
