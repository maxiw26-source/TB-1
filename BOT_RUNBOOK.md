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
