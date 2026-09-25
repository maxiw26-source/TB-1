"""Offline exit-policy, persistence, cost and legacy regression checks."""
import json
import math
from tempfile import TemporaryDirectory
from pathlib import Path
import live_paper_v8_btc as v8

for side, stop, target in [('LONG', 98., 104.), ('SHORT', 102., 96.)]:
    p = v8.build_position({'side': side, 'entry': 100., 'stop': stop})
    assert p['tp2'] == target and p['paper_notional_usdt'] == 100.
    assert p['exit_policy'] == v8.EXIT_POLICY
    assert v8.check_position(p, {'high': 102.1 if side == 'LONG' else 100.1,
                                 'low': 99.9 if side == 'LONG' else 97.9}) is None
    assert p['remaining'] == p['qty'] and p['stop'] == stop
    p = json.loads(json.dumps(p))
    assert v8.check_position(p, {'high': 104.1 if side == 'LONG' else 100.1,
                                 'low': 99.9 if side == 'LONG' else 95.9}) == 'TP2'
    assert p['remaining'] == 0 and p['gross'] == 4.
    assert math.isclose(p['fees'], v8.maker_fee(100., 1.) + v8.maker_fee(target, 1.))
    state = v8.default_state(); state['position'] = p
    with TemporaryDirectory() as folder:
        v8.EVENT_FILE = Path(folder) / 'events.jsonl'
        v8.notify = lambda message: None
        v8.close_trade(state, 'TP2')
    assert state['stats_by_exit_policy'][v8.EXIT_POLICY]['trades'] == 1
    assert state['stats']['trades'] == 1 and state['position'] is None
    p = v8.build_position({'side': side, 'entry': 100., 'stop': stop})
    assert v8.check_position(p, {'high': 105., 'low': 95.}) == 'STOP'
    assert p['gross'] == -2. and p['remaining'] == 0
    assert math.isclose(p['slippage'], v8.slippage(stop, 1.))
    # Existing persisted positions have no exit_policy and retain partial exits.
    old = v8.build_position({'side': side, 'entry': 100., 'stop': stop})
    del old['exit_policy']
    old['tp2'] = 102.6 if side == 'LONG' else 97.4
    assert v8.check_position(old, {'high': 102.1 if side == 'LONG' else 100.1,
                                   'low': 99.9 if side == 'LONG' else 97.9}) == 'TP1'
    assert math.isclose(old['remaining'], .3) and old['stop'] == 100.
    assert v8.check_position(old, {'high': 102.7 if side == 'LONG' else 99.9,
                                   'low': 100.1 if side == 'LONG' else 97.3}) == 'TP2'
    assert math.isclose(old['gross'], 2.18)
print('V8 PAPER 2R SELFTEST ERFOLGREICH: LONG/SHORT, Kosten, Neustart, Altpositionen.')
