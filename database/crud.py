# -*- coding: utf-8 -*-
"""
database/crud.py - 数据库CRUD操作封装
=======================================
功能:
    1. 数据库连接管理（长连接复用模式：启动时建连，复用到底，断了自动重连）
    2. 设备信息管理（注册、上下线状态更新）
    3. 批量数据入库（原始数据包、通道数据、解算结果）
    4. 指令日志记录
    5. 按时间范围查询数据
    6. 事务处理与异常回滚

所有操作嵌入详细日志记录。

作者: VS DataCollector Project
版本: 2.0.0
"""

import logging
from datetime import datetime, timedelta
from contextlib import contextmanager

from sqlalchemy import create_engine, text, and_, desc, asc, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError, OperationalError, DisconnectionError
from sqlalchemy.pool import QueuePool

from .models import (
    Base, DeviceInfo, SensorCalibration, RawDataPacket,
    DeviceStatus, ChannelRawData, ChannelCalcResult, CommandSendLog,
    DeviceMonitorSnapshot
)

logger = logging.getLogger(__name__)


# ============================================================================
# 数据库管理器类
# ============================================================================

class DatabaseManager:
    """
    数据库管理器

    封装所有数据库操作，统一管理连接、会话、事务。
    使用 QueuePool（长连接复用），启动时建立连接，后续所有写入共用同一个连接。
    每次写入前 ping 检测连通性，断了自动重连。
    autocommit=True，不再逐条手动 commit。

    Usage:
        db = DatabaseManager(database_url)
        db.init_tables()  # 首次运行创建表
        db.insert_device(...)
    """

    def __init__(self, database_url, pool_size=10, pool_recycle=1800):
        """
        初始化数据库管理器。

        Args:
            database_url (str): SQLAlchemy连接URL
            pool_size (int): 连接池大小（长连接复用模式，保持 10 个连接支撑多设备并发写入）
            pool_recycle (int): 连接回收时间(秒)，防止 MySQL wait_timeout 断开
        """
        self.database_url = database_url
        self.engine = None
        self.session_factory = None
        self._connected = False
        self._pool_size = pool_size
        self._pool_recycle = pool_recycle

        logger.info("数据库管理器初始化（长连接复用模式: 启动建连,复用到停,断了自愈）")

    # ====================================================================
    # 连接管理
    # ====================================================================

    def connect(self):
        """
        建立数据库引擎（长连接复用模式）。

        QueuePool: 启动时建立连接放入池中，后续所有写入复用池中连接。
        pool_pre_ping=True: 每次从池中取出连接前先 ping，确保连接存活。
        pool_recycle=1800: 超过30分钟的连接自动回收重建，防止 MySQL wait_timeout。
        autocommit=True: 每条 SQL 自动提交，不再手动 commit。

        连接失败时记录错误但不抛出异常，程序可继续运行。

        Returns:
            bool: 引擎创建是否成功
        """
        if self._connected:
            logger.info("数据库引擎已就绪，跳过重复初始化")
            return True

        try:
            # QueuePool: 长连接复用池
            # - pool_size=2: 保持 2 个常驻连接
            # - max_overflow=10: 高峰期最多再开 10 个临时连接
            # - pool_pre_ping=True: 取连接前先 ping，验证存活
            # - pool_recycle=1800: 连接存活超过 30 分钟自动刷新
            # - echo=False: 不打印SQL语句
            self.engine = create_engine(
                self.database_url,
                poolclass=QueuePool,
                pool_size=self._pool_size,
                max_overflow=10,
                pool_pre_ping=True,       # ★ 每次取连接前 ping，断了自动重建
                pool_recycle=self._pool_recycle,  # ★ 30分钟自动换新连接
                echo=False,
                connect_args={
                    'connect_timeout': 10,
                }
            )

            # 创建会话工厂
            self.session_factory = sessionmaker(
                bind=self.engine,
                autoflush=True,
                expire_on_commit=False
            )

            # 测试连接
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                conn.commit()

            # 注册断连自动清理事件
            @event.listens_for(self.engine, "invalidate")
            def _on_invalidate(dbapi_connection, connection_record, exception):
                if exception:
                    logger.warning("数据库连接失效，自动清理并重建: %s", exception)

            self._connected = True
            logger.info("数据库引擎就绪（长连接复用: pool_size=%d, recycle=%ds）",
                       self._pool_size, self._pool_recycle)
            return True

        except OperationalError as e:
            logger.error("数据库连接失败（MySQL不可达）: %s", e)
            self._connected = False
            return False
        except SQLAlchemyError as e:
            logger.error("数据库连接失败（SQLAlchemy错误）: %s", e)
            self._connected = False
            return False
        except Exception as e:
            logger.error("数据库连接失败（未知错误）: %s", e)
            self._connected = False
            return False

    def disconnect(self):
        """
        断开数据库连接，释放连接池资源。
        """
        if self.engine:
            try:
                self.engine.dispose()
                logger.info("数据库连接已断开（连接池已释放）")
            except Exception as e:
                logger.error("断开数据库连接时发生异常: %s", e)
            finally:
                self._connected = False
                self.engine = None
                self.session_factory = None

    def is_connected(self):
        """
        检查数据库引擎是否就绪。

        Returns:
            bool: 引擎是否就绪
        """
        return self._connected and self.engine is not None

    def _ping(self):
        """
        ping 检测数据库连接是否存活。

        Returns:
            bool: 连接是否存活
        """
        if not self.is_connected():
            return False
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                conn.commit()
            return True
        except Exception:
            return False

    def _reconnect(self):
        """
        断连后自动重连。先清理旧引擎，再重新建连。
        """
        logger.warning("检测到数据库连接断开，正在自动重连...")
        if self.engine:
            try:
                self.engine.dispose()
            except Exception:
                pass
        self._connected = False
        self.engine = None
        self.session_factory = None
        return self.connect()

    @contextmanager
    def _get_session(self):
        """
        获取数据库会话的上下文管理器（长连接复用模式）。

        每次从连接池中取出已存在的连接（pool_pre_ping 保证存活），
        写入完成后 session.close() 将连接归还池中供下次复用。
        autocommit=True 模式，每条 SQL 自动提交。

        如果遇到连接断开错误，自动标记连接失效，下次 pool_pre_ping 会重建。

        Yields:
            Session: SQLAlchemy会话对象

        Raises:
            RuntimeError: 数据库未连接时抛出
        """
        if not self.is_connected():
            raise RuntimeError("数据库未连接，无法执行操作")

        session = self.session_factory()
        try:
            yield session
            session.commit()
        except (DisconnectionError, OperationalError) as e:
            # 连接断开：回滚 + 标记失效，pool_pre_ping 会在下次自动重建
            try:
                session.rollback()
            except Exception:
                pass
            logger.error("数据库连接断开: %s (连接已失效，下次自动重建)", e)
            try:
                session.invalidate()
            except Exception:
                pass
            raise
        except SQLAlchemyError as e:
            try:
                session.rollback()
            except Exception:
                pass
            logger.error("数据库事务错误: %s", e)
            raise
        except Exception as e:
            try:
                session.rollback()
            except Exception:
                pass
            logger.error("数据库操作异常: %s", e)
            raise
        finally:
            session.close()  # 归还连接到池中（而非销毁）

    # ====================================================================
    # 表初始化
    # ====================================================================

    def init_tables(self):
        """
        初始化数据库表结构。

        使用SQLAlchemy的create_all方法，根据模型定义自动创建表。
        如果表已存在则跳过（不会覆盖数据）。

        Returns:
            bool: 初始化是否成功
        """
        if not self.is_connected():
            logger.warning("数据库未连接，无法初始化表结构")
            return False

        try:
            Base.metadata.create_all(self.engine)
            logger.info("数据库表结构初始化完成")

            # ---- 表结构迁移: 追加新列 ----
            try:
                with self._get_session() as session:
                    session.execute(text(
                        "ALTER TABLE device_monitor_snapshot "
                        "ADD COLUMN elevation DOUBLE DEFAULT NULL "
                        "COMMENT '水位高程(m)' AFTER water_level"
                    ))
                    session.commit()
                    logger.info("表 device_monitor_snapshot 已追加 elevation 列")
            except Exception:
                pass  # 列已存在或表不存在，忽略

            return True
        except SQLAlchemyError as e:
            logger.error("数据库表结构初始化失败: %s", e)
            return False

    # ====================================================================
    # 设备信息管理
    # ====================================================================

    def register_device(self, udid, device_type='', firmware_version='',
                        device_name='', ip_address='', connect_port=0):
        """
        注册新设备或更新已有设备信息。

        如果设备已存在（相同UDID），则更新设备类型、固件版本等信息，
        但不会覆盖首次注册时间。

        Args:
            udid (str): 设备UDID
            device_type (str): 设备型号
            firmware_version (str): 固件版本
            device_name (str): 设备名称
            ip_address (str): IP地址
            connect_port (int): 连接端口

        Returns:
            bool: 操作是否成功
        """
        try:
            with self._get_session() as session:
                device = session.query(DeviceInfo).filter_by(udid=udid).first()

                if device:
                    # 更新已有设备
                    if device_type:
                        device.device_type = device_type
                    if firmware_version:
                        device.firmware_version = firmware_version
                    if device_name:
                        device.device_name = device_name
                    device.ip_address = ip_address
                    device.connect_port = connect_port
                    logger.info("设备信息已更新: UDID=%s | Type=%s", udid, device_type)
                else:
                    # 新增设备
                    device = DeviceInfo(
                        udid=udid,
                        device_type=device_type,
                        firmware_version=firmware_version,
                        device_name=device_name,
                        register_time=datetime.now(),
                        ip_address=ip_address,
                        connect_port=connect_port,
                    )
                    session.add(device)
                    logger.info("新设备已注册: UDID=%s | Type=%s", udid, device_type)
            return True
        except Exception as e:
            logger.error("注册设备失败 [UDID=%s]: %s", udid, e)
            return False

    def update_device_online_status(self, udid, is_online):
        """
        更新设备上下线状态。

        Args:
            udid (str): 设备UDID
            is_online (bool): 是否在线

        Returns:
            bool: 操作是否成功
        """
        try:
            with self._get_session() as session:
                device = session.query(DeviceInfo).filter_by(udid=udid).first()
                if device:
                    device.is_online = is_online
                    if is_online:
                        device.last_online_time = datetime.now()
                    else:
                        device.last_offline_time = datetime.now()
                    status = "上线" if is_online else "离线"
                    logger.info("设备状态更新: UDID=%s | Status=%s", udid, status)
                else:
                    # 设备不存在则自动注册
                    logger.info("设备不存在，自动注册: UDID=%s", udid)
                    session.add(DeviceInfo(
                        udid=udid,
                        is_online=is_online,
                        register_time=datetime.now(),
                        last_online_time=datetime.now() if is_online else None,
                    ))
            return True
        except Exception as e:
            logger.error("更新设备状态失败 [UDID=%s]: %s", udid, e)
            return False

    def get_device_info(self, udid):
        """
        查询设备信息。

        Args:
            udid (str): 设备UDID

        Returns:
            DeviceInfo or None: 设备信息对象，不存在返回None
        """
        try:
            with self._get_session() as session:
                return session.query(DeviceInfo).filter_by(udid=udid).first()
        except Exception as e:
            logger.error("查询设备信息失败 [UDID=%s]: %s", udid, e)
            return None

    # ====================================================================
    # 传感器标定参数管理
    # ====================================================================

    def get_calibration(self, udid, channel):
        """
        查询指定设备和通道的传感器标定参数。

        Args:
            udid (str): 设备UDID
            channel (int): 通道号

        Returns:
            SensorCalibration or None: 标定参数，不存在返回None
        """
        try:
            with self._get_session() as session:
                return session.query(SensorCalibration).filter_by(
                    udid=udid, channel=channel, is_active=True
                ).first()
        except Exception as e:
            logger.error("查询标定参数失败 [UDID=%s, CH=%d]: %s", udid, channel, e)
            return None

    def set_calibration(self, udid, channel, sensor_type='strain',
                        k_coefficient=1.0, f0_initial=0.0, alpha_temp=0.0,
                        t0_initial=20.0, **kwargs):
        """
        设置或更新传感器标定参数（UPSERT逻辑）。

        Args:
            udid (str): 设备UDID
            channel (int): 通道号
            sensor_type (str): 传感器类型
            k_coefficient (float): K系数
            f0_initial (float): 初始频率
            alpha_temp (float): 温度系数
            t0_initial (float): 初始温度
            **kwargs: 其他可选参数(量程等)

        Returns:
            bool: 操作是否成功
        """
        try:
            with self._get_session() as session:
                cal = session.query(SensorCalibration).filter_by(
                    udid=udid, channel=channel
                ).first()

                if cal:
                    # 更新
                    cal.sensor_type = sensor_type
                    cal.k_coefficient = k_coefficient
                    cal.f0_initial = f0_initial
                    cal.alpha_temp = alpha_temp
                    cal.t0_initial = t0_initial
                    cal.update_time = datetime.now()
                    for key, value in kwargs.items():
                        if hasattr(cal, key):
                            setattr(cal, key, value)
                else:
                    # 新增
                    cal = SensorCalibration(
                        udid=udid,
                        channel=channel,
                        sensor_type=sensor_type,
                        k_coefficient=k_coefficient,
                        f0_initial=f0_initial,
                        alpha_temp=alpha_temp,
                        t0_initial=t0_initial,
                        **{k: v for k, v in kwargs.items()
                           if hasattr(SensorCalibration, k)}
                    )
                    session.add(cal)

                logger.info("标定参数已保存: UDID=%s | CH=%d | Type=%s | K=%.6f | f0=%.2f",
                            udid, channel, sensor_type, k_coefficient, f0_initial)
            return True
        except Exception as e:
            logger.error("保存标定参数失败 [UDID=%s, CH=%d]: %s", udid, channel, e)
            return False

    # ====================================================================
    # 批量数据入库
    # ====================================================================

    def insert_raw_packet(self, udid, raw_frame_hex='', raw_frame_str='',
                          protocol_type='UNKNOWN', is_retransmit=False,
                          retransmit_time=None, checksum_valid=None,
                          checksum_type=None, collect_time=None,
                          parse_status='PENDING', parse_error=None):
        """
        插入一条原始数据包记录。

        Args:
            udid (str): 设备UDID
            raw_frame_hex (str): 原始帧HEX
            raw_frame_str (str): 原始帧字符串
            protocol_type (str): 协议类型
            is_retransmit (bool): 是否补发数据
            retransmit_time (datetime): 补发原始采集时间
            checksum_valid (bool): 校验结果
            checksum_type (str): 校验类型
            collect_time (datetime): 采集时间
            parse_status (str): 解析状态
            parse_error (str): 解析错误信息

        Returns:
            int or None: 插入记录的ID，失败返回None
        """
        try:
            with self._get_session() as session:
                packet = RawDataPacket(
                    udid=udid,
                    raw_frame_hex=raw_frame_hex,
                    raw_frame_str=raw_frame_str,
                    protocol_type=protocol_type,
                    is_retransmit=is_retransmit,
                    retransmit_time=retransmit_time,
                    checksum_valid=checksum_valid,
                    checksum_type=checksum_type,
                    receive_time=datetime.now(),
                    collect_time=collect_time or datetime.now(),
                    parse_status=parse_status,
                    parse_error=parse_error,
                )
                session.add(packet)
                session.flush()  # 获取自增ID
                packet_id = packet.id
                logger.debug("原始数据包已入库: ID=%d | UDID=%s | Proto=%s",
                            packet_id, udid, protocol_type)
                return packet_id
        except Exception as e:
            logger.error("原始数据包入库失败 [UDID=%s]: %s", udid, e)
            return None

    def batch_insert_channel_raw(self, channel_data_list):
        """
        批量插入通道原始数据。

        Args:
            channel_data_list (list[dict]): 通道数据字典列表，每项包含:
                - raw_packet_id (int, required)
                - udid (str, required)
                - channel (int, required)
                - frequency (float, optional)
                - frequency_module (float, optional)
                - temperature (float, optional)
                - thermistor_resistance (float, optional)
                - sensor_type (str, optional)
                - data_quality (str, optional)
                - collect_time (datetime, required)

        Returns:
            list[int]: 插入记录的ID列表，部分失败不影响其他记录
        """
        inserted_ids = []
        try:
            with self._get_session() as session:
                for item in channel_data_list:
                    try:
                        record = ChannelRawData(
                            raw_packet_id=item['raw_packet_id'],
                            udid=item['udid'],
                            channel=item['channel'],
                            frequency=item.get('frequency'),
                            frequency_module=item.get('frequency_module'),
                            temperature=item.get('temperature'),
                            thermistor_resistance=item.get('thermistor_resistance'),
                            sensor_type=item.get('sensor_type'),
                            data_quality=item.get('data_quality', 'VALID'),
                            collect_time=item.get('collect_time', datetime.now()),
                            receive_time=datetime.now(),
                        )
                        session.add(record)
                        session.flush()
                        inserted_ids.append(record.id)
                    except Exception as item_err:
                        logger.error("单条通道数据插入失败 [UDID=%s CH=%d]: %s",
                                    item.get('udid'), item.get('channel'), item_err)

                logger.info("通道原始数据批量入库: %d/%d 条成功",
                           len(inserted_ids), len(channel_data_list))
            return inserted_ids
        except Exception as e:
            logger.error("通道数据批量入库事务失败: %s", e)
            return inserted_ids

    def batch_insert_calc_results(self, calc_result_list):
        """
        批量插入解算结果。

        Args:
            calc_result_list (list[dict]): 解算结果字典列表，每项包含:
                - channel_raw_id (int, required)
                - udid (str, required)
                - channel (int, required)
                - 其他解算参数字段

        Returns:
            list[int]: 插入记录的ID列表
        """
        inserted_ids = []
        try:
            with self._get_session() as session:
                for item in calc_result_list:
                    try:
                        record = ChannelCalcResult(
                            channel_raw_id=item['channel_raw_id'],
                            raw_packet_id=item.get('raw_packet_id'),
                            udid=item['udid'],
                            channel=item['channel'],
                            sensor_type=item.get('sensor_type'),
                            k_coefficient=item.get('k_coefficient'),
                            f0_initial=item.get('f0_initial'),
                            alpha_temp=item.get('alpha_temp'),
                            t0_initial=item.get('t0_initial'),
                            raw_frequency=item.get('raw_frequency'),
                            raw_temperature=item.get('raw_temperature'),
                            calc_value=item.get('calc_value'),
                            calc_unit=item.get('calc_unit'),
                            temperature_corrected=item.get('temperature_corrected', False),
                            calc_status=item.get('calc_status', 'SUCCESS'),
                            calc_error=item.get('calc_error'),
                            calc_time=datetime.now(),
                            collect_time=item.get('collect_time'),
                        )
                        session.add(record)
                        session.flush()
                        inserted_ids.append(record.id)
                    except Exception as item_err:
                        logger.error("单条解算结果插入失败 [UDID=%s CH=%d]: %s",
                                    item.get('udid'), item.get('channel'), item_err)

                logger.info("解算结果批量入库: %d/%d 条成功",
                           len(inserted_ids), len(calc_result_list))
            return inserted_ids
        except Exception as e:
            logger.error("解算结果批量入库事务失败: %s", e)
            return inserted_ids

    def insert_device_status(self, udid, battery_voltage=None,
                             solar_voltage=None, signal_strength=None,
                             device_temperature=None, humidity=None,
                             work_mode=None, error_code=None,
                             collect_time=None, raw_packet_id=None):
        """
        插入设备状态记录。

        Returns:
            int or None: 插入记录的ID
        """
        try:
            with self._get_session() as session:
                status = DeviceStatus(
                    udid=udid,
                    raw_packet_id=raw_packet_id,
                    battery_voltage=battery_voltage,
                    solar_voltage=solar_voltage,
                    signal_strength=signal_strength,
                    device_temperature=device_temperature,
                    humidity=humidity,
                    work_mode=work_mode,
                    error_code=error_code,
                    collect_time=collect_time or datetime.now(),
                    receive_time=datetime.now(),
                )
                session.add(status)
                session.flush()
                logger.debug("设备状态已入库: UDID=%s | Vbat=%s", udid, battery_voltage)
                return status.id
        except Exception as e:
            logger.error("设备状态入库失败 [UDID=%s]: %s", udid, e)
            return None

    # ====================================================================
    # 指令日志
    # ====================================================================

    def log_command(self, udid, command_type, command_content, command_full,
                    status='PENDING', reply_content=None, reply_received=False,
                    error_message=None, retry_count=0):
        """
        记录指令发送日志。

        Args:
            udid (str): 设备UDID
            command_type (str): 指令类型
            command_content (str): 指令内容
            command_full (str): 完整指令
            status (str): 执行状态
            reply_content (str): 应答内容
            reply_received (bool): 是否收到应答
            error_message (str): 错误信息
            retry_count (int): 重试次数

        Returns:
            int or None: 插入记录的ID
        """
        try:
            with self._get_session() as session:
                log_entry = CommandSendLog(
                    udid=udid,
                    command_type=command_type,
                    command_content=command_content,
                    command_full=command_full,
                    reply_content=reply_content,
                    reply_received=reply_received,
                    status=status,
                    error_message=error_message,
                    retry_count=retry_count,
                    send_time=datetime.now(),
                    reply_time=datetime.now() if reply_received else None,
                )
                session.add(log_entry)
                session.flush()
                log_id = log_entry.id
                logger.info("指令日志已记录: ID=%d | UDID=%s | Type=%s | Status=%s",
                           log_id, udid, command_type, status)
                return log_id
        except Exception as e:
            logger.error("指令日志记录失败 [UDID=%s]: %s", udid, e)
            return None

    def update_command_log(self, log_id, status=None, reply_content=None,
                           reply_received=None, error_message=None,
                           retry_count=None):
        """
        更新指令日志记录（用于记录执行结果）。

        Args:
            log_id (int): 日志记录ID
            status (str): 新状态
            reply_content (str): 应答内容
            reply_received (bool): 是否收到应答
            error_message (str): 错误信息
            retry_count (int): 重试次数

        Returns:
            bool: 操作是否成功
        """
        try:
            with self._get_session() as session:
                log_entry = session.query(CommandSendLog).filter_by(id=log_id).first()
                if not log_entry:
                    logger.warning("指令日志记录不存在: ID=%d", log_id)
                    return False

                if status is not None:
                    log_entry.status = status
                if reply_content is not None:
                    log_entry.reply_content = reply_content
                if reply_received is not None:
                    log_entry.reply_received = reply_received
                    if reply_received:
                        log_entry.reply_time = datetime.now()
                if error_message is not None:
                    log_entry.error_message = error_message
                if retry_count is not None:
                    log_entry.retry_count = retry_count

                logger.debug("指令日志已更新: ID=%d | Status=%s", log_id, status)
            return True
        except Exception as e:
            logger.error("更新指令日志失败 [ID=%d]: %s", log_id, e)
            return False

    # ====================================================================
    # 设备监控快照（第三方查询用）
    # ====================================================================

    def insert_device_snapshot(self, udid, site_name='', signal=None,
                               voltage=None, frequency=None, temperature=None,
                               pressure=None, water_level=None, elevation=None,
                               status=None):
        """
        插入设备监控快照记录（append-only日志表）。
        第三方系统可通过 ORDER BY create_time DESC LIMIT 1 查询设备最新状态。

        Args:
            udid (str): 设备ID
            site_name (str): 站点名称
            signal (float): 信号强度(dBm)
            voltage (float): 电压(V)
            frequency (float): 频率(Hz)
            temperature (float): 温度(℃)
            pressure (float): 水压(MPa) P=K*(f0²-f²)+Kt*(t-t0)
            water_level (float): 水位(m) =P/γ
            elevation (float): 水位高程(m) =水位+安装高程
            status (str): 数据状态

        Returns:
            bool: 操作是否成功
        """
        try:
            with self._get_session() as session:
                snapshot = DeviceMonitorSnapshot(
                    udid=udid,
                    site_name=site_name or '',
                    signal=signal,
                    voltage=voltage,
                    frequency=frequency,
                    temperature=temperature,
                    pressure=pressure,
                    water_level=water_level,
                    elevation=elevation,
                    status=status or 'VALID',
                )
                session.add(snapshot)
            return True
        except Exception as e:
            logger.error("插入设备快照失败 [UDID=%s]: %s", udid, e)
            return False

    # ====================================================================
    # 数据查询
    # ====================================================================

    def query_channel_data_by_time(self, udid, channel, start_time, end_time):
        """
        按时间范围查询通道原始数据。

        Args:
            udid (str): 设备UDID
            channel (int): 通道号
            start_time (datetime): 开始时间
            end_time (datetime): 结束时间

        Returns:
            list[ChannelRawData]: 查询结果列表
        """
        try:
            with self._get_session() as session:
                results = session.query(ChannelRawData).filter(
                    and_(
                        ChannelRawData.udid == udid,
                        ChannelRawData.channel == channel,
                        ChannelRawData.collect_time >= start_time,
                        ChannelRawData.collect_time <= end_time,
                    )
                ).order_by(asc(ChannelRawData.collect_time)).all()
                logger.info("查询通道数据: UDID=%s CH=%d | %d条记录",
                           udid, channel, len(results))
                return results
        except Exception as e:
            logger.error("查询通道数据失败 [UDID=%s CH=%d]: %s", udid, channel, e)
            return []

    def query_calc_results_by_time(self, udid, channel, start_time, end_time):
        """
        按时间范围查询解算结果。

        Args:
            udid (str): 设备UDID
            channel (int): 通道号
            start_time (datetime): 开始时间
            end_time (datetime): 结束时间

        Returns:
            list[ChannelCalcResult]: 查询结果列表
        """
        try:
            with self._get_session() as session:
                results = session.query(ChannelCalcResult).filter(
                    and_(
                        ChannelCalcResult.udid == udid,
                        ChannelCalcResult.channel == channel,
                        ChannelCalcResult.calc_time >= start_time,
                        ChannelCalcResult.calc_time <= end_time,
                    )
                ).order_by(asc(ChannelCalcResult.calc_time)).all()
                logger.info("查询解算结果: UDID=%s CH=%d | %d条记录",
                           udid, channel, len(results))
                return results
        except Exception as e:
            logger.error("查询解算结果失败 [UDID=%s CH=%d]: %s", udid, channel, e)
            return []

    def get_online_devices(self):
        """
        获取所有在线设备列表。

        Returns:
            list[DeviceInfo]: 在线设备列表
        """
        try:
            with self._get_session() as session:
                return session.query(DeviceInfo).filter_by(is_online=True).all()
        except Exception as e:
            logger.error("查询在线设备失败: %s", e)
            return []

    def update_raw_packet_parse_status(self, packet_id, parse_status, parse_error=None):
        """
        更新原始数据包的解析状态。

        Args:
            packet_id (int): 数据包ID
            parse_status (str): 解析状态
            parse_error (str): 错误信息

        Returns:
            bool: 操作是否成功
        """
        try:
            with self._get_session() as session:
                packet = session.query(RawDataPacket).filter_by(id=packet_id).first()
                if packet:
                    packet.parse_status = parse_status
                    if parse_error:
                        packet.parse_error = parse_error
            return True
        except Exception as e:
            logger.error("更新解析状态失败 [ID=%d]: %s", packet_id, e)
            return False
