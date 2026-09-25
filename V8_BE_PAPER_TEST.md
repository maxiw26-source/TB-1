# Separate V8 break-even paper experiment

Install on the VPS from `/root/TB-1` with:

```bash
git pull --ff-only && bash scripts/install_v8_be_paper.sh
```

BTC and ADA run as independent `lsob-v8-be-paper@BTC` and
`lsob-v8-be-paper@ADA` services. Each uses its original V8 entry profile,
100 USDT notional, its own state/events, and a complete exit at 2R.
The original V8 services and their saved positions are not modified.

After +1R is touched, a cost-covered stop is armed at the candle close
only if that close is beyond the proposed stop. It is effective on the
next candle. Existing stops take priority over targets when both are
touched in one candle. There are no partial exits in this experiment.

The BE level solves for entry fees, taker exit fees, configured stop
slippage and a buffer of 0.01% of notional (0.01 USDT at 100 USDT).
Funding is assumed zero, consistent with the existing V8 engine; this is
displayed explicitly. The model cannot guarantee a net-positive real
fill: gaps, actual fees, funding and actual slippage can differ.

Two additional dashboard cards show positions, statistics and trade
history. Their simulated PnL is excluded from the main Paper PnL total.
Neither service sends Telegram messages. No order API is added.

This is a forward comparison with matching entry rules, not a paired
comparison of identical fills. Different exit times can cause different
subsequent entries. The old fixed-stop cumulative statistics include
earlier trades; compare the same observation period, not lifetime totals.

Validation: `python selftest_v8_be_paper.py` and existing V8/dashboard tests.
Stop experiments with `systemctl stop lsob-v8-be-paper@BTC lsob-v8-be-paper@ADA`.
