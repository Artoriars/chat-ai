-- business_demo 模拟业务库（被查询目标，模拟真实业务数据源）
-- 列注释刻意带业务语义（状态枚举、金额口径），供 NL2SQL 生成演示
-- SET NAMES 必需：防止 init 导入时中文被按 latin1 双重编码
SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS business_demo DEFAULT CHARSET utf8mb4;
USE business_demo;

CREATE TABLE IF NOT EXISTS users (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '用户ID',
  username   VARCHAR(64)  NOT NULL COMMENT '用户名',
  region     VARCHAR(32)  NOT NULL COMMENT '所属大区：华东/华北/华南/西南',
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间',
  KEY idx_region (region)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户表';

CREATE TABLE IF NOT EXISTS products (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '商品ID',
  product_name VARCHAR(128) NOT NULL COMMENT '商品名称',
  category     VARCHAR(64)  NOT NULL COMMENT '商品类目：数码/家居/食品/服饰',
  unit_price   DECIMAL(10,2) NOT NULL COMMENT '单价（元）',
  KEY idx_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品表';

CREATE TABLE IF NOT EXISTS orders (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '订单ID',
  order_no   VARCHAR(32)  NOT NULL COMMENT '订单号',
  user_id    BIGINT       NOT NULL COMMENT '下单用户ID（关联 users.id）',
  product_id BIGINT       NOT NULL COMMENT '商品ID（关联 products.id）',
  quantity   INT          NOT NULL DEFAULT 1 COMMENT '购买数量',
  pay_amount DECIMAL(12,2) NOT NULL COMMENT '实付金额（元，含运费，不含优惠券抵扣）',
  status     VARCHAR(16)  NOT NULL COMMENT '订单状态：PAID-已支付, REFUNDED-已退款, PENDING-待支付',
  pay_time   DATETIME     NULL COMMENT '支付时间（未支付为 NULL）',
  created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '下单时间',
  UNIQUE KEY uk_order_no (order_no),
  KEY idx_user (user_id),
  KEY idx_pay_time (pay_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单表';

-- ===== 种子数据 =====
INSERT INTO users (id, username, region) VALUES
  (1, 'alice',   '华东'),
  (2, 'bob',     '华北'),
  (3, 'carol',   '华东'),
  (4, 'dave',    '华南'),
  (5, 'erin',    '西南'),
  (6, 'frank',   '华东'),
  (7, 'grace',   '华北'),
  (8, 'henry',   '华南');

INSERT INTO products (id, product_name, category, unit_price) VALUES
  (1, '无线降噪耳机', '数码', 799.00),
  (2, '机械键盘',     '数码', 499.00),
  (3, '空气炸锅',     '家居', 399.00),
  (4, '乳胶枕头',     '家居', 159.00),
  (5, '坚果礼盒',     '食品', 128.00),
  (6, '咖啡豆',       '食品', 88.00),
  (7, '羽绒服',       '服饰', 999.00),
  (8, '运动鞋',       '服饰', 459.00);

INSERT INTO orders (order_no, user_id, product_id, quantity, pay_amount, status, pay_time, created_at) VALUES
  ('SO20260001', 1, 1, 1, 799.00,  'PAID',     '2026-09-01 10:15:00', '2026-09-01 10:10:00'),
  ('SO20260002', 1, 5, 2, 256.00,  'PAID',     '2026-09-03 14:22:00', '2026-09-03 14:20:00'),
  ('SO20260003', 2, 7, 1, 999.00,  'PAID',     '2026-09-05 09:30:00', '2026-09-05 09:28:00'),
  ('SO20260004', 3, 2, 1, 499.00,  'REFUNDED', '2026-09-06 16:40:00', '2026-09-06 16:35:00'),
  ('SO20260005', 3, 3, 1, 399.00,  'PAID',     '2026-09-08 11:05:00', '2026-09-08 11:00:00'),
  ('SO20260006', 4, 8, 2, 918.00,  'PAID',     '2026-09-10 20:12:00', '2026-09-10 20:08:00'),
  ('SO20260007', 5, 6, 3, 264.00,  'PENDING',  NULL,                  '2026-09-12 08:45:00'),
  ('SO20260008', 6, 1, 1, 799.00,  'PAID',     '2026-09-15 13:55:00', '2026-09-15 13:50:00'),
  ('SO20260009', 6, 4, 4, 636.00,  'PAID',     '2026-09-18 19:20:00', '2026-09-18 19:15:00'),
  ('SO20260010', 7, 5, 1, 128.00,  'REFUNDED', '2026-09-20 10:02:00', '2026-09-20 10:00:00'),
  ('SO20260011', 8, 7, 1, 999.00,  'PAID',     '2026-09-22 21:30:00', '2026-09-22 21:25:00'),
  ('SO20260012', 2, 6, 5, 440.00,  'PAID',     '2026-10-01 09:10:00', '2026-10-01 09:05:00'),
  ('SO20260013', 1, 8, 1, 459.00,  'PAID',     '2026-10-05 15:47:00', '2026-10-05 15:40:00'),
  ('SO20260014', 4, 2, 1, 499.00,  'PENDING',  NULL,                  '2026-10-08 12:33:00'),
  ('SO20260015', 7, 3, 2, 798.00,  'PAID',     '2026-10-12 18:20:00', '2026-10-12 18:15:00');
