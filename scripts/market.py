# -*- coding: utf-8 -*-
"""Dato del día: cotizaciones desde la API CSV gratuita de Stooq (sin clave)."""
import csv
import io

import requests

SYMBOLS = {
    "eurusd": ("EUR/USD", "eurusd"),
    "spx": ("S&P 500", "^spx"),
    "dax": ("DAX", "^dax"),
    "nikkei": ("Nikkei 225", "^nkx"),
    "brent": ("Brent", "cb.f"),
}


def get_market(timeout: int = 15):
    """Devuelve {clave: {label, price, date}} o None si falla. Nunca lanza."""
    try:
        syms = ",".join(s for _, s in SYMBOLS.values())
        url = f"https://stooq.com/q/l/?s={syms}&f=sd2t2ohlcv&h&e=csv"
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "DailyWorldBrief/1.0"})
        r.raise_for_status()
        rows = {row["Symbol"].lower(): row for row in csv.DictReader(io.StringIO(r.text))}
        out = {}
        for key, (label, sym) in SYMBOLS.items():
            row = rows.get(sym.lower())
            if row and row.get("Close") not in (None, "", "N/D"):
                out[key] = {"label": label, "price": float(row["Close"]), "date": row.get("Date", "")}
        return out or None
    except Exception:
        return None
