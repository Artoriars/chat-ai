-- M2 用户与数据权限表（默认用户全量授权；登录 UI 落地后接入真实用户）
SET NAMES utf8mb4;

USE chatbi_meta;

CREATE TABLE IF NOT EXISTS user (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  username          VARCHAR(128) NOT NULL,
  grant_all_tables  TINYINT      NOT NULL DEFAULT 0 COMMENT '1=拥有全部表权限（跳过明细权限表）',
  created_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='平台用户（M2 无登录 UI，X-User-Id 头指定）';

CREATE TABLE IF NOT EXISTS user_table_permission (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id        BIGINT       NOT NULL,
  table_name     VARCHAR(255) NOT NULL,
  created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_user_table (user_id, table_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户-表级授权明细（grant_all_tables=0 时生效）';

INSERT INTO user (id, username, grant_all_tables) VALUES (1, 'default', 1)
ON DUPLICATE KEY UPDATE username = username;
