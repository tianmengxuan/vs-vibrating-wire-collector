# -*- coding: utf-8 -*-
"""
tcp_server/server.py - TCP异步服务端核心
==========================================
功能:
    1. 基于asyncio的异步TCP服务端
    2. 支持多设备高并发接入
    3. 设备会话全局管理
    4. 自动更新设备上下线状态到MySQL
    5. 业务链路集成: 数据接收→拆包→解析→解算→入库

作者: VS DataCollector Project
版本: 1.0.0
"""

import asyncio
import logging
import re
from datetime import datetime

from .session import DeviceSession

logger = logging.getLogger(__name__)

# 无效/垃圾 UDID 前缀列表（协议名、HTTP标识等不应作为设备ID）
_GARBAGE_UDID_PREFIXES = ('UNKNOWN', 'SL651_', 'HTTP_', 'HTTPS_', 'SSL_')

# 合法UDID正则：仅包含字母数字下划线连字符，长度2-32
_VALID_UDID_RE = re.compile(r'^[A-Za-z0-9_\-]{2,32}$')


class TCPServer:
    """
    TCP异步服务端。

    管理所有设备连接，协调数据接收、协议解析、解算、入库的完整链路。

    Attributes:
        host (str): 监听地址
        port (int): 监听端口
        device_sessions (dict): {udid: DeviceSession} 在线设备会话表
        server: asyncio.Server实例
    """

    def __init__(self, host='0.0.0.0', port=9000, db_manager=None,
                 calculator=None):
        """
        初始化TCP服务端。

        Args:
            host (str): 监听地址，默认0.0.0.0（所有网卡）
            port (int): 监听端口，默认9000
            db_manager (DatabaseManager): 数据库管理器
            calculator (VibratingWireCalculator): 振弦解算器
        """
        self.host = host
        self.port = port
        self.db = db_manager
        self.calculator = calculator

        # 设备会话表: {UDID: DeviceSession}
        self.device_sessions = {}
        self._sessions_lock = asyncio.Lock()

        # asyncio服务端
        self.server = None
        self._running = False
        self.gui_data_callback = None  # GUI数据回调 (udid, data_dict) -> None
        self._on_device_connect = None  # GUI上线通知 (udid, addr) -> None

        logger.info("TCP服务端初始化: %s:%d", host, port)

    # ====================================================================
    # 设备会话管理
    # ====================================================================

    async def _register_session(self, session):
        """
        注册设备会话（UDID已知时）。

        Args:
            session (DeviceSession): 设备会话实例
        """
        async with self._sessions_lock:
            if session.udid:
                # 如果同UDID已有旧会话，先关闭
                old_session = self.device_sessions.get(session.udid)
                if old_session and old_session is not session:
                    logger.warning("设备重复连接，关闭旧会话 [%s]", session.udid)
                    await old_session.close()

                self.device_sessions[session.udid] = session

                # 通知GUI设备上线
                if self._on_device_connect:
                    try:
                        addr_str = f"{session.addr[0]}:{session.addr[1]}"
                        self._on_device_connect(session.udid, addr_str)
                    except Exception:
                        pass

    async def _unregister_session(self, session):
        """
        注销设备会话。

        Args:
            session (DeviceSession): 设备会话实例
        """
        async with self._sessions_lock:
            if session.udid and session.udid in self.device_sessions:
                if self.device_sessions[session.udid] is session:
                    del self.device_sessions[session.udid]

    async def get_session(self, udid):
        """
        获取指定UDID的设备会话。

        Args:
            udid (str): 设备UDID

        Returns:
            DeviceSession or None: 设备会话
        """
        async with self._sessions_lock:
            return self.device_sessions.get(udid)

    async def get_online_udids(self):
        """
        获取所有在线设备UDID列表。

        Returns:
            list[str]: UDID列表
        """
        async with self._sessions_lock:
            return list(self.device_sessions.keys())

    # ====================================================================
    # 连接处理
    # ====================================================================

    async def _handle_client(self, reader, writer):
        """
        处理新的客户端连接。

        为每个连接创建DeviceSession，启动接收循环，
        并在断开时自动清理。

        Args:
            reader (StreamReader): 读取流
            writer (StreamWriter): 写入流
        """
        session = None
        addr = writer.get_extra_info('peername', ('unknown', 0))

        try:
            # 创建设备会话
            session = DeviceSession(
                reader=reader,
                writer=writer,
                on_data_received=self._on_device_data,
                on_disconnect=self._on_device_disconnect,
            )

            logger.info("新设备连接: %s:%d", addr[0], addr[1])

            # 启动接收循环
            await session.receive_loop()

        except asyncio.CancelledError:
            logger.info("客户端处理被取消 [%s:%d]", addr[0], addr[1])
        except Exception as e:
            logger.error("客户端处理异常 [%s:%d]: %s", addr[0], addr[1], e, exc_info=True)
        finally:
            if session:
                # 确保会话关闭并触发断线处理
                if session.is_running:
                    await session.close()

    # ====================================================================
    # 业务回调
    # ====================================================================

    async def _on_device_data(self, session, data):
        """
        设备数据接收回调。

        完整的业务处理链路:
            1. 记录原始数据
            2. 协议识别解析
            3. 原始数据包入库
            4. 通道原始数据入库
            5. 振弦解算
            6. 解算结果入库

        Args:
            session (DeviceSession): 设备会话
            data (bytes): 原始数据
        """
        udid = session.udid or 'UNKNOWN'

        try:
            # 动态导入（避免循环依赖）
            from protocol.dispatcher import identify_and_parse

            # ---- 步骤1: 协议解析 ----
            logger.info("收到设备数据 [%s]: 长度=%d  HEX=%s", udid, len(data),
                        data.hex().upper())

            parse_result = identify_and_parse(data)

            # 更新UDID（排除垃圾UDID和乱码）
            parsed_udid = parse_result.get('udid', '')
            if parsed_udid and parsed_udid not in ('', 'UNKNOWN', 'UNKNOWN_DEVICE'):
                if not any(parsed_udid.startswith(p) for p in _GARBAGE_UDID_PREFIXES):
                    # 额外正则校验：UDID必须为合法格式（字母数字_-，2~32字符）
                    if _VALID_UDID_RE.match(parsed_udid):
                        if not session.udid:
                            session.set_udid(parsed_udid)
                            await self._register_session(session)
                            udid = parsed_udid
                    else:
                        logger.warning("过滤乱码UDID: %s... (协议=%s)",
                                      parsed_udid[:20], parse_result.get('protocol'))

            # ---- 步骤2: 原始数据包入库（仅有效UDID） ----
            raw_packet_id = None
            if self.db and self.db.is_connected():
                if udid not in ('', 'UNKNOWN', 'UNKNOWN_DEVICE') and not any(
                        udid.startswith(p) for p in _GARBAGE_UDID_PREFIXES):
                    raw_packet_id = self.db.insert_raw_packet(
                        udid=udid,
                        raw_frame_hex=parse_result['raw_packet'].get('raw_hex', ''),
                        raw_frame_str=parse_result['raw_packet'].get('raw_str', ''),
                        protocol_type=parse_result['protocol'],
                        is_retransmit=parse_result.get('is_retransmit', False),
                        retransmit_time=parse_result.get('retransmit_time'),
                        checksum_valid=parse_result.get('checksum_valid'),
                        checksum_type='CRC16' if 'CRC' in str(parse_result.get('checksum_valid', '')) else 'NONE',
                        collect_time=parse_result.get('timestamp'),
                        parse_status=parse_result['status'],
                        parse_error=parse_result.get('error'),
                    )
                if raw_packet_id:
                    logger.debug("原始数据包已入库: ID=%d [%s]", raw_packet_id, udid)
                elif raw_packet_id is None and udid not in ('', 'UNKNOWN', 'UNKNOWN_DEVICE'):
                    logger.warning("原始数据包入库失败 [%s]", udid)

            # ---- 步骤3: 设备状态入库 ----
            if self.db and self.db.is_connected() and raw_packet_id:
                self.db.insert_device_status(
                    udid=udid,
                    battery_voltage=parse_result.get('battery_voltage') or parse_result.get('voltage'),
                    signal_strength=parse_result.get('signal'),
                    device_temperature=parse_result.get('device_temp'),
                    work_mode=parse_result.get('work_mode'),
                    collect_time=parse_result.get('timestamp'),
                    raw_packet_id=raw_packet_id,
                )

            # ---- 步骤4: 通道原始数据入库 ----
            channels = parse_result.get('channels', [])
            channel_ids = []

            if channels and self.db and self.db.is_connected() and raw_packet_id:
                channel_data_list = []
                for ch in channels:
                    channel_data_list.append({
                        'raw_packet_id': raw_packet_id,
                        'udid': udid,
                        'channel': ch.get('channel', 0),
                        'frequency': ch.get('frequency'),
                        'frequency_module': ch.get('freq_module') or ch.get('frequency_module'),
                        'temperature': ch.get('temp') or ch.get('temperature'),
                        'thermistor_resistance': ch.get('thermistor_r') or ch.get('thermistor_resistance'),
                        'sensor_type': ch.get('sensor_type'),
                        'data_quality': ch.get('data_quality', 'VALID'),
                        'collect_time': parse_result.get('timestamp') or datetime.now(),
                    })

                channel_ids = self.db.batch_insert_channel_raw(channel_data_list)
                logger.info("通道数据入库: %d/%d 条 [%s]", len(channel_ids), len(channels), udid)

            # ---- 步骤5: 振弦解算 ----
            calc_results = []
            if self.calculator and channels:
                # 关联 channel_raw_id
                for i, ch in enumerate(channels):
                    if i < len(channel_ids):
                        ch['channel_raw_id'] = channel_ids[i]
                    ch['udid'] = udid

                calc_results, saved_count = self.calculator.process_channel_data(
                    channels, raw_packet_id=raw_packet_id
                )
                logger.info("解算完成 [%s]: %d/%d 条成功", udid, saved_count, len(channels))

            # ---- 步骤5.5: 推送给GUI（仅有效设备） ----
            if self.gui_data_callback and udid not in ('', 'UNKNOWN', 'UNKNOWN_DEVICE'):
                if not any(udid.startswith(p) for p in _GARBAGE_UDID_PREFIXES):
                    try:
                        gui_channels = []
                        for i, ch in enumerate(channels):
                            ch_data = {
                                'channel': ch.get('channel', 0),
                                'frequency': ch.get('frequency'),
                                'temp': ch.get('temp') or ch.get('temperature'),
                                'freq_module': ch.get('freq_module') or ch.get('frequency_module'),
                                'thermistor_r': ch.get('thermistor_r'),
                                'data_quality': ch.get('data_quality', 'VALID'),
                            }
                            if i < len(calc_results):
                                cr = calc_results[i]
                                ch_data['calc_value'] = cr.get('calc_value')
                                ch_data['calc_unit'] = cr.get('calc_unit')
                                ch_data['sensor_type'] = cr.get('sensor_type')
                            gui_channels.append(ch_data)

                        self.gui_data_callback(udid, {
                            'signal': parse_result.get('signal'),
                            'voltage': parse_result.get('voltage') or parse_result.get('battery_voltage'),
                            'channels': gui_channels,
                            'raw_hex': parse_result['raw_packet'].get('raw_hex', ''),
                            'raw_str': parse_result['raw_packet'].get('raw_str', ''),
                            'protocol': parse_result.get('protocol', ''),
                            'data_len': len(data),
                            'addr': f"{session.addr[0]}:{session.addr[1]}",
                        })
                    except Exception as gui_err:
                        logger.debug("GUI回调异常: %s", gui_err)

            # ---- 步骤6: 更新解析状态 ----
            if self.db and self.db.is_connected() and raw_packet_id:
                self.db.update_raw_packet_parse_status(
                    raw_packet_id,
                    parse_result['status'],
                    parse_result.get('error'),
                )

        except Exception as e:
            logger.error("数据处理异常 [%s]: %s", udid, e, exc_info=True)

    async def _on_device_disconnect(self, session):
        """
        设备断开回调。

        更新设备上下线状态到数据库。

        Args:
            session (DeviceSession): 设备会话
        """
        udid = session.udid or f"IP_{session.addr[0]}"
        logger.info("设备断开: %s | %s:%d", udid, session.addr[0], session.addr[1])

        # 注销会话
        await self._unregister_session(session)

        # 更新数据库状态
        if self.db and self.db.is_connected():
            self.db.update_device_online_status(udid, False)

    # ====================================================================
    # 服务启停
    # ====================================================================

    async def start(self):
        """
        启动TCP服务端。

        开始监听指定端口，进入异步事件循环。

        Returns:
            bool: 启动是否成功
        """
        try:
            self.server = await asyncio.start_server(
                self._handle_client,
                host=self.host,
                port=self.port,
                backlog=100,  # 连接队列长度
            )
            self._running = True

            # 获取实际监听的地址
            addrs = ', '.join(str(s.getsockname()) for s in self.server.sockets)
            logger.info("=" * 60)
            logger.info("TCP服务端已启动: %s", addrs)
            logger.info("  监听地址: %s:%d", self.host, self.port)
            logger.info("  在线设备: %d", len(self.device_sessions))
            logger.info("=" * 60)

            return True

        except OSError as e:
            logger.error("TCP服务启动失败（端口可能被占用）: %s", e)
            return False
        except Exception as e:
            logger.error("TCP服务启动失败: %s", e)
            return False

    async def stop(self):
        """
        停止TCP服务端。

        关闭所有设备连接，停止接收新连接，释放资源。
        """
        logger.info("正在停止TCP服务端...")
        self._running = False

        # 关闭所有设备会话
        async with self._sessions_lock:
            sessions_to_close = list(self.device_sessions.values())
            self.device_sessions.clear()

        for session in sessions_to_close:
            try:
                await session.close()
            except Exception as e:
                logger.error("关闭设备会话异常 [%s]: %s",
                           session.udid or 'UNKNOWN', e)

        # 关闭服务端
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("TCP服务端已停止")

    @property
    def is_running(self):
        """服务是否在运行"""
        return self._running

    # ====================================================================
    # 发送指令到指定设备
    # ====================================================================

    async def send_command_to_device(self, udid, command_bytes, timeout=15, max_retries=3):
        """
        向指定设备发送指令。

        Args:
            udid (str): 设备UDID
            command_bytes (bytes): 完整指令（含 #\\r\\n）
            timeout (int): 超时时间(秒)
            max_retries (int): 最大重试次数

        Returns:
            tuple: (success, reply)
        """
        session = await self.get_session(udid)
        if not session:
            logger.warning("设备不在线: %s", udid)
            return False, None

        return await session.send_command(command_bytes, timeout, max_retries)

    async def send_command_to_all(self, command_bytes, timeout=15):
        """
        向所有在线设备广播指令（慎用）。

        Args:
            command_bytes (bytes): 完整指令
            timeout (int): 超时时间

        Returns:
            dict: {udid: (success, reply)}
        """
        results = {}
        async with self._sessions_lock:
            sessions = list(self.device_sessions.items())

        for udid, session in sessions:
            try:
                success, reply = await session.send_command(
                    command_bytes, timeout=timeout, max_retries=1
                )
                results[udid] = (success, reply)
            except Exception as e:
                logger.error("广播指令失败 [%s]: %s", udid, e)
                results[udid] = (False, str(e))

        return results
