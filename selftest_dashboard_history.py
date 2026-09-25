import csv
import json
import tempfile
from pathlib import Path
from dashboard_history import paper_history, live_history, trade
from monitor_dashboard import paper_view
p=dict(side='LONG',entry=100,stop=98,tp1=102,tp2=104,qty=1,opened_at=1000,exit_policy='full_2r_v1')
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder)
    rows=[dict(event='PAPER_ENTRY',details=p),dict(timestamp=2000,event='PAPER_TRADE_CLOSED',details=dict(side='LONG',entry=100,stop=98,tp2=104,net_pnl_usdt=3.95,reason='TP2'))]
    (root/'v8_paper_events.jsonl').write_text('\n'.join(map(json.dumps,rows))+'\nbad')
    h=paper_history(root,'v8_btc');t=h['trades'][0]
    assert t['rr']==2 and t['realized_r']==1.975 and 'unlesbare' in h['note']
    assert t['exit'] is None
    assert paper_history(root,'v12_ada')['trades']==[]
    with (root/'trade_events.csv').open('w') as f:
        w=csv.writer(f);w.writerow(['timestamp','event','symbol','details'])
        w.writerow(['now','POSITION_TRACKING_STARTED','BTCUSDT',json.dumps({'exchange_position_id':'123'})])
    h=live_history(root,[dict(positionId='123',entryPrice='100',closePrice='102',realizedPNL='2',fee='.1',funding='0',mtime=2000),dict(positionId='manual')])
    assert len(h['trades'])==1 and h['trades'][0]['net']==1.9
    assert h['trades'][0]['rr'] is None and '1 nicht zuordenbare' in h['note']
legacy=dict(p);legacy.pop('exit_policy');legacy['tp2']=102.6
assert abs(trade({'reason':'STOP','net':-.1},legacy)['rr']-1.09)<1e-9
assert trade({'entry':100,'stop':98,'target':104,'qty':1,'net':3.9})['rr']==2
assert trade({'net':'NaN'})['net'] is None
v=paper_view({'position':p},'v8_btc')
assert v['position']['target']==104 and 'tp1' not in v['position'] and not v['legacy_position']
print('Dashboard history tests passed: 2R, legacy, missing data, corrupt log, live attribution.')
