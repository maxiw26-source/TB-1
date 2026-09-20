# LSOB V7 – Betrieb

## Start

```bash
python live_paper_v7_hardened.py
```

Der Bot:

- lädt BTCUSDT- und ETHUSDT-Livedaten,
- verwendet die bestehende V7-Strategie,
- speichert Pending-Setups dauerhaft,
- erkennt Entry, Invalidierung und Ablauf,
- erstellt bei erreichtem Entry einen Orderplan,
- wartet auf eine ausdrückliche Bestätigung,
- speichert Status und Ereignisse lokal.

## Status prüfen

```bash
python live_paper_v7_hardened.py status
```

## Orderplan bestätigen

```bash
python live_paper_v7_hardened.py approve
```

Die Bestätigung startet die interne Positionsüberwachung des bestätigten Plans. Sie sendet **keine autonome Echtgeld-Order** an Bitunix.

## Orderplan ablehnen

```bash
python live_paper_v7_hardened.py reject
```

## Persistente Dateien

Diese Dateien werden lokal auf dem VPS erzeugt und nicht zu GitHub hochgeladen:

- `runtime_state.json`
- `pending_approval.json`
- `trade_events.csv`
- `.env`

## Verhalten nach TP1

- 70 % der simulierten Restmenge werden als geschlossen markiert.
- Stop wird auf Entry (Break-even) gesetzt.
- Die restlichen 30 % werden bis TP2 oder Break-even/Stop überwacht.

## Neustart

Beim Neustart lädt der Bot automatisch:

- Pending-Setups
- offene intern überwachte Positionen
- zuletzt verarbeitete Kerzen
- Setup-IDs gegen Doppelverarbeitung

Dadurch geht der lokale Bot-Zustand bei einem normalen VPS-Neustart nicht verloren.


## Guarded Live-Ausführung

Die neue Live-Ausführungslogik liegt in `bitunix_live.py`. Sie ist standardmäßig **ausgeschaltet**.

Wichtige `.env`-Schalter:

```text
ENABLE_LIVE_EXECUTION=false
LIVE_ALLOWED_SYMBOLS=BTCUSDT,ETHUSDT
LIVE_MAX_NOTIONAL_USDT=10
LIVE_MAX_ENTRY_DEVIATION_PERCENT=0.20
LIVE_MAX_APPROVAL_AGE_SECONDS=120
BITUNIX_POSITION_MODE=ONE_WAY
```

Vor jeder möglichen Live-Order werden geprüft:

- ausdrückliche Approval-Freigabe
- kein Testplan
- Symbol-Whitelist
- Bitunix API-Support und Symbolstatus OPEN
- Mindestmenge und Mengenpräzision
- maximales Notional
- maximale Preisabweichung vom geplanten Entry
- Bestätigung nicht älter als das Zeitlimit
- keine bestehende Futures-Position
- keine bestehende offene Futures-Order
- keine Wiederholung desselben bestätigten Plans

Die Order wird mit einer eindeutigen `clientId` registriert. Falls die Antwort nach dem Senden unklar ist, wird der Versuch als `UNCERTAIN` markiert und **nicht automatisch wiederholt**.

Der Live-Entry verwendet MARKET und hängt einen vollständigen Exchange-seitigen Stop sowie TP2 als MARKET-Trigger an die Entry-Order. Das schützt die Position auch dann, wenn der VPS ausfällt.

### Guardrail-Selbsttest

```bash
python selftest_live_guardrails.py
```

Dieser Test verwendet keine echten Orders.

### Noch nicht automatisch aktiv

Die Datei `bitunix_live.py` ist vorbereitet, aber die produktive V7-Schleife führt Live-Orders noch nicht selbst aus. Das bleibt bewusst getrennt, bis der Guardrail-Test und ein API-Preflight auf dem VPS erfolgreich waren.
