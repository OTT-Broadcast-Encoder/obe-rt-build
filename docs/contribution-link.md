# Collegamento Uno4 → sviluppo

Rilievi del 21 ottobre 2026 sul server indicato dall'utente, tramite SSH.
Il ricevitore di questa prova è **mini-server**, non il PC sulla linea domestica
da 2,5 Gbit/s. Le misure valgono per questo percorso e vanno ripetute sul PC
se sarà la destinazione operativa. Nessun profilo di trasmissione in onda è
stato cambiato; non sono state installate dipendenze sul server remoto.

## Risultati osservati

| Prova | Risultato | Cosa misura |
| --- | --- | --- |
| ICMP, 10 pacchetti | RTT min/medio/max 29,085/30,039/31,081 ms; mdev 0,639 ms; 0/10 persi | Solo il breve campione ICMP, non perdita UDP/SRT |
| Payload SSH/TCP, offerta 20 Mbit/s per 60 s | 149.979.136 byte ricevuti; 18,1586 Mbit/s su 66,061 s tra primo e ultimo record | Goodput del test limitato, non capacità massima della linea |
| Variazione degli arrivi applicativi SSH | 20,707 ms | Stima che include buffering e ritrasmissioni TCP; non jitter UDP |
| Multicast PAL attraverso SSH, circa 20 s | 3.250.520 byte; 1,3018 Mbit/s | Bitrate osservato della sorgente, non banda disponibile |
| UDP pubblico 20879 verso Uno4 | Nessuno dei tre datagrammi di prova ricevuto | Raggiungibilità non riuscita in questa configurazione |
| Apertura UDP simultanea dei due estremi | Uno4 riceve 120 datagrammi in 12 s; sviluppo ne riceve 0 | Il traffico uscente apre il verso sviluppo → Uno4, ma il ritorno resta bloccato |
| Apertura simultanea con risposta echo | Uno4 riceve 40 datagrammi in 6 s; sviluppo ne riceve 0 | Anche le risposte agli indirizzi sorgente osservati non arrivano |

I dati del trasferimento limitato sono in
`tests/.build/contribution-ssh-20mbps.json`, quelli della cattura in
`tests/.build/contribution-live/capture.json` e della raggiungibilità in
`tests/.build/uno4-udp-reachability.json`. Questi artefatti locali non sono
versionati. TCP consegna un flusso ordinato e nasconde le ritrasmissioni:
non si può ricavare la perdita UDP dal conteggio dei byte ricevuti.
Le prove successive sono in `tests/.build/uno4-udp-rendezvous.json` e
`tests/.build/uno4-udp-echo.json`; sono verifiche di raggiungibilità con
pochi datagrammi, non misure statistiche di perdita o jitter.

## Sorgente attuale

È stato letto `udp://239.255.197.106:2079` con lo `srt-live-transmit` già
installato sul server (versione 1.4.0), usando SSH come trasporto verso
mini-server. H.264 720×576 a 25 frame/s, `field_order=tt`, BT.470bg,
range limitato; MP2 stereo 48 kHz. SAR segnalato 16:11, DAR 20:11:
confrontarlo con il playout prima di riutilizzarlo in un profilo HD.

Il rilevatore `idet` su 250 frame riporta principalmente contenuto
progressivo (235) e campi non determinati (16). Con una sorgente poco
movimentata questo non certifica la cadenza originale; servono movimento
e campi temporalmente distinguibili. Errori iniziali PPS/POC sono stati
osservati entrando nel GOP già in corso. La prova sintetica
[1080i50](1080i-validation.md) è distinta da questa sorgente PAL.

## Esecuzione ripetibile senza installazioni remote

`tests/contribution-ssh.py` richiede Python 3 già presente ai due estremi
e SSH autenticato. Genera record numerati, limita ritmo e durata e non
conserva il payload. Non inserire credenziali negli argomenti o nei log.

```sh
python3 tests/contribution-ssh.py --self-test
python3 tests/contribution-ssh.py --host utente@server \
  --control-socket /percorso/socket-ssh \
  --rate-mbps 20 --seconds 60 \
  --output tests/.build/contribution-ssh-20mbps.json
```

Il socket è opzionale se SSH è già configurato. Lo script ammette fino
a 100 Mbit/s e 60 s; il valore massimo è un limite dello strumento, non
un'autorizzazione a saturare una linea. Un timeout o un record mancante
invalida la prova. Il tempo di ricezione può superare quello di invio
quando il percorso o il processo accumulano dati nei buffer.

## Passaggio ancora necessario per SRT diretto

Il server è dietro il gateway `192.168.38.1`, con indirizzo di ingresso
`192.168.38.101`. Le policy INPUT/OUTPUT locali sono ACCEPT e la tabella NAT del
server è vuota. Senza traffico uscente i datagrammi verso
`185.67.236.4:20879` non arrivano; con apertura simultanea arrivano al
server ma le risposte verso lo sviluppo restano assenti. Le policy
INPUT/OUTPUT di mini-server sono ACCEPT; anche il percorso di ritorno
attraverso il gateway locale `192.168.100.1` richiede verifica.
Non è stato individuato un mapping automatico UPnP/NAT-PMP utilizzabile.
Non sono state aggiunte regole al firewall del server.

L'inoltro proposto è **UDP pubblico 20879 → 192.168.38.101:20879**,
da applicare sul gateway/NAT che gestisce l'indirizzo pubblico. Se esiste
un ulteriore NAT a monte, anche quel livello deve inoltrare la porta.
L'utente ha autorizzato la predisposizione; manca l'accesso di gestione
dei gateway. Il mapping sul lato Uno4, da solo, non è ancora dimostrato
sufficiente: va verificato anche il ritorno verso `45.3.58.95:20879`.
Non occorre cambiare multicast o servizi di produzione.

Dopo l'inoltro: ripetere il probe UDP temporaneo, usare mini-server come
caller SRT e il server come listener, con chiave effimera AES-256; misurare
RTT, jitter/perdita prima del recupero, ritrasmissioni e pacchetti non
recuperati, poi interrompere e riavviare il mittente. La compatibilità
SRT 1.4.0 remota ↔ 1.5.7 locale va verificata su questo percorso.
La [cifratura e riconnessione OBE](input-srt.md) sono già collaudate in
laboratorio, ma non costituiscono una prova SRT attraverso Internet.
