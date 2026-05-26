"""
NH투자증권(005940) 대시보드 자동 업데이트
- pykrx: 일봉(1년) + 월봉(5년) + 경쟁사 시세
- Google News RSS: 뉴스 링크 수집
- Claude API: 뉴스 카테고리·요약
"""

import json, re, os, sys, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import anthropic
from pykrx import stock

TICKER             = "005940"
COMPANY_NAME       = "NH투자증권"
SHARES_OUTSTANDING = 367_542_536

today      = datetime.now()
today_str  = today.strftime("%Y%m%d")
date_label = today.strftime("%Y년 %m월 %d일")
start_1y   = (today - timedelta(days=400)).strftime("%Y%m%d")
start_5y   = (today - timedelta(days=365*5+30)).strftime("%Y%m%d")

print(f"[{COMPANY_NAME}] 업데이트 시작: {date_label}")

# ── 1. 일봉 OHLCV (1년) ──────────────────────────────────────
try:
    df = stock.get_market_ohlcv_by_date(start_1y, today_str, TICKER).dropna()
    if df.empty:
        df = stock.get_market_ohlcv_by_date(start_1y,
             (today-timedelta(days=1)).strftime("%Y%m%d"), TICKER).dropna()

    latest = df.iloc[-1]; prev = df.iloc[-2] if len(df)>=2 else latest
    current_price    = int(latest["종가"])
    prev_close       = int(prev["종가"])
    price_change     = current_price - prev_close
    price_change_pct = round((price_change/prev_close)*100, 2)
    volume           = int(latest["거래량"])
    open_p, high_p, low_p = int(latest["시가"]), int(latest["고가"]), int(latest["저가"])
    mktcap_trillion  = round(current_price * SHARES_OUTSTANDING / 1e12, 2)
    week52_high = int(df["고가"].max()); week52_low = int(df["저가"].min())

    ohlcv_daily = [{
        "fullDate": d.strftime("%Y-%m-%d"), "d": d.strftime("%m/%d"),
        "o": int(r["시가"]), "h": int(r["고가"]), "l": int(r["저가"]),
        "c": int(r["종가"]), "v": int(r["거래량"])
    } for d, r in df.iterrows()]
    print(f"  일봉: {len(ohlcv_daily)}일 | 현재가: {current_price:,}원 ({price_change_pct:+.2f}%)")
except Exception as e:
    print(f"[ERROR] 일봉: {e}"); sys.exit(1)

# ── 2. 월봉 OHLCV (5년) ──────────────────────────────────────
try:
    df5 = stock.get_market_ohlcv_by_date(start_5y, today_str, TICKER).dropna()
    dfm = df5.resample("ME").agg({"시가":"first","고가":"max","저가":"min","종가":"last","거래량":"sum"}).dropna()
    ohlcv_yearly = [{
        "fullDate": d.strftime("%Y-%m"), "d": d.strftime("%Y/%m"),
        "o":int(r["시가"]),"h":int(r["고가"]),"l":int(r["저가"]),"c":int(r["종가"]),"v":int(r["거래량"])
    } for d, r in dfm.iterrows()]
    print(f"  월봉: {len(ohlcv_yearly)}개월")
except Exception as e:
    print(f"[WARN] 월봉: {e}"); ohlcv_yearly = []

# ── 3. 경쟁사 현재가 ──────────────────────────────────────────
COMPETITORS = [
    {"ticker":"006800","name":"미래에셋증권","color":"#E8372C","shares":3_500_000_000},
    {"ticker":"016360","name":"삼성증권",    "color":"#0076CE","shares":  97_000_000},
    {"ticker":"039490","name":"키움증권",    "color":"#FF6600","shares":  37_000_000},
    {"ticker":"030610","name":"한국금융지주","color":"#005BAC","shares":  72_000_000},
]
comp_data = []
for c in COMPETITORS:
    try:
        df_c = stock.get_market_ohlcv_by_date(
            (today-timedelta(days=5)).strftime("%Y%m%d"), today_str, c["ticker"]).dropna()
        if not df_c.empty:
            price = int(df_c.iloc[-1]["종가"])
            prev_c = int(df_c.iloc[-2]["종가"]) if len(df_c)>=2 else price
            chg = round((price-prev_c)/prev_c*100, 2)
            w52h = int(df_c["고가"].max()); w52l = int(df_c["저가"].min())
            mc   = round(price * c["shares"] / 1e12, 2)
            comp_data.append({**c, "price":price,"change_pct":chg,"week52_high":w52h,"week52_low":w52l,"mktcap_trillion":mc})
            print(f"  경쟁사 {c['name']}: {price:,}원 ({chg:+.2f}%)")
    except Exception as e:
        print(f"  [WARN] {c['name']}: {e}")
        comp_data.append({**c,"price":0,"change_pct":0,"week52_high":0,"week52_low":0,"mktcap_trillion":0})

# ── 4. Google News RSS ────────────────────────────────────────
news_items = []
rss_url = "https://news.google.com/rss/search?q=NH투자증권+주가+증권&hl=ko&gl=KR&ceid=KR:ko"
try:
    req = urllib.request.Request(rss_url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        tree = ET.parse(resp)
    root = tree.getroot(); channel = root.find("channel")
    for item in (channel.findall("item") if channel else [])[:25]:
        title = item.findtext("title","").strip()
        link  = item.findtext("link","").strip()
        desc  = item.findtext("description","").strip()
        pub   = item.findtext("pubDate","").strip()
        # 날짜 파싱
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(pub); pub_str = dt.strftime("%Y-%m-%d")
        except: pub_str = pub[:10] if len(pub)>=10 else pub
        # HTML 태그 제거
        desc  = re.sub(r"<[^>]+>","",desc).strip()[:120]
        news_items.append({"title":title,"link":link,"desc":desc,"date":pub_str,"category":"","sentiment":"neutral"})
    print(f"  뉴스: {len(news_items)}건 수집")
except Exception as e:
    print(f"  [WARN] RSS: {e}")

# ── 5. Claude API: 뉴스 분류·요약 ────────────────────────────
try:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    news_titles = "\n".join([f"{i+1}. {n['title']} ({n['date']})" for i,n in enumerate(news_items[:25])])
    prompt = f"""
오늘({date_label}) NH투자증권(005940) 관련 뉴스 목록입니다.
아래 JSON 형식으로만 응답하세요 (코드블록, 설명 없이).

뉴스 목록:
{news_titles}

{{
  "summary": "3문장 이내 핵심 시황 요약 (한국어)",
  "classifications": [
    {{"index": 1, "category": "카테고리", "sentiment": "positive|neutral|negative", "short_desc": "한 줄 설명"}},
    ...
  ]
}}

카테고리 예시: 주가전망, 실적·이익, 업황·시황, 해외동향, 금리·채권, 노사관계, IPO·기업공개, 인사·조직, 기타
인덱스는 뉴스 번호(1~25)로 매칭합니다.
"""
    resp = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=2000,
        messages=[{"role":"user","content":prompt}]
    )
    raw = re.sub(r"```json|```","",resp.content[0].text.strip()).strip()
    ai  = json.loads(raw)
    news_summary = ai.get("summary","시황 요약 없음")
    for cls in ai.get("classifications",[]):
        idx = cls["index"] - 1
        if 0 <= idx < len(news_items):
            news_items[idx]["category"]  = cls.get("category","")
            news_items[idx]["sentiment"] = cls.get("sentiment","neutral")
            news_items[idx]["desc"]      = cls.get("short_desc", news_items[idx]["desc"])
    print(f"  Claude 분류 완료: {news_summary[:40]}...")
except Exception as e:
    print(f"  [WARN] Claude: {e}")
    news_summary = f"{date_label} 기준 NH투자증권 시황을 불러오지 못했습니다."

# ── 6. 최종 데이터 패키지 ─────────────────────────────────────
dashboard_data = {
    "company": COMPANY_NAME, "company_en": "NH Investment & Securities",
    "ticker": TICKER, "date": date_label,
    "updated_at": today.strftime("%Y-%m-%d %H:%M KST"),
    "price": {
        "current": current_price, "change": price_change,
        "change_pct": price_change_pct, "open": open_p,
        "high": high_p, "low": low_p, "prev_close": prev_close
    },
    "volume": volume, "market_cap_trillion": mktcap_trillion,
    "week52": {"high": week52_high, "low": week52_low},
    # 재무 지표 (KIND/KIND DART 기준 — 매년 갱신 필요)
    "fundamentals": {
        "eps": 3850, "bps": 28400, "dps": 700,
        "dividend_yield": round(700/current_price*100, 2),
        "pbr": round(current_price/28400, 2),
        "per": round(current_price/3850, 1),
        "roe": 13.8, "employees": 3200
    },
    # 연간 실적 (최근 4개년 — 갱신 필요)
    "annual": [
        {"year":"2022","revenue":5.1,"op_income":0.62,"op_margin":12.1},
        {"year":"2023","revenue":4.8,"op_income":0.45,"op_margin":9.4},
        {"year":"2024","revenue":6.2,"op_income":0.81,"op_margin":13.1},
        {"year":"2025","revenue":7.4,"op_income":1.02,"op_margin":13.8},
    ],
    # 분기 실적 (최근 5분기 — 갱신 필요)
    "quarterly": [
        {"q":"2025 Q1","revenue":1.65,"op_income":0.21,"yoy":"+8.2%"},
        {"q":"2025 Q2","revenue":1.72,"op_income":0.24,"yoy":"+11.5%"},
        {"q":"2025 Q3","revenue":1.89,"op_income":0.27,"yoy":"+18.3%"},
        {"q":"2025 Q4","revenue":2.14,"op_income":0.30,"yoy":"+21.7%"},
        {"q":"2026 Q1","revenue":1.98,"op_income":0.26,"yoy":"+25.8%"},
    ],
    # 경쟁사
    "competitors": comp_data,
    # 애널리스트 의견 (갱신 필요)
    "analyst": {
        "consensus": "매수",
        "counts": {"buy":12,"neutral":3,"sell":0},
        "target_avg": 17500, "target_high": 22000, "target_low": 14000,
        "firms": [
            {"firm":"NH투자증권","opinion":"적극매수","target":22000,"note":"자사 추정"},
            {"firm":"미래에셋증권","opinion":"매수","target":18000,"note":"브로커리지 회복"},
            {"firm":"삼성증권","opinion":"매수","target":17500,"note":"IB 실적 호조"},
            {"firm":"키움증권","opinion":"매수","target":17000,"note":"배당 매력"},
            {"firm":"한국투자증권","opinion":"매수","target":16500,"note":"거래대금 증가"},
            {"firm":"신한투자증권","opinion":"중립","target":15000,"note":"밸류에이션 부담"},
        ]
    },
    # 차트
    "chart": {"dates":[d["d"] for d in ohlcv_daily], "prices":[d["c"] for d in ohlcv_daily]},
    "ohlcv": ohlcv_daily, "ohlcv_yearly": ohlcv_yearly,
    # 뉴스
    "news": {"summary": news_summary, "items": news_items[:25]}
}

# ── 7. index.html 갱신 ────────────────────────────────────────
try:
    with open("index.html","r",encoding="utf-8") as f: html=f.read()
    js   = json.dumps(dashboard_data, ensure_ascii=False, indent=2)
    new  = f"const DASHBOARD_DATA = {js};"
    pat  = r"const DASHBOARD_DATA = \{[\s\S]*?\};"
    html = re.sub(pat, new, html) if re.search(pat, html) else html.replace("// __DATA_PLACEHOLDER__", new)
    with open("index.html","w",encoding="utf-8") as f: f.write(html)
    print("[SUCCESS] index.html 업데이트 완료")
except Exception as e:
    print(f"[ERROR] HTML 업데이트: {e}"); sys.exit(1)
