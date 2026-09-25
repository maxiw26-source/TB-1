"""Allowlisted, bounded history views. Never return raw events or credentials."""
import csv
import io
import json
import math
from datetime import datetime, timezone

FILES = {'v8_btc':'v8_paper_events.jsonl','v8_ada':'v8_ada_paper_events.jsonl',
         'v12_ada':'v12_ada_paper_events.jsonl','v20_ada':'v20_ada_paper_events.jsonl'}
REASONS = {'STOP':'Stop erreicht','TP2':'Gewinnziel erreicht','TARGET':'Gewinnziel erreicht',
           'TP':'Gewinnziel erreicht','STOP_TICK':'Stop bei Kursabfrage erreicht',
           'TARGET_TICK':'Gewinnziel bei Kursabfrage erreicht','TIME':'Maximale Haltedauer',
           'TIME_TICK':'Maximale Haltedauer'}

def num(x):
    try:
        n=float(x)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError): return None

def stamp(x):
    n=num(x)
    if n is None: return None
    try: return datetime.fromtimestamp(n/1000 if n>1e11 else n,timezone.utc).isoformat()
    except (ValueError,OverflowError,OSError): return None

def tail(path):
    with path.open('rb') as f:
        f.seek(0,2); size=f.tell(); f.seek(max(0,size-2_000_000))
        if size>2_000_000: f.readline()
        return f.read().decode('utf-8',errors='replace'),size>2_000_000

def trade(d,entry=None,ts=None):
    p=entry or {}; e=num(d.get('entry',p.get('entry')))
    stop=num(d.get('initial_stop') or p.get('initial_stop') or p.get('stop') or (d.get('stop') if 'target' in d else None))
    target=num(d.get('target',d.get('tp2',p.get('target',p.get('tp2')))))
    qty=num(d.get('qty',p.get('qty'))); risk=abs(e-stop) if e is not None and stop is not None else None
    policy=d.get('exit_policy',p.get('exit_policy'))
    rr=abs(target-e)/risk if risk and target is not None else None
    legacy=policy is None and p.get('tp1') is not None
    if legacy and rr is not None:
        tp1=num(p.get('tp1')); rr=.7*abs(tp1-e)/risk+.3*rr if tp1 is not None else None
    net=num(d.get('net',d.get('net_pnl_usdt'))); gross=num(d.get('gross'))
    reason=str(d.get('reason',''))[:80]
    costs=num(d.get('fees'))
    if costs is None and num(d.get('entry_fee')) is not None and num(d.get('exit_fee')) is not None:
        costs=num(d['entry_fee'])+num(d['exit_fee'])
    return dict(side=str(d.get('side',p.get('side','—')))[:12],entry=e,exit=num(d.get('exit')),
        stop=stop,target=target,rr=rr,rr_note='70 % TP1 / 30 % TP2' if legacy else 'Vollständiges Ziel' if rr is not None else 'Nicht protokolliert',
        net=net,fees=costs,slippage=num(d.get('slippage')),
        funding=num(d.get('funding_assumed_usdt',d.get('funding_assumed'))),
        realized_r=net/(risk*qty) if net is not None and risk and qty else None,
        outcome='Gewinn' if net is not None and net>0 else 'Verlust' if net is not None and net<0 else 'Null' if net==0 else 'Unbekannt',
        reason=REASONS.get(reason,'Ausstiegsgrund nicht protokolliert'+(' ('+reason+')' if reason else '')),
        explanation='Bruttogewinn durch Kosten aufgezehrt' if gross is not None and gross>0 and net is not None and net<0 else None,
        opened_at=stamp(d.get('entry_ts',p.get('entry_ts',p.get('opened_at')))),
        closed_at=stamp(d.get('exit_ts',ts)))

def paper_history(root,key):
    result={'trades':[],'note':'Letzte 100 Abschlüsse aus bis zu 2 MB Ereignissen. Fehlende Angaben: —. R-Ergebnis nach protokollierten Kosten.'}
    try: text,limited=tail(root/FILES[key])
    except FileNotFoundError: return dict(result,note='Noch keine Ereignisdatei vorhanden.')
    except OSError: return dict(result,note='Ereignisdatei nicht lesbar.')
    entry=None; bad=0
    for line in text.splitlines():
        try:
            row=json.loads(line); d=row['details']; kind=row['event']
            if not isinstance(d,dict): raise ValueError()
            if kind in ('PAPER_ENTRY','ENTRY'): entry=d
            elif kind in ('PAPER_TRADE_CLOSED','EXIT'):
                result['trades'].append(trade(d,entry,row.get('timestamp',row.get('ts')))); entry=None
        except (ValueError,KeyError,TypeError): bad+=1
    result['trades']=result['trades'][-100:][::-1]
    if limited: result['note']+=' Ältere Ereignisse außerhalb des Lesefensters.'
    if bad: result['note']+=f' {bad} unlesbare Ereignisse ausgelassen.'
    return result

def live_history(root,rows):
    tracked={}
    try:
        text,_=tail(root/'trade_events.csv')
        for row in csv.DictReader(io.StringIO(text),fieldnames=['timestamp','event','symbol','details']):
            if row.get('event')!='POSITION_TRACKING_STARTED': continue
            try:
                d=json.loads(row['details']); pid=d.get('exchange_position_id')
                if pid: tracked[str(pid)]=d
            except (ValueError,TypeError): continue
    except OSError: pass
    trades=[]; unmatched=0
    for row in rows:
        p=tracked.get(str(row.get('positionId')))
        if not p: unmatched+=1; continue
        gross=num(row.get('realizedPNL')); fee=num(row.get('fee')); funding=num(row.get('funding'))
        net=gross-fee+funding if all(x is not None for x in (gross,fee,funding)) else None
        d=dict(entry=row.get('entryPrice'),exit=row.get('closePrice'),side=row.get('side'),
               net=net,gross=gross,fees=fee,exit_ts=row.get('mtime'))
        t=trade(d); t['rr']=None; t['rr_note']='Live-Risikoplan nicht vollständig verknüpft'; t['symbol']=str(row.get('symbol',''))[:20]; trades.append(t)
    trades.sort(key=lambda t:t['closed_at'] or '',reverse=True)
    return {'trades':trades[:100], 'note':f'Nur über Positions-ID V7 zugeordnete Börsenabschlüsse. {unmatched} nicht zuordenbare Kontopositionen ausgelassen. Börsenabfrage: maximal 100 je Symbol. Ausstiegsursache nicht von der Börse bestätigt.'}
