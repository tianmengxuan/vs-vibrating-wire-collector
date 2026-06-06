-- ============================================================================
-- schema.sql - VS振弦数据采集器 MySQL建表语句
-- ============================================================================
-- 说明: 运行前请先创建数据库
--   CREATE DATABASE vs_collector DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- 执行方式:
--   mysql -u root -p vs_collector < schema.sql
--
-- 版本: 1.0.0
-- 日期: 2026-05-05
-- ============================================================================

-- ============================================================================
-- 表1: device_info - 设备信息表
-- ============================================================================
CREATE TABLE IF NOT EXISTS `device_info` (
    `udid`              VARCHAR(32)     NOT NULL            COMMENT '设备唯一识别码(UDID)',
    `device_type`       VARCHAR(50)     DEFAULT ''          COMMENT '设备型号，如VS410、VS120',
    `firmware_version`  VARCHAR(20)     DEFAULT ''          COMMENT '固件版本号',
    `device_name`       VARCHAR(100)    DEFAULT ''          COMMENT '设备名称/备注',
    `is_online`         TINYINT(1)      NOT NULL DEFAULT 0  COMMENT '是否在线: 0=离线, 1=在线',
    `last_online_time`  DATETIME        DEFAULT NULL        COMMENT '最后上线时间',
    `last_offline_time` DATETIME        DEFAULT NULL        COMMENT '最后离线时间',
    `register_time`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '设备首次注册时间',
    `ip_address`        VARCHAR(45)     DEFAULT ''          COMMENT '设备IP地址',
    `connect_port`      INT             DEFAULT 0           COMMENT '设备连接端口号',
    PRIMARY KEY (`udid`),
    INDEX `idx_device_online` (`is_online`),
    INDEX `idx_device_register` (`register_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='设备信息表';


-- ============================================================================
-- 表2: sensor_calibration - 传感器标定参数表
-- ============================================================================
CREATE TABLE IF NOT EXISTS `sensor_calibration` (
    `id`                INT             NOT NULL AUTO_INCREMENT COMMENT '自增主键ID',
    `udid`              VARCHAR(32)     NOT NULL            COMMENT '设备UDID',
    `channel`           SMALLINT        NOT NULL DEFAULT 1  COMMENT '传感器通道号(1-16)',
    `sensor_type`       VARCHAR(20)     NOT NULL DEFAULT 'strain' COMMENT '传感器类型: strain/osmotic/displacement/rebar',
    `k_coefficient`     DOUBLE          NOT NULL DEFAULT 1.0 COMMENT 'K系数(灵敏度系数)',
    `f0_initial`        DOUBLE          NOT NULL DEFAULT 0.0 COMMENT 'f0初始频率(Hz)',
    `alpha_temp`        DOUBLE          NOT NULL DEFAULT 0.0 COMMENT 'α温度修正系数(/℃)',
    `t0_initial`        DOUBLE          NOT NULL DEFAULT 20.0 COMMENT 'T0初始温度(℃)',
    `range_min`         DOUBLE          DEFAULT NULL        COMMENT '量程下限',
    `range_max`         DOUBLE          DEFAULT NULL        COMMENT '量程上限',
    `freq_min`          DOUBLE          DEFAULT 400.0       COMMENT '有效频率下限(Hz)',
    `freq_max`          DOUBLE          DEFAULT 6000.0      COMMENT '有效频率上限(Hz)',
    `thermistor_b`      DOUBLE          DEFAULT 3950.0      COMMENT '热敏电阻B值(K)',
    `thermistor_r25`    DOUBLE          DEFAULT 10000.0     COMMENT '热敏电阻25℃阻值(Ω)',
    `is_active`         TINYINT(1)      NOT NULL DEFAULT 1  COMMENT '是否启用',
    `create_time`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `update_time`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (`id`),
    UNIQUE INDEX `idx_cal_udid_channel` (`udid`, `channel`),
    INDEX `idx_cal_udid` (`udid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='传感器标定参数表';


-- ============================================================================
-- 表3: raw_data_packet - 原始数据包表
-- ============================================================================
CREATE TABLE IF NOT EXISTS `raw_data_packet` (
    `id`                BIGINT          NOT NULL AUTO_INCREMENT COMMENT '自增主键ID',
    `udid`              VARCHAR(32)     NOT NULL            COMMENT '设备UDID',
    `raw_frame_hex`     TEXT            DEFAULT NULL        COMMENT '原始数据帧(HEX字符串)',
    `raw_frame_str`     TEXT            DEFAULT NULL        COMMENT '原始数据帧(字符串格式)',
    `protocol_type`     VARCHAR(20)     NOT NULL DEFAULT 'UNKNOWN' COMMENT '协议类型',
    `is_retransmit`     TINYINT(1)      NOT NULL DEFAULT 0  COMMENT '是否补发数据',
    `retransmit_time`   DATETIME        DEFAULT NULL        COMMENT '补发数据原始采集时间',
    `checksum_valid`    TINYINT(1)      DEFAULT NULL        COMMENT '校验是否通过',
    `checksum_type`     VARCHAR(20)     DEFAULT NULL        COMMENT '校验类型: CRC16/SUM/XOR',
    `receive_time`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '接收时间',
    `collect_time`      DATETIME        DEFAULT NULL        COMMENT '设备端采集时间',
    `parse_status`      VARCHAR(20)     NOT NULL DEFAULT 'PENDING' COMMENT '解析状态: PENDING/SUCCESS/FAILED',
    `parse_error`       VARCHAR(500)    DEFAULT NULL        COMMENT '解析失败原因',
    PRIMARY KEY (`id`),
    INDEX `idx_raw_udid` (`udid`),
    INDEX `idx_raw_udid_time` (`udid`, `receive_time`),
    INDEX `idx_raw_udid_collect` (`udid`, `collect_time`),
    INDEX `idx_raw_receive_time` (`receive_time`),
    INDEX `idx_raw_parse_status` (`parse_status`),
    INDEX `idx_raw_retransmit` (`is_retransmit`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='原始数据包表';


-- ============================================================================
-- 表4: device_status - 设备状态表
-- ============================================================================
CREATE TABLE IF NOT EXISTS `device_status` (
    `id`                BIGINT          NOT NULL AUTO_INCREMENT COMMENT '自增主键ID',
    `udid`              VARCHAR(32)     NOT NULL            COMMENT '设备UDID',
    `raw_packet_id`     BIGINT          DEFAULT NULL        COMMENT '关联原始数据包ID',
    `battery_voltage`   DOUBLE          DEFAULT NULL        COMMENT '电池电压(V)',
    `solar_voltage`     DOUBLE          DEFAULT NULL        COMMENT '太阳能板电压(V)',
    `signal_strength`   DOUBLE          DEFAULT NULL        COMMENT '信号强度(dBm)',
    `device_temperature` DOUBLE         DEFAULT NULL        COMMENT '设备内部温度(℃)',
    `humidity`          DOUBLE          DEFAULT NULL        COMMENT '设备内部湿度(%RH)',
    `work_mode`         VARCHAR(20)     DEFAULT NULL        COMMENT '工作模式',
    `error_code`        VARCHAR(50)     DEFAULT NULL        COMMENT '设备错误码',
    `collect_time`      DATETIME        NOT NULL            COMMENT '采集时间',
    `receive_time`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '接收时间',
    PRIMARY KEY (`id`),
    INDEX `idx_status_udid` (`udid`),
    INDEX `idx_status_udid_time` (`udid`, `collect_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='设备状态表';


-- ============================================================================
-- 表5: channel_raw_data - 通道原始数据表
-- ============================================================================
CREATE TABLE IF NOT EXISTS `channel_raw_data` (
    `id`                    BIGINT          NOT NULL AUTO_INCREMENT COMMENT '自增主键ID',
    `raw_packet_id`         BIGINT          NOT NULL            COMMENT '关联原始数据包ID',
    `udid`                  VARCHAR(32)     NOT NULL            COMMENT '设备UDID',
    `channel`               SMALLINT        NOT NULL DEFAULT 1  COMMENT '通道号(1-16)',
    `sensor_type`           VARCHAR(20)     DEFAULT NULL        COMMENT '传感器类型',
    `frequency`             DOUBLE          DEFAULT NULL        COMMENT '频率(Hz)',
    `frequency_module`      DOUBLE          DEFAULT NULL        COMMENT '频模数(f²/1000)',
    `temperature`           DOUBLE          DEFAULT NULL        COMMENT '温度(℃)',
    `thermistor_resistance` DOUBLE          DEFAULT NULL        COMMENT '热敏电阻阻值(Ω)',
    `data_quality`          VARCHAR(10)     NOT NULL DEFAULT 'VALID' COMMENT '数据质量',
    `collect_time`          DATETIME        NOT NULL            COMMENT '采集时间',
    `receive_time`          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '接收时间',
    PRIMARY KEY (`id`),
    INDEX `idx_chraw_packet` (`raw_packet_id`),
    INDEX `idx_chraw_udid` (`udid`),
    INDEX `idx_chraw_udid_time` (`udid`, `collect_time`),
    INDEX `idx_chraw_udid_channel` (`udid`, `channel`, `collect_time`),
    INDEX `idx_chraw_collect_time` (`collect_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='通道原始数据表';


-- ============================================================================
-- 表6: channel_calc_result - 通道解算结果表
-- ============================================================================
CREATE TABLE IF NOT EXISTS `channel_calc_result` (
    `id`                    BIGINT          NOT NULL AUTO_INCREMENT COMMENT '自增主键ID',
    `channel_raw_id`        BIGINT          NOT NULL            COMMENT '关联通道原始数据ID',
    `raw_packet_id`         BIGINT          DEFAULT NULL        COMMENT '关联原始数据包ID',
    `udid`                  VARCHAR(32)     NOT NULL            COMMENT '设备UDID',
    `channel`               SMALLINT        NOT NULL DEFAULT 1  COMMENT '通道号',
    `sensor_type`           VARCHAR(20)     DEFAULT NULL        COMMENT '传感器类型',
    `k_coefficient`         DOUBLE          DEFAULT NULL        COMMENT '使用的K系数',
    `f0_initial`            DOUBLE          DEFAULT NULL        COMMENT '使用的初始频率f0(Hz)',
    `alpha_temp`            DOUBLE          DEFAULT NULL        COMMENT '使用的温度系数α',
    `t0_initial`            DOUBLE          DEFAULT NULL        COMMENT '使用的初始温度T0(℃)',
    `raw_frequency`         DOUBLE          DEFAULT NULL        COMMENT '原始频率(Hz)',
    `raw_temperature`       DOUBLE          DEFAULT NULL        COMMENT '原始温度(℃)',
    `calc_value`            DOUBLE          DEFAULT NULL        COMMENT '解算物理量值',
    `calc_unit`             VARCHAR(10)     DEFAULT NULL        COMMENT '物理量单位',
    `temperature_corrected` TINYINT(1)      NOT NULL DEFAULT 0  COMMENT '是否温度修正',
    `calc_status`           VARCHAR(20)     NOT NULL DEFAULT 'SUCCESS' COMMENT '解算状态',
    `calc_error`            VARCHAR(500)    DEFAULT NULL        COMMENT '解算异常描述',
    `calc_time`             DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '解算时间',
    `collect_time`          DATETIME        DEFAULT NULL        COMMENT '原始采集时间',
    PRIMARY KEY (`id`),
    INDEX `idx_calc_chraw` (`channel_raw_id`),
    INDEX `idx_calc_udid` (`udid`),
    INDEX `idx_calc_udid_time` (`udid`, `calc_time`),
    INDEX `idx_calc_udid_collect` (`udid`, `collect_time`),
    INDEX `idx_calc_channel` (`udid`, `channel`, `calc_time`),
    INDEX `idx_calc_status` (`calc_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='通道解算结果表';


-- ============================================================================
-- 表7: command_send_log - 指令发送日志表
-- ============================================================================
CREATE TABLE IF NOT EXISTS `command_send_log` (
    `id`                BIGINT          NOT NULL AUTO_INCREMENT COMMENT '自增主键ID',
    `udid`              VARCHAR(32)     NOT NULL            COMMENT '目标设备UDID',
    `command_type`      VARCHAR(20)     NOT NULL            COMMENT '指令类型: SETM/GETP/SETP/SAVE/INFO/OTHER',
    `command_content`   VARCHAR(500)    NOT NULL            COMMENT '指令内容(不含自动追加的#\\r\\n)',
    `command_full`      VARCHAR(500)    NOT NULL            COMMENT '实际发送的完整指令(含#\\r\\n)',
    `reply_content`     TEXT            DEFAULT NULL        COMMENT '设备应答内容',
    `reply_received`    TINYINT(1)      NOT NULL DEFAULT 0  COMMENT '是否收到应答',
    `status`            VARCHAR(20)     NOT NULL DEFAULT 'PENDING' COMMENT '执行状态',
    `retry_count`       SMALLINT        NOT NULL DEFAULT 0  COMMENT '重试次数',
    `error_message`     VARCHAR(500)    DEFAULT NULL        COMMENT '错误信息',
    `send_time`         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发送时间',
    `reply_time`        DATETIME        DEFAULT NULL        COMMENT '收到应答时间',
    PRIMARY KEY (`id`),
    INDEX `idx_cmd_udid` (`udid`),
    INDEX `idx_cmd_udid_time` (`udid`, `send_time`),
    INDEX `idx_cmd_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指令发送日志表';


-- ============================================================================
-- 表8: device_monitor_snapshot - 设备监控快照表（第三方查询用）
-- ============================================================================
CREATE TABLE IF NOT EXISTS `device_monitor_snapshot` (
    `id`                BIGINT          NOT NULL AUTO_INCREMENT COMMENT '自增主键ID',
    `udid`              VARCHAR(32)     NOT NULL            COMMENT '设备ID(UDID)',
    `site_name`         VARCHAR(100)    DEFAULT ''          COMMENT '站点名称',
    `signal`            DOUBLE          DEFAULT NULL        COMMENT '信号强度(dBm)',
    `voltage`           DOUBLE          DEFAULT NULL        COMMENT '电压(V)',
    `frequency`         DOUBLE          DEFAULT NULL        COMMENT '频率(Hz)',
    `temperature`       DOUBLE          DEFAULT NULL        COMMENT '温度(℃)',
    `pressure`          DOUBLE          DEFAULT NULL        COMMENT '水压(MPa) P=K×(f0²-f²)+Kt×(t-t0)',
    `water_level`       DOUBLE          DEFAULT NULL        COMMENT '水位(m) =P/γ',
    `elevation`         DOUBLE          DEFAULT NULL        COMMENT '水位高程(m) =水位+安装高程',
    `status`            VARCHAR(20)     DEFAULT 'VALID'     COMMENT '数据状态',
    `create_time`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    PRIMARY KEY (`id`),
    INDEX `idx_snapshot_udid` (`udid`),
    INDEX `idx_snapshot_time` (`create_time`),
    INDEX `idx_snapshot_udid_time` (`udid`, `create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='设备监控快照表（第三方查询）';
