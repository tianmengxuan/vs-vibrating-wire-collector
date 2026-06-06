# -*- coding: utf-8 -*-
"""
protocol/parser.py - 分协议数据包解析
=======================================
支持协议类型:
    1. STR2.0 格式 - 字符串格式V2.0
    2. STR3.0 格式 - 字符串格式V3.0（含BATV=标识）
    3. HEX 格式   - 16进制固定91字节格式
    4. STR1.0 格式 - 字符串格式V1.0（固定156字节）
    5. SL-651 HEX格式 - 水利部SL651协议（0x7E7E开头）
    6. SL-651 STR格式 - 水利部SL651协议字符串格式（0x01开头）
    7. CUSTOM_HEX 格式 - 设备专属HEX协议（UDID>+纯HEX，紧凑型）

输入: 原始数据帧（bytes 或 str）
输出: 结构化字典，包含所有协议定义字段

作者: VS DataCollector Project
版本: 1.0.0
"""

import re
import struct
import logging
from datetime import datetime

from .checksum import crc16_modbus, checksum_xor, checksum_sum8

logger = logging.getLogger(__name__)


# ============================================================================
# 辅助函数
# ============================================================================

def _hex_to_bytes(hex_str):
    """HEX字符串转bytes，支持空格分隔"""
    hex_str = hex_str.replace(' ', '')
    if len(hex_str) % 2 != 0:
        hex_str = '0' + hex_str
    return bytes.fromhex(hex_str)


def _bytes_to_hex(data):
    """bytes转HEX字符串"""
    if isinstance(data, str):
        return data
    return data.hex().upper() if isinstance(data, bytes) else str(data)


def _parse_ascii_number(ascii_str):
    """解析ASCII数字字符串为float"""
    try:
        return float(ascii_str.strip())
    except (ValueError, TypeError):
        return None


def _parse_bcd_bytes(data, offset, length):
    """解析BCD编码字节为整数"""
    try:
        result = 0
        for i in range(length):
            byte = data[offset + i]
            result = result * 100 + ((byte >> 4) & 0x0F) * 10 + (byte & 0x0F)
        return result
    except (IndexError, TypeError):
        return None


# ============================================================================
# STR2.0 格式解析
# ============================================================================

def parse_str20(raw_data):
    """
    解析 STR2.0 格式数据包。

    STR2.0 格式特征:
        以设备UDID开头，后跟">"分隔符，数据字段以"="分隔键值对。
        示例: "VS001>CH1=1234.5,CH2=1245.6,...TEMP=25.3,VOLT=12.5\r\n"

    Args:
        raw_data (str or bytes): 原始数据帧

    Returns:
        dict: 解析结果，包含:
            - protocol: 'STR2.0'
            - udid: 设备UDID
            - channels: 通道数据列表 [{'channel': int, 'frequency': float, 'temp': float}, ...]
            - device_temp: 设备温度
            - voltage: 电池/电源电压
            - signal: 信号强度 (可选)
            - timestamp: 采集时间戳 (可选)
            - status: 'SUCCESS' or 'FAILED'
            - error: 错误信息
    """
    result = {
        'protocol': 'STR2.0',
        'udid': '',
        'channels': [],
        'device_temp': None,
        'voltage': None,
        'signal': None,
        'timestamp': None,
        'status': 'SUCCESS',
        'error': None,
    }

    try:
        # 统一转为字符串
        if isinstance(raw_data, bytes):
            raw_str = raw_data.decode('ascii', errors='ignore').strip()
        else:
            raw_str = str(raw_data).strip()

        # 移除尾部的 \r\n
        raw_str = raw_str.rstrip('\r\n')

        # 分割 UDID 和数据部分
        if '>' in raw_str:
            udid, data_part = raw_str.split('>', 1)
            result['udid'] = udid.strip()
        else:
            # 无UDID前缀，尝试从数据中提取
            data_part = raw_str
            result['udid'] = 'UNKNOWN'

        # 解析键值对
        # 分割数据字段（以逗号分隔的键值对）
        pairs = data_part.replace(';', ',').split(',')

        for pair in pairs:
            pair = pair.strip()
            if '=' not in pair:
                continue

            key, value = pair.split('=', 1)
            key = key.strip().upper()
            value = value.strip()

            # CH1, CH2, ... 通道频率数据
            ch_match = re.match(r'^CH(\d+)$', key)
            if ch_match:
                channel = int(ch_match.group(1))
                freq = _parse_ascii_number(value)
                result['channels'].append({
                    'channel': channel,
                    'frequency': freq,
                    'temp': None,  # STR2.0 可能不含温度
                })
                continue

            # T1, T2, ... 通道温度数据
            temp_match = re.match(r'^T(\d+)$', key)
            if temp_match:
                channel = int(temp_match.group(1))
                temp_val = _parse_ascii_number(value)
                # 找到对应通道
                for ch in result['channels']:
                    if ch['channel'] == channel:
                        ch['temp'] = temp_val
                        break
                continue

            # 设备级参数
            if key in ('TEMP', 'T', 'DEVTEMP'):
                result['device_temp'] = _parse_ascii_number(value)
            elif key in ('VOLT', 'V', 'BATV', 'POWERV'):
                result['voltage'] = _parse_ascii_number(value)
            elif key in ('SIGNAL', 'RSSI', 'CSQ'):
                result['signal'] = _parse_ascii_number(value)
            elif key in ('TIME', 'DATETIME', 'TS'):
                try:
                    result['timestamp'] = datetime.strptime(
                        value, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    result['timestamp'] = None

        logger.debug("STR2.0解析: UDID=%s | 通道数=%d",
                    result['udid'], len(result['channels']))

    except Exception as e:
        result['status'] = 'FAILED'
        result['error'] = f"STR2.0解析异常: {str(e)}"
        logger.error("STR2.0解析失败: %s", e)

    return result


# ============================================================================
# STR3.0 格式解析
# ============================================================================

def parse_str30(raw_data):
    """
    解析 STR3.0 格式数据包。

    STR3.0 格式特征:
        包含 "BATV=" 标识，数据字段更丰富。
        示例: "VS001>CH1=1234.5,CH2=1245.6,...,BATV=12.5,TEMP=25.3,...\r\n"

    Args:
        raw_data (str or bytes): 原始数据帧

    Returns:
        dict: 解析结果（字段同STR2.0，新增battery_voltage, solar_voltage等）
    """
    result = {
        'protocol': 'STR3.0',
        'udid': '',
        'channels': [],
        'device_temp': None,
        'voltage': None,
        'battery_voltage': None,
        'solar_voltage': None,
        'signal': None,
        'work_mode': None,
        'timestamp': None,
        'status': 'SUCCESS',
        'error': None,
    }

    try:
        if isinstance(raw_data, bytes):
            raw_str = raw_data.decode('ascii', errors='ignore').strip()
        else:
            raw_str = str(raw_data).strip()

        raw_str = raw_str.rstrip('\r\n')

        # 分割UDID
        if '>' in raw_str:
            udid, data_part = raw_str.split('>', 1)
            result['udid'] = udid.strip()
        else:
            data_part = raw_str
            result['udid'] = 'UNKNOWN'

        pairs = data_part.replace(';', ',').split(',')

        for pair in pairs:
            pair = pair.strip()
            if '=' not in pair:
                continue

            key, value = pair.split('=', 1)
            key = key.strip().upper()
            value = value.strip()

            # CH1, CH2, ... 通道频率
            ch_match = re.match(r'^CH(\d+)$', key)
            if ch_match:
                channel = int(ch_match.group(1))
                freq = _parse_ascii_number(value)
                result['channels'].append({
                    'channel': channel,
                    'frequency': freq,
                    'temp': None,
                    'freq_module': None,
                    'thermistor_r': None,
                })
                continue

            # T1, T2, ... 温度
            temp_match = re.match(r'^T(\d+)$', key)
            if temp_match:
                channel = int(temp_match.group(1))
                temp_val = _parse_ascii_number(value)
                for ch in result['channels']:
                    if ch['channel'] == channel:
                        ch['temp'] = temp_val
                        break
                continue

            # M1, M2, ... 频模数
            mod_match = re.match(r'^M(\d+)$', key)
            if mod_match:
                channel = int(mod_match.group(1))
                mod_val = _parse_ascii_number(value)
                for ch in result['channels']:
                    if ch['channel'] == channel:
                        ch['freq_module'] = mod_val
                        break
                continue

            # R1, R2, ... 热敏电阻
            r_match = re.match(r'^R(\d+)$', key)
            if r_match:
                channel = int(r_match.group(1))
                r_val = _parse_ascii_number(value)
                for ch in result['channels']:
                    if ch['channel'] == channel:
                        ch['thermistor_r'] = r_val
                        break
                continue

            # 设备参数
            if key in ('BATV', 'POWERV', 'VBAT'):
                result['battery_voltage'] = _parse_ascii_number(value)
            elif key in ('SOLARV', 'VSOLAR', 'VSOL'):
                result['solar_voltage'] = _parse_ascii_number(value)
            elif key in ('TEMP', 'DEVTEMP', 'TEMP1'):
                result['device_temp'] = _parse_ascii_number(value)
            elif key in ('SIGNAL', 'RSSI', 'CSQ', 'SIG'):
                result['signal'] = _parse_ascii_number(value)
            elif key in ('MODE', 'WORKMODE'):
                result['work_mode'] = value
            elif key in ('TIME', 'DATETIME', 'TS'):
                try:
                    result['timestamp'] = datetime.strptime(
                        value, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    try:
                        result['timestamp'] = datetime.strptime(
                            value, '%Y%m%d%H%M%S')
                    except ValueError:
                        pass

        # 合并voltage字段
        if result['battery_voltage'] is not None:
            result['voltage'] = result['battery_voltage']

        logger.debug("STR3.0解析: UDID=%s | 通道数=%d | BATV=%s",
                    result['udid'], len(result['channels']),
                    result['battery_voltage'])

    except Exception as e:
        result['status'] = 'FAILED'
        result['error'] = f"STR3.0解析异常: {str(e)}"
        logger.error("STR3.0解析失败: %s", e)

    return result


# ============================================================================
# HEX 格式解析（固定91字节）
# ============================================================================

def parse_hex(raw_data):
    """
    解析 HEX 格式数据包（固定91字节）。

    HEX 格式帧结构（91字节）:
        [地址(1B)] [功能码(1B)] [数据长度(2B)] [数据区(84B)] [CRC16(2B)] [结束符(1B)]
        其中数据区包含: 设备ID(16B) + 时间(6B BCD) + 工作状态(1B) +
                       电池电压(2B) + 信号(1B) + 1-16通道数据(每通道3B) + ...
        注：具体字节偏移需根据协议文档确认，以下为典型实现。

    Args:
        raw_data (bytes): 原始HEX数据帧

    Returns:
        dict: 解析结果
    """
    result = {
        'protocol': 'HEX',
        'udid': '',
        'channels': [],
        'device_temp': None,
        'voltage': None,
        'battery_voltage': None,
        'signal': None,
        'work_mode': None,
        'timestamp': None,
        'status': 'SUCCESS',
        'error': None,
    }

    try:
        # 确保是bytes
        if isinstance(raw_data, str):
            raw_bytes = _hex_to_bytes(raw_data)
        else:
            raw_bytes = raw_data

        data_len = len(raw_bytes)

        # 验证数据长度（HEX格式固定91字节，但实际设备可能有差异）
        # 不强制要求正好91字节，允许一些容差
        if data_len < 20:
            result['status'] = 'FAILED'
            result['error'] = f"HEX数据长度不足: {data_len}字节（至少20字节）"
            return result

        # 提取CRC16校验（最后2字节为CRC，倒数第3字节可能为结束符）
        # 假设格式: [数据(88B)] [CRC16(2B)] [结束符0x0D(1B)] = 91字节
        # 根据协议文档实际调整
        if data_len >= 91:
            data_part = raw_bytes[:88]
            crc_bytes = raw_bytes[88:90]
            end_byte = raw_bytes[90]
        else:
            data_part = raw_bytes[:-2]
            crc_bytes = raw_bytes[-2:]

        # 验证CRC16
        expected_crc = (crc_bytes[1] << 8) | crc_bytes[0]  # 小端序
        actual_crc = crc16_modbus(data_part)

        result['checksum_valid'] = (actual_crc == expected_crc)

        if not result['checksum_valid']:
            logger.warning("HEX CRC校验失败: 期望=0x%04X, 实际=0x%04X",
                         expected_crc, actual_crc)
            result['status'] = 'FAILED'
            result['error'] = f"CRC校验失败: 期望=0x{expected_crc:04X}, 实际=0x{actual_crc:04X}"
            result['protocol'] = 'HEX'
            return result

        # ---- 解析数据区 ----
        # 典型结构（参考稳控科技协议文档）:
        # 偏移0: 地址
        # 偏移1: 功能码
        # 偏移2-3: 数据长度
        # 偏移4-19: 设备ID(16B ASCII)
        # 偏移20-25: 时间(6B BCD: 年月日时分秒)
        # 偏移26: 工作状态
        # 偏移27-28: 电池电压(x100)
        # 偏移29: 信号强度
        # 偏移30-31: 设备温度(x100, 有符号)
        # 偏移32+: 通道数据

        # 提取设备ID (UDID)
        try:
            udid_bytes = data_part[4:20].rstrip(b'\x00').rstrip(b'\xFF')
            result['udid'] = udid_bytes.decode('ascii', errors='ignore').strip()
        except Exception:
            result['udid'] = f"HEX_{data_part[0]:02X}"

        # 提取时间（BCD编码）
        try:
            if len(data_part) >= 26:
                year = 2000 + _parse_bcd_bytes(data_part, 20, 1)
                month = _parse_bcd_bytes(data_part, 21, 1)
                day = _parse_bcd_bytes(data_part, 22, 1)
                hour = _parse_bcd_bytes(data_part, 23, 1)
                minute = _parse_bcd_bytes(data_part, 24, 1)
                second = _parse_bcd_bytes(data_part, 25, 1)
                try:
                    result['timestamp'] = datetime(
                        year, month, day, hour, minute, second)
                except ValueError:
                    result['timestamp'] = datetime.now()
        except Exception:
            result['timestamp'] = datetime.now()

        # 电池电压（x100）
        if len(data_part) >= 29:
            try:
                vbat = struct.unpack('>H', data_part[27:29])[0]
                result['battery_voltage'] = vbat / 100.0
                result['voltage'] = result['battery_voltage']
            except Exception:
                pass

        # 信号强度
        if len(data_part) >= 30:
            result['signal'] = data_part[29]

        # 设备温度（x100, 有符号）
        if len(data_part) >= 32:
            try:
                temp_raw = struct.unpack('>h', data_part[30:32])[0]
                result['device_temp'] = temp_raw / 100.0
            except Exception:
                pass

        # 解析通道数据（每通道3字节: 频率高2B + 状态1B）
        # 通道数据从偏移32开始（32:16通道 = 48字节）
        channel_start_offset = 32
        for ch_idx in range(16):
            offset = channel_start_offset + ch_idx * 3
            if offset + 3 > len(data_part):
                break
            try:
                freq_raw = struct.unpack('>H', data_part[offset:offset + 2])[0]
                freq = freq_raw / 10.0  # 频率 x10 存储
                status_byte = data_part[offset + 2]
                if freq > 0:
                    data_quality = 'VALID'
                    if status_byte & 0x80:  # 超量程标志
                        data_quality = 'OVERFLOW'
                    result['channels'].append({
                        'channel': ch_idx + 1,
                        'frequency': freq,
                        'temp': None,
                        'data_quality': data_quality,
                    })
            except Exception:
                continue

        logger.debug("HEX解析: UDID=%s | 通道数=%d | CRC=%s",
                    result['udid'], len(result['channels']),
                    'OK' if result['checksum_valid'] else 'FAIL')

    except Exception as e:
        result['status'] = 'FAILED'
        result['error'] = f"HEX解析异常: {str(e)}"
        logger.error("HEX解析失败: %s", e)

    return result


# ============================================================================
# STR1.0 格式解析（固定156字节）
# ============================================================================

def parse_str10(raw_data):
    """
    解析 STR1.0 格式数据包（固定156字节）。

    STR1.0 是较早版本的协议格式，数据帧固定156字节。
    以字符串形式传输，字段用逗号分隔。

    典型格式:
        "VS001,2024-01-15 10:30:00,CH1=1234.5,T1=25.3,...CH16=0,T16=0,...V=12.5,SIG=28\r\n"

    Args:
        raw_data (str or bytes): 原始数据帧

    Returns:
        dict: 解析结果
    """
    result = {
        'protocol': 'STR1.0',
        'udid': '',
        'channels': [],
        'device_temp': None,
        'voltage': None,
        'signal': None,
        'timestamp': None,
        'status': 'SUCCESS',
        'error': None,
    }

    try:
        if isinstance(raw_data, bytes):
            raw_str = raw_data.decode('ascii', errors='ignore').strip()
        else:
            raw_str = str(raw_data).strip()

        raw_str = raw_str.rstrip('\r\n')

        # STR1.0格式：逗号分隔
        fields = raw_str.split(',')

        # 第一个字段可能是UDID或时间
        idx = 0
        # 尝试识别UDID（非时间格式）
        if idx < len(fields):
            first_field = fields[idx].strip()
            if not re.match(r'\d{4}-\d{2}-\d{2}', first_field):
                result['udid'] = first_field
                idx += 1
            else:
                # 第一个字段是时间，可能无UDID前缀
                result['udid'] = 'STR10_UNKNOWN'

        # 解析时间
        if idx < len(fields):
            time_str = fields[idx].strip()
            try:
                result['timestamp'] = datetime.strptime(
                    time_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    result['timestamp'] = datetime.strptime(
                        time_str, '%Y%m%d%H%M%S')
                except ValueError:
                    result['timestamp'] = datetime.now()
            idx += 1

        # 解析通道数据
        for i in range(16):
            ch = i + 1
            channel_data = {'channel': ch, 'frequency': None, 'temp': None}

            if idx < len(fields):
                ch_field = fields[idx].strip()
                ch_match = re.match(r'CH(\d+)=(.+)', ch_field, re.IGNORECASE)
                if ch_match:
                    channel_data['frequency'] = _parse_ascii_number(
                        ch_match.group(2))
                idx += 1

            if idx < len(fields):
                t_field = fields[idx].strip()
                t_match = re.match(r'T(\d+)=(.+)', t_field, re.IGNORECASE)
                if t_match:
                    channel_data['temp'] = _parse_ascii_number(
                        t_match.group(2))
                    idx += 1

            if channel_data['frequency'] is not None and channel_data['frequency'] > 0:
                result['channels'].append(channel_data)

        # 解析设备参数
        while idx < len(fields):
            field = fields[idx].strip()
            if '=' in field:
                key, value = field.split('=', 1)
                key = key.strip().upper()
                value = value.strip()
                if key in ('V', 'VOLT', 'POWER', 'BATV'):
                    result['voltage'] = _parse_ascii_number(value)
                elif key in ('SIG', 'SIGNAL', 'RSSI'):
                    result['signal'] = _parse_ascii_number(value)
                elif key in ('TEMP', 'T', 'DEVTEMP'):
                    result['device_temp'] = _parse_ascii_number(value)
            idx += 1

        logger.debug("STR1.0解析: UDID=%s | 通道数=%d",
                    result['udid'], len(result['channels']))

    except Exception as e:
        result['status'] = 'FAILED'
        result['error'] = f"STR1.0解析异常: {str(e)}"
        logger.error("STR1.0解析失败: %s", e)

    return result


# ============================================================================
# CUSTOM_HEX 格式解析 - 设备专属HEX协议（紧凑型）
# ============================================================================

def parse_custom_hex(raw_data):
    """
    解析 设备专属HEX协议（紧凑型自定义格式）。

    格式特征:
        - UDID> + 纯HEX字符串（无逗号、等号分隔符）
        - 数据紧凑排列，按字符偏移定位字段

    字节偏移表（HEX字符串中每2字符=1字节，索引从0起）:
        ┌──────────┬──────┬─────────────────────────────────┐
        │ 字符偏移  │ 长度  │ 字段含义                        │
        ├──────────┼──────┼─────────────────────────────────┤
        │ 0~1      │ 1字节 │ 设备地址（16进制→10进制）        │
        │ 2~5      │ 2字节 │ 数据记录号（大端）              │
        │ 8~9      │ 1字节 │ 4G信号强度（范围0-31）          │
        │ 16~19    │ 2字节 │ 设备供电电压（大端, ÷1000, V） │
        │ 24~27    │ 2字节 │ CH01振弦频率（大端, ÷10, Hz） │
        │ 28~31    │ 2字节 │ CH01传感器温度（大端, ÷10, ℃）│
        │ ...      │ ...   │ 更多通道依次类推...             │
        └──────────┴──────┴─────────────────────────────────┘

    Args:
        raw_data (str or bytes): 原始数据帧（不含UDID>前缀，或可含）

    Returns:
        dict: 解析结果
    """
    result = {
        'protocol': 'CUSTOM_HEX',
        'udid': '',
        'channels': [],
        'device_addr': None,         # 设备地址
        'record_number': None,       # 数据记录号
        'device_temp': None,
        'voltage': None,
        'battery_voltage': None,
        'signal': None,
        'record_number': None,
        'timestamp': None,
        'status': 'SUCCESS',
        'error': None,
    }

    try:
        # 统一转为字符串
        if isinstance(raw_data, bytes):
            raw_str = raw_data.decode('ascii', errors='ignore').strip()
        else:
            raw_str = str(raw_data).strip()

        raw_str = raw_str.rstrip('\r\n')

        # 如果有UDID>前缀，先剥离
        hex_part = raw_str
        if '>' in raw_str:
            _, hex_part = raw_str.rsplit('>', 1)

        # 清洗: 移除所有空格和非HEX字符
        hex_clean = ''.join(c for c in hex_part.upper() if c in '0123456789ABCDEF')

        if len(hex_clean) < 2:
            result['status'] = 'FAILED'
            result['error'] = f"CUSTOM_HEX数据长度不足: {len(hex_clean)//2}字节"
            return result

        # ---- 辅助函数: 从HEX字符串指定位置提取值 ----
        def _hex_value(start_char, byte_count, signed=False):
            """从HEX字符串的字符偏移提取数值（大端序）"""
            end_char = start_char + byte_count * 2
            if end_char > len(hex_clean):
                return None
            hex_bytes = hex_clean[start_char:end_char]
            try:
                raw_val = int(hex_bytes, 16)
                if signed and byte_count >= 1:
                    # 有符号: 检查最高位
                    max_val = 1 << (byte_count * 8)
                    if raw_val >= (max_val >> 1):
                        raw_val -= max_val
                return raw_val
            except ValueError:
                return None

        # ================================================================
        # 解析各字段（按协议偏移表）
        # ================================================================

        # 设备地址 (0~1)
        result['device_addr'] = _hex_value(0, 1)

        # 数据记录号 (2~5)
        result['record_number'] = _hex_value(2, 2)

        # 4G信号强度 (8~9)
        signal_raw = _hex_value(8, 1)
        if signal_raw is not None:
            result['signal'] = signal_raw if 0 <= signal_raw <= 31 else None

        # 设备供电电压 (16~19), 大端2字节, ÷1000
        volt_raw = _hex_value(16, 2)
        if volt_raw is not None:
            result['battery_voltage'] = round(volt_raw / 1000.0, 3)
            result['voltage'] = result['battery_voltage']

        # CH01振弦频率 (24~27), 大端2字节, ÷10
        freq_raw = _hex_value(24, 2)
        if freq_raw is not None and freq_raw > 0:
            freq_val = round(freq_raw / 10.0, 1)
            result['channels'].append({
                'channel': 1,
                'frequency': freq_val,
                'temp': None,
                'data_quality': 'VALID',
            })

        # CH01传感器温度 (28~31), 大端2字节, ÷10
        temp_raw = _hex_value(28, 2)
        if temp_raw is not None:
            # 温度可能有符号（负温度场景）
            temp_val = round(temp_raw / 10.0, 1)
            if result['channels']:
                result['channels'][0]['temp'] = temp_val
            result['device_temp'] = temp_val

        # 更多通道（如有足够数据，每通道4字节: 频率2B + 温度2B）
        # CH02从偏移32开始: 32~35频率, 36~39温度
        for ch in range(2, 17):  # 最多16通道
            base_offset = 24 + (ch - 1) * 8  # 每通道8个字符=4字节
            ch_freq_raw = _hex_value(base_offset, 2)
            ch_temp_raw = _hex_value(base_offset + 4, 2)

            if ch_freq_raw is None or ch_freq_raw == 0:
                continue  # 无数据或频率为0，跳过

            ch_data = {
                'channel': ch,
                'frequency': round(ch_freq_raw / 10.0, 1),
                'temp': round(ch_temp_raw / 10.0, 1) if ch_temp_raw is not None else None,
                'data_quality': 'VALID',
            }
            result['channels'].append(ch_data)

        logger.debug("CUSTOM_HEX解析: Addr=%s | Record=%s | CHs=%d | V=%.2fV | Sig=%s",
                    result['device_addr'], result['record_number'],
                    len(result['channels']),
                    result['voltage'] or 0, result['signal'])

    except Exception as e:
        result['status'] = 'FAILED'
        result['error'] = f"CUSTOM_HEX解析异常: {str(e)}"
        logger.error("CUSTOM_HEX解析失败: %s", e)

    return result


# ============================================================================
# SL-651 协议解析
# ============================================================================

def parse_sl651(raw_data):
    """
    解析 SL-651 协议数据包。

    SL-651 有两种格式:
        - HEX格式: 以 0x7E 0x7E 开头
        - STR格式: 以 0x01 开头

    Args:
        raw_data (bytes or str): 原始数据帧

    Returns:
        dict: 解析结果
    """
    result = {
        'protocol': 'SL-651',
        'udid': '',
        'channels': [],
        'device_temp': None,
        'voltage': None,
        'signal': None,
        'timestamp': None,
        'status': 'SUCCESS',
        'error': None,
    }

    try:
        if isinstance(raw_data, str):
            raw_bytes = _hex_to_bytes(raw_data)
            raw_str = raw_data  # 尝试字符串解析
        else:
            raw_bytes = raw_data
            raw_str = ''

        # 判断是HEX还是STR格式
        if len(raw_bytes) >= 2 and raw_bytes[0] == 0x7E and raw_bytes[1] == 0x7E:
            # HEX格式解析
            result['protocol'] = 'SL-651_HEX'
            # 简化解析: 跳过SL-651头部(2B)和长度(2B)，
            # 提取站址和观测数据
            if len(raw_bytes) >= 8:
                # 站址通常在偏移8-12位置
                station_id = raw_bytes[4:8].hex().upper()
                result['udid'] = f"SL651_{station_id}"

            result['timestamp'] = datetime.now()

            logger.debug("SL-651 HEX解析: 站址=%s | 长度=%d",
                        result['udid'], len(raw_bytes))

        elif len(raw_bytes) >= 1 and raw_bytes[0] == 0x01:
            # STR格式解析（以SOH开头）
            result['protocol'] = 'SL-651_STR'
            # 尝试提取站址（SL-651 STR格式站址在偏移1-5位置）
            try:
                if len(raw_bytes) >= 6:
                    # 站址为5字节BCD编码
                    station_bytes = raw_bytes[1:6]
                    station_id = station_bytes.hex().upper()
                    result['udid'] = f"SL651_{station_id}"
                else:
                    result['udid'] = 'SL651_UNKNOWN'
            except Exception:
                pass

            result['timestamp'] = datetime.now()

            logger.debug("SL-651 STR解析: 长度=%d", len(raw_bytes))

        else:
            result['status'] = 'FAILED'
            result['error'] = "无法识别的SL-651格式"
            return result

    except Exception as e:
        result['status'] = 'FAILED'
        result['error'] = f"SL-651解析异常: {str(e)}"
        logger.error("SL-651解析失败: %s", e)

    return result


# ============================================================================
# 模块自检
# ============================================================================

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                       format='%(asctime)s | %(levelname)s | %(message)s')

    print("=" * 60)
    print("protocol/parser.py 模块自检")
    print("=" * 60)

    # 测试STR2.0解析
    print("\n[1] STR2.0 格式解析:")
    str20_sample = "VS001>CH1=1234.5,CH2=1245.6,CH3=0,CH4=1256.8,TEMP=25.3,VOLT=12.5,SIGNAL=28\r\n"
    result = parse_str20(str20_sample)
    print(f"  UDID: {result['udid']}")
    print(f"  通道数: {len(result['channels'])}")
    for ch in result['channels']:
        print(f"    CH{ch['channel']}: freq={ch['frequency']}Hz, temp={ch['temp']}℃")
    print(f"  设备温度: {result['device_temp']}℃")
    print(f"  电压: {result['voltage']}V")
    print(f"  信号: {result['signal']}dBm")

    # 测试STR3.0解析
    print("\n[2] STR3.0 格式解析:")
    str30_sample = "VS410>CH1=1234.5,T1=25.3,M1=1524.0,R1=10850,CH2=1245.6,T2=25.1,M2=1551.5,R2=10840,BATV=12.5,SOLARV=13.8,TEMP=26.1,SIGNAL=28,MODE=AUTO\r\n"
    result = parse_str30(str30_sample)
    print(f"  UDID: {result['udid']}")
    print(f"  通道数: {len(result['channels'])}")
    for ch in result['channels']:
        print(f"    CH{ch['channel']}: freq={ch['frequency']}Hz, "
              f"temp={ch['temp']}℃, M={ch['freq_module']}, R={ch['thermistor_r']}Ω")
    print(f"  电池电压: {result['battery_voltage']}V")
    print(f"  太阳能: {result['solar_voltage']}V")
    print(f"  工作模式: {result['work_mode']}")

    # 测试STR1.0解析
    print("\n[3] STR1.0 格式解析:")
    str10_sample = "VS001,2024-01-15 10:30:00,CH1=1234.5,T1=25.3,CH2=1245.6,T2=25.1,V=12.5,SIG=28\r\n"
    result = parse_str10(str10_sample)
    print(f"  UDID: {result['udid']}")
    print(f"  通道数: {len(result['channels'])}")
    print(f"  时间: {result['timestamp']}")
    print(f"  电压: {result['voltage']}V")

    print("\n自检完成!")
