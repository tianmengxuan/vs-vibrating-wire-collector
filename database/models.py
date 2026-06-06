# -*- coding: utf-8 -*-
"""
database/models.py - SQLAlchemy ORM 数据模型
=============================================
定义8张核心数据表：
    1. device_info              - 设备信息表
    2. sensor_calibration        - 传感器标定参数表
    3. raw_data_packet           - 原始数据包表
    4. device_status             - 设备状态表
    5. channel_raw_data          - 通道原始数据表
    6. channel_calc_result       - 解算结果表
    7. command_send_log          - 指令日志表
    8. device_monitor_snapshot   - 监控快照表（第三方查询）

所有表使用InnoDB引擎，UTF8MB4字符集。
关键查询路径均建立联合索引：(UDID, 采集时间)。

作者: VS DataCollector Project
版本: 1.0.0
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, Text,
    DateTime, Boolean, SmallInteger, Index, ForeignKey,
    create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


# ============================================================================
# 表1: device_info - 设备信息表
# ============================================================================
class DeviceInfo(Base):
    """
    设备信息表
    记录所有接入系统的设备基本信息。
    UDID为设备唯一识别码，作为主键。
    """
    __tablename__ = 'device_info'

    # ---- 主键 ----
    udid = Column(
        String(32), primary_key=True, nullable=False,
        comment='设备唯一识别码(UDID)，如VS001、VS410_HW401'
    )

    # ---- 基本信息 ----
    device_type = Column(
        String(50), nullable=True, default='',
        comment='设备型号，如VS410、VS120'
    )
    firmware_version = Column(
        String(20), nullable=True, default='',
        comment='固件版本号'
    )
    device_name = Column(
        String(100), nullable=True, default='',
        comment='设备名称/备注'
    )

    # ---- 连接状态 ----
    is_online = Column(
        Boolean, nullable=False, default=False,
        comment='是否在线: 0=离线, 1=在线'
    )
    last_online_time = Column(
        DateTime, nullable=True,
        comment='最后上线时间'
    )
    last_offline_time = Column(
        DateTime, nullable=True,
        comment='最后离线时间'
    )

    # ---- 注册信息 ----
    register_time = Column(
        DateTime, nullable=False, default=datetime.now,
        comment='设备首次注册时间'
    )
    ip_address = Column(
        String(45), nullable=True, default='',
        comment='设备IP地址(IPv4/IPv6)'
    )
    connect_port = Column(
        Integer, nullable=True, default=0,
        comment='设备连接端口号'
    )

    # ---- 索引 ----
    __table_args__ = (
        Index('idx_device_online', 'is_online'),
        Index('idx_device_register', 'register_time'),
        {'comment': '设备信息表', 'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4'}
    )

    def __repr__(self):
        return f"<DeviceInfo(udid={self.udid}, type={self.device_type}, online={self.is_online})>"


# ============================================================================
# 表2: sensor_calibration - 传感器标定参数表
# ============================================================================
class SensorCalibration(Base):
    """
    传感器标定参数表
    记录每个设备每个通道的传感器标定参数。
    振弦解算时从此表读取K、f0、α、T0等参数。
    联合唯一约束：(UDID, 通道号)。
    """
    __tablename__ = 'sensor_calibration'

    # ---- 主键 ----
    id = Column(
        Integer, primary_key=True, autoincrement=True,
        comment='自增主键ID'
    )

    # ---- 关联设备 ----
    udid = Column(
        String(32), nullable=False,
        comment='设备UDID，关联device_info.udid'
    )
    channel = Column(
        SmallInteger, nullable=False, default=1,
        comment='传感器通道号(1-16)'
    )

    # ---- 标定参数（振弦解算核心参数） ----
    sensor_type = Column(
        String(20), nullable=False, default='strain',
        comment='传感器类型: strain(应变计)/osmotic(渗压计)/displacement(位移计)/rebar(钢筋计)'
    )
    k_coefficient = Column(
        Float, nullable=False, default=1.0,
        comment='K系数(灵敏度系数)，单位因传感器类型而异'
    )
    f0_initial = Column(
        Float, nullable=False, default=0.0,
        comment='f0初始频率(Hz)，无荷载时的基准频率'
    )
    alpha_temp = Column(
        Float, nullable=False, default=0.0,
        comment='α温度修正系数，单位因传感器类型而异/℃'
    )
    t0_initial = Column(
        Float, nullable=False, default=20.0,
        comment='T0初始温度(℃)，标定时的基准温度'
    )

    # ---- 量程与校验 ----
    range_min = Column(
        Float, nullable=True, default=None,
        comment='量程下限'
    )
    range_max = Column(
        Float, nullable=True, default=None,
        comment='量程上限'
    )
    freq_min = Column(
        Float, nullable=True, default=400.0,
        comment='有效频率下限(Hz)，通常400Hz以上为有效'
    )
    freq_max = Column(
        Float, nullable=True, default=6000.0,
        comment='有效频率上限(Hz)，通常6000Hz以下为有效'
    )

    # ---- 热敏电阻参数 ----
    thermistor_b = Column(
        Float, nullable=True, default=3950.0,
        comment='热敏电阻B值(K)，用于温度换算'
    )
    thermistor_r25 = Column(
        Float, nullable=True, default=10000.0,
        comment='热敏电阻25℃时阻值(Ω)'
    )

    # ---- 状态 ----
    is_active = Column(
        Boolean, nullable=False, default=True,
        comment='是否启用: 0=禁用, 1=启用'
    )
    create_time = Column(
        DateTime, nullable=False, default=datetime.now,
        comment='创建时间'
    )
    update_time = Column(
        DateTime, nullable=False, default=datetime.now,
        onupdate=datetime.now,
        comment='最后更新时间'
    )

    # ---- 索引 ----
    __table_args__ = (
        Index('idx_cal_udid', 'udid'),
        Index('idx_cal_udid_channel', 'udid', 'channel', unique=True),
        {'comment': '传感器标定参数表', 'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4'}
    )

    def __repr__(self):
        return (f"<SensorCalibration(udid={self.udid}, ch={self.channel}, "
                f"type={self.sensor_type}, K={self.k_coefficient})>")


# ============================================================================
# 表3: raw_data_packet - 原始数据包表
# ============================================================================
class RawDataPacket(Base):
    """
    原始数据包表
    记录所有设备上报的原始数据帧及解析元信息。
    是数据追溯的第一级入口，通过此表可关联到通道原始数据和解算结果。
    """
    __tablename__ = 'raw_data_packet'

    # ---- 主键 ----
    id = Column(
        BigInteger, primary_key=True, autoincrement=True,
        comment='自增主键ID'
    )

    # ---- 设备标识 ----
    udid = Column(
        String(32), nullable=False, index=True,
        comment='设备UDID'
    )

    # ---- 数据包内容 ----
    raw_frame_hex = Column(
        Text, nullable=True,
        comment='原始数据帧(HEX字符串)'
    )
    raw_frame_str = Column(
        Text, nullable=True,
        comment='原始数据帧(字符串格式，适用于STR协议)'
    )

    # ---- 协议信息 ----
    protocol_type = Column(
        String(20), nullable=False, default='UNKNOWN',
        comment='协议类型: STR1.0/STR2.0/STR3.0/HEX/SL651_HEX/SL651_STR/UNKNOWN'
    )
    is_retransmit = Column(
        Boolean, nullable=False, default=False,
        comment='是否补发数据: 0=实时数据, 1=补发数据'
    )
    retransmit_time = Column(
        DateTime, nullable=True,
        comment='补发数据原始采集时间（来自@YYYY-MM-DD HH:MM:SS标识）'
    )

    # ---- 校验信息 ----
    checksum_valid = Column(
        Boolean, nullable=True,
        comment='校验是否通过: None=未校验, 0=失败, 1=通过'
    )
    checksum_type = Column(
        String(20), nullable=True,
        comment='校验类型: CRC16/SUM/XOR/NONE'
    )

    # ---- 时间戳 ----
    receive_time = Column(
        DateTime, nullable=False, default=datetime.now, index=True,
        comment='服务端接收时间'
    )
    collect_time = Column(
        DateTime, nullable=True,
        comment='设备端采集时间（从数据包中解析）'
    )

    # ---- 解析状态 ----
    parse_status = Column(
        String(20), nullable=False, default='PENDING',
        comment='解析状态: PENDING(待解析)/SUCCESS(成功)/FAILED(失败)'
    )
    parse_error = Column(
        String(500), nullable=True,
        comment='解析失败原因'
    )

    # ---- 索引 ----
    __table_args__ = (
        Index('idx_raw_udid_time', 'udid', 'receive_time'),
        Index('idx_raw_udid_collect', 'udid', 'collect_time'),
        Index('idx_raw_parse_status', 'parse_status'),
        Index('idx_raw_retransmit', 'is_retransmit'),
        {'comment': '原始数据包表', 'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4'}
    )

    def __repr__(self):
        return (f"<RawDataPacket(id={self.id}, udid={self.udid}, "
                f"proto={self.protocol_type}, time={self.receive_time})>")


# ============================================================================
# 表4: device_status - 设备状态表
# ============================================================================
class DeviceStatus(Base):
    """
    设备状态表
    记录设备上报的自检信息和运行状态，如电压、信号强度、温度等。
    """
    __tablename__ = 'device_status'

    # ---- 主键 ----
    id = Column(
        BigInteger, primary_key=True, autoincrement=True,
        comment='自增主键ID'
    )

    # ---- 关联 ----
    udid = Column(
        String(32), nullable=False, index=True,
        comment='设备UDID'
    )
    raw_packet_id = Column(
        BigInteger, nullable=True,
        comment='关联的原始数据包ID(raw_data_packet.id)'
    )

    # ---- 设备运行参数 ----
    battery_voltage = Column(
        Float, nullable=True,
        comment='电池电压(V)'
    )
    solar_voltage = Column(
        Float, nullable=True,
        comment='太阳能板电压(V)'
    )
    signal_strength = Column(
        Float, nullable=True,
        comment='信号强度(dBm)'
    )
    device_temperature = Column(
        Float, nullable=True,
        comment='设备内部温度(℃)'
    )
    humidity = Column(
        Float, nullable=True,
        comment='设备内部湿度(%RH)'
    )

    # ---- 运行状态 ----
    work_mode = Column(
        String(20), nullable=True,
        comment='工作模式: AUTO/MANUAL/SLEEP'
    )
    error_code = Column(
        String(50), nullable=True,
        comment='设备错误码'
    )

    # ---- 时间戳 ----
    collect_time = Column(
        DateTime, nullable=False, index=True,
        comment='采集时间'
    )
    receive_time = Column(
        DateTime, nullable=False, default=datetime.now,
        comment='接收时间'
    )

    # ---- 索引 ----
    __table_args__ = (
        Index('idx_status_udid_time', 'udid', 'collect_time'),
        {'comment': '设备状态表', 'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4'}
    )

    def __repr__(self):
        return (f"<DeviceStatus(udid={self.udid}, Vbat={self.battery_voltage}, "
                f"time={self.collect_time})>")


# ============================================================================
# 表5: channel_raw_data - 通道原始数据表
# ============================================================================
class ChannelRawData(Base):
    """
    通道原始数据表
    记录每个通道解析后的原始频率、频模、温度等数据。
    一个原始数据包可对应多条通道数据（1-16通道）。
    """
    __tablename__ = 'channel_raw_data'

    # ---- 主键 ----
    id = Column(
        BigInteger, primary_key=True, autoincrement=True,
        comment='自增主键ID'
    )

    # ---- 关联 ----
    raw_packet_id = Column(
        BigInteger, nullable=False, index=True,
        comment='关联的原始数据包ID(raw_data_packet.id)'
    )
    udid = Column(
        String(32), nullable=False, index=True,
        comment='设备UDID'
    )

    # ---- 通道信息 ----
    channel = Column(
        SmallInteger, nullable=False, default=1,
        comment='传感器通道号(1-16)'
    )
    sensor_type = Column(
        String(20), nullable=True,
        comment='传感器类型'
    )

    # ---- 原始测量值 ----
    frequency = Column(
        Float, nullable=True,
        comment='频率(Hz)，振弦传感器谐振频率'
    )
    frequency_module = Column(
        Float, nullable=True,
        comment='频模数(frequency²/1000)，用于解算的中间值'
    )
    temperature = Column(
        Float, nullable=True,
        comment='温度(℃)，传感器测点温度'
    )
    thermistor_resistance = Column(
        Float, nullable=True,
        comment='热敏电阻阻值(Ω)'
    )

    # ---- 数据质量 ----
    data_quality = Column(
        String(10), nullable=False, default='VALID',
        comment='数据质量: VALID(有效)/INVALID(无效)/OVERFLOW(溢出)/ZERO_FREQ(零频率)'
    )

    # ---- 时间戳 ----
    collect_time = Column(
        DateTime, nullable=False, index=True,
        comment='采集时间'
    )
    receive_time = Column(
        DateTime, nullable=False, default=datetime.now,
        comment='接收时间'
    )

    # ---- 索引 ----
    __table_args__ = (
        Index('idx_chraw_udid_time', 'udid', 'collect_time'),
        Index('idx_chraw_packet', 'raw_packet_id'),
        Index('idx_chraw_udid_channel', 'udid', 'channel', 'collect_time'),
        {'comment': '通道原始数据表', 'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4'}
    )

    def __repr__(self):
        return (f"<ChannelRawData(udid={self.udid}, ch={self.channel}, "
                f"freq={self.frequency}, temp={self.temperature})>")


# ============================================================================
# 表6: channel_calc_result - 通道解算结果表
# ============================================================================
class ChannelCalcResult(Base):
    """
    通道解算结果表
    记录振弦解算后的物理量值（应变、渗压、位移、力等）。
    通过channel_raw_id关联到原始数据，实现数据追溯链。
    """
    __tablename__ = 'channel_calc_result'

    # ---- 主键 ----
    id = Column(
        BigInteger, primary_key=True, autoincrement=True,
        comment='自增主键ID'
    )

    # ---- 关联追溯 ----
    channel_raw_id = Column(
        BigInteger, nullable=False, index=True,
        comment='关联的通道原始数据ID(channel_raw_data.id)'
    )
    raw_packet_id = Column(
        BigInteger, nullable=True,
        comment='关联的原始数据包ID(raw_data_packet.id)'
    )
    udid = Column(
        String(32), nullable=False, index=True,
        comment='设备UDID'
    )
    channel = Column(
        SmallInteger, nullable=False, default=1,
        comment='通道号'
    )

    # ---- 解算参数（记录本次解算使用的参数） ----
    sensor_type = Column(
        String(20), nullable=True,
        comment='传感器类型'
    )
    k_coefficient = Column(
        Float, nullable=True,
        comment='使用的K系数'
    )
    f0_initial = Column(
        Float, nullable=True,
        comment='使用的初始频率f0(Hz)'
    )
    alpha_temp = Column(
        Float, nullable=True,
        comment='使用的温度系数α'
    )
    t0_initial = Column(
        Float, nullable=True,
        comment='使用的初始温度T0(℃)'
    )

    # ---- 输入值 ----
    raw_frequency = Column(
        Float, nullable=True,
        comment='原始频率(Hz)'
    )
    raw_temperature = Column(
        Float, nullable=True,
        comment='原始温度(℃)'
    )

    # ---- 解算结果 ----
    calc_value = Column(
        Float, nullable=True,
        comment='解算物理量值'
    )
    calc_unit = Column(
        String(10), nullable=True,
        comment='物理量单位: με/kPa/mm/kN'
    )
    temperature_corrected = Column(
        Boolean, nullable=False, default=False,
        comment='是否进行了温度修正'
    )

    # ---- 结果状态 ----
    calc_status = Column(
        String(20), nullable=False, default='SUCCESS',
        comment='解算状态: SUCCESS/WARNING/ERROR/NO_CALIBRATION/OVER_RANGE'
    )
    calc_error = Column(
        String(500), nullable=True,
        comment='解算异常描述'
    )

    # ---- 时间戳 ----
    calc_time = Column(
        DateTime, nullable=False, default=datetime.now, index=True,
        comment='解算时间'
    )
    collect_time = Column(
        DateTime, nullable=True,
        comment='原始数据采集时间'
    )

    # ---- 索引 ----
    __table_args__ = (
        Index('idx_calc_udid_time', 'udid', 'calc_time'),
        Index('idx_calc_udid_collect', 'udid', 'collect_time'),
        Index('idx_calc_channel', 'udid', 'channel', 'calc_time'),
        Index('idx_calc_status', 'calc_status'),
        {'comment': '通道解算结果表', 'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4'}
    )

    def __repr__(self):
        return (f"<ChannelCalcResult(udid={self.udid}, ch={self.channel}, "
                f"value={self.calc_value}{self.calc_unit})>")


# ============================================================================
# 表7: command_send_log - 指令发送日志表
# ============================================================================
class CommandSendLog(Base):
    """
    指令发送日志表
    记录所有通过TCP下发的配置指令及其应答。
    用于追溯设备配置变更历史。
    """
    __tablename__ = 'command_send_log'

    # ---- 主键 ----
    id = Column(
        BigInteger, primary_key=True, autoincrement=True,
        comment='自增主键ID'
    )

    # ---- 目标设备 ----
    udid = Column(
        String(32), nullable=False, index=True,
        comment='目标设备UDID'
    )

    # ---- 指令内容 ----
    command_type = Column(
        String(20), nullable=False,
        comment='指令类型: SETM/GETP/SETP/SAVE/INFO/OTHER'
    )
    command_content = Column(
        String(500), nullable=False,
        comment='指令内容（不含自动追加的#\\r\\n）'
    )
    command_full = Column(
        String(500), nullable=False,
        comment='实际发送的完整指令（含#\\r\\n）'
    )

    # ---- 应答 ----
    reply_content = Column(
        Text, nullable=True,
        comment='设备应答内容'
    )
    reply_received = Column(
        Boolean, nullable=False, default=False,
        comment='是否收到应答'
    )

    # ---- 执行状态 ----
    status = Column(
        String(20), nullable=False, default='PENDING',
        comment='执行状态: PENDING/SENT/SUCCESS/FAILED/TIMEOUT/RETRY'
    )
    retry_count = Column(
        SmallInteger, nullable=False, default=0,
        comment='重试次数'
    )
    error_message = Column(
        String(500), nullable=True,
        comment='错误信息'
    )

    # ---- 时间戳 ----
    send_time = Column(
        DateTime, nullable=False, default=datetime.now, index=True,
        comment='发送时间'
    )
    reply_time = Column(
        DateTime, nullable=True,
        comment='收到应答时间'
    )

    # ---- 索引 ----
    __table_args__ = (
        Index('idx_cmd_udid_time', 'udid', 'send_time'),
        Index('idx_cmd_status', 'status'),
        {'comment': '指令发送日志表', 'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4'}
    )

    def __repr__(self):
        return (f"<CommandSendLog(udid={self.udid}, type={self.command_type}, "
                f"status={self.status})>")


# ============================================================================
# 表8: device_monitor_snapshot - 设备监控快照表（第三方查询用）
# ============================================================================
class DeviceMonitorSnapshot(Base):
    """
    设备监控快照表 - 供第三方系统直接查询最新监测数据。
    每条记录为设备当前时刻的监控快照（append-only日志表）。
    第三方可通过 ORDER BY create_time DESC 获取最新一条。
    """
    __tablename__ = 'device_monitor_snapshot'

    # ---- 主键 ----
    id = Column(
        BigInteger, primary_key=True, autoincrement=True,
        comment='自增主键ID'
    )

    # ---- 核心字段 ----
    udid = Column(
        String(32), nullable=False, index=True,
        comment='设备ID(UDID)'
    )
    site_name = Column(
        String(100), nullable=True, default='',
        comment='站点名称'
    )
    signal = Column(
        Float, nullable=True,
        comment='信号强度(dBm)'
    )
    voltage = Column(
        Float, nullable=True,
        comment='电压(V)'
    )
    frequency = Column(
        Float, nullable=True,
        comment='频率(Hz)'
    )
    temperature = Column(
        Float, nullable=True,
        comment='温度(℃)'
    )
    pressure = Column(
        Float, nullable=True,
        comment='水压(MPa) P=K×(f0²-f²)+Kt×(t-t0)'
    )
    water_level = Column(
        Float, nullable=True,
        comment='水位(m) =P/γ'
    )
    elevation = Column(
        Float, nullable=True,
        comment='水位高程(m) =水位+安装高程'
    )
    status = Column(
        String(20), nullable=True, default='VALID',
        comment='数据状态: VALID/INVALID/OVERFLOW/ZERO_FREQ'
    )

    # ---- 时间戳 ----
    create_time = Column(
        DateTime, nullable=False, default=datetime.now, index=True,
        comment='记录创建时间'
    )

    # ---- 索引 ----
    __table_args__ = (
        Index('idx_snapshot_udid', 'udid'),
        Index('idx_snapshot_time', 'create_time'),
        Index('idx_snapshot_udid_time', 'udid', 'create_time'),
        {'comment': '设备监控快照表（第三方查询）', 'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4'}
    )

    def __repr__(self):
        return (f"<DeviceMonitorSnapshot(udid={self.udid}, signal={self.signal}, "
                f"water={self.water_level}, time={self.create_time})>")
