"""
보험설계사용 카톡 자동 발송 시스템 — 메인 실행 스크립트

매일 평일 아침 Windows 작업스케줄러가 이 파일을 실행합니다.
실행 흐름:
  1. credentials.json 로드 + 검증
  2. Claude API로 오늘 메시지 생성 (Opus 4.7 → Sonnet 4.6 fallback)
  3. 카카오톡 PC 자동 로그인 (pyautogui + 색상 탐색)
  4. Playwright로 bocare.co.kr 자동 발송
  5. 발송 완료 폴링 (최대 5분)
  6. sent-log.txt 기록
  7. 카카오톡 PC 자동 로그아웃
  8. Windows 토스트 알림
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRED_PATH = HERE / "credentials.json"
PROMPT_PATH = HERE / "daily-message-prompt.md"
LOG_PATH = HERE / "run.log"
SENT_LOG_PATH = HERE / "sent-log.txt"


# ---------- logging ----------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("kakao-auto")


# ---------- config ----------

REQUIRED_FIELDS = [
    "kakao_id", "kakao_pw",
    "anthropic_api_key",
    "user_signature_start", "user_signature_end_name", "user_phone",
    "customer_group_value",
]


def load_config() -> dict:
    if not CRED_PATH.exists():
        raise SystemExit(
            f"credentials.json 없음. credentials.json.template을 복사 후 채워주세요. ({CRED_PATH})"
        )
    cfg = json.loads(CRED_PATH.read_text(encoding="utf-8"))
    missing = []
    for f in REQUIRED_FIELDS:
        v = cfg.get(f)
        if not v or str(v).startswith("여기에") or v in ("", "0", 0):
            missing.append(f)
    if missing:
        raise SystemExit(f"credentials.json 미입력 필드: {missing}")
    cfg.setdefault("send_speed", "fast")
    return cfg


# ---------- message generation ----------

MODELS = ["claude-opus-4-7", "claude-sonnet-4-6"]


def read_recent_sent_log(days: int = 7) -> str:
    if not SENT_LOG_PATH.exists():
        return "(이전 발송 기록 없음 — 첫 발송)"
    lines = SENT_LOG_PATH.read_text(encoding="utf-8").splitlines()
    cutoff = dt.date.today() - dt.timedelta(days=days)
    recent = []
    for ln in lines:
        try:
            date_str = ln.split(" | ", 1)[0]
            d = dt.datetime.strptime(date_str, "%Y-%m-%d %H:%M").date()
            if d >= cutoff:
                recent.append(ln)
        except Exception:
            continue
    return "\n".join(recent[-20:]) if recent else "(최근 7일 기록 없음)"


def generate_message(cfg: dict) -> str:
    from anthropic import Anthropic
    from anthropic import APIError, APIStatusError

    client = Anthropic(api_key=cfg["anthropic_api_key"])
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    today = dt.date.today()
    weekday_kr = "월화수목금토일"[today.weekday()]
    season = ["겨울", "봄", "여름", "가을"][(today.month % 12) // 3]

    user_msg = f"""오늘 날짜: {today.isoformat()} ({weekday_kr}요일, {season})

시그니처 시작: {cfg['user_signature_start']}
시그니처 끝 이름: {cfg['user_signature_end_name']}
전화번호: {cfg['user_phone']}

[최근 7일 발송 이력]
{read_recent_sent_log(7)}

위 가이드에 맞춰 오늘 발송할 카톡 메시지 본문만 출력하세요."""

    last_err = None
    for attempt in range(5):
        for model in MODELS:
            try:
                log.info(f"메시지 생성 시도 (model={model}, attempt={attempt+1})")
                resp = client.messages.create(
                    model=model,
                    max_tokens=1500,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_msg}],
                )
                text = "".join(
                    b.text for b in resp.content if getattr(b, "type", "") == "text"
                ).strip()
                if text:
                    log.info(f"메시지 생성 성공 ({len(text)}자, model={model})")
                    return text
                last_err = "빈 응답"
            except (APIError, APIStatusError) as e:
                last_err = str(e)
                log.warning(f"API 에러 (model={model}): {e}")
                continue
            except Exception as e:
                last_err = str(e)
                log.warning(f"기타 에러 (model={model}): {e}")
                continue
        sleep_s = 2 ** attempt
        log.info(f"전 모델 실패, {sleep_s}초 대기 후 재시도")
        time.sleep(sleep_s)

    raise RuntimeError(f"메시지 생성 5회 재시도 모두 실패. 마지막 에러: {last_err}")


# ---------- kakao PC login/logout ----------

def _import_pyautogui():
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.2
    return pyautogui


def _find_yellow_talk_logo(pyautogui) -> tuple[int, int] | None:
    """카톡 로그인 창의 노란 TALK 로고 중심을 색상으로 동적 탐색.

    카카오 노랑 RGB ≈ (254, 229, 0). 화면을 격자 샘플링해서
    노란 픽셀 군집의 무게중심을 반환.
    """
    from PIL import ImageGrab
    img = ImageGrab.grab()
    w, h = img.size
    px = img.load()

    xs, ys = [], []
    step = 6
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = px[x, y][:3]
            if r >= 240 and 200 <= g <= 240 and b <= 60:
                xs.append(x); ys.append(y)
    if len(xs) < 50:
        return None
    cx, cy = sum(xs) // len(xs), sum(ys) // len(ys)
    log.info(f"TALK 로고 추정 중심: ({cx}, {cy}), 노란픽셀 {len(xs)}개")
    return cx, cy


def kakao_login(cfg: dict) -> None:
    """카카오톡 PC 자동 로그인.

    가정: 카카오톡 PC가 실행되어 있고 로그인 화면이 떠 있거나, 트레이에서 띄울 수 있음.
    """
    pyautogui = _import_pyautogui()
    import subprocess

    # 카카오톡 PC 띄우기 시도 (보통 시작 메뉴에 KakaoTalk이라는 이름으로 등록되어 있음)
    try:
        subprocess.Popen(
            ["powershell", "-Command", "Start-Process 'KakaoTalk' -ErrorAction SilentlyContinue"],
            shell=False,
        )
    except Exception as e:
        log.warning(f"카톡 자동 실행 실패 (이미 실행 중이면 무시): {e}")
    time.sleep(3)

    talk = _find_yellow_talk_logo(pyautogui)
    if talk is None:
        log.warning("카톡 로그인 화면 못 찾음 — 이미 로그인된 상태로 가정")
        return

    cx, cy = talk
    # 비밀번호 박스는 통상 TALK 로고 아래쪽 80~140px, 같은 x. 두 위치 시도.
    candidates = [(cx, cy + 110), (cx, cy + 140), (cx, cy + 80)]

    for px_x, px_y in candidates:
        pyautogui.click(px_x, px_y)
        time.sleep(0.3)
        # 비밀번호 필드 비우기
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("delete")
        time.sleep(0.1)
        pyautogui.typewrite(cfg["kakao_pw"], interval=0.04)
        time.sleep(0.3)
        pyautogui.press("enter")
        log.info(f"비밀번호 입력 시도 위치 ({px_x}, {px_y})")
        time.sleep(4)
        # 로그인 성공하면 노란 로고 사라짐
        if _find_yellow_talk_logo(pyautogui) is None:
            log.info("카톡 로그인 성공으로 추정")
            return
    log.warning("카톡 자동 로그인 모두 실패 — 발송은 시도하지만 카톡 측에서 차단될 수 있음")


def kakao_logout() -> None:
    """카카오톡 PC 로그아웃 — 사이드바 톱니바퀴 클릭 → Alt+N (다른 계정으로 로그인)."""
    pyautogui = _import_pyautogui()
    from PIL import ImageGrab
    img = ImageGrab.grab()
    w, h = img.size
    px = img.load()

    # 카톡 사이드바 어두운 회색 영역 탐색 (좌측 좁은 띠). RGB 30~70 부근.
    xs = []
    ys_max = 0
    step = 4
    sample_x_max = min(120, w // 6)
    for y in range(0, h, step):
        for x in range(0, sample_x_max, step):
            r, g, b = px[x, y][:3]
            if 25 <= r <= 75 and 25 <= g <= 75 and 25 <= b <= 75:
                xs.append(x)
                ys_max = max(ys_max, y)
    if not xs:
        log.warning("카톡 사이드바 못 찾음 — 로그아웃 스킵")
        return

    side_x = sum(xs) // len(xs)
    # 톱니바퀴는 사이드바 하단. 화면 하단에서 살짝 위.
    gear_y = ys_max - 30
    pyautogui.click(side_x, gear_y)
    time.sleep(0.6)
    # 톱니바퀴 메뉴에서 단축키 Alt+N: "다른 계정으로 로그인"
    pyautogui.hotkey("alt", "n")
    time.sleep(1)
    # 확인 대화상자가 뜨면 Enter
    pyautogui.press("enter")
    log.info(f"카톡 로그아웃 시도 (gear 추정 ({side_x}, {gear_y}))")


# ---------- bocare automation ----------

BOCARE_LOGIN_URL = "https://www.bocare.co.kr/v2/views/account/login"
BOCARE_SEND_URL = "https://www.bocare.co.kr/v2/views/crm/webzine/webzine_transmission/katok/#step/1"

SPEED_TO_VALUE = {"slow": "25", "normal": "50", "fast": "100", "very_fast": "200"}


def bocare_send(cfg: dict, message: str) -> int:
    """bocare.co.kr 자동 발송. 발송 대상 인원수 반환."""
    from playwright.sync_api import sync_playwright

    speed_value = SPEED_TO_VALUE.get(cfg.get("send_speed", "fast"), "100")
    group_value = str(cfg["customer_group_value"])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(30_000)

        # 1) 로그인 — 카카오 간편로그인 사용
        page.goto(BOCARE_LOGIN_URL)
        page.wait_for_load_state("networkidle")

        kakao_btn_selectors = [
            'a:has-text("카카오로 로그인")',
            'button:has-text("카카오로 로그인")',
            'a:has-text("카카오")',
            'button:has-text("카카오")',
            'img[alt*="카카오"]',
            '.kakao-login, .btn-kakao, .login-kakao',
        ]

        popup = None
        try:
            with ctx.expect_page(timeout=8000) as popup_info:
                for sel in kakao_btn_selectors:
                    try:
                        page.click(sel, timeout=2000)
                        break
                    except Exception:
                        continue
            popup = popup_info.value
            log.info("카카오 로그인 팝업 감지됨")
        except Exception:
            log.info("팝업 안 뜸 — 같은 창에서 카카오 OAuth로 리디렉션된 것으로 가정")

        auth_page = popup if popup is not None else page
        auth_page.wait_for_load_state("domcontentloaded")
        time.sleep(1)

        # 카카오 OAuth 페이지에서 ID/PW 입력
        for sel in ['input[name="loginId"]', 'input#loginId--1',
                    'input[name="email"]', 'input[type="email"]',
                    'input[name="loginKey"]']:
            try:
                auth_page.fill(sel, cfg["kakao_id"], timeout=2500)
                break
            except Exception:
                continue
        for sel in ['input[name="password"]', 'input#password--2',
                    'input[type="password"]']:
            try:
                auth_page.fill(sel, cfg["kakao_pw"], timeout=2500)
                break
            except Exception:
                continue
        for sel in ['button.btn_g.highlight.submit',
                    'button[type="submit"]:has-text("로그인")',
                    'button:has-text("로그인")']:
            try:
                auth_page.click(sel, timeout=2500)
                break
            except Exception:
                continue
        log.info("카카오 OAuth 로그인 제출")

        # 동의 화면이 나오면 "전체 동의 후 계속하기" 또는 "동의하고 계속하기" 클릭
        try:
            for sel in ['button:has-text("전체 동의")',
                        'button:has-text("동의하고 계속")',
                        'button:has-text("계속하기")']:
                try:
                    auth_page.click(sel, timeout=2500)
                    log.info(f"동의 화면 통과: {sel}")
                    break
                except Exception:
                    continue
        except Exception:
            pass

        # 팝업이었으면 닫힐 때까지 대기
        if popup is not None:
            try:
                popup.wait_for_event("close", timeout=15000)
            except Exception:
                pass

        page.wait_for_load_state("networkidle")
        log.info("bocare 로그인 완료")

        # 2) 발송 페이지
        page.goto(BOCARE_SEND_URL)
        page.wait_for_load_state("networkidle")
        time.sleep(1.5)

        # 3) 그룹 선택
        try:
            page.select_option("select", value=group_value)
        except Exception as e:
            log.warning(f"select_option 실패: {e} — 첫 번째 select에 강제 적용 시도")
            page.evaluate(
                """(val) => {
                    const s = document.querySelector('select');
                    s.value = val;
                    s.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                group_value,
            )
        time.sleep(1)

        # 4) 마스터 체크박스 (전체 선택) — JS click이 가장 안정적
        page.evaluate("""
            () => {
                const cb = document.querySelector('thead input[type="checkbox"], input.master, input#checkAll, input.checkall');
                if (cb) cb.click();
            }
        """)
        time.sleep(0.5)

        # 5) 발송대상추가
        try:
            page.click('button:has-text("발송대상추가"), button:has-text("발송 대상 추가")')
        except Exception as e:
            log.warning(f"발송대상추가 클릭 실패: {e}")
        time.sleep(1)

        # 6) 다음
        page.click('button:has-text("다음"), a:has-text("다음")')
        page.wait_for_load_state("networkidle")
        time.sleep(1)

        # 7) 발송 대상 인원수 파악 (best effort)
        recipients = 0
        try:
            txt = page.locator('button[type="submit"]').first.inner_text(timeout=5000)
            import re
            m = re.search(r"\[(\d+)\s*명", txt)
            if m:
                recipients = int(m.group(1))
        except Exception:
            pass

        # 8) 메시지 입력 + 이벤트 강제 트리거
        page.evaluate(
            """(msg) => {
                const ta = document.querySelector('textarea#honorifics, textarea[name="honorifics"], textarea');
                if (!ta) throw new Error('textarea 못 찾음');
                const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
                setter.call(ta, msg);
                for (const ev of ['input','change','keyup','blur']) {
                    ta.dispatchEvent(new Event(ev, { bubbles: true }));
                }
            }""",
            message,
        )
        time.sleep(0.5)

        # 9) 발송 속도 선택
        page.evaluate(
            """(val) => {
                const radios = document.querySelectorAll('input[name="katok_speed"]');
                for (const r of radios) {
                    if (r.value === val || r.id === 'katok_speed2') {
                        r.checked = true;
                        r.click();
                        r.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
            }""",
            speed_value,
        )
        time.sleep(0.5)

        # 10) 발송 버튼 (1차)
        page.evaluate("""
            () => {
                const btns = document.querySelectorAll('button[type="submit"]');
                for (const b of btns) {
                    if (b.textContent.includes('발송')) { b.click(); return; }
                }
            }
        """)
        log.info("1차 발송 버튼 클릭")
        time.sleep(2)

        # 11) 확인 모달의 #checkAlert 체크 + 모달 내 "발송" 클릭
        page.evaluate("""
            () => {
                const ck = document.querySelector('#checkAlert');
                if (ck) { ck.checked = true; ck.click(); }
            }
        """)
        time.sleep(0.3)
        page.evaluate("""
            () => {
                const modalBtns = document.querySelectorAll('.modal button, .swal2-confirm, button');
                for (const b of modalBtns) {
                    if (b.offsetParent !== null && b.textContent.trim() === '발송') {
                        b.click();
                        return;
                    }
                }
            }
        """)
        log.info("확인 모달 발송 클릭")

        # 12) 발송 완료 대기 — "카카오톡 발송 진행중" 사라질 때까지
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                visible = page.evaluate(
                    "() => document.body.innerText.includes('카카오톡 발송 진행중')"
                )
            except Exception:
                visible = False
            if not visible:
                log.info("발송 진행중 표시 사라짐 — 완료로 간주")
                break
            time.sleep(5)
        else:
            log.warning("5분 내에 발송 완료 표시 안 나타남 — 그래도 종료")

        time.sleep(3)
        ctx.close()
        browser.close()
    return recipients


# ---------- toast ----------

def toast(title: str, body: str) -> None:
    try:
        from win10toast_click import ToastNotifier
        ToastNotifier().show_toast(title, body, duration=10, threaded=True)
    except Exception as e:
        log.warning(f"토스트 알림 실패 (무시): {e}")


# ---------- sent log ----------

def append_sent_log(message: str, recipients: int) -> None:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = message.replace("\n", " ")[:120]
    SENT_LOG_PATH.open("a", encoding="utf-8").write(
        f"{now} | {recipients}명 | {summary}\n"
    )


# ---------- main ----------

def main() -> int:
    try:
        log.info("=" * 60)
        log.info("일일 카톡 자동 발송 시작")
        cfg = load_config()

        message = generate_message(cfg)
        log.info(f"오늘 메시지 ({len(message)}자):\n{message}")

        kakao_login(cfg)

        recipients = bocare_send(cfg, message)
        log.info(f"발송 완료 (대상 {recipients}명)")

        append_sent_log(message, recipients)

        try:
            kakao_logout()
        except Exception as e:
            log.warning(f"카톡 로그아웃 실패 (무시): {e}")

        toast("카톡 자동 발송 완료", f"{recipients}명에게 발송 완료")
        log.info("정상 종료")
        return 0
    except SystemExit:
        raise
    except Exception as e:
        log.error("실패:\n" + traceback.format_exc())
        toast("카톡 자동 발송 실패", f"{e}\n자세한 내용은 run.log 확인")
        return 1


if __name__ == "__main__":
    sys.exit(main())
