#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
원주시 및 강원특별자치도 청년 정책 자동 모니터링 및 AI 스마트 알림 봇
-----------------------------------------------------------------
[사용자 프로필]
- 연령: 2003년생 (만 23세 청년)
- 주민등록 주소지: 강원특별자치도 원주시 (원주시 및 강원도 지자체 수혜 자격)
- 전공 및 분야: 화훼조형, 원예, 전시 디스플레이, 문화예술 기획, 청년 창업, 구직·취업 지원
- 상태: 대학 졸업예정 / 청년 미취업자 (인천 현장실무 및 학업 병행 중)
[알림 채널]
- Gmail 이메일 (Port 465 SSL / 587 듀얼 포트 지원, 일일 브리핑 단일 통합 발송) 및 텔레그램 푸시 지원
"""

import os
import sys
import json
import logging
import re
import smtplib
import unicodedata
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("PolicyMonitor")

# 환경변수 로드 및 특수문자/공백 정규화
def clean_ascii_secret(val):
    if not val:
        return ""
    normalized = unicodedata.normalize("NFKD", val)
    cleaned = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", "", cleaned)

NOTIFICATION_CHANNEL = os.getenv("NOTIFICATION_CHANNEL", "email").lower()
GMAIL_USER = os.getenv("GMAIL_USER", "").strip()
GMAIL_APP_PASSWORD = clean_ascii_secret(os.getenv("GMAIL_APP_PASSWORD", ""))
RECEIVER_EMAIL = (os.getenv("RECEIVER_EMAIL", "").strip()) or GMAIL_USER

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
HISTORY_FILE = os.getenv("HISTORY_FILE", "history.json")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

EXCLUDE_KEYWORDS = [
    "거주불명자", "행정상 관리주소", "최고공고", "직권말소", "무단전출",
    "도로명주소", "교통유발부담금", "공시송달", "수의계약", "재결신청",
    "개별공시지가", "분묘개장", "토지거래허가", "지적재조사", "산지전용",
    "폐기물", "정화조", "단수안내", "체납", "과태료"
]

USER_PROFILE_PROMPT = """
당신은 대한민국 청년 정책 매칭 전문 컨설턴트 AI입니다.
다음 청년의 구체적 프로필을 기준으로 공고문이 해당 청년에게 실제로 유효하고 신청 가능한지 엄격하게 검증하세요.

[사용자 프로필]
1. 연령: 2003년생 (현재 만 23세, 청년기본법 및 강원도 청년 기준 만 18~34세 또는 39세 범위에 완벽 부합)
2. 거주지: 주민등록 주소지 공식 강원특별자치도 원주시 (실제 생활은 인천에서 현장 실습 중이나 지자체 권리는 원주시/강원도에 묶여 있음)
3. 학적/구직상태: 화훼조형 및 전시 디스플레이 전공, 대학 졸업예정 및 미취업 구직자
4. 희망/관련 분야:
   - 화훼, 원예, 조경, 농업/농식품(청년농업인, 스마트팜 등)
   - 전시 디스플레이, 문화예술 기획, 공간연출
   - 청년 창업 자금 및 시제품 제작 지원
   - 청년 구직활동지원금, 취업준비쿠폰, 자격증 시험 응시료, 면접비, 주거비/월세 지원 등 보편적 청년 지원 사업

[공고문 정보]
- 출처: {source}
- 제목: {title}
- 본문 발췌:
{content}

[판단 기준 및 출력 요구사항]
1. `is_eligible`: 해당 청년이 신청 자격을 만족하고(나이, 거주지 원주/강원, 미취업/졸업예정), 관심 분야 또는 청년 지원 혜택에 부합하면 true, 나이 초과/타 시군 한정/일반 기업 대상 등 부적합하면 false.
2. `match_reason`: 부합하는 핵심 사유 1~2문장 (예: "만 23세 원주시 거주 미취업 청년 대상 시험 응시료 최대 10만 원 지원 사업으로 신청 자격 충족")
3. `summary_3lines`: 공고문을 일일이 읽지 않아도 즉시 행동할 수 있는 핵심 3줄 요약
   - line1 (지원 내용 및 금액): 구체적 지원 사항과 액수/혜택
   - line2 (신청 자격): 연령, 주소지, 소득/학적 등 핵심 자격 요건
   - line3 (접수 마감일 및 링크): 신청 기간/마감일과 접수 방법

반드시 아래 JSON 형식으로만 응답하세요(코드블록 없이 순수 JSON만 반환):
{{
  "is_eligible": true 또는 false,
  "match_reason": "부합 이유",
  "summary_3lines": {{
    "line1_support": "지원 내용 및 금액",
    "line2_qualification": "신청 자격 요건",
    "line3_deadline": "접수 마감일 및 링크"
  }}
}}
"""


def load_history():
    """이미 처리한 공고 목록 로드 (메일 발송 미완료 건은 자동 복구)"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
                for k, v in list(history.items()):
                    if v.get("status") == "matched_and_notified" and not v.get("mail_delivered", False):
                        del history[k]
                return history
        except Exception as e:
            logger.warning(f"히스토리 파일 로드 실패: {e}")
    return {}


def save_history(history):
    """처리한 공고 목록 저장"""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"히스토리 파일 저장 실패: {e}")


def build_digest_html(matched_items):
    """여러 건의 정책을 깔끔한 모바일 반응형 카드 형태로 하나로 묶는 템플릿"""
    cards_html = ""
    for idx, item in enumerate(matched_items, 1):
        summary = item.get("summary_3lines", {})
        clean_title = re.sub(r"[\r\n\t]+", " ", item["title"]).strip()
        cards_html += f"""
        <div style="background: #ffffff; border: 1px solid #e1e4e8; border-radius: 10px; margin-bottom: 20px; padding: 20px; box-shadow: 0 2px 6px rgba(0,0,0,0.04);">
          <div style="margin-bottom: 10px;">
            <span style="background: #e8f0fe; color: #1a73e8; font-size: 12px; font-weight: bold; padding: 3px 8px; border-radius: 12px;">#{idx} 🏛 {item['source']}</span>
            <h3 style="font-size: 16px; margin: 8px 0; color: #202124; line-height: 1.4;">{clean_title}</h3>
          </div>
          <div style="background: #f8f9fa; border-left: 3px solid #1a73e8; padding: 10px 14px; margin-bottom: 14px; font-size: 13px; color: #3c4043; line-height: 1.5;">
            <strong>🎯 맞춤 선정 사유:</strong> {item['match_reason']}
          </div>
          <div style="font-size: 13px; line-height: 1.6; color: #3c4043;">
            <p style="margin: 4px 0;"><b>• 지원 내용:</b> {summary.get('line1_support', '-')}</p>
            <p style="margin: 4px 0;"><b>• 신청 자격:</b> {summary.get('line2_qualification', '-')}</p>
            <p style="margin: 4px 0;"><b>• 접수 마감:</b> {summary.get('line3_deadline', '-')}</p>
          </div>
          <div style="margin-top: 14px;">
            <a href="{item['url']}" target="_blank" style="display: inline-block; background: #1a73e8; color: #ffffff !important; text-decoration: none; padding: 8px 18px; border-radius: 6px; font-size: 13px; font-weight: bold;">공고문 원문 및 신청 바로가기 →</a>
          </div>
        </div>
        """

    full_html = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', sans-serif; background-color: #f6f8fa; margin: 0; padding: 20px;">
      <div style="max-width: 640px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #1a73e8, #174ea6); color: #ffffff; padding: 24px; border-radius: 12px 12px 0 0; text-align: left;">
          <h2 style="margin: 0 0 6px 0; font-size: 20px;">🌿 원주·강원 청년정책 맞춤 알림</h2>
          <p style="margin: 0; font-size: 13px; opacity: 0.95;">조건에 부합하는 신규 지원사업 <b>{len(matched_items)}건</b>이 발굴되었습니다.</p>
        </div>
        <div style="background: #ffffff; padding: 20px; border-radius: 0 0 12px 12px; border: 1px solid #e1e4e8; border-top: none;">
          {cards_html}
          <div style="text-align: center; padding-top: 14px; font-size: 12px; color: #5f6368; border-top: 1px solid #f1f3f4;">
            본 메일은 2003년생 원주시 주민등록 청년(화훼·원예·전시·창업·구직 지원) 맞춤 자동화 봇에 의해 발송되었습니다.
          </div>
        </div>
      </div>
    </body>
    </html>
    """
    return full_html


def send_gmail_digest(matched_items):
    """Gmail SMTP를 통한 브리핑 이메일 전송 (Port 465 SSL 우선 접속으로 연결 끊김 원천 차단)"""
    if not matched_items:
        return True
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        logger.warning("GMAIL_USER 또는 GMAIL_APP_PASSWORD가 비어 있어 메일 전송을 건너뜁니다.")
        return False

    count = len(matched_items)
    if count == 1:
        clean_title = re.sub(r"[\r\n\t]+", " ", matched_items[0]["title"]).strip()
        subject_str = f"[원주·강원 청년정책 알림] {clean_title}"
    else:
        first_title = re.sub(r"[\r\n\t]+", " ", matched_items[0]["title"]).strip()
        subject_str = f"[원주·강원 청년정책 알림] {first_title} 외 {count-1}건"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject_str, "utf-8")
    msg["From"] = formataddr((str(Header("청년정책 비서", "utf-8")), GMAIL_USER))
    msg["To"] = RECEIVER_EMAIL

    html_content = build_digest_html(matched_items)
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    # 1차 시도: Port 465 SSL (GitHub Actions / 클라우드 환경에서 가장 안정적이며 차단 없음)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        logger.info(f"📧 Gmail 발송 성공 (Port 465 SSL) -> {RECEIVER_EMAIL} ({count}건 수록)")
        return True
    except Exception as e1:
        logger.warning(f"Port 465 SSL 접속 실패 ({e1}), Port 587로 재시도합니다.")

    # 2차 시도: Port 587 STARTTLS (폴백)
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=25) as server:
            server.ehlo()
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        logger.info(f"📧 Gmail 발송 성공 (Port 587 TLS) -> {RECEIVER_EMAIL} ({count}건 수록)")
        return True
    except Exception as e2:
        logger.error(f"Gmail 발송 최종 실패: {e2}")
        return False


def send_telegram_digest(matched_items):
    """텔레그램 푸시 알림 발송"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    success_all = True
    for item in matched_items:
        summary = item.get("summary_3lines", {})
        clean_title = re.sub(r"[\r\n\t]+", " ", item["title"]).strip()
        message_text = (
            f"🌿 <b>[원주/강원 맞춤 청년정책 알림]</b>\n\n"
            f"📌 <b>{clean_title}</b>\n"
            f"🏛 <b>출처:</b> {item['source']}\n\n"
            f"💡 <b>맞춤 선정 사유:</b>\n"
            f"{item['match_reason']}\n\n"
            f"📋 <b>핵심 3줄 실행 요약:</b>\n"
            f"• <b>지원 내용:</b> {summary.get('line1_support', '-')}\n"
            f"• <b>신청 자격:</b> {summary.get('line2_qualification', '-')}\n"
            f"• <b>접수 마감:</b> {summary.get('line3_deadline', '-')}\n\n"
            f"🔗 <a href='{item['url']}'>공고문 바로가기 (원문 링크)</a>"
        )
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        try:
            res = requests.post(api_url, json=payload, timeout=10)
            if res.status_code != 200:
                success_all = False
        except Exception:
            success_all = False
    return success_all


def call_llm_filter(title, source, content, url):
    """Google Gemini API 또는 OpenAI를 활용한 2차 AI 심층 필터링 및 3줄 요약"""
    prompt = USER_PROFILE_PROMPT.format(
        source=source,
        title=title,
        content=content[:3000]
    )

    if GEMINI_API_KEY:
        try:
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
            }
            res = requests.post(gemini_url, json=payload, timeout=20)
            if res.status_code == 200:
                result_json = res.json()
                text_resp = result_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                return json.loads(text_resp)
        except Exception as e:
            logger.error(f"Gemini API 호출 오류: {e}")

    if OPENAI_API_KEY:
        try:
            res = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"}
                },
                timeout=20
            )
            if res.status_code == 200:
                text_resp = res.json()["choices"][0]["message"]["content"]
                return json.loads(text_resp)
        except Exception as e:
            logger.error(f"OpenAI API 호출 오류: {e}")

    # 규칙 기반 정밀 폴백
    content_lower = content.lower()
    title_lower = title.lower()
    is_youth = any(k in title_lower or k in content_lower for k in ["청년", "만 18세", "만 19세", "만 34세", "만 39세", "미취업"])
    is_domain = any(k in title_lower or k in content_lower for k in ["원주", "강원", "도내", "구직", "취업", "창업", "화훼", "원예", "농업", "응시료", "면접"])

    if is_youth and is_domain:
        return {
            "is_eligible": True,
            "match_reason": "만 23세 원주시 주민등록 청년 지원 요건 및 구직/창업/특화 분야 키워드 일치",
            "summary_3lines": {
                "line1_support": "공고문 상세 내용 및 지원 혜택 참조 (링크 확인)",
                "line2_qualification": "원주시/강원도 거주 만 18~34세(또는 39세) 청년 및 미취업자",
                "line3_deadline": "공고문 세부 마감일정 참조 (하단 원문 링크 바로가기)"
            }
        }

    return {"is_eligible": False}


def fetch_wonju_city_notices():
    """1. 원주시청 공식 고시공고 수집"""
    notices = []
    base_url = "https://www.wonju.go.kr/www/selectBbsNttList.do?bbsNo=140&key=216"
    try:
        res = requests.get(base_url, headers=DEFAULT_HEADERS, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            rows = soup.select("table.p-table tbody tr, table.board-list tbody tr, tbody tr")
            for row in rows:
                link_elem = row.select_one("a")
                if not link_elem:
                    continue
                title = link_elem.get_text(strip=True)
                href = link_elem.get("href", "")
                ntt_match = re.search(r"nttNo=(\d+)", href)
                ntt_no = ntt_match.group(1) if ntt_match else href
                detail_url = urljoin("https://www.wonju.go.kr", href)
                notices.append({
                    "id": f"wonju_city_{ntt_no}",
                    "title": title,
                    "url": detail_url,
                    "source": "원주시청 공고/고시"
                })
    except Exception as e:
        logger.error(f"원주시청 공고 수집 오류: {e}")
    return notices


def fetch_wonju_youth_center():
    """2. 원주시 청년지원센터 새소식 공고 수집"""
    notices = []
    base_url = "https://www.wonju.go.kr/wjyouth/selectBbsNttList.do?bbsNo=1190&key=5882"
    try:
        res = requests.get(base_url, headers=DEFAULT_HEADERS, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            rows = soup.select("table tbody tr")
            for row in rows:
                link_elem = row.select_one("a")
                if not link_elem:
                    continue
                title = link_elem.get_text(strip=True)
                href = link_elem.get("href", "")
                ntt_match = re.search(r"nttNo=(\d+)", href)
                ntt_no = ntt_match.group(1) if ntt_match else href
                detail_url = urljoin("https://www.wonju.go.kr/wjyouth/", href)
                notices.append({
                    "id": f"wonju_youth_{ntt_no}",
                    "title": title,
                    "url": detail_url,
                    "source": "원주시 청년지원센터"
                })
    except Exception as e:
        logger.error(f"원주시 청년지원센터 수집 오류: {e}")
    return notices


def fetch_gangwon_youth_portal():
    """3. 강원특별자치도 청년포털 및 일자리정보망 공고 수집"""
    notices = []
    base_url = "https://job.gwd.go.kr/gwjob/job/jobs_news"
    try:
        res = requests.get(base_url, headers=DEFAULT_HEADERS, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            rows = soup.select("table tbody tr, ul.list_wrap li")
            for row in rows:
                link_elem = row.select_one("a")
                if not link_elem:
                    continue
                title = link_elem.get_text(strip=True)
                href = link_elem.get("href", "")
                seq_match = re.search(r"articleSeq=(\d+)", href)
                seq_no = seq_match.group(1) if seq_match else href
                detail_url = urljoin("https://job.gwd.go.kr", href)
                notices.append({
                    "id": f"gangwon_youth_{seq_no}",
                    "title": title,
                    "url": detail_url,
                    "source": "강원청년포털·일자리정보망"
                })
    except Exception as e:
        logger.error(f"강원청년포털 수집 오류: {e}")
    return notices


def fetch_detail_content(url):
    """공고 상세 본문 텍스트 추출 (타임아웃 10초 설정)"""
    try:
        res = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            content_div = (
                soup.select_one(".bbs_detail_content") or
                soup.select_one(".board_view_cont") or
                soup.select_one(".view_con") or
                soup.select_one(".p-view") or
                soup.select_one("article") or
                soup.select_one("main")
            )
            if content_div:
                return content_div.get_text(separator="\n", strip=True)
            return soup.get_text(separator="\n", strip=True)[:3000]
    except Exception as e:
        logger.warning(f"상세 본문 추출 생략 ({url}): {e}")
    return ""


def main():
    logger.info(f"=== 원주시/강원도 청년정책 모니터링 시작 (채널: {NOTIFICATION_CHANNEL}) ===")
    history = load_history()

    sources = [
        fetch_wonju_youth_center(),
        fetch_wonju_city_notices(),
        fetch_gangwon_youth_portal()
    ]

    all_scraped = []
    for s in sources:
        all_scraped.extend(s)

    logger.info(f"총 {len(all_scraped)}건의 최신 공고를 수집했습니다.")

    matched_items = []
    for notice in all_scraped:
        n_id = notice["id"]
        title = notice["title"]

        if n_id in history:
            continue

        if any(bad_kw in title for bad_kw in EXCLUDE_KEYWORDS):
            history[n_id] = {"status": "excluded_keyword", "title": title}
            continue

        logger.info(f"신규 공고 분석 대상: [{notice['source']}] {title}")
        content = fetch_detail_content(notice["url"])

        analysis = call_llm_filter(title, notice["source"], content, notice["url"])

        if analysis.get("is_eligible", False):
            reason = analysis.get("match_reason", "조건 부합")
            summary = analysis.get("summary_3lines", {})
            logger.info(f"🎯 적합 정책 발굴! -> {title}")

            notice["match_reason"] = reason
            notice["summary_3lines"] = summary
            matched_items.append(notice)
        else:
            history[n_id] = {"status": "unmatched", "title": title}

    # 적합 정책이 발굴된 경우 통합 브리핑 발송
    if matched_items:
        logger.info(f"총 {len(matched_items)}건의 적합 정책을 통합 브리핑으로 발송합니다.")
        email_sent = True
        if NOTIFICATION_CHANNEL in ["email", "all"]:
            email_sent = send_gmail_digest(matched_items)

        if NOTIFICATION_CHANNEL in ["telegram", "all"]:
            send_telegram_digest(matched_items)

        if email_sent:
            for item in matched_items:
                history[item["id"]] = {
                    "status": "matched_and_notified",
                    "mail_delivered": True,
                    "title": item["title"],
                    "notified_at": datetime.now().isoformat(),
                    "summary": item.get("summary_3lines", {})
                }
            logger.info(f"=== 모니터링 완료: {len(matched_items)}건 발송 성공 및 이력 기록 완료 ===")
        else:
            logger.warning("발송 실패로 인해 재시도를 위해 이력에 저장하지 않았습니다.")
    else:
        logger.info("=== 모니터링 완료: 오늘 새로 발굴된 신규 적합 정책이 없습니다. ===")

    save_history(history)


if __name__ == "__main__":
    main()
