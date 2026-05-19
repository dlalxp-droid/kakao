# 보험설계사용 카톡 자동 발송 시스템

매일 평일 아침 정해진 시각에 bocare.co.kr을 통해 보험 고객 전원에게 카카오톡을 자동 발송합니다.
메시지 본문은 Claude API(Opus 4.7)가 매일 새로 생성합니다.

## 빠른 시작 (Windows)

```powershell
# 1. 이 리포 clone
git clone <repo-url> 카톡자동발송
cd 카톡자동발송\automation

# 2. 셋업 스크립트 실행 (관리자 PowerShell)
powershell -ExecutionPolicy Bypass -File .\setup.ps1

# 3. credentials.json 채우기 (메모장)
notepad credentials.json

# 4. 즉시 테스트 실행 (실제 발송됨 — 주의!)
Start-ScheduledTask -TaskName "BocareKakaoAutoSend"
```

## 사전 요구사항

- Windows 10/11
- Python 3.10+ (PATH 등록 필수)
- 카카오톡 PC 설치 및 본인 계정 로그인 가능 상태
- 보케어톡 PC 프로그램 설치 및 항상 실행 상태
- bocare.co.kr 유료 계정 (**카카오 간편로그인** 사용 가정 — bocare에 카카오 계정으로 연결돼 있어야 함)
- Anthropic API 키 (https://console.anthropic.com/settings/keys)

## 파일 구조

```
automation/
├── main.py                      메인 실행 스크립트
├── credentials.json.template    자격증명 템플릿
├── credentials.json             (직접 생성) 실제 자격증명 — gitignore됨
├── daily-message-prompt.md      메시지 톤 가이드 (자유 수정 가능)
├── requirements.txt             Python 의존성
├── setup.ps1                    Windows 셋업 스크립트
├── sent-log.txt                 (자동 생성) 발송 이력
└── run.log                      (자동 생성) 실행 로그
```

## 운영

### 메시지 톤 바꾸기

`daily-message-prompt.md` 편집. main.py 재시작 필요 없음 (매 실행시 파일 다시 읽음).

### 발송 시각 바꾸기

`setup.ps1`의 `$SendTime` 수정 후 다시 실행. 또는:

```powershell
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "08:00"
Set-ScheduledTask -TaskName "BocareKakaoAutoSend" -Trigger $t
```

### 자주 쓰는 명령

```powershell
Get-ScheduledTaskInfo -TaskName "BocareKakaoAutoSend"   # 다음 실행 시각
Start-ScheduledTask    -TaskName "BocareKakaoAutoSend"   # 지금 실행
Disable-ScheduledTask  -TaskName "BocareKakaoAutoSend"   # 일시 정지
Enable-ScheduledTask   -TaskName "BocareKakaoAutoSend"   # 다시 가동
Unregister-ScheduledTask -TaskName "BocareKakaoAutoSend" -Confirm:$false   # 삭제
```

### 로그 확인

```powershell
Get-Content .\run.log -Tail 50          # 최근 50줄
Get-Content .\sent-log.txt -Tail 10     # 발송 이력
```

## 자동화 흐름

매일 평일 07:25 (기본):

1. `credentials.json` 로드 + 필드 검증
2. `daily-message-prompt.md` 시스템 프롬프트로 Claude API 호출
   - Opus 4.7 → 과부하 시 Sonnet 4.6 fallback
   - 최대 5회 재시도 (exponential backoff)
3. 카카오톡 PC 자동 로그인
   - 화면을 캡처해 노란 TALK 로고 색상 (RGB ≈ 254,229,0) 무게중심 탐색
   - 그 아래쪽 좌표를 비밀번호 박스로 추정해 클릭 → 비밀번호 입력 → Enter
4. Playwright Chromium으로 bocare.co.kr 자동화
   - "카카오로 로그인" 버튼 클릭 → 카카오 OAuth 팝업에서 `kakao_id`/`kakao_pw` 입력 → 동의 화면 자동 통과
   - 카톡 발송 페이지 (`#step/1`)
   - 그룹 선택 (`customer_group_value`)
   - 마스터 체크박스 (전체 선택) → 발송대상추가 → 다음
   - 메시지 입력 (`textarea#honorifics` + `dispatchEvent` 사이트 state sync)
   - 발송 속도 `빠름` 라디오 선택
   - 발송 버튼 클릭 → 확인 모달 `#checkAlert` 체크 + `발송` 클릭
5. "카카오톡 발송 진행중" 텍스트 사라질 때까지 폴링 (최대 5분)
6. `sent-log.txt`에 발송 기록 추가 (날짜, 인원수, 메시지 요약)
7. 카카오톡 PC 자동 로그아웃
   - 사이드바 어두운 회색 띠 검출 → 톱니바퀴 추정 클릭 → `Alt+N` (다른 계정 로그인)
8. Windows 토스트 알림 (성공/실패 + 인원수)

## ⚠️ 알려진 주의사항

### 1. 카카오톡 자동 로그인은 약관 회색지대
카카오 약관상 자동화 도구로 로그인 반복은 권장되지 않습니다. 매일 반복하면 카카오가 "비정상 활동"으로 감지해 계정을 일시 잠금할 가능성이 있습니다. **본업 카톡 계정과 분리된 영업용 부계정 사용을 강력 권장**합니다.

### 2. PC 상태 의존
- PC가 꺼져있으면 자동 실행 안 됨 → 다음 평일로 미뤄짐
- 절전 모드: 작업스케줄러가 PC를 깨우도록 `StartWhenAvailable` 옵션 켜둠
- 보케어톡 PC 프로그램이 실행 중이어야 함 (작업표시줄 확인)

### 3. 좌표 변동 가능성
- 카카오톡 PC 창 위치가 매번 다르면 로그인/로그아웃 실패 가능
- main.py에 동적 색상 탐색 로직 있지만 100% 보장 안 됨
- **첫 며칠은 모니터링 권장**

### 4. 메시지 내용 책임
- Claude가 생성한 메시지는 사실 검증되지 않습니다
- 보험 청구 가능 여부 등 디테일은 사용자가 매일 발송 전 점검 권장

### 5. 비용
- Claude API: 매일 메시지 1통 ≈ 1센트 미만 (월 약 200원)
- bocare 유료 구독료는 별도 (이 시스템과 무관)

## 문제 해결

### "credentials.json 미입력 필드: [...]"
해당 필드를 메모장으로 직접 채우세요. `여기에_...` 자리표시자를 실제 값으로 교체.

### "메시지 생성 5회 재시도 모두 실패"
- Anthropic API 키 만료/오타 → `credentials.json` 확인
- 일시적 서버 과부하라면 그날만 수동 발송, 다음 날 자동 복구

### "카톡 로그인 화면 못 찾음"
- 카톡 PC가 실행 안 되어 있거나 이미 로그인 상태
- 이미 로그인 상태면 정상 (스크립트가 로그인 건너뜀)
- 진짜 실패면 카톡 PC 창을 화면 좌측 또는 중앙에 띄워두기

### "발송 진행중에서 멈춤"
- 카톡 PC 로그아웃 상태 → 자동 로그인 실패 가능
- `run.log`에서 "비밀번호 입력 시도 위치" 확인
- 보케어톡 PC 프로그램 미실행 가능 → 작업표시줄 확인

### 메시지 너무 길거나 너무 짧음
`daily-message-prompt.md`의 "길이: 150~350자" 부분 수정

## 메시지 톤 수정

`daily-message-prompt.md`만 편집하면 됩니다. main.py 수정 불필요.

예시: 더 가벼운 톤으로 바꾸려면 가이드의 "베테랑 보험설계사" → "친근한 동네 보험설계사", 본문 길이 "150~350자" → "100~200자" 등.

## 직접 한 번 테스트하기 (개발용)

```powershell
.\.venv\Scripts\python.exe main.py
```

## 라이선스

개인 사용 용도. bocare.co.kr 및 카카오톡 PC 약관을 준수하는 범위 내에서 사용하세요.
