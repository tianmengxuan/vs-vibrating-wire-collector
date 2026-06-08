# -*- coding: utf-8 -*-
"""
tcp_server/session.py - 设备会话管理
=====================================
功能:
    1. 维护每个TCP连接对应的设备会话
    2. Buffer粘包拆包处理
    3. 指令队列管理（独立队列，支持同步下发、等待应答、超时重发）
    4. 设备上下线状态管理
    5. 数据接收回调

作者: VS DataCollector Project
版本: 1.0.0
"""

import asyncio
import logging
import time
import re
from datetime import datetime

logger = logging.getLogger(__name__)

READ_TIMEOUT_SECONDS = 60
IDLE_TIMEOUT_SECONDS = 3600


# HTTP请求 / 非设备流量特征关键词（接收到这些直接丢弃）
_NON_DEVICE_PATTERNS = [
    rb'^(GET|POST|PUT|DELETE|HEAD|OPTIONS|CONNECT|PATCH|TRACE)\s',
    rb'HTTP/\d\.\d',
    rb'Host:\s',
    rb'User-Agent:\s',
    rb'Accept-Language:\s',
    rb'Accept-Encoding:\s',
    rb'Content-Type:\s',
    rb'Cookie:\s',
    rb'github\.com',
    rb'\.microsoft\.com',
    rb'\.google\.com',
]

# TLS ClientHello 特征字节（16=Handshake, 03 01~03 03=TLS 1.x）
_TLS_CLIENT_HELLO = rb'\x16\x03'
# SSH 特征
_SSH_HEADER = rb'SSH-'


class DeviceSession:
    """
    设备会话类。

    每个TCP连接对应一个DeviceSession实例。
    负责数据收发、粘包拆包、指令队列管理。

    Attributes:
        udid (str): 设备UDID
        reader (StreamReader): asyncio读取流
        writer (StreamWriter): asyncio写入流
        addr (tuple): 客户端地址(ip, port)
        connected_at (datetime): 连接建立时间
        last_activity (float): 最后活跃时间戳
        buffer (bytearray): 接收缓冲区
    """

    # 数据包识别常量
    PACKET_TERMINATOR = b'\r\n'  # STR格式结束符
    HEX_MIN_LEN = 88             # HEX最小长度 (91-3)
    HEX_MAX_LEN = 100            # HEX最大容忍长度
    STR10_LEN = 156              # STR1.0固定长度
    SL651_HEADER = b'\x7E\x7E'   # SL-651 HEX头

    def __init__(self, reader, writer, on_data_received=None, on_disconnect=None):
        """
        初始化设备会话。

        Args:
            reader (StreamReader): asyncio读取流
            writer (StreamWriter): asyncio写入流
            on_data_received (callable): 数据接收回调 (session, data) -> None
            on_disconnect (callable): 断开连接回调 (session) -> None
        """
        self.reader = reader
        self.writer = writer
        self.addr = writer.get_extra_info('peername', ('unknown', 0))
        self.connected_at = datetime.now()
        self.last_activity = time.time()

        # 设备标识（从数据包解析后赋值）
        self.udid = ''

        # 接收缓冲区
        self.buffer = bytearray()
        self._max_buffer_size = 1024 * 1024  # 1MB 缓冲区上限

        # 回调函数
        self._on_data_received = on_data_received
        self._on_disconnect = on_disconnect

        # 指令队列
        self.command_queue = asyncio.Queue(maxsize=100)
        self._pending_command = None  # 当前待应答的指令
        self._reply_event = asyncio.Event()
        self._reply_data = None
        self._command_lock = asyncio.Lock()

        # 会话状态
        self._running = True

        logger.info("设备会话已建立: %s:%d | time=%s",
                    self.addr[0], self.addr[1],
                    self.connected_at.strftime('%Y-%m-%d %H:%M:%S'))

    # ====================================================================
    # 数据发送
    # ====================================================================

    async def send_data(self, data):
        """
        向设备发送数据。

        Args:
            data (bytes): 要发送的数据

        Returns:
            bool: 发送是否成功
        """
        if not self._running:
            logger.warning("会话已关闭，无法发送数据 [%s]", self.udid)
            return False

        try:
            self.writer.write(data)
            await self.writer.drain()
            self.last_activity = time.time()
            logger.debug("数据已发送 [%s]: %s", self.udid, data.hex() if len(data) < 50 else f"{data[:50].hex()}...")
            return True
        except Exception as e:
            logger.error("发送数据失败 [%s]: %s", self.udid, e)
            return False

    # ====================================================================
    # 指令下发与应答
    # ====================================================================

    async def send_command(self, command_bytes, timeout=15, max_retries=3):
        """
        发送指令并等待应答（带超时重试）。

        协议要求:
            所有下发指令末尾强制追加 "#\\r\\n"，
            此函数假设调用方已经正确构建了完整指令（含结束符）。

        Args:
            command_bytes (bytes): 完整指令字节
            timeout (int): 每次等待应答的超时时间(秒)
            max_retries (int): 最大重试次数

        Returns:
            tuple: (success, reply_data)
        """
        async with self._command_lock:
            for attempt in range(1, max_retries + 1):
                try:
                    # 重置应答事件
                    self._reply_event.clear()
                    self._reply_data = None
                    self._pending_command = command_bytes

                    # 发送指令
                    success = await self.send_data(command_bytes)
                    if not success:
                        logger.warning("指令发送失败 [%s] (第%d次)", self.udid, attempt)
                        continue

                    logger.info("指令已下发 [%s] (第%d次): %s",
                                self.udid, attempt, command_bytes.decode('ascii', errors='replace').strip())

                    # 等待应答
                    try:
                        await asyncio.wait_for(
                            self._reply_event.wait(), timeout=timeout
                        )
                        reply = self._reply_data
                        if reply:
                            logger.info("收到指令应答 [%s]: %s",
                                        self.udid,
                                        reply.decode('ascii', errors='replace').strip() if isinstance(reply, bytes) else str(reply).strip())
                            self._pending_command = None
                            return True, reply
                        else:
                            logger.warning("指令无应答 [%s] (第%d次)", self.udid, attempt)
                    except asyncio.TimeoutError:
                        logger.warning("指令超时 [%s] (第%d次, %ds)", self.udid, attempt, timeout)

                except Exception as e:
                    logger.error("指令下发异常 [%s] (第%d次): %s", self.udid, attempt, e)

            # 所有重试失败
            self._pending_command = None
            logger.error("指令下发最终失败 [%s]: 已重试%d次", self.udid, max_retries)
            return False, None

    async def wait_for_reply(self, timeout=15):
        """
        等待设备应答（独立使用，不受_command_lock限制）。

        Args:
            timeout (int): 超时时间(秒)

        Returns:
            bytes or None: 应答数据
        """
        self._reply_event.clear()
        self._reply_data = None

        try:
            await asyncio.wait_for(self._reply_event.wait(), timeout=timeout)
            return self._reply_data
        except asyncio.TimeoutError:
            return None

    # ====================================================================
    # 数据接收与粘包拆包
    # ====================================================================

    async def receive_loop(self):
        """
        数据接收主循环。

        持续从TCP流中读取数据，处理粘包拆包，
        识别完整数据帧后通过回调提交给上层处理。

        协议识别规则:
            1. 包含 "BATV=" → STR2.0/3.0格式
            2. 固定91字节 → HEX格式
            3. 固定156字节 → STR1.0格式
            4. 以 0x7E7E 开头 → SL-651 HEX格式
            5. 以 0x01 开头 → SL-651 STR格式
            6. 以 UDID> 开头 → STR格式（通用）
        """
        logger.info("开始接收数据 [%s:%d]", self.addr[0], self.addr[1])

        last_received_at = time.monotonic()

        try:
            while self._running:
                try:
                    # 读取数据块
                    chunk = await asyncio.wait_for(
                        self.reader.read(4096), timeout=READ_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    # 连续无数据达到阈值后主动断开
                    idle_seconds = time.monotonic() - last_received_at
                    if idle_seconds >= IDLE_TIMEOUT_SECONDS:
                        logger.warning("设备空闲超时 [%s]: 1小时无数据，主动断开", self.udid or f"{self.addr[0]}:{self.addr[1]}")
                        break
                    continue

                if not chunk:
                    # 连接关闭
                    logger.info("设备断开连接 [%s:%d]", self.addr[0], self.addr[1])
                    break

                self.last_activity = time.time()
                last_received_at = time.monotonic()

                # 快速过滤非设备流量（HTTP爬虫等）— 丢弃后不追加到缓冲区
                if self._looks_like_non_device(chunk):
                    logger.debug("检测到非设备流量 [%s:%d], 丢弃 %d 字节",
                                self.addr[0], self.addr[1], len(chunk))
                    continue

                # 追加到缓冲区
                self.buffer.extend(chunk)

                # 缓冲区大小保护
                if len(self.buffer) > self._max_buffer_size:
                    logger.warning("缓冲区溢出 [%s:%d]: 大小=%d, 清空",
                                  self.addr[0], self.addr[1], len(self.buffer))
                    self.buffer.clear()
                    continue

                # ---- 粘包拆包: 尝试从缓冲区提取完整数据包 ----
                extracted_packets = self._extract_packets()

                for packet in extracted_packets:
                    logger.debug("提取完整数据包 [%s:%d]: 长度=%d",
                                self.addr[0], self.addr[1], len(packet))

                    # 更新UDID（从数据包中提取）
                    self._try_extract_udid(packet)

                    # 检查是否为指令应答
                    if self._is_command_reply(packet):
                        self._reply_data = packet
                        self._reply_event.set()
                        logger.debug("数据帧识别为指令应答 [%s]", self.udid)
                        continue

                    # 通过回调传递给上层处理
                    if self._on_data_received:
                        try:
                            await self._on_data_received(self, packet)
                        except Exception as cb_err:
                            logger.error("数据回调异常 [%s]: %s", self.udid, cb_err, exc_info=True)

        except asyncio.CancelledError:
            logger.info("接收循环被取消 [%s]", self.udid)
        except ConnectionResetError:
            logger.warning("连接被重置 [%s:%d]", self.addr[0], self.addr[1])
        except ConnectionAbortedError:
            logger.warning("连接被中断 [%s:%d]", self.addr[0], self.addr[1])
        except OSError as e:
            logger.error("连接OS错误 [%s:%d]: %s", self.addr[0], self.addr[1], e)
        except Exception as e:
            logger.error("接收循环异常 [%s:%d]: %s", self.addr[0], self.addr[1], e, exc_info=True)
        finally:
            await self.close()

    def _extract_packets(self):
        """
        从缓冲区提取完整数据包（粘包拆包）。

        协议识别与拆包规则:
            - STR格式: 以 \\r\\n 为帧结束符（但需排除 @YYYY-MM-DD 补发标识的影响）
            - HEX格式: 固定91字节（允许 ±2 字节容差）
            - STR1.0格式: 固定156字节（允许 ±5 字节容差）
            - SL-651: 需要根据帧头帧尾识别

        Returns:
            list[bytes]: 完整的数据包列表
        """
        packets = []

        while len(self.buffer) > 0:
            extracted = False

            # ---- 规则1: SL-651 HEX（0x7E7E开头）----
            if len(self.buffer) >= 2 and self.buffer[0:2] == self.SL651_HEADER:
                # SL-651帧: 头(2B) + 长度(2B) + 数据(N B) + CRC(2B) + 尾(1B 0x7E)
                # 需要找到结束的0x7E
                end_idx = self.buffer.find(b'\x7E', 6)
                if end_idx > 0 and end_idx + 1 <= len(self.buffer):
                    pkt = bytes(self.buffer[:end_idx + 1])
                    self.buffer = self.buffer[end_idx + 1:]
                    packets.append(pkt)
                    extracted = True
                    continue

            # ---- 规则2: SL-651 STR（0x01开头）----
            if len(self.buffer) >= 1 and self.buffer[0] == 0x01:
                # 查找ETX(0x03)或ETB(0x17)
                end_idx = -1
                for i in range(1, min(len(self.buffer), 512)):
                    if self.buffer[i] in (0x03, 0x17):
                        end_idx = i
                        break
                if end_idx > 0:
                    pkt = bytes(self.buffer[:end_idx + 1])
                    self.buffer = self.buffer[end_idx + 1:]
                    packets.append(pkt)
                    extracted = True
                    continue

            # ---- 规则3: HEX格式（~91字节）----
            # 检查缓冲区中是否存在一个可能的HEX帧
            # HEX帧特征: 第2字节是功能码(0x01-0x06), 第3-4字节是长度
            if len(self.buffer) >= 10:
                func_code = self.buffer[1] if len(self.buffer) > 1 else 0
                if 0x01 <= func_code <= 0x06 or func_code == 0x10 or func_code == 0x03:
                    # 尝试按91字节提取
                    if len(self.buffer) >= self.HEX_MIN_LEN:
                        # 使用滑动窗口法: 查找可能的CRC16位置
                        # 简化: 直接取91字节
                        pkt_len = min(91, len(self.buffer))
                        pkt = bytes(self.buffer[:pkt_len])
                        self.buffer = self.buffer[pkt_len:]
                        packets.append(pkt)
                        extracted = True
                        continue

            # ---- 规则4: STR格式（以 \r\n 结束）----
            rn_idx = self.buffer.find(b'\r\n')
            if rn_idx >= 0:
                # 检查 \r\n 之后是否为 @时间戳（补发数据标识）
                after_rn = self.buffer[rn_idx + 2:rn_idx + 2 + 22]  # @YYYY-MM-DD HH:MM:SS
                if after_rn.startswith(b'@') and len(after_rn) >= 20:
                    # 补发数据: 找到时间戳后的下一个 \r\n 或取22字节
                    second_rn = self.buffer.find(b'\r\n', rn_idx + 2)
                    if second_rn >= 0:
                        pkt = bytes(self.buffer[:second_rn + 2])
                        self.buffer = self.buffer[second_rn + 2:]
                    else:
                        # 可能不完整，等待更多数据
                        break
                else:
                    pkt = bytes(self.buffer[:rn_idx + 2])
                    self.buffer = self.buffer[rn_idx + 2:]

                packets.append(pkt)
                extracted = True
                continue

            # ---- 规则5: 超过STR1.0长度阈值(156+N)的可能是STR1.0包 ----
            # 这种格式可能没有\r\n结束符
            if len(self.buffer) >= self.STR10_LEN:
                pkt = bytes(self.buffer[:self.STR10_LEN])
                self.buffer = self.buffer[self.STR10_LEN:]
                packets.append(pkt)
                extracted = True
                continue

            # 没有提取到完整包，等待更多数据
            if not extracted:
                break

        return packets

    def _try_extract_udid(self, data):
        """
        尝试从数据包中提取设备UDID。

        格式: UDID + ">" + 数据帧
        如: "VS001>CH1=1234.5,..."

        Args:
            data (bytes): 数据包
        """
        if self.udid:
            return

        try:
            # 尝试按ASCII解码
            text = data.decode('ascii', errors='ignore')
            if '>' in text:
                udid_part = text.split('>')[0].strip()
                # UDID通常是字母数字组合，不含特殊字符
                if udid_part and len(udid_part) <= 32:
                    # 去除末尾可能附带的冒号、分号等干扰字符
                    udid_part = udid_part.rstrip(':;, \t')
                    # 检查是否是合理的UDID（不含空格、控制字符等）
                    import re
                    if re.match(r'^[A-Za-z0-9_\-]+$', udid_part):
                        self.udid = udid_part
                        logger.info("识别设备UDID: %s (来自数据包)", self.udid)
        except Exception:
            pass

    @staticmethod
    def _looks_like_non_device(data):
        """快速识别非设备流量（HTTP爬虫、TLS扫描等），返回True表示应丢弃"""
        if len(data) < 4:
            return False

        # 检查TLS/SSL握手（HTTPS扫描器连接TCP端口）
        if data[:2] == _TLS_CLIENT_HELLO and 0x01 <= data[2] <= 0x03:
            return True

        # 检查SSH扫描
        if data[:4] == _SSH_HEADER:
            return True

        # 检查HTTP特征
        for pattern in _NON_DEVICE_PATTERNS:
            if re.search(pattern, data[:512]):
                return True
        return False

    def _is_command_reply(self, data):
        """
        判断数据包是否为指令应答（而非主动上报数据）。

        指令应答特征:
            - 以 $, @, # 等特殊字符开头
            - 不包含 CH1=, CH2=, BATV= 等数据上报特征
            - 数据长度较短（通常<100字节）

        Args:
            data (bytes): 数据包

        Returns:
            bool: 是否为指令应答
        """
        if not self._pending_command:
            return False

        try:
            text = data.decode('ascii', errors='ignore').strip()

            # 排除数据上报（包含通道数据特征）
            data_keywords = ['CH1=', 'CH2=', 'BATV=', 'TEMP=', 'VOLT=']
            for kw in data_keywords:
                if kw in text.upper():
                    return False

            # 如果当前有待应答指令，且数据不像是上报数据，认为是应答
            if len(data) < 200:
                return True

        except Exception:
            pass

        return False

    # ====================================================================
    # 会话关闭
    # ====================================================================

    async def close(self):
        """关闭设备会话。"""
        if not self._running:
            return

        self._running = False
        logger.info("关闭设备会话 [%s] | %s:%d", self.udid, self.addr[0], self.addr[1])

        # 触发断开回调
        if self._on_disconnect:
            try:
                await self._on_disconnect(self)
            except Exception as e:
                logger.error("断开回调异常 [%s]: %s", self.udid, e)

        # 关闭连接
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass

    @property
    def is_running(self):
        """会话是否正在运行"""
        return self._running

    def set_udid(self, udid):
        """
        手动设置设备UDID。

        Args:
            udid (str): 设备UDID
        """
        self.udid = udid
        logger.info("设备UDID已设置: %s", udid)

    def __repr__(self):
        return (f"<DeviceSession(udid={self.udid}, addr={self.addr[0]}:{self.addr[1]}, "
                f"running={self._running})>")
