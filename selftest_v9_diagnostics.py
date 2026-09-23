"""Offline V9 diagnostic sanity checks."""
from diagnose_v9 import summarize


def main():
    rows = [
        {"net": 8.0, "gross": 10.0, "costs": 2.0},
        {"net": -7.0, "gross": -6.0, "costs": 1.0},
    ]
    result = summarize(rows)
    assert result["trades"] == 2
    assert result["wins"] == 1
    assert result["gross"] == 4.0
    assert result["fees"] == 3.0
    assert result["net"] == 1.0
    assert abs(result["pf"] - 8.0 / 7.0) < 1e-9
    print("V9 DIAGNOSE SELFTEST ERFOLGREICH")


if __name__ == "__main__":
    main()
