-- 네이버 부동산 매물 모니터 DB 스키마
-- 필드 구성은 fin.land.naver.com front-api v1 응답(2026-08-22 실측) 기준.
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS complexes (
  complex_no   TEXT PRIMARY KEY,          -- 네이버 단지 번호
  name         TEXT NOT NULL,
  address      TEXT,
  total_households INTEGER,
  added_at     TEXT DEFAULT (datetime('now'))
);

-- 수집 스냅샷 (검토 요청 1회 = 스냅샷 1개)
CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_id  INTEGER PRIMARY KEY AUTOINCREMENT,
  collected_at TEXT NOT NULL,             -- ISO8601, Asia/Seoul
  notes        TEXT,
  source_dir   TEXT                        -- 이 스냅샷을 만든 수집 폴더 (재적재 방지)
);

-- 수집 agent 산출물: 스냅샷 시점의 전 매물 (원본 그대로)
-- 네이버 응답의 대표매물과 그 묶음 멤버를 모두 1행씩 펼쳐서 넣는다.
CREATE TABLE IF NOT EXISTS raw_articles (
  snapshot_id     INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
  article_no      TEXT NOT NULL,          -- 네이버 매물 번호 (articleNumber)
  complex_no      TEXT NOT NULL REFERENCES complexes(complex_no),
  trade_type      TEXT NOT NULL,          -- A1=매매, B1=전세, B2=월세, B3=단기
  building        TEXT,                   -- 동 (dongName, 예: '106')
  floor_info      TEXT,                   -- 예: '저/34', '3/34'
  floor_target    TEXT,                   -- 해당 층 또는 대역 ('저'/'중'/'고'/'3')
  floor_total     INTEGER,                -- 총 층수
  price_krw       INTEGER,                -- 만원. 거래유형별 대표가(매매가 or 보증금)
  deal_price_krw  INTEGER,                -- 만원. 매매가
  warrant_price_krw INTEGER,              -- 만원. 보증금
  rent_price_krw  INTEGER,                -- 만원. 월세
  mgmt_fee_krw    INTEGER,                -- 원. 관리비 (managementFeeAmount)
  price_change    INTEGER,                -- priceChangeStatus (0=변동없음)
  area_name       TEXT,                   -- 평형 타입명 (supplySpaceName, 예: '113C')
  area_supply     REAL,                   -- 공급면적 ㎡
  area_exclusive  REAL,                   -- 전용면적 ㎡
  direction       TEXT,                   -- 방향 코드 (SS=남, EE=동, ES=동남 …)
  direction_std   TEXT,                   -- 방향 기준 (예: '거실 기준')
  description     TEXT,                   -- 매물 특징 설명
  realtor         TEXT,                   -- 중개사무소명 (brokerageName)
  broker_cp       TEXT,                   -- 매물 제공 CP (cpId)
  confirmed_date  TEXT,                   -- 확인매물 날짜 (articleConfirmDate)
  exposure_date   TEXT,                   -- 노출 시작일 (exposureStartDate) = 등록 시점
  verification    TEXT,                   -- 확인 유형 (verificationType)
  -- 네이버 자체 동일매물묶기 결과 (duplicatedArticleInfo)
  naver_group     TEXT,                   -- 묶음 대표 매물번호 (묶임 없으면 자기 자신)
  is_repr         INTEGER NOT NULL DEFAULT 1,  -- 1=대표매물, 0=묶음에 딸린 매물
  naver_group_n   INTEGER,                -- 묶음 내 매물 수
  naver_realtor_n INTEGER,                -- 묶음 내 중개사 수 (realtorCount)
  naver_grp_min   INTEGER,                -- 묶음 최저가 (만원)
  naver_grp_max   INTEGER,                -- 묶음 최고가 (만원)
  raw_json        TEXT,                   -- 원본 응답 보존
  PRIMARY KEY (snapshot_id, article_no)
);

-- dedup agent 산출물: 스냅샷별 중복 그룹 배정
CREATE TABLE IF NOT EXISTS dedup_assignments (
  snapshot_id  INTEGER NOT NULL,
  article_no   TEXT NOT NULL,
  group_key    TEXT NOT NULL,             -- 같은 실물이라 판정된 그룹 ID
  method       TEXT NOT NULL,             -- 'rule' | 'ai' | 'singleton' | 'naver'
  confidence   REAL,                      -- 0~1
  note         TEXT,
  PRIMARY KEY (snapshot_id, article_no),
  FOREIGN KEY (snapshot_id, article_no) REFERENCES raw_articles(snapshot_id, article_no)
);

-- 스냅샷별 정합성 진단 (누락/과다 감시)
CREATE TABLE IF NOT EXISTS integrity_checks (
  snapshot_id     INTEGER NOT NULL,
  complex_no      TEXT NOT NULL,
  trade_type      TEXT NOT NULL,
  naver_listed    INTEGER,               -- 네이버가 알려준 총 수 (totalCount, 대표매물 기준)
  collected       INTEGER,               -- 수집한 대표매물 수
  collected_raw   INTEGER,               -- 수집한 원시 매물 수 (묶음 멤버 포함)
  naver_grouped   INTEGER,               -- 네이버 동일매물묶기 후 개수
  our_grouped     INTEGER,               -- 자체 dedup 후 개수
  status          TEXT,                  -- 'ok' | 'missing' | 'over'
  detail          TEXT,
  PRIMARY KEY (snapshot_id, complex_no, trade_type)
);

CREATE INDEX IF NOT EXISTS idx_raw_articles_cx ON raw_articles(complex_no, trade_type, snapshot_id);
CREATE INDEX IF NOT EXISTS idx_raw_articles_grp ON raw_articles(snapshot_id, naver_group);
CREATE INDEX IF NOT EXISTS idx_dedup_group ON dedup_assignments(snapshot_id, group_key);

-- ── 조사일(스냅샷)을 가로지르는 물건 정체성 ──────────────────────
-- raw_articles는 이미 스냅샷 단위로 쌓이지만, dedup 그룹키는 스냅샷 안에서만
-- 유효하다. 같은 실물이 조사일마다 다른 그룹키를 받으므로 "이 물건이 지난
-- 조사에도 있었나 / 호가가 변했나"를 물을 수 없다. 그 연결을 여기서 만든다.
CREATE TABLE IF NOT EXISTS unit_links (
  snapshot_id  INTEGER NOT NULL,
  group_key    TEXT NOT NULL,            -- dedup_assignments.group_key (스냅샷 지역)
  unit_id      TEXT NOT NULL,            -- 스냅샷을 가로지르는 물건 ID
  match        TEXT,                     -- 'article'(매물번호 겹침) | 'key'(물건키) | 'new'
  PRIMARY KEY (snapshot_id, group_key)
);

-- 조사일별 물건 상태와 직전 조사 대비 변화.
-- 'gone'은 사라진 것이 관측된 조사일에 기록한다(그 조사일에는 물건이 없다).
CREATE TABLE IF NOT EXISTS unit_events (
  snapshot_id      INTEGER NOT NULL,
  unit_id          TEXT NOT NULL,
  complex_no       TEXT,
  trade_type       TEXT,
  event            TEXT NOT NULL,        -- 'new' | 'gone' | 'up' | 'down' | 'flat'
  price_krw        INTEGER,              -- 이 조사일의 호가 (gone이면 NULL)
  prev_price_krw   INTEGER,              -- 직전 조사일의 호가
  delta_krw        INTEGER,              -- price - prev_price
  prev_snapshot_id INTEGER,
  days_listed      INTEGER,              -- 최초 관측 조사일로부터 경과일
  PRIMARY KEY (snapshot_id, unit_id)
);

CREATE INDEX IF NOT EXISTS idx_unit_links_unit ON unit_links(unit_id, snapshot_id);
CREATE INDEX IF NOT EXISTS idx_unit_events_unit ON unit_events(unit_id, snapshot_id);
CREATE INDEX IF NOT EXISTS idx_unit_events_snap ON unit_events(snapshot_id, event);
