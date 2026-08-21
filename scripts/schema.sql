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
  notes        TEXT
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
