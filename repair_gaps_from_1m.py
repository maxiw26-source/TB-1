"""Reconstruct missing Bitunix 5m candles only from five verified 1m bars."""
import argparse
import json
import math
import time
from pathlib import Path
from historical_data import request_klines, normalize, REQUEST_DELAY_SECONDS
from repair_history_gaps import read_rows, missing_times, atomic_save, DAY_MS, STEP

MINUTE = 60000


def aggregate(items, ts):
    expected = {ts + i*MINUTE for i in range(5)}
    found = {}
    for item in items:
        c = normalize(item)
        t = c['timestamp']
        if t not in expected:
            continue
        if 'baseVol' not in item:
            raise ValueError('Volumen fehlt')
        values = [c[k] for k in ('open','high','low','close','volume')]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Nicht endliche Werte')
        if min(values[:4]) <= 0 or c['volume'] < 0 or not (
            c['low'] <= min(c['open'],c['close']) <= max(c['open'],c['close']) <= c['high']):
            raise ValueError('Ungueltige OHLCV')
        if t in found and found[t] != c:
            raise ValueError('Widerspruechliche doppelte Minute')
        found[t] = c
    if set(found) != expected:
        raise ValueError(f'Nur {len(found)}/5 Minuten vorhanden')
    bars = [found[t] for t in sorted(found)]
    return dict(timestamp=ts, open=bars[0]['open'], close=bars[-1]['close'],
                high=max(c['high'] for c in bars), low=min(c['low'] for c in bars),
                volume=sum(c['volume'] for c in bars))


def repair(symbol, days, limit):
    path = Path('historical_data') / f'{symbol}_5m.csv'
    rows = read_rows(path)
    cutoff = max(rows) - days*DAY_MS
    missing = [t for t in missing_times(sorted(rows)) if t >= cutoff]
    print(f'{symbol}: {len(missing)} Luecken; maximal {limit} Anfragen', flush=True)
    added = 0
    for ts in missing[:limit]:
        try:
            response = request_klines(symbol, '1m', ts-MINUTE, ts+STEP)
            if response.get('code') != 0:
                raise ValueError(f"API-Code {response.get('code')}")
            items = response.get('data') or []
            candle = aggregate(items, ts)
            # Re-read before saving to preserve rows added by another process.
            current = read_rows(path)
            if ts in current:
                continue
            # Persist original source response before changing the historical CSV.
            evidence = path.parent / 'reconstructed_1m_sources'
            evidence.mkdir(exist_ok=True)
            with (evidence / f'{symbol}_{ts}.json').open('w') as f:
                json.dump(dict(symbol=symbol, interval='1m', source='Bitunix kline',
                               retrieved_at=time.time(), data=items, aggregate=candle), f, allow_nan=False)
            current[ts] = candle
            atomic_save(path, current)
            added += 1
            print(f'{symbol} {ts}: aus 5/5 Minuten ergaenzt', flush=True)
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            print(f'{symbol} {ts}: NICHT ergaenzt: {exc}', flush=True)
        time.sleep(REQUEST_DELAY_SECONDS)
    remaining = sum(t >= cutoff for t in missing_times(sorted(read_rows(path))))
    print(f'FERTIG: {added} ergaenzt; {remaining} Luecken verbleiben. Bestehende Kerzen unveraendert.', flush=True)


def selftest():
    items = [dict(time=i*MINUTE, open=10+i, high=12+i, low=9+i, close=11+i, baseVol=2) for i in range(5)]
    c = aggregate(list(reversed(items)), 0)
    assert c == dict(timestamp=0, open=10, high=16, low=9, close=15, volume=10)
    for bad in (items[:-1], items+[dict(items[0], close=10)],
                [dict(x, baseVol=float('nan')) for x in items],
                [dict(x, low=100) for x in items]):
        try:
            aggregate(bad, 0)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid data accepted')
    assert aggregate(items+items, 0) == c
    print('1M-REPARATUR SELFTEST OK: OHLCV, Vollstaendigkeit, Duplikate, ungueltige Werte')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--symbol', default='ADAUSDT', choices=['ADAUSDT','ETHUSDT','BTCUSDT'])
    p.add_argument('--days', type=int, default=300)
    p.add_argument('--max-requests', type=int, default=30)
    p.add_argument('--selftest', action='store_true')
    a = p.parse_args()
    if not 1 <= a.max_requests <= 100 or a.days < 1:
        p.error('Ungueltige Tages-/Anfragenzahl')
    if a.selftest:
        selftest()
    else:
        repair(a.symbol, a.days, a.max_requests)
