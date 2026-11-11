#!/usr/bin/env python3
"""네이버 금융뉴스 댓글수 기준 텔레그램 알림.

News search: NAVER API HUB Search (official; legacy endpoint optional).
Comment count: NAVER news commentBox internal, unofficial (subject to change).
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

KST = timezone(timedelta(hours=9))
NOW = datetime.now(timezone.utc)
ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "data" / "sent.json"
ARTICLE_RE = re.compile(r"/article/(?:view/)?(\d{2,4})/(\d{10})(?:[/?#]|$)")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FinanceNewsCommentAlert/1.0)"}


def read_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def as_int(value: Any, name: str, lower: int = 1) -> int:
    x = int(value)
    if x < lower:
        raise ValueError(f"{name} must be >= {lower}")
    return x


def get_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"GitHub Secret / 환경변수 {name} 값이 없습니다")
    return value


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=0.8, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=frozenset(["GET"]), respect_retry_after_header=True)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(HEADERS)
    return session


def strip_tags(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", text or "")).strip()


def article_ids(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if parsed.netloc not in {"n.news.naver.com", "news.naver.com", "m.news.naver.com"}:
        return None
    match = ARTICLE_RE.search(parsed.path)
    return (match.group(1), match.group(2)) if match else None


def search_news(session: requests.Session, config: dict, client_id: str, client_secret: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=as_int(config.get("lookback_hours", 24), "lookback_hours"))
    endpoint = os.environ.get("NAVER_SEARCH_ENDPOINT", "https://naverapihub.apigw.ntruss.com/search/v1/news")
    if not endpoint.startswith("https://") or not (
        urlparse(endpoint).netloc in {"openapi.naver.com", "naverapihub.apigw.ntruss.com"}
    ):
        raise ValueError("NAVER_SEARCH_ENDPOINT는 네이버 공식 도메인만 허용됩니다")
    if urlparse(endpoint).netloc == "naverapihub.apigw.ntruss.com":
        headers = {"X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": client_secret}
    else:
        headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}

    records: dict[str, dict] = {}
    queries = config.get("queries") or []
    pages_per_query = as_int(config.get("pages_per_query", 1), "pages_per_query")
    page_size = min(100, as_int(config.get("page_size", 50), "page_size"))
    for query in queries:
        for page in range(pages_per_query):
            start = 1 + page * page_size
            if start > 1000:
                break
            resp = session.get(endpoint, params={"query": query, "display": page_size,
                  "start": start, "sort": "date"}, headers=headers, timeout=25)
            if not resp.ok:
                # 오류 원인만 출력하며 인증키 자체는 출력하지 않습니다.
                try:
                    payload = resp.json()
                except (ValueError, requests.exceptions.JSONDecodeError):
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                detail = payload.get("error", {})
                if not isinstance(detail, dict):
                    detail = {}
                code = detail.get("errorCode") or payload.get("errorCode") or payload.get("code") or "확인 불가"
                message = detail.get("message") or payload.get("errorMessage") or payload.get("message") or ""
                extra = detail.get("details") or payload.get("details") or ""
                # 응답 텍스트에서 만일의 인증정보 노출을 방지합니다.
                safe_message = (str(message) + " " + str(extra)).replace(client_id, "[MASKED]").replace(client_secret, "[MASKED]")
                safe_message = safe_message[:240].replace("\n", " ")
                print(f"[네이버 API 진단] HTTP {resp.status_code}; 오류코드={code}; 설명={safe_message}", file=sys.stderr)
                if resp.status_code == 401:
                    print("[안내] GitHub Secrets의 NAVER_CLIENT_ID/SECRET 확인 및 NAVER API HUB 이용신청 상태 점검이 필요합니다. 인증키 내용은 공유하지 마세요.", file=sys.stderr)
                if client_id[:1] in ('\"', "'") or client_secret[:1] in ('\"', "'"):
                    print("[안내] 인증키 앞에 따옴표가 포함된 것으로 보입니다. GitHub Secrets에 따옴표 없이 복사하세요.", file=sys.stderr)
            resp.raise_for_status()
            items = resp.json().get("items") or []
            any_new = False
            for raw in items:
                ids = article_ids(raw.get("link", ""))
                if not ids:
                    continue
                try:
                    pubdate = parsedate_to_datetime(raw["pubDate"]).astimezone(timezone.utc)
                except (ValueError, TypeError, KeyError):
                    continue
                if pubdate < cutoff or pubdate > now + timedelta(minutes=5):
                    continue
                title = strip_tags(raw.get("title", ""))
                snippet = strip_tags(raw.get("description", ""))
                if not topic_allowed(title, snippet, config):
                    continue
                oid, aid = ids
                key = f"{oid}/{aid}"
                any_new = True
                if key not in records:
                    records[key] = {"key": key, "title": title, "snippet": snippet,
                                    "url": f"https://n.news.naver.com/mnews/article/{oid}/{aid}",
                                    "oid": oid, "aid": aid, "published": pubdate}
            if len(items) < page_size or not any_new:
                break
    return sorted(records.values(), key=lambda r: r["published"], reverse=True)


def topic_allowed(title: str, snippet: str, config: dict) -> bool:
    haystack = f"{title} {snippet}".lower()
    included = config.get("include_terms", [])
    excluded = config.get("exclude_terms", [])
    return (not included or any(term.lower() in haystack for term in included)) and not any(
        term.lower() in haystack for term in excluded
    )


def parse_comment_response(raw: str) -> int:
    value = raw.strip()
    if not value.startswith("{"):
        first, last = value.find("("), value.rfind(")")
        if first < 0 or last <= first:
            raise ValueError("댓글 API의 JSON/JSONP 형식을 읽을 수 없습니다")
        value = value[first + 1:last]
    data = json.loads(value)
    if data.get("success") is False:
        raise RuntimeError(f"댓글 API: {data.get('code')}: {data.get('message')}")
    result = data.get("result") or {}
    count = (result.get("count") or {}).get("comment")
    if count is None:
        raise ValueError("댓글 개수 필드가 없습니다")
    return int(count)


def get_comment_count(session: requests.Session, article: dict) -> int:
    oid, aid = article["oid"], article["aid"]
    api = "https://apis.naver.com/commentBox/cbox/web_naver_list_jsonp.json"
    params = {"ticket": "news", "templateId": "default_economy", "pool": "cbox5",
              "lang": "ko", "country": "KR", "objectId": f"news{oid},{aid}",
              "pageSize": 1, "indexSize": 10, "page": 1, "sort": "FAVORITE",
              "initialize": "true"}
    headers = {"Referer": article["url"],
               "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}
    last_error: Exception | None = None
    for template in ("default_economy", "default_society"):
        params["templateId"] = template
        try:
            resp = session.get(api, params=params, headers=headers, timeout=18)
            resp.raise_for_status()
            return parse_comment_response(resp.text)
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
    raise RuntimeError(f"기사 댓글 확인 실패: {last_error}")


def load_sent() -> dict[str, str]:
    if not STATE_PATH.exists():
        return {}
    obj = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {}


def save_sent(sent: dict[str, str]):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(sent, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")


def clean_sent(sent: dict[str, str]) -> dict[str, str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    result = {}
    for key, timestamp in sent.items():
        try:
            dt = datetime.fromisoformat(timestamp)
            if dt.tzinfo and dt.astimezone(timezone.utc) >= cutoff:
                result[key] = timestamp
        except (ValueError, TypeError):
            continue
    return result


def send_telegram(session: requests.Session, bot_token: str, chat_id: str, text: str):
    response = session.post(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                  "link_preview_options": {"is_disabled": True}}, timeout=25)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {str(data)[:300]}")




# 금융정책·시장 관련 수치를 우선합니다. 단순 송고시각·날짜 표기는 가점하지 않습니다.
FINANCIAL_NUMBER_RE = re.compile(
    r"(?<![\w])(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(?:조\s*\d*\s*억?\s*원?|억\s*원|만원|원|%|％|퍼센트|%p|％p|bp|bps|"
    r"명|건|개|가구|곳|개사|배|개월|년간|분기|억원|조원|달러|유로|엔|위안|"
    r"기가와트|GW|MW|만\s*주|주|포인트|p)(?=\W|$)", re.I
)
DISPATCH_RE = re.compile(
    r"(?:\b\d{1,2}시\s*\d{0,2}분?\b|\b\d{1,2}:\d{2}\b|"
    r"\b(?:오전|오후)\s*\d{1,2}시|\b\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}\b|"
    r"\b\d{1,2}일\s*(?:오전|오후)?\s*\d{1,2}시)")
DISPATCH_WORDS = ("송고", "입력", "수정", "기사입력", "최종수정", "기자 =", "기자=", "무단 전재", "재배포")
FINANCIAL_CONTEXT = ("금융", "대출", "은행", "금리", "부채", "가계", "증권", "보험", "주가", "시장", "채권", "원화", "환율", "투자", "자본", "예산", "지원", "규제", "손실", "실적", "수익", "증가", "감소", "상승", "하락", "인하", "인상", "비율", "규모", "한도")


def concise_summary(text: str, max_chars: int = 520) -> str:
    """기사 원문에서 주요 수치가 포함된 문장 최대 3개를 발췌합니다.

    수치 자체는 원문 그대로 유지하고, 본문에 없는 숫자·사실을 생성하지 않습니다.
    기사 본문이 짧으면 3문장 미만일 수 있습니다.
    """
    text = html.unescape(re.sub(r"<[^>]*>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "기사 본문 또는 검색 소개문을 확인할 수 없습니다."
    # 마침표 뒤의 숫자(3.5%)에는 분리되지 않으며, 날짜·소수점 숫자는 보존합니다.
    raw_sentences = re.split(r"(?<=[.!?。])\s+(?=[가-힣A-Za-z0-9\(\[\"“‘])", text)
    candidates = []
    for idx, sentence in enumerate(raw_sentences):
        sentence = sentence.strip()
        if len(sentence) < 18 or any(x in sentence for x in DISPATCH_WORDS):
            continue
        # 송고 시각이나 기자명만 실린 문장은 제외합니다.
        scrubbed = DISPATCH_RE.sub("", sentence)
        if len(scrubbed.strip(" ,.-()[]")) < 18:
            continue
        counts = len(FINANCIAL_NUMBER_RE.findall(sentence))
        ctx = sum(1 for term in FINANCIAL_CONTEXT if term in sentence)
        # 초반에 등장하는 핵심 개요 문장 + 수치가 있는 본문 문장 중심
        score = min(counts, 3) * 5 + min(ctx, 4) * 0.9 + (3 if idx == 0 else 0) - idx * 0.025
        if counts == 0 and idx >= 3:
            score -= 2
        candidates.append((idx, sentence, score, counts))
    if not candidates:
        return text[:max_chars].rstrip() + ("…" if len(text) > max_chars else "")

    chosen = []
    used = 0
    # 앞부분의 핵심 맥락을 우선 포함하되, 다른 두 문장은 수치 중심으로 고릅니다.
    context = next((c for c in candidates[:4] if c[2] >= 0 and len(c[1]) <= max_chars // 2), None)
    if context is not None:
        chosen.append(context)
        used += len(context[1])
    ranked = sorted((c for c in candidates if c not in chosen), key=lambda c: c[2], reverse=True)
    for cand in ranked:
        if len(chosen) >= 3:
            break
        if len(cand[1]) + used + 1 <= max_chars:
            chosen.append(cand)
            used += len(cand[1]) + 1
    # 발췌된 문장은 원문 순서로 배열해서 맥락을 유지합니다.
    chosen.sort(key=lambda c: c[0])
    if not chosen:
        return candidates[0][1][:max_chars].rstrip() + ("…" if len(candidates[0][1]) > max_chars else "")
    return " ".join(c[1] for c in chosen)


def get_article_source(session: requests.Session, record: dict) -> tuple[str, str]:
    """공개된 기사 본문이 없을 경우 검색 소개문만 사용합니다."""
    try:
        resp = session.get(record["url"], timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        body = soup.select_one("#dic_area") or soup.select_one("#articeBody")
        if body:
            for elt in body.select("script, style, .end_photo_org, .byline_s, .article_journalist, .reporter_area"):
                elt.decompose()
            content = re.sub(r"\s+", " ", body.get_text(" ", strip=True)).strip()
            if len(content) >= 120:
                return content[:11000], "기사 본문"
    except requests.RequestException as exc:
        print(f"본문 조회 실패 {record['key']}: {type(exc).__name__}", file=sys.stderr)
    return record.get("snippet", ""), "검색 소개문"


def extract_response_text(payload: dict) -> str:
    """Responses API의 output 텍스트만 안전하게 추출합니다."""
    parts = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for piece in item.get("content", []):
            if piece.get("type") == "output_text":
                parts.append(piece.get("text", ""))
    return "\n".join(parts).strip()


def ai_summary(session: requests.Session, record: dict, api_key: str, model: str) -> tuple[str, str]:
    content, source = get_article_source(session, record)
    if not content.strip():
        return "확인 가능한 기사 내용이 부족하여 요약하지 않았습니다.", "본문 부족"
    instruction = (
        "당신은 한국 금융정책·금융시장 뉴스 브리핑 담당자입니다. 입력된 기사 이외의 외부 지식은 사용하지 마세요. "
        "한국어로 반드시 3개의 완결된 문장으로 요약하세요. 번호나 불릿 없이 한 문단으로 작성하세요. "
        "1문장: 무엇이 발표되거나 발생했는지, 2문장: 본문에 명시된 핵심 금액·비율·증감폭·지원 규모·적용 대상 등, "
        "3문장: 기사에 실제로 서술된 시장·금융회사·소비자 영향이나 쟁점. "
        "중요 수치가 여러 개면 정책과 가장 직접 관련된 1~3개를 정확한 단위로 포함하세요. "
        "송고/입력/수정시각, 기자명, 기사 번호, 주소, 단순 날짜는 핵심 수치로 취급하지 마세요. "
        "실제 시행일, 발표된 기한 등 정책적으로 중요한 날짜는 필요한 경우만 포함하세요. "
        "기사에 없는 숫자, 인과관계, 전망, 평가를 만들어내지 마세요. "
        "숫자나 영향이 없으면 없다는 사실을 자연스럽게 표현하세요. "
        "기사 본문 대신 검색 소개문만 있으면 확인된 내용으로만 간략히 서술하세요. "
        "총 400자 이내로 쓰세요."
    )
    body = {
        "model": model,
        "instructions": instruction,
        "input": f"기사제목: {record['title']}\n자료 종류: {source}\n기사내용:\n{content}",
        "max_output_tokens": 850,
        "store": False,
    }
    response = session.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body, timeout=65,
    )
    if not response.ok:
        # 보안상 API 키나 요청 본문은 출력하지 않습니다.
        print(f"AI 요약 API HTTP {response.status_code}: {record['key']}", file=sys.stderr)
        response.raise_for_status()
    summary = extract_response_text(response.json()).strip()
    if not summary:
        raise RuntimeError("AI API가 요약문을 반환하지 않았습니다")
    return summary[:420], f"AI 요약 · {source}"


def article_summary(session: requests.Session, record: dict, api_key: str, model: str) -> tuple[str, str]:
    try:
        return ai_summary(session, record, api_key, model)
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        print(f"AI 요약 실패 {record['key']}: {type(exc).__name__}; 기존 발췌 방식으로 대체", file=sys.stderr)
        content, source = get_article_source(session, record)
        return concise_summary(content, max_chars=450), f"AI 실패·{source} 발췌"

def alert_text(record: dict, count: int, threshold: int, summary: str = "", summary_source: str = "") -> str:
    published = record["published"].astimezone(KST).strftime("%m/%d %H:%M")
    title = html.escape(record["title"])
    url = html.escape(record["url"], quote=True)
    safe_summary = html.escape(summary[:450])
    return (f"🔔 <b>댓글 많은 금융뉴스</b>\n"
            f"💬 댓글 <b>{count:,}개</b> (기준 {threshold:,}개)\n"
            f"📰 {title}\n"
            f"📝 <b>요약</b> ({html.escape(summary_source)}): {safe_summary}\n"
            f"🕘 게시 {published} (한국시간)\n"
            f"🔗 <a href=\"{url}\">기사 보기</a>")


def main():
    config = read_config()
    threshold = as_int(os.environ.get("COMMENT_THRESHOLD", config.get("comment_threshold", 5)), "comment_threshold")
    max_checks = as_int(config.get("max_comment_checks", 200), "max_comment_checks")
    max_alerts = as_int(config.get("max_alerts_per_run", 10), "max_alerts_per_run")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    client_id = get_env("NAVER_CLIENT_ID")
    client_secret = get_env("NAVER_CLIENT_SECRET")
    bot_token = get_env("TELEGRAM_BOT_TOKEN") if not dry_run else ""
    chat_id = get_env("TELEGRAM_CHAT_ID") if not dry_run else ""
    api_key = get_env("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
    session = make_session()
    seen = clean_sent(load_sent())
    records = search_news(session, config, client_id, client_secret)
    pending = [r for r in records if r["key"] not in seen]
    print(f"수집 {len(records)}건 / 미발송 {len(pending)}건 / 댓글 확인 최대 {max_checks}건")
    qualifying = []
    successes = 0
    failures = 0
    for article in pending[:max_checks]:
        try:
            count = get_comment_count(session, article)
            successes += 1
            if count >= threshold:
                qualifying.append((article, count))
        except Exception as exc:
            failures += 1
            print(f"댓글 조회 실패 {article['key']}: {exc}", file=sys.stderr)
        time.sleep(0.20)
    if pending and successes == 0:
        raise RuntimeError("모든 댓글 수 조회 실패: 비공식 댓글 API 변경 또는 차단 가능성")
    print(f"댓글 확인 성공 {successes}건 / 실패 {failures}건 / 기준 충족 {len(qualifying)}건")
    qualifying.sort(key=lambda pair: pair[0]["published"], reverse=True)
    for article, count in qualifying[:max_alerts]:
        summary, summary_source = article_summary(session, article, api_key, model)
        message = alert_text(article, count, threshold, summary, summary_source)
        if dry_run:
            print("[DRY RUN]", re.sub(r"<[^>]+>", "", message))
        else:
            send_telegram(session, bot_token, chat_id, message)
            seen[article["key"]] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            print(f"전송 성공: {article['key']} 댓글 {count}건")
            # 일부 전송 후 장애 발생해도 이미 보낸 기사는 재발송하지 않도록 즉시 기록
            save_sent(seen)
        time.sleep(0.3)
    if not dry_run:
        save_sent(seen)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
