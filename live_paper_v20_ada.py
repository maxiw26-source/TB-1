"""V20 ADA forward paper, fixed original rules, 100 USDT, public data only.
Observed-price entries; completed 5m bars plus sampled ticker exits.
Partial entry candle excluded from OHLC exits. No historical entry replay.
"""
import fcntl
import json
import math
import sys
import time
from pathlib import Path
import backtest_v20_support_resistance as strategy
from historical_data import request_klines, normalize
from live_paper_v12_ada import fetch_price
from backtest_v12_hourly_trend import aggregate_hourly, HOUR_MS

FIVE = 300000
STATE = Path('v20_ada_paper_state.json')
EVENTS = Path('v20_ada_paper_events.jsonl')
LOCK = Path('v20_ada_paper.lock')


def load_state():
    if STATE.exists():
        s=json.loads(STATE.read_text())
        if s.get('version')!='v20_original' or not isinstance(s.get('stats'),dict):
            raise ValueError('V20 State ungueltig')
        return s
    return dict(version='v20_original',last_closed_ts=None,position=None,halted=False,
                stage='Initialisierung',error=None,notional_usdt=100,
                stats=dict(trades=0,wins=0,losses=0,net_usdt=0.,fees_usdt=0.,funding_assumed_usdt=0.))


def save(s):
    tmp=STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(s,indent=2,allow_nan=False))
    tmp.replace(STATE)


def event(kind,details):
    with EVENTS.open('a') as f:
        f.write(json.dumps(dict(ts=int(time.time()),event=kind,details=details))+'\n')
    print(kind,flush=True)


def fetch_bars(now):
    last=now//FIVE*FIVE-FIVE
    first=now//HOUR_MS*HOUR_MS-96*HOUR_MS
    seen={}
    for lo in range(first,last+1,198*FIVE):
        hi=min(last,lo+197*FIVE)
        payload=request_klines('ADAUSDT','5m',lo-FIVE,hi+FIVE)
        if payload.get('code')!=0:
            raise RuntimeError('ADA 5m API-Fehler')
        for row in payload.get('data') or []:
            b=normalize(row);ts=b['timestamp']
            if lo<=ts<=hi and ts%FIVE==0:
                if not all(math.isfinite(b[k]) and b[k]>0 for k in ('open','high','low','close')):
                    raise ValueError('Ungueltige Kerze')
                seen[ts]=b
    return [seen[t] for t in sorted(seen)]


def settle(s,raw,ts,why):
    p=s['position'];d=1 if p['side']=='LONG' else -1
    price=raw*(1-d*strategy.SLIP)
    fees=strategy.NOTIONAL*strategy.FEE+price*p['qty']*strategy.FEE
    funding=strategy.NOTIONAL*.0001*max(0,ts//(8*HOUR_MS)-p['entry_ts']//(8*HOUR_MS))
    gross=d*(price-p['entry'])*p['qty'];net=gross-fees-funding
    st=s['stats'];st['trades']+=1;st['wins' if net>0 else 'losses']+=1
    st['net_usdt']+=net;st['fees_usdt']+=fees;st['funding_assumed_usdt']+=funding
    s['position']=None
    return dict(**p,exit=price,exit_ts=ts,reason=why,net=net,fees=fees,funding_assumed=funding)


def step(s,bars,price,now,allow_entry=True):
    """Deterministic paper transition. Returns events to log after state commit."""
    if not math.isfinite(price) or price<=0:
        raise ValueError('Ungueltiger Preis')
    expected=now//FIVE*FIVE-FIVE
    if not bars or bars[-1]['timestamp']!=expected:
        raise RuntimeError('Aktuelle abgeschlossene 5m-Kerze fehlt')
    events=[];last=s['last_closed_ts'];s['error']=None
    if last is None:
        s['last_closed_ts']=expected
        s['stage']='Wartet auf Signal'
        events.append(('START',dict(last_closed_ts=expected)))
    elif s.get('halted'):
        s['stage']='Datenluecke – Pruefung erforderlich'
    else:
        new=[b for b in bars if b['timestamp']>last]
        cursor=last
        for b in new:
            ts=b['timestamp'];p=s['position']
            if p and ts!=cursor+FIVE:
                s['halted']=True;s['error']='5m-Datenluecke bei offener Paper-Position'
                events.append(('DATA_GAP',dict(after=cursor)));break
            cursor=ts
            if p and ts>=p['entry_ts']:
                d=1 if p['side']=='LONG' else -1
                hit=strategy.exit_hit(dict(p,direction=d),b)
                if hit:
                    events.append(('EXIT',settle(s,hit[0],ts+FIVE,hit[1])))
                elif ts+FIVE-p['entry_ts']>=24*HOUR_MS:
                    events.append(('EXIT',settle(s,b['close'],ts+FIVE,'TIME')))
        if s.get('halted'):
            s['stage']='Datenluecke – Pruefung erforderlich'
        else:
            p=s['position']
            if p:
                d=1 if p['side']=='LONG' else -1
                if d*(price-p['stop'])<=0:
                    events.append(('EXIT',settle(s,price,now,'STOP_TICK')))
                elif d*(price-p['target'])>=0:
                    events.append(('EXIT',settle(s,p['target'],now,'TARGET_TICK')))
                elif now-p['entry_ts']>=24*HOUR_MS:
                    events.append(('EXIT',settle(s,price,now,'TIME_TICK')))
            hour=now//HOUR_MS*HOUR_MS
            # One fresh boundary only, no entry at initial start or restart.
            if (allow_entry and s['position'] is None and expected==hour-FIVE
                    and expected-last==FIVE and 0<=now-hour<=90000):
                setup=strategy.signals(aggregate_hourly(bars)).get(hour)
                if setup:
                    d,stop,atr,level=setup;fill=price*(1+d*strategy.SLIP)
                    risk=d*(fill-stop)
                    if .5*atr<=risk<=2*atr and risk>0:
                        s['position']=dict(side='LONG' if d==1 else 'SHORT',entry=fill,
                            stop=stop,target=fill+d*2*risk,qty=100/fill,entry_ts=now,
                            level=level,notional_usdt=100)
                        events.append(('ENTRY',dict(s['position'])))
                    else:
                        events.append(('SKIP_ENTRY_DISTANCE',dict(price=price)))
            s['last_closed_ts']=expected
            s['stage']='Position offen' if s['position'] else 'Wartet auf Signal'
    s['price']=price;s['price_at_ms']=now
    p=s['position']
    s['unrealized_gross']=(1 if p['side']=='LONG' else -1)*(price-p['entry'])*p['qty'] if p else 0.
    if s.get('halted'):
        s['unrealized_gross']=None
        s['error']='Paper-Auswertung pausiert: Datenluecke bei offener Position'
    return events


def run():
    # Prevent two processes from writing to the same simulated account.
    with LOCK.open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        cached=[];fresh_start=True
        while True:
            s=load_state()
            try:
                now=int(time.time()*1000)
                if not cached or cached[-1]['timestamp']!=now//FIVE*FIVE-FIVE:
                    cached=fetch_bars(now)
                price=fetch_price();now=int(time.time()*1000)
                events=step(s,cached,price,now,allow_entry=not fresh_start)
                save(s);fresh_start=False
                for kind,details in events:
                    event(kind,details)
            except Exception as exc:
                # Reload committed state: discard incomplete in-memory transitions.
                s=load_state();s['error']=f'{type(exc).__name__}: {exc}'
                s['stage']='Datenabruf pruefen';save(s)
                print(s['error'],flush=True)
            time.sleep(20)


def main():
    cmd=sys.argv[1] if len(sys.argv)>1 else 'status'
    if cmd=='run':
        print('V20 ADA ORIGINAL PAPER gestartet – 100 USDT, keine Orders',flush=True)
        run()
    elif cmd=='status':
        print(json.dumps(load_state(),indent=2,ensure_ascii=False))
    else:
        raise SystemExit('Verfuegbar: run, status')


if __name__=='__main__':
    main()
