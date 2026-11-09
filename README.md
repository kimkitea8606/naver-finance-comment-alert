# 네이버 금융뉴스 댓글 기준 텔레그램 알림

## 하는 일

- 네이버 **공식 뉴스 검색 API**로 금융 관련 최신 기사 후보 수집
- 네이버 뉴스 댓글창에서 이용되는 **비공식 commentBox API**로 총 댓글 수 확인
- 최근 24시간 기사 중 댓글 **5개 이상**일 때 텔레그램 전송
- 평일 한국시간 08:15, 10:15, 12:15, 14:15, 16:15, 18:15, 20:15 자동 실행
- 같은 기사 중복 알림 방지 (`data/sent.json` 에 전송 기록 보존)

> 기사 전체를 빠짐없이 감시하는 것은 아닙니다. 검색어 및 호출량 한도 내에서 수집한 기사만 검사합니다.
> 댓글 수 API는 **공식 공개 API가 아니므로** 네이버의 변경/차단으로 동작하지 않을 수 있습니다.
> 실제 네이버/텔레그램 API 요청을 통한 실시간 정상 동작은 사용자의 키로 별도 실행 확인이 필요합니다.

## 준비 단계

1. GitHub에 **새 저장소** `naver-finance-comment-alert`를 만들고 이 압축파일 안의 폴더/파일을 업로드합니다. 기존 정책자료 봇과 분리하는 것을 권장합니다.
2. 네이버 개발자센터 <https://developers.naver.com/apps/#/register>에서 `검색` API를 사용하는 애플리케이션을 등록해 Client ID와 Client Secret을 확보합니다.
3. GitHub 저장소 `Settings → Secrets and variables → Actions → New repository secret`에서 아래 4개를 저장합니다.
   - `NAVER_CLIENT_ID`: 네이버 검색 API Client ID
   - `NAVER_CLIENT_SECRET`: 네이버 검색 API Client Secret
   - `TELEGRAM_BOT_TOKEN`: 기존 텔레그램 봇 토큰 **비공개 값**
   - `TELEGRAM_CHAT_ID`: 메시지를 받을 채팅 ID
4. `Settings → Actions → General → Workflow permissions`에서 이 저장소의 워크플로가 커밋을 기록할 수 있도록 `Read and write permissions`를 허용합니다 (관리 정책에 따라 제한될 수 있음).
5. 저장소 `Actions → NAVER Finance News Comment Alert → Run workflow`로 수동 실행해 보고 텔레그램 수신과 로그를 확인합니다. 이후 예약 실행됩니다.

**주의:** 키나 봇 토큰을 공개 저장소 파일·채팅에 붙여넣지 마세요. 기존에 공개된 토큰이 있으면 BotFather로 재발급하세요.

## 주요 설정

`config.json`만 바꾸면 다음 항목을 조절할 수 있습니다.

- `comment_threshold`: 댓글 수 기준 (기본 **5**)
- `lookback_hours`: 최근 며칠/몇 시간 기사 수집 여부 (기본 **24시간**)
- `queries`: 금융뉴스 검색 키워드 목록
- `include_terms`, `exclude_terms`: 뉴스 내용 관련성 분류에 사용
- `max_comment_checks`: 한 번에 댓글을 확인할 기사 수 (기본 **200건**)
- `max_alerts_per_run`: 한 번에 전송할 기사 최대 수 (기본 **10건**)

주기는 `.github/workflows/finance-news-alert.yml`의 `cron` 설정으로 변경합니다.

## 별도 테스트

```bash
python -m pip install -r requirements.txt
export NAVER_CLIENT_ID='내_아이디'
export NAVER_CLIENT_SECRET='내_시크릿'
DRY_RUN=true python monitor.py
```

`DRY_RUN=true`는 텔레그램으로 보내지 않고 적합한 기사만 화면에 표시합니다.

## 한계 / 운영 체크

- 댓글 API에서 0이 나왔다고 해서 실제 댓글이 0인 경우인지 댓글 비활성화 상태인지까지 구분하지 못할 수 있습니다.
- 기사 제목과 검색어를 기반으로 범위를 좁히므로 금융 기사가 아닌 것이 섞이거나 금융 기사가 빠질 수 있습니다.
- 네이버 댓글 비공식 인터페이스는 언제든 변경될 수 있으므로 최초 수동 실행 결과를 보고 조정하세요.
- GitHub 예약 실행은 부하에 따라 지연되거나 일부 실행이 누락될 수 있습니다.
- 중복 방지용 `data/sent.json`을 GitHub 저장소에 자동 커밋합니다. Actions에 쓰기 권한이 반드시 필요합니다.
- 네이버 Search Open API 대신 API HUB를 사용한다면 `NAVER_SEARCH_ENDPOINT` 환경변수를 `https://naverapihub.apigw.ntruss.com/search/v1/news`로 변경하고 해당 허브의 API 키를 설정하세요.
