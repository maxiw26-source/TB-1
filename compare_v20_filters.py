"""V20 ADA sensitivity: baseline and three predeclared single-rule changes.
TRAIN only; no TEST performance computed, no trading services modified.
"""
import argparse
import csv
from pathlib import Path
import backtest_v20_support_resistance as base
from backtest_v12_hourly_trend import HOUR_MS, DAY_MS, aggregate_hourly
from backtest_v8_walkforward import load_csv
from backtest_v9 import timestamp_text

VARIANTS = [
    ('Original', 48, 6, .25),
    ('Niveaus_24h', 24, 6, .25),
    ('Retest_12h', 48, 12, .25),
    ('Retestzone_0.40ATR', 48, 6, .40),
]

ZONE_VARIANTS = [('Original', 48, 6, .25), ('Niveaus_24h', 24, 6, .25), ('Niveaus_12h', 12, 6, .25)]

def signals(hour, channel=48, retest_hours=6, zone=.25):
    out, pending = {}, None
    for i in range(max(channel, 15), len(hour)):
        c = hour[i]
        history = hour[i-max(channel, 15):i+1]
        if any(b['timestamp']-a['timestamp'] != HOUR_MS for a,b in zip(history,history[1:])):
            pending = None
            continue
        atr = sum(max(hour[j]['high']-hour[j]['low'],
                      abs(hour[j]['high']-hour[j-1]['close']),
                      abs(hour[j]['low']-hour[j-1]['close'])) for j in range(i-14,i))/14
        if atr <= 0:
            continue
        if pending:
            d, level, born, frozen_atr = pending
            # Keep breakout level and volatility fixed until retest/expiry.
            invalid = d*(c['close']-level) < -.25*frozen_atr
            if i-born > retest_hours or invalid:
                pending = None
            else:
                touch = c['low'] <= level+zone*frozen_atr and c['high'] >= level-zone*frozen_atr
                if touch and d*(c['close']-level)>0 and d*(c['close']-c['open'])>0:
                    stop = (min(c['low'],level)-.25*frozen_atr if d==1
                            else max(c['high'],level)+.25*frozen_atr)
                    out[c['timestamp']+HOUR_MS] = (d,stop,frozen_atr,level)
                    pending = None
                continue
        top = max(b['high'] for b in hour[i-channel:i])
        bottom = min(b['low'] for b in hour[i-channel:i])
        if c['close'] > top+.1*atr:
            pending = (1,top,i,atr)
        elif c['close'] < bottom-.1*atr:
            pending = (-1,bottom,i,atr)
    return out


def metrics(trades):
    net=sum(t['net'] for t in trades)
    gain=sum(max(t['net'],0) for t in trades)
    loss=-sum(min(t['net'],0) for t in trades)
    balance=peak=dd=0.0
    for t in trades:
        balance+=t['net'];peak=max(peak,balance);dd=max(dd,peak-balance)
    return dict(trades=len(trades), net=net, pf=gain/loss if loss else (float('inf') if gain else 0),
                mean=net/len(trades) if trades else 0, realized_dd_usdt=dd,
                fees=sum(t['fees'] for t in trades),
                double_fee_net=net-sum(t['fees'] for t in trades))


def selftest():
    base.selftest()
    import random
    rng=random.Random(20)
    hour=[];price=100
    for i in range(1500):
        op=price;price=max(10,op+rng.uniform(-2,2))
        hour.append(dict(timestamp=i*HOUR_MS,open=op,close=price,
                         high=max(op,price)+rng.random(),low=min(op,price)-rng.random(),volume=1))
    assert signals(hour)==base.signals(hour), 'Baseline differs'
    for name,ch,r,z in VARIANTS + ZONE_VARIANTS:
        early=signals(hour[:1000],ch,r,z)
        full=signals(hour,ch,r,z)
        assert early=={k:v for k,v in full.items() if k<=1000*HOUR_MS},name
    for cut in (16, 20, 30, 50):
        early = signals(hour[:cut], 12)
        assert early == {k:v for k,v in signals(hour, 12).items() if k <= cut*HOUR_MS}
    print('VERGLEICH SELFTEST OK: Original identisch; alle Varianten zeitlich kausal')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--selftest',action='store_true')
    parser.add_argument('--zones', action='store_true', help='Vergleiche nur 48h, 24h und 12h Niveaus')
    args=parser.parse_args()
    if args.selftest:
        selftest();return
    print('V20 ADA FILTERVERGLEICH – NUR TRAIN, 100 USDT je Position',flush=True)
    print('Jede Variante aendert eine Regel; Stop, Ziel, Kosten und 5m-Ausfuehrung wie V20.',flush=True)
    five=load_csv('ADAUSDT','5m')
    if not five or any(b['timestamp']<=a['timestamp'] for a,b in zip(five,five[1:])):
        raise ValueError('Fehlende oder doppelte Kursdaten')
    end=five[-1]['timestamp']//DAY_MS*DAY_MS
    split=end-90*DAY_MS;start=split-180*DAY_MS
    # Historical TEST is not passed to signal generation or simulation.
    train=[b for b in five if b['timestamp']<split]
    hour=aggregate_hourly(train)
    covered=sum(start<=b['timestamp']<split for b in hour)/(180*24)
    print(f'TRAIN {timestamp_text(start)} bis {timestamp_text(split)} | 1h-Abdeckung {covered:.1%}',flush=True)
    if covered<.95 or not hour or hour[0]['timestamp']>start-49*HOUR_MS:
        raise ValueError('TRAIN-Abdeckung oder Warmup unzureichend')
    rows=[]
    for name,ch,r,z in (ZONE_VARIANTS if args.zones else VARIANTS):
        setup=signals(hour,ch,r,z)
        result=base.simulate(train,setup,start,split)
        if 'invalid' in result:
            print(name,'KEIN ERGEBNIS:',result['invalid'],flush=True)
            continue
        trades=result['trades'];m=metrics(trades)
        # Attribute realized trade PnL by exit into three 60-day blocks.
        blocks=[sum(t['net'] for t in trades if start+j*60*DAY_MS<t['exit_ts']<=start+(j+1)*60*DAY_MS) for j in range(3)]
        print(f"{name} | Trades {m['trades']} | PF {m['pf']:.2f} | Netto {m['net']:+.2f} | je Trade {m['mean']:+.3f} | Realisiert-DD {m['realized_dd_usdt']:.2f} USDT | Netto doppelte Gebuehr {m['double_fee_net']:+.2f}",flush=True)
        print('  60-Tage-Bloecke (Exit-Zuordnung): '+ ' / '.join(f'{v:+.2f}' for v in blocks)+' USDT',flush=True)
        rows.append(dict(variant=name,**m,block1=blocks[0],block2=blocks[1],block3=blocks[2]))
    if rows:
        folder=Path('v20_filter_results');folder.mkdir(exist_ok=True)
        with (folder/('ADAUSDT_train_zones.csv' if args.zones else 'ADAUSDT_train_comparison.csv')).open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print('FERTIG. TRAIN-Vergleich ist Entwicklung, kein unabhaengiger Profitabilitaetsnachweis. Keine Orders.',flush=True)


if __name__=='__main__':
    main()
