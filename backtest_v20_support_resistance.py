"""Research V20: prior 48h support/resistance breakout followed by retest.
Fixed first-pass rules, causal hourly signals, execution on actual 5m bars.
No order API. Reused historical TEST is not an independent holdout.
"""
import argparse
import csv
from collections import Counter
from pathlib import Path
from backtest_v8_walkforward import load_csv
from backtest_v12_hourly_trend import aggregate_hourly, HOUR_MS, DAY_MS
from backtest_v9 import timestamp_text, TAKER_FEE_PCT, SLIPPAGE_PCT

FIVE = 300000
NOTIONAL = 100.0
FEE = TAKER_FEE_PCT / 100
SLIP = SLIPPAGE_PCT / 100


def signals(hour):
    out, pending = {}, None
    for i in range(48, len(hour)):
        c = hour[i]
        history = hour[i-48:i+1]
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
            if i-born > 6 or invalid:
                pending = None
            else:
                touch = c['low'] <= level+.25*frozen_atr and c['high'] >= level-.25*frozen_atr
                if touch and d*(c['close']-level)>0 and d*(c['close']-c['open'])>0:
                    stop = (min(c['low'],level)-.25*frozen_atr if d==1
                            else max(c['high'],level)+.25*frozen_atr)
                    out[c['timestamp']+HOUR_MS] = (d,stop,frozen_atr,level)
                    pending = None
                continue
        top = max(b['high'] for b in hour[i-48:i])
        bottom = min(b['low'] for b in hour[i-48:i])
        if c['close'] > top+.1*atr:
            pending = (1,top,i,atr)
        elif c['close'] < bottom-.1*atr:
            pending = (-1,bottom,i,atr)
    return out


def exit_hit(p,b):
    d=p['direction']; stop=p['stop']; target=p['target']
    if (b['low']<=stop if d==1 else b['high']>=stop):
        return (min(b['open'],stop) if d==1 else max(b['open'],stop)), 'STOP'
    if (b['high']>=target if d==1 else b['low']<=target):
        return target,'TARGET'
    return None


def simulate(five,setup,start,end):
    bars={b['timestamp']:b for b in five if start<=b['timestamp']<end}
    trades=[]; p=None; skipped=Counter(); previous=None
    def close(raw,ts,reason):
        nonlocal p
        price=raw*(1-p['direction']*SLIP)
        fees=NOTIONAL*FEE+price*p['qty']*FEE
        gross=p['direction']*(price-p['entry'])*p['qty']
        funding=NOTIONAL*.0001*max(0,ts//(8*HOUR_MS)-p['entry_ts']//(8*HOUR_MS))
        trades.append(dict(entry_ts=p['entry_ts'],exit_ts=ts,side='LONG' if p['direction']==1 else 'SHORT',
                           entry=p['entry'],exit=price,level=p['level'],stop=p['stop'],target=p['target'],
                           gross=gross,fees=fees,funding_assumed=funding,net=gross-fees-funding,reason=reason))
        p=None
    for ts,b in sorted(bars.items()):
        if p and previous is not None and ts-previous != FIVE:
            return {'invalid':'Fehlende 5m-Kerze bei offener Position: '+timestamp_text(previous+FIVE)}
        previous=ts
        if p is None and ts in setup:
            d,stop,atr,level=setup[ts]
            entry=b['open']*(1+d*SLIP)
            risk=d*(entry-stop)
            if .5*atr<=risk<=2*atr and risk>0:
                p=dict(direction=d,entry=entry,stop=stop,target=entry+d*2*risk,
                       qty=NOTIONAL/entry,entry_ts=ts,level=level)
            else:
                skipped['ENTRY_DISTANCE']+=1
        if p:
            hit=exit_hit(p,b)
            if hit:
                close(hit[0],ts+FIVE,hit[1])
            elif ts+FIVE-p['entry_ts']>=24*HOUR_MS:
                close(b['close'],ts+FIVE,'TIME')
    if p:
        if end-FIVE not in bars:
            return {'invalid':'Letzte Ausfuehrungskerze fehlt'}
        close(bars[end-FIVE]['close'],end,'WINDOW_END')
    missing=sum(start<=ts<end and ts not in bars for ts in setup)
    if missing:
        return {'invalid':f'{missing} Entry-Kerzen fehlen'}
    return {'trades':trades,'skipped':dict(skipped)}


def selftest():
    h=[dict(timestamp=i*HOUR_MS,open=100,close=100,high=101,low=99,volume=1) for i in range(48)]
    h += [dict(timestamp=48*HOUR_MS,open=100,close=102,high=103,low=100,volume=1),
          dict(timestamp=49*HOUR_MS,open=101,close=102,high=103,low=101,volume=1)]
    s=signals(h)
    assert 50*HOUR_MS in s and 49*HOUR_MS not in s
    future=h+[dict(timestamp=50*HOUR_MS,open=1000,close=1000,high=1001,low=999,volume=1)]
    assert {k:v for k,v in signals(future).items() if k<=50*HOUR_MS}==s
    p=dict(direction=1,stop=98,target=104)
    assert exit_hit(p,dict(open=100,high=105,low=97))==(98,'STOP')
    assert exit_hit(p,dict(open=95,high=100,low=94))==(95,'STOP')
    start=50*HOUR_MS
    bars=[dict(timestamp=start,open=102,close=102,high=102.1,low=101.9),
          dict(timestamp=start+2*FIVE,open=102,close=102,high=102.1,low=101.9)]
    assert 'invalid' in simulate(bars,s,start,start+3*FIVE)
    bars=[dict(timestamp=start,open=102,close=102,high=102.1,low=101.9)]
    trade=simulate(bars,s,start,start+FIVE)['trades'][0]
    assert trade['fees']>0 and trade['net']<0
    print('V20 SELFTEST OK: kein Zukunftswissen, Retest erst nach Ausbruch, Stop-first, Gap, Kosten')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--symbols',nargs='+',default=['BTCUSDT','ETHUSDT','ADAUSDT'])
    parser.add_argument('--selftest',action='store_true')
    args=parser.parse_args()
    if args.selftest:
        selftest(); return
    print('V20 SUPPORT/RESISTANCE RETEST – NUR HISTORISCH',flush=True)
    print('48h Hoch/Tief; 1h-Ausbruch >0.1 ATR; Retest innerhalb 6h mit Bestaetigung; Einstieg naechstes 5m-Open.',flush=True)
    print('100 USDT/Trade; Stop hinter Retest, Ziel 2R, max 24h; 5m Stop-first. '
          f'Taker {FEE:.3%}/Seite, Slippage {SLIP:.3%}/Seite; Funding-Annahme 0.01% je 8h immer Kosten.',flush=True)
    print('TRAIN 180 Tage / TEST 90 Tage. TEST bereits mehrfach eingesehen; kein unabhaengiger Nachweis.',flush=True)
    for symbol in args.symbols:
        print('\n'+symbol,flush=True)
        try:
            five=load_csv(symbol,'5m')
            if len(five)<600 or any(b['timestamp']-a['timestamp']<=0 for a,b in zip(five,five[1:])):
                raise ValueError('Zu wenig Daten oder doppelte Zeitstempel')
            hour=aggregate_hourly(five)
            end=five[-1]['timestamp']//DAY_MS*DAY_MS
            split=end-90*DAY_MS; start=split-180*DAY_MS
            if not hour or hour[0]['timestamp']>start-49*HOUR_MS:
                raise ValueError('Zu wenig Warmup')
            setup=signals(hour)
            for label,lo,hi in [('TRAIN',start,split),('TEST',split,end)]:
                coverage=sum(lo<=b['timestamp']<hi for b in hour)/((hi-lo)/HOUR_MS)
                n=sum(lo<=ts<hi for ts in setup)
                print(f'{label} {timestamp_text(lo)} bis {timestamp_text(hi)} | 1h-Abdeckung {coverage:.1%} | Setups {n}',flush=True)
                if coverage<.95:
                    print('KEIN ERGEBNIS: Datenabdeckung unter 95%');continue
                r=simulate(five,setup,lo,hi)
                if 'invalid' in r:
                    print('KEIN ERGEBNIS:',r['invalid']);continue
                t=r['trades']; gains=sum(max(x['net'],0) for x in t);losses=-sum(min(x['net'],0) for x in t)
                pf=gains/losses if losses else (float('inf') if gains else 0)
                print(f"{label} Trades {len(t)} | Gewinner {sum(x['net']>0 for x in t)} | PF {pf:.2f} | Netto {sum(x['net'] for x in t):+.2f} USDT | Gebuehren {sum(x['fees'] for x in t):.2f} | Funding-Annahme {sum(x['funding_assumed'] for x in t):.2f} | Uebersprungen {r['skipped']}",flush=True)
                folder=Path('v20_results');folder.mkdir(exist_ok=True)
                with (folder/f'{symbol}_{label.lower()}.csv').open('w',newline='') as f:
                    writer=csv.DictWriter(f,fieldnames=['entry_ts','exit_ts','side','entry','exit','level','stop','target','gross','fees','funding_assumed','net','reason'])
                    writer.writeheader();writer.writerows(t)
        except (OSError,ValueError) as exc:
            print('KEIN ERGEBNIS:',exc,flush=True)
    print('FERTIG – CSVs in v20_results; keine Orders.',flush=True)


if __name__=='__main__':
    main()
