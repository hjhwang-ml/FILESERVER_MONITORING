-- filehub v4 — 폴더 기능 (2026-10-21 · 코드 초안 · 현진님이 DB 서버(.103)에서 실행)
-- 실행 전: mysqldump 로 filehub DB 백업 · 아래 0) 결과부터 보고 판단
-- 실행: mysql filehub < add_folders.sql   (계정 · 비밀번호는 현진님 것 · 명령 줄에 비밀번호를 적지 않는다)

-- 0) 먼저 볼 것 (읽기만) ---------------------------------------------------
--   SHOW CREATE TABLE files\G
--   SHOW COLUMNS FROM audit_logs LIKE 'action';
--   → action 이 ENUM(...) 이면 맨 아래 4) 를 같이 실행 (아니면 4) 는 건너뜀)

-- 1) 폴더 표 ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS folders (
  id         INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name       VARCHAR(100) NOT NULL,
  parent_id  INT UNSIGNED NULL,                 -- NULL = 맨 위(공유 드라이브)
  dept       VARCHAR(20)  NULL,                 -- 부서 태그 (app.py DEPARTMENTS 의 키)
  created_by INT NULL,                          -- users.id (지워진 사용자도 남게 FK 는 걸지 않음)
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_folders_parent (parent_id),
  CONSTRAINT fk_folders_parent FOREIGN KEY (parent_id) REFERENCES folders (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2) 파일이 어느 폴더에 있나 ----------------------------------------------
ALTER TABLE files
  ADD COLUMN IF NOT EXISTS folder_id INT UNSIGNED NULL,
  ADD KEY IF NOT EXISTS idx_files_folder (folder_id);

-- 3) 지금 있는 부서(dept) 를 맨 위 폴더로 만들고, 파일을 그 폴더에 넣기 ----
INSERT INTO folders (name, dept)
SELECT v.name, v.dept FROM (
  SELECT '공용문서' AS name, 'general' AS dept UNION ALL
  SELECT '진료/처방과', 'doctor' UNION ALL
  SELECT '간호부', 'nurse' UNION ALL
  SELECT '원무행정과', 'admin' UNION ALL
  SELECT '영상의학과(PACS)', 'pacs'
) v
WHERE NOT EXISTS (SELECT 1 FROM folders f WHERE f.parent_id IS NULL AND f.name = v.name);

UPDATE files x
JOIN folders d ON d.parent_id IS NULL AND d.dept = x.dept
SET x.folder_id = d.id
WHERE x.folder_id IS NULL;

-- 확인
SELECT d.name, COUNT(x.id) AS files FROM folders d
LEFT JOIN files x ON x.folder_id = d.id AND x.is_deleted = 0
GROUP BY d.id ORDER BY d.name;

-- 4) (0) 에서 audit_logs.action 이 ENUM 일 때만) 'FOLDER' 행위 더하기 -------
--   ALTER TABLE audit_logs MODIFY action ENUM('LOGIN','LOGOUT','ACCOUNT_LOCKED','UPLOAD',
--     'DOWNLOAD','UPDATE','DELETE','FOLDER') NOT NULL;
--   ※ 지금 ENUM 목록을 SHOW 결과 그대로 옮기고 'FOLDER' 만 끝에 더할 것

-- 되돌리기 (v3 판으로 돌아갈 때) -------------------------------------------
--   ALTER TABLE files DROP KEY idx_files_folder, DROP COLUMN folder_id;
--   DROP TABLE folders;
