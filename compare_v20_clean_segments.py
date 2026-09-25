"""Retrospective comparison on shared complete data segments; not a full-period backtest."""
import argparse
import math
from compare_v20_filters import signals, metrics, ZONE_VARIANTS, selftest as signal_test
from backtest_v20_support_resistance import simulate, FIVE
from backtest_v12_hourly_trend import aggregate_hourly, HOUR_MS, DAY_MS
from backtest_v8_walkforward import load_csv
from backtest_v9 import timestamp_text


def valid(b):
    v = [b[k] for k in ('open','high','low','close','volume')]
    return (all(math.isfinite(x) for x in v) and min(v[:4]) > 0 and v[4] >= 0
            and b['low'] <= min(b['open'],b['close']) <= max(b['open'],b['close']) <= b['high'])


def segments(bars):
    run = []
    for b in bars:
        if not valid(b):
            if run:
                yield run
            run = []
            continue
        if run and b['timestamp'] != run[-1]['timestamp'] + FIVE:
            yield run
            run = []
        run.append(b)
    if run:
        yield run


def windows(bars, start, end):
    for run in segments(bars):
        first_hour = ((run[0]['timestamp']+HOUR_MS-1)//HOUR_MS)*HOUR_MS
        lo = max(start, first_hour + 55*HOUR_MS)
        hi = min(end, run[-1]['timestamp']+FIVE)
        # Identical 55h warmup and 24h exit allowance for every variant.
        entry_end = hi - 24*HOUR_MS
        if entry_end > lo:
            yield run, lo, entry_end, hi


def selftest():
    signal_test()
    bars = [dict(timestamp=i*FIVE, open=100.,high=101.,low=99.,close=100.,volume=1.) for i in range(1200)]
    assert len(list(segments(bars))) == 1
    gapped = bars[:600]+bars[601:]
    assert len(list(segments(gapped))) == 2
    broken = [dict(b) for b in bars]
    broken[600]['high'] = 98.
    assert len(list(segments(broken))) == 2
    w = list(windows(bars,0,1200*FIVE))
    assert len(w)==1 and w[0][1]==55*HOUR_MS and w[0][2]==76*HOUR_MS
    assert not list(windows(bars[:900],0,900*FIVE))
    print('SEGMENT SELFTEST OK: Luecken, OHLCV, gemeinsame Zeitfenster, Warmup, Auslauf')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--selftest',action='store_true')
    a=p.parse_args()
    if a.selftest:
        selftest(); return
    five=load_csv('ADAUSDT','5m')
    if not five or any(b['timestamp']<=a['timestamp'] for a,b in zip(five,five[1:])):
        raise ValueError('Fehlende/unsortierte/doppelte Daten')
    if any(b['timestamp']%FIVE for b in five):
        raise ValueError('Zeitstempel nicht auf 5m ausgerichtet')
    end=five[-1]['timestamp']//DAY_MS*DAY_MS-90*DAY_MS
    start=end-180*DAY_MS
    train=[b for b in five if start-56*HOUR_MS<=b['timestamp']<end]
    spans=list(windows(train,start,end))
    print('V20 ADA – GEMEINSAME SAUBERE TRAIN-ABSCHNITTE, 100 USDT',flush=True)
    print('Retrospektive Auswahl nach Datenqualitaet. Kein voller 180-Tage-Test; kein unabhaengiger Nachweis.',flush=True)
    print('55h Warmup; letzte 24h jedes Abschnitts nur Positionsauslauf. Keine Trades ueber Luecken.',flush=True)
    usable=sum(stop-lo for _,lo,stop,_ in spans)
    print(f'Entry-Zeiten {usable/DAY_MS:.2f}/180 Tage; ausgeschlossen {(180*DAY_MS-usable)/DAY_MS:.2f} Tage',flush=True)
    cursor=start
    for _,lo,stop,hi in spans:
        if lo>cursor:
            print('KEINE ENTRIES:',timestamp_text(cursor),'bis',timestamp_text(lo),flush=True)
        print('ENTRIES:',timestamp_text(lo),'bis',timestamp_text(stop),'| Auslauf bis',timestamp_text(hi),flush=True)
        cursor=stop
    if cursor<end:
        print('KEINE ENTRIES:',timestamp_text(cursor),'bis',timestamp_text(end),flush=True)
    if not spans:
        print('KEIN ERGEBNIS: keine ausreichend langen Abschnitte');return
    for name,ch,r,z in ZONE_VARIANTS:
        trades=[]
        for run,lo,stop,hi in spans:
            setup={t:v for t,v in signals(aggregate_hourly(run),ch,r,z).items() if lo<=t<stop}
            result=simulate(run,setup,lo,hi)
            if 'invalid' in result:
                raise ValueError(result['invalid'])
            assert all(t['reason']!='WINDOW_END' for t in result['trades'])
            trades.extend(result['trades'])
        m=metrics(trades)
        print(f"{name}: Trades {m['trades']} | PF {m['pf']:.2f} | Netto {m['net']:+.2f} USDT | Realisiert-DD {m['realized_dd_usdt']:.2f} | doppelte Gebuehren {m['double_fee_net']:+.2f}",flush=True)
    print('FERTIG: Kosten wie V20; eingeschraenkte historische Auswertung. Keine Bot-Aenderung.',flush=True)


if __name__=='__main__':
    main()
