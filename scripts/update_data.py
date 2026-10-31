#!/usr/bin/env python3
"""Собирает data.json для дашборда из открытого API KASE (список облигаций).

Запуск:  python scripts/update_data.py            (берёт данные с сайта)
         python scripts/update_data.py файл.json  (берёт сохранённый ответ API, для проверки)
Если данные получить не удалось, старый data.json остаётся нетронутым.
Зависимости: pip install requests beautifulsoup4
"""
import json, re, sys, time, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data.json"
ISSUERS = json.loads((Path(__file__).parent / "issuers.json").read_text(encoding="utf-8"))["issuers"]
API = ("https://kase.kz/api/instruments/securities?ticker_category=alternative_commercial_bonds,"
       "alternative_bonds,main_bonds,main_commercial_bonds,private_bonds&category=bond&is_excluded=false&search=")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; bonds-dashboard/1.0)", "Accept": "application/json",
           "Referer": "https://kase.kz/ru/markets/corporate-bonds"}


def levels(volumes):
    """Ликвидность по терцилям оборота (в тенге) среди выпусков, по которым были сделки."""
    vals = sorted(v for v in volumes.values() if v)
    if not vals:
        return {k: "low" for k in volumes}
    hi, mid = vals[int(len(vals) * 2 / 3)], vals[int(len(vals) / 3)]
    return {k: ("high" if v and v >= hi else "medium" if v and v >= mid else "low") for k, v in volumes.items()}


ISSUER_URL = "https://kase.kz/ru/listing/issuers/{code}"
VALUE = re.compile(r"(-?\d[\d ]*,\d\d)\s*(тыс\.|млн\.?)?\s*([A-Z]{3})")


def _row(text, label, n):
    i = text.find(label)
    if i < 0:
        return []
    out = []
    for num, unit, _cur in VALUE.findall(text[i + len(label): i + len(label) + 160])[:n]:
        k = {"тыс.": 1e3, "млн": 1e6, "млн.": 1e6}.get(unit, 1)
        out.append(float(num.replace(" ", "").replace(",", ".")) * k)
    return out


def parse_reporting(html, today):
    """Оценка отчётности по странице эмитента: (good / medium / weak / unknown, пояснение)."""
    from bs4 import BeautifulSoup
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    m = re.search(r"Финансовые показатели((?: \d\d\.\d\d\.\d\d)+)", text)
    if not m:
        return "unknown", "нет данных на странице эмитента"
    dates = m.group(1).split()
    d, mth, y = (int(x) for x in dates[0].split("."))
    last = dt.date(2000 + y, mth, d)
    age = (today - last).days / 30.4
    assets, liab, profit = (_row(text, lbl, len(dates)) for lbl in
                            ("Совокупные активы", "Совокупные обязательства", "Чистая прибыль"))
    if not (assets and liab and profit):
        return "unknown", f"отчётность на {dates[0]}, часть показателей не найдена"
    ok_balance, ok_profit = assets[0] > liab[0], profit[0] > 0
    note = (f"данные на {dates[0]}; активы {'больше' if ok_balance else 'не больше'} обязательств; "
            f"чистая прибыль {'есть' if ok_profit else 'отсутствует'}")
    if age > 12 or not ok_balance:
        return "weak", note
    if age <= 6 and ok_profit:
        return "good", note
    return "medium", note


def fetch_reporting(codes, today):
    import requests
    res = {}
    for code in codes:
        try:
            r = requests.get(ISSUER_URL.format(code=code), headers=HEADERS, timeout=30)
            r.raise_for_status()
            res[code] = parse_reporting(r.text, today)
        except Exception as e:
            res[code] = ("unknown", f"страница эмитента не загрузилась ({type(e).__name__})")
        time.sleep(0.5)
    return res


def ddmmyyyy(iso):
    try:
        return dt.date.fromisoformat((iso or "")[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return ""


def build(items, reporting=None):
    reporting = reporting or {}
    items = [x for x in items if x.get("code") and (x.get("ticker") or {}).get("excl_date") is None]
    lv = levels({x["code"]: x.get("volkzt") for x in items})
    companies = {}
    for x in items:
        tk = x.get("ticker") or {}
        code = x.get("org_code") or x["code"]
        info = ISSUERS.get(code, {})
        dtm = x.get("dtm")
        level = lv[x["code"]]
        if x.get("market_makers") and level == "low":
            level = "medium"                       # у выпуска есть маркет-мейкер
        bid, ask = x.get("best_bid"), x.get("best_offer")
        c = companies.setdefault(code, {
            "id": code.lower(), "kind": "corp",
            "name": info.get("name") or x.get("org_short_name_ru") or x.get("org_name_ru") or code,
            "sector": info.get("sector", ""),
            "reporting": info.get("reporting") or reporting.get(code, ("unknown", ""))[0],
            "reportingNote": info.get("note") or reporting.get(code, ("", ""))[1], "bonds": []})
        c["bonds"].append({
            "ticker": x["code"], "isin": tk.get("nin", ""),
            "currency": tk.get("currency") or x.get("currency_type") or "KZT",
            "ytm": x.get("dohod"), "price": x.get("price") or x.get("close_price"),
            "coupon": tk.get("cupon"), "couponFreq": "", "maturity": ddmmyyyy(tk.get("finish_date")),
            "termMonths": round(dtm / 30.4) if dtm is not None else None,
            "liquidity": {"level": level, "trades": x.get("dealcnt"),
                          "volume": round(x["volkzt"] / 1e6, 3) if x.get("volkzt") else None,
                          "marketMaker": bool(x.get("market_makers"))},
            "bid": {"price": bid} if bid else None, "ask": {"price": ask} if ask else None,
            "asOf": x.get("date0"),
            "kaseUrl": f"https://kase.kz/ru/investors/bonds/{x['code']}",
        })
    return {"demo": False, "source": "kase.kz", "updatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "companies": sorted(companies.values(), key=lambda c: c["name"])}


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            items = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        else:
            import requests
            r = requests.get(API, headers=HEADERS, timeout=40)
            r.raise_for_status()
            items = r.json()
        if not isinstance(items, list) or not items:
            raise RuntimeError("API вернул пустой или неожиданный ответ")
        codes = sorted({x.get("org_code") for x in items if x.get("org_code")})
        reporting = fetch_reporting(codes, dt.date.today()) if len(sys.argv) == 1 else {}
        data = build(items, reporting)
        if not data["companies"]:
            raise RuntimeError("Не найдено ни одного выпуска")
    except Exception as e:
        print("ОШИБКА:", e, file=sys.stderr)
        sys.exit(1)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Эмитентов:", len(data["companies"]), "выпусков:", sum(len(c["bonds"]) for c in data["companies"]))
