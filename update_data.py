#!/usr/bin/env python3
"""Собирает data.json для дашборда из публичных страниц KASE.

ВАЖНО: адреса и названия колонок KASE (SOURCES, COLUMNS) заданы по общей логике сайта
и НЕ проверены на живых страницах. Перед первым запуском откройте страницы в браузере
и поправьте SOURCES/COLUMNS. При любой ошибке старый data.json остаётся нетронутым.
Зависимости: pip install requests beautifulsoup4
"""
import json, re, sys, datetime as dt
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data.json"
ISSUERS = json.loads((Path(__file__).parent / "issuers.json").read_text(encoding="utf-8"))

SOURCES = {"bonds": "https://kase.kz/ru/bonds/"}           # TODO: проверить адрес списка облигаций
COLUMNS = {                                                 # заголовок колонки -> поле
    "код": "ticker", "isin": "isin", "валюта": "currency", "цена": "price",
    "доходность": "ytm", "купон": "coupon", "погашение": "maturity",
    "покупка": "bid_price", "объём покупки": "bid_volume",
    "продажа": "ask_price", "объём продажи": "ask_volume", "время заявки": "ask_time",
    "сделок за 30 дней": "trades30d", "объём за 30 дней": "volume30d",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; bonds-dashboard/1.0)"}


def num(s):
    s = re.sub(r"[^\d,.\-]", "", (s or "").replace("\xa0", "").replace(" ", "")).replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def ytm(price, coupon, freq, years, face=100.0):
    """Доходность к погашению (% годовых), метод бисекции. price — % от номинала."""
    n = max(1, round(years * freq))
    c = face * coupon / 100 / freq
    def pv(y):
        r = y / freq
        return sum(c / (1 + r) ** k for k in range(1, n + 1)) + face / (1 + r) ** n
    lo, hi = -0.5, 5.0
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if pv(mid) > price else (lo, mid)
    return round(mid * 100, 2)


def term_months(maturity, now):
    try:
        d = dt.datetime.strptime(maturity.strip(), "%d.%m.%Y").replace(tzinfo=dt.timezone.utc)
        return max(0, round((d - now).days / 30.4))
    except ValueError:
        return None


def liquidity_level(trades, volume_mln, cur):
    ref = 200 if cur == "KZT" else 0.4
    if trades >= 20 and volume_mln >= ref: return "high"
    if trades >= 5: return "medium"
    return "low"


def fetch_rows():
    html = requests.get(SOURCES["bonds"], headers=HEADERS, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        raise RuntimeError("Таблица облигаций не найдена: проверьте SOURCES и вёрстку KASE")
    heads = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
    idx = {COLUMNS[h]: i for i, h in enumerate(heads) if h in COLUMNS}
    for need in ("ticker", "currency", "price"):
        if need not in idx:
            raise RuntimeError(f"Нет колонки «{need}». Найдены: {heads}")
    rows = []
    for tr in table.find_all("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) >= len(heads):
            rows.append({k: tds[i] for k, i in idx.items()})
    return rows


def build(rows):
    now = dt.datetime.now(dt.timezone.utc)
    companies = {}
    for r in rows:
        t = r["ticker"]
        issuer = next((v for p, v in ISSUERS["issuers"].items() if t.startswith(p)), None)
        if not issuer:          # эмитент не в списке: государственные и неизвестные бумаги пропускаем
            continue
        cur = r["currency"].upper()
        ask_p, bid_p = num(r.get("ask_price")), num(r.get("bid_price"))
        c = companies.setdefault(issuer["id"], {
            "id": issuer["id"], "name": issuer["name"], "sector": issuer.get("sector", ""),
            "reporting": issuer["reporting"], "reportingNote": issuer.get("note", ""), "bonds": []})
        c["bonds"].append({
            "ticker": t, "isin": r.get("isin", ""), "currency": cur,
            "ytm": num(r.get("ytm")), "price": num(r["price"]), "coupon": num(r.get("coupon")),
            "couponFreq": "раз в полгода", "maturity": r.get("maturity", ""), "termMonths": term_months(r.get("maturity", ""), now),
            "liquidity": {"level": liquidity_level(num(r.get("trades30d")) or 0, num(r.get("volume30d")) or 0, cur),
                          "trades30d": num(r.get("trades30d")), "volume30d": num(r.get("volume30d"))},
            "bid": {"price": bid_p, "volume": num(r.get("bid_volume"))} if bid_p else None,
            "ask": {"price": ask_p, "volume": num(r.get("ask_volume")),
                    "confirmedAt": r.get("ask_time") or None} if ask_p else None,
            "kaseUrl": f"https://kase.kz/ru/investors/bonds/{t}",
        })
    return {"demo": False, "source": "kase.kz", "updatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "companies": list(companies.values())}


if __name__ == "__main__":
    try:
        data = build(fetch_rows())
        if not data["companies"]:
            raise RuntimeError("Не найдено ни одного выпуска из списка эмитентов")
    except Exception as e:                      # старый data.json не трогаем
        print("ОШИБКА:", e, file=sys.stderr)
        sys.exit(1)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Записано выпусков:", sum(len(c["bonds"]) for c in data["companies"]))
