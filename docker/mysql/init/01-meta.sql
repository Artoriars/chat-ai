-- chatbi_meta 元数据主存储初始化（与 backend/app/models.py 的 SQLAlchemy 定义保持一致）
-- 字符集必须 utf8mb4（注释含中文口径说明）
-- SET NAMES 必需：docker-entrypoint 的 mysql 客户端默认字符集不可靠，否则中文被按 latin1 双重编码入库
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS datasource (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  name          VARCHAR(128)  NOT NULL COMMENT '数据源名称（业务可读）',
  db_type       VARCHAR(32)   NOT NULL DEFAULT 'mysql' COMMENT '数据库类型（当前仅 mysql）',
  host          VARCHAR(255)  NOT NULL,
  port          INT           NOT NULL DEFAULT 3306,
  database_name VARCHAR(128)  NOT NULL,
  username      VARCHAR(128)  NOT NULL,
  created_at    TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据源注册表（M2 起多实例使用，凭据走密钥管理）';

CREATE TABLE IF NOT EXISTS table_meta (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  datasource_id  BIGINT        NOT NULL DEFAULT 1 COMMENT '数据源 ID（M1 单数据源恒为 1）',
  table_name     VARCHAR(255)  NOT NULL,
  table_comment  VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '表注释',
  table_rows     BIGINT        NOT NULL DEFAULT 0 COMMENT '采集时的估算行数',
  schema_hash    CHAR(64)      NOT NULL COMMENT '该表结构指纹（列名+类型+注释 sha256），增量 diff 基础',
  collected_at   TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_ds_table (datasource_id, table_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='表结构采集结果';

CREATE TABLE IF NOT EXISTS column_meta (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  table_meta_id    BIGINT        NOT NULL,
  column_name      VARCHAR(255)  NOT NULL,
  data_type        VARCHAR(128)  NOT NULL COMMENT '列类型（COLUMN_TYPE 全称）',
  is_nullable      VARCHAR(8)    NOT NULL DEFAULT 'YES',
  column_key       VARCHAR(8)    NOT NULL DEFAULT '' COMMENT 'PRI/UNI/MUL/空',
  column_comment   VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '列注释',
  ordinal_position INT           NOT NULL,
  UNIQUE KEY uk_table_col (table_meta_id, column_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='列结构采集结果';

CREATE TABLE IF NOT EXISTS metric (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  name           VARCHAR(128)  NOT NULL COMMENT '指标名（规范名）',
  aliases        JSON          NULL COMMENT '业务别名数组，如 ["销售额","成交额","gmv"]',
  definition     VARCHAR(2048) NOT NULL COMMENT '口径定义（人话）',
  expression     VARCHAR(2048) NOT NULL COMMENT '规范 SQL 表达式',
  base_filter    VARCHAR(2048) NULL COMMENT '口径基础过滤条件',
  time_semantics VARCHAR(255)  NULL COMMENT '时间语义（如：按 pay_time 支付时间计）',
  owner          VARCHAR(128)  NULL COMMENT '口径负责人',
  version        INT           NOT NULL DEFAULT 1 COMMENT '口径版本号',
  status         VARCHAR(32)   NOT NULL DEFAULT 'pending_review' COMMENT 'active/pending_review/disabled',
  created_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_name_version (name, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='业务指标口径（M2 起注入硬约束，M1 预留）';

-- 预置示例口径（M2 启用硬约束注入）
INSERT INTO metric (name, aliases, definition, expression, base_filter, time_semantics, owner, version, status)
VALUES ('GMV', JSON_ARRAY('销售额', '成交额', 'gmv'),
        '已支付订单的实付金额总和（不含退款）',
        'SUM(orders.pay_amount)',
        "status = 'PAID'",
        '按 pay_time（支付时间）计',
        'data-team', 1, 'pending_review')
ON DUPLICATE KEY UPDATE name = name;
