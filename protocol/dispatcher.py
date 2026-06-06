# -*- coding: utf-8 -*-
"""
protocol/dispatcher.py - 协议自动识别与分发
=============================================
功能:
    1. 根据数据帧特征自动判断协议类型
    2. 分发到对应的解析函数
    3. 处理补发数据标识（@YYYY-MM-DD HH:MM:SS）
    4. 返回统一的结构化结果

识别规则（按优先级）:
    1. 以 0x7E 0x7E 开头              → SL-651 HEX
    2. 以 0x01 开头（非字符串）        → SL-651 STR
    3. 包含 "BATV="                   → STR3.0
    4. 固定91字节（+/- 2字节容差）     → HEX
    5. 包含 ">" 分隔符                 → STR2.0
    6. 固定156字节                     → STR1.0
    7. 其他                           → UNKNOWN

作者: VS DataCollector Project
版本: 1.0.0
"""

import re
import logging
from datetime import datetime

from .parser import parse_str20, parse_str30, parse_hex, parse_str10, parse_sl651, parse_custom_hex

logger = logging.getLogger(__name__)


# ============================================================================
# 补发数据标识处理
# ============================================================================

def _extract_retransmit_info(raw_str):
    """
    从原始数据中提取补发数据的时间标识。

    补发数据格式: 数据帧末尾带 "@YYYY-MM-DD HH:MM:SS"

    Args:
        raw_str (str): 原始数据字符串

    Returns:
        tuple: (清理后的数据, is_retransmit, retransmit_datetime)
    """
    is_retransmit = False
    retransmit_time = None
    cleaned_str = raw_str

    # 匹配 @YYYY-MM-DD HH:MM:SS 格式
    pattern = r'@(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*$'
    match = re.search(pattern, raw_str)
    if match:
        try:
            retransmit_time = datetime.strptime(
                match.group(1), '%Y-%m-%d %H:%M:%S')
            is_retransmit = True
            # 移除时间标识
            cleaned_str = raw_str[:match.start()].strip()
            logger.debug("检测到补发数据: 原始采集时间=%s", match.group(1))
        except ValueError:
            pass

    return cleaned_str, is_retransmit, retransmit_time


def _strip_udid_prefix(raw_str):
    """
    去除设备UDID前缀 "UDID>"。

    设备TCP上报格式: UDID + ">" + 数据帧
    SL-651协议无UDID前缀。

    Args:
        raw_str (str): 原始数据字符串

    Returns:
        tuple: (UDID, 去除前缀后的数据)
    """
    udid = ''
    data_part = raw_str

    # 形如 "VS001>data..." 或 "VS410_HW401>data..."
    # UDID通常是字母数字组合，不含空格
    match = re.match(r'^([A-Za-z0-9_-]+)>(.*)', raw_str)
    if match:
        udid = match.group(1)
        data_part = match.group(2)
        logger.debug("提取UDID前缀: %s", udid)

    return udid, data_part


# ============================================================================
# 协议类型识别
# ============================================================================

def identify_protocol(raw_data):
    """
    根据数据帧特征自动判断协议类型。

    按优先级依次匹配识别规则，返回第一个匹配的协议类型。

    识别规则:
        1. 以 0x7E 0x7E 开头               → SL651_HEX
        2. 包含 "BATV="                     → STR3.0
        3. 以 0x01 开头（非ASCII可打印字符）→ SL651_STR
        4. 固定91字节（+/-3字节容差）       → HEX
        5. 包含 ">" 分隔符，且数据部分为纯HEX → CUSTOM_HEX
        6. 包含 ">" 分隔符                  → STR2.0
        7. 固定156字节（+/-5字节容差）      → STR1.0
        8. 其他                            → UNKNOWN

    Args:
        raw_data (bytes or str): 原始数据帧

    Returns:
        str: 协议类型标识
    """
    # 统一获取两种表示
    if isinstance(raw_data, bytes):
        raw_bytes = raw_data
        try:
            raw_str = raw_data.decode('ascii', errors='ignore')
        except Exception:
            raw_str = ''
    else:
        raw_str = str(raw_data)
        try:
            raw_bytes = raw_data.encode('ascii', errors='ignore') if isinstance(raw_data, str) else raw_data
        except Exception:
            raw_bytes = b''

    data_len = len(raw_bytes)

    # 规则1: 0x7E 0x7E → SL-651 HEX
    if data_len >= 2 and raw_bytes[0] == 0x7E and raw_bytes[1] == 0x7E:
        logger.debug("协议识别: SL-651 HEX (0x7E7E开头)")
        return 'SL651_HEX'

    # 规则2: BATV= → STR3.0
    if 'BATV=' in raw_str.upper():
        logger.debug("协议识别: STR3.0 (含BATV=)")
        return 'STR3.0'

    # 规则3: 0x01 开头 (非可打印ASCII) → SL-651 STR
    if data_len >= 1 and raw_bytes[0] == 0x01:
        # 进一步检查第二个字符是否非可打印
        if data_len >= 2 and raw_bytes[1] < 0x20:
            logger.debug("协议识别: SL-651 STR (0x01开头)")
            return 'SL651_STR'

    # 规则4: 91字节 ± 3 → HEX
    if 88 <= data_len <= 94:
        logger.debug("协议识别: HEX (长度=%d, 接近91字节)", data_len)
        return 'HEX'

    # 规则5: 包含 ">" 且数据部分为纯HEX → CUSTOM_HEX
    if '>' in raw_str:
        parts = raw_str.split('>', 1)
        if len(parts) == 2:
            data_after_gt = parts[1].rstrip('\r\n')
            # 去除补发标识
            data_after_gt = re.sub(r'@\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$', '', data_after_gt).strip()
            # 判断是否为纯HEX字符串（仅含0-9, A-F, a-f）
            hex_only = ''.join(c for c in data_after_gt.upper() if c in '0123456789ABCDEF')
            if hex_only and len(hex_only) >= 4:
                # 纯HEX数据，不含逗号/等号分隔符 → CUSTOM_HEX
                total_chars = len(data_after_gt)
                hex_chars = sum(1 for c in data_after_gt.upper() if c in '0123456789ABCDEF')
                hex_ratio = hex_chars / max(total_chars, 1)
                if hex_ratio > 0.85 and ',' not in data_after_gt and '=' not in data_after_gt:
                    logger.debug("协议识别: CUSTOM_HEX (UDID>+纯HEX, 长度=%d字符, HEX占比=%.0f%%)",
                                total_chars, hex_ratio * 100)
                    return 'CUSTOM_HEX'

    # 规则6: 包含 ">" 分隔符 → STR2.0
    if '>' in raw_str:
        logger.debug("协议识别: STR2.0/3.0 (含>分隔符)")
        return 'STR2.0'

    # 规则6: 156字节 ± 5 → STR1.0
    if 151 <= data_len <= 161:
        logger.debug("协议识别: STR1.0 (长度=%d, 接近156字节)", data_len)
        return 'STR1.0'

    # 规则7: 无法识别
    logger.warning("协议识别失败: 长度=%d, 前20字节=%s",
                  data_len, raw_bytes[:20].hex() if data_len >= 20 else raw_bytes.hex())
    return 'UNKNOWN'


# ============================================================================
# 统一解析入口
# ============================================================================

def identify_and_parse(raw_data):
    """
    协议自动识别并解析 - 统一入口函数。

    处理流程:
        1. 检测补发数据标识（@时间戳）
        2. 提取UDID前缀（"UDID>"）
        3. 自动识别协议类型
        4. 分发到对应解析函数
        5. 补全UDID字段
        6. 返回统一结构化结果

    Args:
        raw_data (bytes or str): 从TCP接收到的原始数据帧

    Returns:
        dict: 统一解析结果，包含以下字段:
            - protocol: 协议类型
            - udid: 设备UDID
            - channels: 通道数据列表
            - is_retransmit: 是否补发数据
            - retransmit_time: 补发原始时间
            - timestamp: 采集时间戳
            - device_temp: 设备温度
            - voltage: 电压
            - signal: 信号强度
            - raw_packet: 原始数据包信息
            - status: 整体解析状态
            - error: 错误信息
    """
    result = {
        'protocol': 'UNKNOWN',
        'udid': '',
        'channels': [],
        'is_retransmit': False,
        'retransmit_time': None,
        'timestamp': None,
        'device_temp': None,
        'voltage': None,
        'battery_voltage': None,
        'signal': None,
        'device_addr': None,         # CUSTOM_HEX: 设备地址
        'record_number': None,       # CUSTOM_HEX: 数据记录号
        'raw_packet': {
            'raw_hex': '',
            'raw_str': '',
        },
        'status': 'PENDING',
        'error': None,
    }

    try:
        # ---- 预处理: 统一数据格式 ----
        if isinstance(raw_data, bytes):
            result['raw_packet']['raw_hex'] = raw_data.hex().upper()
            raw_str = raw_data.decode('ascii', errors='ignore')
            result['raw_packet']['raw_str'] = raw_str
        else:
            result['raw_packet']['raw_str'] = str(raw_data)
            try:
                result['raw_packet']['raw_hex'] = raw_data.encode('ascii').hex().upper()
            except Exception:
                pass
            raw_str = str(raw_data)
            raw_bytes = raw_data.encode('ascii', errors='ignore') if isinstance(raw_data, str) else raw_data

        # ---- 步骤1: 检测补发数据 ----
        cleaned_str, is_retransmit, retransmit_time = _extract_retransmit_info(raw_str)
        result['is_retransmit'] = is_retransmit
        result['retransmit_time'] = retransmit_time

        # ---- 步骤2: 提取UDID前缀 ----
        prefix_udid, data_part = _strip_udid_prefix(cleaned_str)

        # 确保raw_bytes是最新数据
        if isinstance(raw_data, bytes):
            raw_bytes_for_parse = raw_data  # 补发标识在bytes中不改变
        else:
            raw_bytes_for_parse = cleaned_str.encode('ascii', errors='ignore')

        # ---- 步骤3: 识别协议类型 ----
        protocol = identify_protocol(raw_bytes_for_parse)

        # 对于STR协议和CUSTOM_HEX，如果已经去除了UDID前缀，用data_part解析
        # 但要保留UDID前缀中的UDID
        if protocol.startswith('STR') or protocol in ('CUSTOM_HEX', 'UNKNOWN'):
            # 字符串协议，优先用字符串数据
            parse_input = data_part if data_part else cleaned_str
        else:
            parse_input = raw_bytes_for_parse

        # ---- 步骤4: 分发解析 ----
        if protocol == 'STR2.0':
            parse_result = parse_str20(parse_input)
        elif protocol == 'STR3.0':
            parse_result = parse_str30(parse_input)
        elif protocol == 'HEX':
            parse_result = parse_hex(parse_input)
        elif protocol == 'STR1.0':
            parse_result = parse_str10(parse_input)
        elif protocol == 'CUSTOM_HEX':
            parse_result = parse_custom_hex(parse_input)
        elif protocol in ('SL651_HEX', 'SL651_STR'):
            parse_result = parse_sl651(parse_input)
        else:
            # 无法识别的协议，尝试STR2.0解析
            logger.warning("未知协议类型，尝试以STR2.0格式解析")
            parse_result = parse_str20(parse_input)
            protocol = parse_result.get('protocol', 'UNKNOWN')

        # ---- 步骤5: 合并结果 ----
        result['protocol'] = protocol

        # UDID优先级: 解析结果 > 前缀提取 > 默认值
        if parse_result.get('udid') and parse_result['udid'] not in ('', 'UNKNOWN'):
            result['udid'] = parse_result['udid']
        elif prefix_udid:
            result['udid'] = prefix_udid
        elif parse_result.get('udid'):
            result['udid'] = parse_result['udid']
        else:
            result['udid'] = 'UNKNOWN_DEVICE'

        # 合并其他字段
        for field in ['channels', 'device_temp', 'voltage', 'signal',
                       'timestamp', 'battery_voltage', 'solar_voltage',
                       'work_mode', 'checksum_valid',
                       'device_addr', 'record_number']:
            if field in parse_result and parse_result[field] is not None:
                result[field] = parse_result[field]

        # ---- 步骤6: 最终状态 ----
        if parse_result.get('status') == 'SUCCESS':
            result['status'] = 'SUCCESS'
            logger.info("协议解析成功: UDID=%s | Proto=%s | 通道数=%d",
                       result['udid'], protocol, len(result['channels']))
            # 输出各通道原始数据
            for ch in result['channels']:
                freq = ch.get('frequency')
                temp = ch.get('temp') or ch.get('temperature')
                logger.debug("  CH%d: f=%sHz T=%s℃", ch.get('channel'),
                            f"{freq:.1f}" if freq else '-',
                            f"{temp:.1f}" if temp else '-')
        else:
            result['status'] = 'FAILED'
            result['error'] = parse_result.get('error', '解析失败')
            logger.error("协议解析失败: UDID=%s | Proto=%s | %s",
                        result['udid'], protocol, result['error'])

    except Exception as e:
        result['status'] = 'FAILED'
        result['error'] = f"协议识别解析异常: {str(e)}"
        logger.error("协议识别解析异常: %s", e, exc_info=True)

    return result


# ============================================================================
# 模块自检
# ============================================================================

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                       format='%(asctime)s | %(levelname)s | %(message)s')

    print("=" * 60)
    print("protocol/dispatcher.py 模块自检")
    print("=" * 60)

    # 测试用例
    test_cases = [
        ("VS001>CH1=1234.5,CH2=1245.6,TEMP=25.3,VOLT=12.5\r\n",
         "STR2.0 标准格式"),
        ("VS410>CH1=1234.5,T1=25.3,CH2=1245.6,T2=25.1,BATV=12.5,TEMP=26.1\r\n",
         "STR3.0 格式(含BATV=)"),
        ("VS001>CH1=1234.5,CH2=1245.6,TEMP=25.3@2024-01-15 08:30:00\r\n",
         "补发数据(含@时间标识)"),
    ]

    for data, description in test_cases:
        print(f"\n[测试] {description}:")
        print(f"  输入: {data[:60]}...")
        result = identify_and_parse(data)
        print(f"  协议: {result['protocol']}")
        print(f"  UDID: {result['udid']}")
        print(f"  通道数: {len(result['channels'])}")
        print(f"  补发: {'是' if result['is_retransmit'] else '否'}")
        if result['is_retransmit']:
            print(f"  补发时间: {result['retransmit_time']}")
        print(f"  状态: {result['status']}")

    # HEX协议识别测试
    print("\n[测试] HEX协议识别（91字节模拟）:")
    # 构造一个91字节的模拟HEX帧
    hex_frame = bytes([0xA5]) + b'\x03' + b'\x00\x54' + b'VS410_HW401_____' + \
                bytes([0x24, 0x01, 0x15, 0x10, 0x30, 0x00]) + \
                bytes([0x01]) + bytes([0x04, 0xE2]) + bytes([0x1C]) + \
                bytes([0x0A, 0x46]) + b'\x00' * 54
    # 填充到91字节
    hex_frame = hex_frame[:91]
    if len(hex_frame) < 91:
        hex_frame += b'\xFF' * (91 - len(hex_frame))

    result = identify_and_parse(hex_frame)
    print(f"  协议: {result['protocol']}")
    print(f"  UDID: {result['udid']}")
    print(f"  数据长度: {len(hex_frame)}字节")
    print(f"  状态: {result['status']}")

    print("\n自检完成!")
