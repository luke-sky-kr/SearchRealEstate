-- 네이버 부동산 매물 모니터 DB 스키마
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
CREATE TABLE IF NOT EXISTS raw_articles (
  snapshot_id     INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
  article_no      TEXT NOT NULL,          -- 네이버 매물 번호
  complex_no      TEXT NOT NULL REFERENCES complexes(complex_no),
  trade_type      TEXT NOT NULL,          -- A1=매매, B1=전세
  building        TEXT,                   -- 동 (예: '101동')
  floor_info      TEXT,                   -- 예: '중/23', '15/23'
  price_krw       INTEGER,                -- 만원 단위 (매매가 or 전세보증금)
  area_name       TEXT,                   -- 평형 타입명 (예: '115A')
  area_supply     REAL,                   -- 공급면적 ㎡
  area_exclusive  REAL,                   -- 전용면적 ㎡
  direction       TEXT,
  description     TEXT,                   -- 매물 특징 설명
  realtor         TEXT,                   -- 중개사무소명
  confirmed_date  TEXT,                   -- 확인매물 날짜
  same_addr_cnt   INTEGER,               -- 네이버 동일매물묶기 개수
  same_addr_max   INTEGER,               -- 묶음 내 최고가 (만원)
  same_addr_min   INTEGER,               -- 묶음 내 최저가 (만원)
  raw_json        TEXT,                   -- 원본 응답 보존
  PRIMARY KEY (snapshot_id, article_no)
);

-- dedup agent 산출물: 스냅샷별 중복 그룹 배정
CREATE TABLE IF NOT EXISTS dedup_assignments (
  snapshot_id  INTEGER NOT NULL,
  article_no   TEXT NOT NULL,
  group_key    TEXT NOT NULL,             -- 같은 실물이라 판정된 그룹 ID
  method       TEXT NOT NULL,             -- 'rule' | 'ai' | 'singleton'
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
  naver_listed    INTEGER,               -- 네이버 화면 표시 매물 수(묶기 전)
  collected       INTEGER,               -- 실제 수집한 매물 수
  naver_grouped   INTEGER,               -- 네이버 동일매물묶기 후 개수
  our_grouped     INTEGER,               -- 자체 dedup 후 개수
  status          TEXT,                  -- 'ok' | 'missing' | 'over'
  detail          TEXT,
  PRIMARY KEY (snapshot_id, complex_no, trade_type)
);

CREATE INDEX IF NOT EXISTS idx_raw_articles_cx ON raw_articles(complex_no, trade_type, snapshot_id);
CREATE INDEX IF NOT EXISTS idx_dedup_group ON dedup_assignments(snapshot_id, group_key);
