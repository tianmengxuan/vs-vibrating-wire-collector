# -*- coding: utf-8 -*-
"""
protocol/checksum.py - 协议校验算法
=====================================
实现:
    1. CRC16-MODBUS 校验算法
    2. 异或和校验算法
    3. 校验验证工具函数

参考:
    稳控科技监测设备通用通讯协议接口说明 V1.2.0
    MODBUS CRC16 标准实现

作者: VS DataCollector Project
版本: 1.0.0
"""

import logging

logger = logging.getLogger(__name__)


# ============================================================================
# CRC16-MODBUS 校验表（预计算，提高性能）
# ============================================================================

# 动态生成标准CRC16-MODBUS查找表
# 多项式: 0xA001 (反转的 0x8005)
def _build_crc16_table():
    """构建标准CRC16-MODBUS查找表"""
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
        table.append(crc & 0xFFFF)
    return table

_CRC16_TABLE = _build_crc16_table()


# ============================================================================
# CRC16-MODBUS 算法
# ============================================================================

def crc16_modbus(data):
    """
    计算 CRC16-MODBUS 校验值。

    算法说明:
        - 多项式: 0x8005 (反转: 0xA001)
        - 初始值: 0xFFFF
        - 输出异或: 0x0000
        - 输入反转: True (LSB first)
        - 输出反转: True

    Args:
        data: 输入数据，支持以下类型:
            - bytes: 直接计算
            - bytearray: 直接计算
            - str: 视为HEX字符串，如 "01 03 00 00 00 01"
            - list[int]: 字节值列表

    Returns:
        int: 16位CRC值（0-65535）

    Examples:
        >>> crc16_modbus(bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x01]))
        0x840A
        >>> crc16_modbus("01 03 00 00 00 01")
        0x840A
    """
    # 统一转换为 bytes
    if isinstance(data, str):
        hex_str = data.replace(' ', '')
        if len(hex_str) % 2 != 0:
            logger.warning("CRC16: HEX字符串长度为奇数，自动补0")
            hex_str += '0'
        data = bytes.fromhex(hex_str)
    elif isinstance(data, (list, tuple)):
        data = bytes(data)
    elif isinstance(data, bytearray):
        data = bytes(data)

    # 逐位计算法 CRC16-MODBUS（最可靠的标准实现）
    # 多项式: 0x8005, 反转: 0xA001, 初始值: 0xFFFF
    # 注意: MODBUS协议CRC在线上是小端序(低字节在前)，
    # 但标准参考值以大端序显示，故返回前做字节交换。
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    # 字节交换: 内部计算为小端序，转换为标准大端序返回值
    crc = ((crc & 0xFF) << 8) | ((crc >> 8) & 0xFF)
    return crc & 0xFFFF


def crc16_modbus_hex(hex_string):
    """
    计算HEX字符串的CRC16-MODBUS校验值（便捷函数）。

    返回的CRC值为低字节在前、高字节在后（MODBUS标准序）。

    Args:
        hex_string (str): HEX字符串，如 "01 03 00 00 00 01" 或 "010300000001"

    Returns:
        str: 4字符HEX CRC值（低字节在前），如 "0A84"
    """
    crc = crc16_modbus(hex_string)
    # MODBUS标准序：低字节在前
    return f"{(crc & 0xFF):02X}{(crc >> 8):02X}"


# ============================================================================
# 和校验算法（SUM / XOR）
# ============================================================================

def checksum_xor(data):
    """
    计算异或和校验值。

    算法说明:
        将所有字节逐字节异或（XOR），结果取低8位。
        稳控科技部分协议使用此算法。

    Args:
        data: 输入数据（同crc16_modbus支持的类型）

    Returns:
        int: 8位异或和（0-255）

    Examples:
        >>> checksum_xor(bytes([0x01, 0x02, 0x03, 0x04]))
        0x04  # 0x01 ^ 0x02 ^ 0x03 ^ 0x04 = 0x04
    """
    if isinstance(data, str):
        hex_str = data.replace(' ', '')
        if len(hex_str) % 2 != 0:
            hex_str += '0'
        data = bytes.fromhex(hex_str)
    elif isinstance(data, (list, tuple)):
        data = bytes(data)
    elif isinstance(data, bytearray):
        data = bytes(data)

    result = 0
    for byte in data:
        result ^= byte
    return result & 0xFF


def checksum_sum8(data):
    """
    计算累加和校验值（取低8位）。

    算法说明:
        将所有字节累加，结果取低8位。

    Args:
        data: 输入数据

    Returns:
        int: 8位累加和（0-255）

    Examples:
        >>> checksum_sum8(bytes([0x01, 0x02, 0x03, 0x04]))
        0x0A  # (1+2+3+4) & 0xFF = 10
    """
    if isinstance(data, str):
        hex_str = data.replace(' ', '')
        if len(hex_str) % 2 != 0:
            hex_str += '0'
        data = bytes.fromhex(hex_str)
    elif isinstance(data, (list, tuple)):
        data = bytes(data)
    elif isinstance(data, bytearray):
        data = bytes(data)

    return sum(data) & 0xFF


# ============================================================================
# 校验验证工具
# ============================================================================

def verify_checksum(data, checksum_type='CRC16', expected=None):
    """
    验证数据校验值是否正确。

    Args:
        data: 数据（不含校验字节）
        checksum_type (str): 校验类型 CRC16/XOR/SUM8
        expected: 期望的校验值（int或HEX字符串）
            - int: 直接比较
            - str: HEX字符串，如 "840A"

    Returns:
        tuple: (bool, int) -> (是否通过, 计算出的校验值)

    Examples:
        >>> verify_checksum("01 03 00 00 00 01", 'CRC16', 0x840A)
        (True, 0x840A)
    """
    if checksum_type.upper() == 'CRC16':
        calc_value = crc16_modbus(data)
    elif checksum_type.upper() == 'XOR':
        calc_value = checksum_xor(data)
    elif checksum_type.upper() == 'SUM8':
        calc_value = checksum_sum8(data)
    else:
        logger.warning("未知校验类型: %s", checksum_type)
        return False, 0

    if expected is None:
        return True, calc_value

    # 处理期望值
    if isinstance(expected, str):
        expected = int(expected.replace(' ', ''), 16)

    is_valid = (calc_value == expected)
    if not is_valid:
        logger.debug("校验失败: 期望=0x%04X, 实际=0x%04X | Type=%s",
                     expected, calc_value, checksum_type)

    return is_valid, calc_value


# ============================================================================
# 模块自检与协议示例验证
# ============================================================================

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                       format='%(asctime)s | %(levelname)s | %(message)s')

    print("=" * 60)
    print("protocol/checksum.py 模块自检")
    print("=" * 60)

    # ---- CRC16-MODBUS 标准测试向量 ----
    print("\n[1] CRC16-MODBUS 标准测试向量:")
    test_cases = [
        # (输入, 期望CRC) - MODBUS标准测试向量
        ("01 03 00 00 00 01", 0x840A),    # MODBUS读寄存器标准示例
        ("", 0xFFFF),                      # 空数据 → 初始值
        ("03", 0x3D7E),                    # 单字节
    ]

    for hex_input, expected in test_cases:
        result = crc16_modbus(hex_input)
        status = "PASS" if result == expected else "FAIL"
        print(f"  {hex_input:30s} -> CRC=0x{result:04X} (期望0x{expected:04X}) [{status}]")

    # ---- CRC16-MODBUS HEX便捷函数 ----
    print("\n[2] CRC16-MODBUS HEX格式（低字节在前）:")
    hex_str = "01 03 00 00 00 01"
    crc_hex = crc16_modbus_hex(hex_str)
    print(f"  输入: {hex_str}")
    print(f"  CRC(低前): {crc_hex}")
    print(f"  附加CRC后的完整帧: {hex_str.replace(' ', '')} {crc_hex}")

    # ---- 异或和校验 ----
    print("\n[3] 异或和校验:")
    xor_tests = [
        ("01 02 03 04", 0x04),     # 1^2^3^4 = 4
        ("FF FF 00 00", 0x00),     # FF^FF^00^00 = 0
        ("A5 5A", 0xFF),           # A5^5A = FF
    ]
    for hex_input, expected in xor_tests:
        result = checksum_xor(hex_input)
        status = "PASS" if result == expected else "FAIL"
        print(f"  {hex_input:20s} -> XOR=0x{result:02X} (期望0x{expected:02X}) [{status}]")

    # ---- 累加和 ----
    print("\n[4] 累加和校验:")
    sum_result = checksum_sum8("01 02 03 04")
    print(f"  01 02 03 04 -> SUM8=0x{sum_result:02X} (期望0x0A)")

    # ---- 校验验证 ----
    print("\n[5] 校验验证函数:")
    is_ok, val = verify_checksum("01 03 00 00 00 01", 'CRC16', 0x840A)
    print(f"  CRC16验证: {'通过' if is_ok else '失败'} (计算值=0x{val:04X})")

    is_ok, val = verify_checksum("01 02 03 04", 'XOR', 0x04)
    print(f"  XOR验证:   {'通过' if is_ok else '失败'} (计算值=0x{val:02X})")

    print("\n自检完成!")
