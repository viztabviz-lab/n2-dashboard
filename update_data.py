"""
NH투자증권(005940) 대시보드 자동 업데이트
- pykrx: 일봉(1년) + 월봉(5년) + 경쟁사 시세
- Google News RSS: 뉴스 링크 수집
- Claude API: 뉴스 카테고리·요약
"""

import json, re, os, sys, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import anthropic
from pykrx import stock

# ── 한국 시간 기준 ────────────────────────────────────────────
KST = timezone(timedelta(hours=9))
today = datetime.now(KST)
today_str  = today.strftime("%Y%m%d")
date_label = today.strftime("%Y년 %m월 %d일")
start_1y   = (today - timedelta(days=400)).strftime("%Y%m%d")
start_5y   = (today - timedelta(days=365*5+30)).strftime("%Y%m%d")

TICKER             = "005940"
COMPANY_NAME       = "NH투자증권"
SHARES_OUTSTANDING = 367_542_536

print(f"[{COMPANY_NAME}] 업데이트 시작: {date_label} (KST)")

# ── 1. 일봉 OHLCV (1년) ──────────────────────────────────────
try:
    df = stock.get_market_ohlcv_by_date(start_1y, today_str, TICKER)
    df = df.dropna()

    # 당일 데이터가 없으면 전 거래일로 폴백 (주말·공휴일 대응)
    if df.empty:
        for days_back in range(1, 8):
            fallback = (today - timedelta(days=days_back)).strftime("%Y%m%d")
            df = stock.get_market_ohlcv_by_date(start_1y, fallback, TICKER).dropna()
            if not df.empty:
                print(f"  폴백: {fallback} 기준 데이터 사용")
                break

    if df.empty:
        raise ValueError("거래 데이터 없음")

    latest = df.iloc[-1]
    prev   = df.iloc[-2] if len(df) >= 2 else latest

    current_price    = int(latest["종가"])
    prev_close       = int(prev["종가"])
    price_change     = current_price - prev_close
    price_change_pct = round((price_change / prev_close) * 100, 2)
    volume           = int(latest["거래량"])
    open_p  = int(latest["시가"])
    high_p  = int(latest["고가"])
    low_p   = int(latest["저가"])
    mktcap_trillion  = round(current_price * SHARES_OUTSTANDING / 1e12, 2)
    week52_high = int(df["고가"].max())
    week52_low  = int(df["저가"].min())

    ohlcv_daily = [{
        "fullDate": d.strftime("%Y-%m-%d"),
        "d":        d.strftime("%m/%d"),
        "o": int(r["시가"]), "h": int(r["고가"]),
        "l": int(r["저가"]), "c": int(r["종가"]),
        "v": int(r["거래량"])
    } for d, r in df.iterrows()]

    print(f"  일봉: {len(ohlcv_daily)}일 | {current_price:,}원 ({price_change_pct:+.2f}%)")

except Exception as e:
    print(f"[ERROR] 일봉 수집 실패: {e}")
    sys.exit(1)

# ── 2. 월봉 OHLCV (5년) ──────────────────────────────────────
ohlcv_yearly = []
try:
    df5 = stock.get_market_ohlcv_by_date(start_5y, today_str, TICKER).dropna()

    # pandas 버전 호환 (2.2+ = "ME", 구버전 = "M")
    try:
        dfm = df5.resample("ME").agg({
            "시가":"first","고가":"max","저가":"min","종가":"last","거래량":"sum"
        }).dropna()
    except Exception:
        dfm = df5.resample("M").agg({
            "시가":"first","고가":"max","저가":"min","종가":"last","거래량":"sum"
        }).dropna()

    ohlcv_yearly = [{
        "fullDate": d.strftime("%Y-%m"),
        "d":        d.strftime("%Y/%m"),
        "o": int(r["시가"]), "h": int(r["고가"]),
        "l": int(r["저가"]), "c": int(r["종가"]),
        "v": int(r["거래량"])
    } for d, r in dfm.iterrows()]
    print(f"  월봉: {len(ohlcv_yearly)}개월")

except Exception as e:
    print(f"  [WARN] 월봉 수집 실패: {e}")

# ── 3. 경쟁사 현재가 ──────────────────────────────────────────
COMPETITORS = [
    # 발행주식수: KIND 공시 기준 (시총/현재가로 역산 검증)
    {"ticker":"006800","name":"미래에셋증권","color":"#E8372C","shares":215_000_000},   # 시총 ~15조
    {"ticker":"016360","name":"삼성증권",    "color":"#0076CE","shares": 89_000_000},   # 시총 ~9.9조
    {"ticker":"039490","name":"키움증권",    "color":"#FF6600","shares": 37_000_000},   # 시총 ~17조
    {"ticker":"071050","name":"한국금융지주","color":"#005BAC","shares": 60_000_000},   # 030610(교보증권) → 071050(한국금융지주) 수정
]
comp_data = []
for c in COMPETITORS:
    try:
        start_c = (today - timedelta(days=10)).strftime("%Y%m%d")
        df_c = stock.get_market_ohlcv_by_date(start_c, today_str, c["ticker"]).dropna()
        if not df_c.empty:
            price  = int(df_c.iloc[-1]["종가"])
            prev_c = int(df_c.iloc[-2]["종가"]) if len(df_c) >= 2 else price
            chg    = round((price - prev_c) / prev_c * 100, 2)
            mc     = round(price * c["shares"] / 1e12, 2)
            comp_data.append({
                **c,
                "price": price, "change_pct": chg,
                "week52_high": int(df_c["고가"].max()),
                "week52_low":  int(df_c["저가"].min()),
                "mktcap_trillion": mc
            })
            print(f"  경쟁사 {c['name']}: {price:,}원 ({chg:+.2f}%)")
        else:
            raise ValueError("빈 데이터")
    except Exception as e:
        print(f"  [WARN] {c['name']}: {e}")
        comp_data.append({
            **c, "price":0,"change_pct":0,
            "week52_high":0,"week52_low":0,"mktcap_trillion":0
        })

# ── 4. Google News RSS ────────────────────────────────────────
def _clean_title(t):
    """제목에서 '- 한국경제' 등 언론사 출처 제거"""
    return re.sub(r'\s*[-–|]\s*[^-–|\s][^-–|]{0,25}$', '', (t or '')).strip()

def _parse_pub_date(pub):
    """RSS pubDate → YYYY-MM-DD 변환"""
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(pub).strftime("%Y-%m-%d")
    except Exception:
        return pub[:10] if len(pub) >= 10 else ""

def _js_category(title):
    """JS와 동일한 기본 분류 (Claude 실패 시 폴백)"""
    t = title
    if any(k in t for k in ["실적","순이익","영업이익","매출","1Q","2Q","3Q","4Q","분기"]): return "실적·이익"
    if any(k in t for k in ["목표주가","상향","하향","매수","중립","매도","투자의견"]): return "주가전망"
    if any(k in t for k in ["거래대금","증권업","시황","업황","코스피"]): return "업황·시황"
    if any(k in t for k in ["금리","채권","금통위","기준금리"]): return "금리·채권"
    if any(k in t for k in ["IMA","발행어음","상품","서비스","출시"]): return "상품·서비스"
    if any(k in t for k in ["배당","자사주","주주환원"]): return "주주환원"
    if any(k in t for k in ["인사","대표","CEO","부사장"]): return "인사·조직"
    if any(k in t for k in ["IPO","상장","공모","청약"]): return "IPO·공모"
    return "증권·금융"

def _js_sentiment(title):
    P = ["상향","급등","호조","최고","증가","성장","개선","매수","상승","신고가","강세","돌파"]
    N = ["하향","급락","부진","하락","감소","우려","악화","손실","적자","약세"]
    if any(w in title for w in P): return "positive"
    if any(w in title for w in N): return "negative"
    return "neutral"

news_items = []
rss_url = "https://news.google.com/rss/search?q=NH투자증권+005940&hl=ko&gl=KR&ceid=KR:ko"
try:
    req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        tree = ET.parse(resp)
    channel = tree.getroot().find("channel")
    for item in (channel.findall("item") if channel else [])[:40]:
        title = _clean_title(item.findtext("title", "").strip())
        link  = item.findtext("link", "").strip()
        desc  = re.sub(r"<[^>]+>", "", item.findtext("description", "")).strip()[:150]
        pub   = _parse_pub_date(item.findtext("pubDate", "").strip())
        if not title:
            continue
        news_items.append({
            "title":     title,
            "link":      link,
            "desc":      desc,
            "date":      pub,
            "category":  _js_category(title),   # 기본 분류 (Claude로 덮어씀)
            "sentiment": _js_sentiment(title),  # 기본 감성 (Claude로 덮어씀)
        })
    # 최신순 정렬
    news_items.sort(key=lambda x: x["date"], reverse=True)
    news_items = news_items[:30]
    print(f"  뉴스: {len(news_items)}건 수집 (최신순)")
except Exception as e:
    print(f"  [WARN] RSS 수집 실패: {e}")

# ── 5. Claude API: 뉴스 분류·요약 ────────────────────────────
news_summary = f"NH투자증권은 1Q26 순이익 4,757억원으로 컨센서스를 크게 상회했습니다. 위탁매매·IB·WM 수수료 모두 호조를 보이며 IMA 신규 인가로 WM 상품 확대가 기대됩니다. 미래에셋·키움 등 주요 증권사들이 목표주가를 44,000원으로 상향하며 매수 의견을 유지했습니다."
try:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    news_list = "\n".join(
        f"{i+1}. [{n['date']}] {n['title']}"
        for i, n in enumerate(news_items)
    )
    prompt = f"""다음은 NH투자증권(005940) 관련 최신 뉴스입니다.
아래 JSON 형식으로만 응답하세요. 코드블록·설명 없이 순수 JSON만 반환하세요.

뉴스 목록:
{news_list}

{{
  "summary": "전체 뉴스를 종합한 3문장 이내 시황 요약 (한국어, 증권 전문가 어조, 구체적 수치 포함)",
  "items": [
    {{"index": 1, "category": "카테고리", "sentiment": "positive|neutral|negative", "desc": "한 줄 핵심 요약 (30자 이내)"}},
    ...뉴스 수만큼...
  ]
}}

카테고리 목록 (반드시 이 중 하나 선택):
실적·이익, 주가전망, 업황·시황, 금리·채권, 상품·서비스, 주주환원, 인사·조직, IPO·공모, 파생·ETF, 증권·금융"""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"```json|```", "", raw).strip()
    ai  = json.loads(raw)

    if ai.get("summary"):
        news_summary = ai["summary"]

    for ai_item in ai.get("items", []):
        idx = ai_item.get("index", 0) - 1
        if 0 <= idx < len(news_items):
            news_items[idx]["category"]  = ai_item.get("category")  or news_items[idx]["category"]
            news_items[idx]["sentiment"] = ai_item.get("sentiment") or news_items[idx]["sentiment"]
            if ai_item.get("desc"):
                news_items[idx]["desc"] = ai_item["desc"]

    print(f"  Claude 분석 완료 ({len(news_items)}건): {news_summary[:50]}...")

except Exception as e:
    print(f"  [WARN] Claude API 실패 (기본 분류 사용): {e}")

# ── 6. 최종 데이터 패키지 ─────────────────────────────────────
dashboard_data = {
    "company": COMPANY_NAME,
    "company_en": "NH Investment & Securities",
    "ticker": TICKER,
    "date": date_label,
    "updated_at": today.strftime("%Y-%m-%d %H:%M KST"),
    "price": {
        "current": current_price, "change": price_change,
        "change_pct": price_change_pct, "open": open_p,
        "high": high_p, "low": low_p, "prev_close": prev_close
    },
    "volume": volume,
    "market_cap_trillion": mktcap_trillion,
    "week52": {"high": week52_high, "low": week52_low},
    "fundamentals": {
        "eps": 2446, "bps": 30500, "dps": 700,
        "dividend_yield": round(700 / current_price * 100, 2),
        "pbr": round(current_price / 30500, 2),
        "per": round(current_price / 2446, 1),
        "roe": 14.2, "employees": 3200
    },
    "annual": [
        {"year":"2022","revenue":5.1,"op_income":0.62,"op_margin":12.1},
        {"year":"2023","revenue":4.8,"op_income":0.45,"op_margin":9.4},
        {"year":"2024","revenue":6.2,"op_income":0.81,"op_margin":13.1},
        {"year":"2025","revenue":8.3,"op_income":1.18,"op_margin":14.2},
    ],
    "quarterly": [
        {"q":"2025 Q2","revenue":1.96,"op_income":0.27,"yoy":"+18.7%"},
        {"q":"2025 Q3","revenue":2.21,"op_income":0.33,"yoy":"+31.2%"},
        {"q":"2025 Q4","revenue":2.31,"op_income":0.35,"yoy":"+28.4%"},
        # 2026 Q1 공식발표 (2026.04.23): 영업이익 6,367억(+120.3%), ROE 19.6%
        {"q":"2026 Q1","revenue":2.77,"op_income":0.64,"yoy":"+120.3%"},
    ],
    "competitors": comp_data,
    "analyst": {
        "consensus": "매수",
        "counts": {"buy": 12, "neutral": 0, "sell": 0},
        "target_avg": 42500, "target_high": 44000, "target_low": 37000,
        "firms": [
            {"firm":"미래에셋증권","opinion":"매수","target":44000,"note":"IMA 출시·ROE 개선 (2026.05.15)"},
            {"firm":"키움증권",   "opinion":"매수","target":44000,"note":"목표주가 상향 (2026.04.24)"},
            {"firm":"삼성증권",   "opinion":"매수","target":41000,"note":"자본시장 호황 수혜 (2026.04.24)"},
            {"firm":"한국투자증권","opinion":"매수","target":40000,"note":"WM 수익 확대"},
            {"firm":"신한투자증권","opinion":"매수","target":37000,"note":"보수적 추정"},
        ]
    },
    "chart": {
        "dates":  [d["d"] for d in ohlcv_daily],
        "prices": [d["c"] for d in ohlcv_daily]
    },
    "ohlcv":        ohlcv_daily,
    "ohlcv_yearly": ohlcv_yearly,
    "news": {"summary": news_summary, "items": news_items[:30]}
}

# ── 7. index.html 갱신 ────────────────────────────────────────
try:
    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()

    js_block = json.dumps(dashboard_data, ensure_ascii=False, indent=2)
    new_code  = f"const DASHBOARD_DATA = {js_block};"
    pattern   = r"const DASHBOARD_DATA = \{[\s\S]*?\};"

    if re.search(pattern, html):
        html = re.sub(pattern, new_code, html)
        print("[SUCCESS] index.html 정규식 교체 완료")
    else:
        html = html.replace("// __DATA_PLACEHOLDER__", new_code)
        print("[SUCCESS] index.html 플레이스홀더 교체 완료")

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  날짜: {date_label}")
    print(f"  가격: {current_price:,}원 ({price_change_pct:+.2f}%)")
    print(f"  뉴스: {len(news_items)}건")

except Exception as e:
    print(f"[ERROR] index.html 업데이트 실패: {e}")
    sys.exit(1)
