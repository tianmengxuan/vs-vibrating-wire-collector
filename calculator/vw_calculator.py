# -*- coding: utf-8 -*-
"""
calculator/vw_calculator.py - 振弦数据解算核心
================================================
功能:
    1. 振弦传感器通用解算: F = K × (f² - f0²) + α×(T - T0)
    2. 支持4种传感器类型: 应变计、渗压计、位移计、钢筋计
    3. 热敏电阻阻值转温度
    4. 数据合法性校验（超量程、零频率、超出范围）
    5. 自动从MySQL读取标定参数
    6. 解算结果自动入库

参考:
    稳控科技监测设备通用通讯协议接口说明 V1.2.0
    VS1XX_VS4XX用户手册 V1.40

作者: VS DataCollector Project
版本: 1.0.0
"""

import math
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# ============================================================================
# 传感器类型定义
# ============================================================================

# 传感器类型 → 物理量单位映射（水压公式: MPa）
SENSOR_UNITS = {
    'strain': 'MPa',         # 水压(MPa)
    'osmotic': 'MPa',        # 水压(MPa)
    'displacement': 'MPa',   # 水压(MPa)
    'rebar': 'MPa',          # 水压(MPa)
    'stress': 'MPa',         # 水压(MPa)
    'crack': 'MPa',          # 水压(MPa)
    'settlement': 'MPa',     # 水压(MPa)
    'inclinometer': 'MPa',   # 水压(MPa)
}

# 传感器类型中文名
SENSOR_NAMES = {
    'strain': '应变计',
    'osmotic': '渗压计',
    'displacement': '位移计',
    'rebar': '钢筋计',
    'stress': '应力计',
    'crack': '裂缝计',
    'settlement': '沉降计',
    'inclinometer': '倾角计',
}

# 默认量程范围
DEFAULT_RANGES = {
    'strain': (-3000, 3000),
    'osmotic': (0, 7000),
    'displacement': (-200, 200),
    'rebar': (-500, 500),
}


# ============================================================================
# 热敏电阻转温度
# ============================================================================

def thermistor_to_temperature(resistance, r25=10000.0, b_value=3950.0, t25=298.15):
    """
    热敏电阻阻值转换为温度。

    使用Steinhart-Hart简化方程（B参数方程）:
        1/T = 1/T25 + (1/B) × ln(R/R25)
        即: T = 1 / (1/T25 + ln(R/R25)/B)
        结果再转换为摄氏度: ℃ = K - 273.15

    Args:
        resistance (float): 热敏电阻阻值(Ω)
        r25 (float): 25℃时的阻值(Ω)，默认10000Ω
        b_value (float): B值(K)，默认3950K
        t25 (float): 25℃对应的开尔文温度，默认298.15K

    Returns:
        float or None: 温度(℃)

    Examples:
        >>> round(thermistor_to_temperature(10000.0), 1)
        25.0
        >>> round(thermistor_to_temperature(10850.0, r25=10000, b_value=3950), 1)
        # 阻值增大 → 温度降低
    """
    if resistance is None or resistance <= 0:
        logger.debug("热敏电阻阻值无效: %s Ω", resistance)
        return None

    try:
        # 简化B参数方程: 1/T = 1/T25 + (1/B) * ln(R/R25)
        ratio = resistance / r25
        if ratio <= 0:
            logger.warning("热敏电阻比值无效: R=%.2f, R25=%.2f", resistance, r25)
            return None

        ln_ratio = math.log(ratio)
        inv_t = (1.0 / t25) + (ln_ratio / b_value)

        if inv_t <= 0:
            logger.warning("热敏电阻换算异常: 1/T=%.6f", inv_t)
            return None

        temp_k = 1.0 / inv_t
        temp_c = temp_k - 273.15

        logger.debug("热敏电阻转温度: R=%.2fΩ → T=%.2f℃ (B=%d, R25=%d)",
                    resistance, temp_c, int(b_value), int(r25))

        return round(temp_c, 2)

    except (ValueError, ZeroDivisionError, OverflowError) as e:
        logger.error("热敏电阻转温度异常: %s", e)
        return None


# ============================================================================
# 振弦解算公式
# ============================================================================

def calculate_physical_value(frequency, k_coefficient=1.0, f0_initial=0.0,
                              temperature=None, alpha_temp=0.0, t0_initial=20.0):
    """
    振弦传感器水压计算（频率与水压关系）。

    核心公式:
        P = K × (f0² - f²) + Kt × (t - t0)

    其中:
        P    - 水压值(MPa)
        K    - 传感器灵敏度系数（正值）
        f    - 当前频率(Hz)
        f0   - 初始频率(Hz，零压力时频率，通常 > f)
        Kt   - 温度修正系数（α）
        t    - 当前温度(℃)
        t0   - 初始温度(℃)

    振弦原理: 受压 → 频率降低 → f0² - f² > 0 → P 为正值

    Args:
        frequency (float): 当前测量频率(Hz)
        k_coefficient (float): K系数(灵敏度)
        f0_initial (float): 初始频率f0(Hz，零压力基准)
        temperature (float or None): 当前温度(℃)
        alpha_temp (float): 温度修正系数Kt
        t0_initial (float): 初始温度T0(℃)

    Returns:
        tuple: (水压值MPa, 是否进行了温度修正)

    Examples:
        >>> # f=2128Hz, f0=2180Hz, K=0.00035, T=21℃, T0=20℃, Kt=0.001
        >>> value, corrected = calculate_physical_value(2128.0, 0.00035, 2180.0, 21.0, 0.001, 20.0)
        >>> round(value, 3)
        # P = 0.00035*(2180²-2128²) + 0.001*(21-20) = 0.078 + 0.001 = 0.079 MPa
    """
    if frequency is None or frequency <= 0:
        logger.warning("频率无效: %.2f Hz", frequency if frequency else 0)
        return None, False

    # ---- 步骤1: 计算频率平方差（水压公式: f0² - f²）----
    f2 = frequency * frequency
    f02 = f0_initial * f0_initial
    freq_term = f02 - f2

    # ---- 步骤2: 主项计算 P = K × (f0² - f²) ----
    physical_value = k_coefficient * freq_term

    # ---- 步骤3: 温度修正 P = K×(f0²-f²) + Kt×(t-t0) ----
    temperature_corrected = False
    if temperature is not None and alpha_temp != 0:
        temp_correction = alpha_temp * (temperature - t0_initial)
        physical_value += temp_correction
        temperature_corrected = True
        logger.debug("温度修正: ΔT=%.2f℃, 修正量=%.4f | Kt=%.6f",
                    temperature - t0_initial, temp_correction, alpha_temp)

    # 保留6位小数
    physical_value = round(physical_value, 6)

    logger.debug("振弦水压解算: f=%.2fHz, f0=%.2fHz, K=%.6f, "
                "Δf²=%.2f, P=%.4fMPa, 温度修正=%s",
                frequency, f0_initial, k_coefficient,
                freq_term, physical_value,
                '是' if temperature_corrected else '否')

    return physical_value, temperature_corrected


# ============================================================================
# 数据合法性校验
# ============================================================================

def validate_channel_data(frequency, calibration=None):
    """
    校验通道数据的合法性。

    检查项:
        1. 频率是否为0（传感器故障/未连接）
        2. 频率是否超出传感器有效频率范围（通常400-6000Hz）
        3. 物理量是否超出传感器量程（需先解算后校验）

    Args:
        frequency (float): 频率值(Hz)
        calibration (SensorCalibration or dict or None): 标定参数

    Returns:
        str: 数据质量标识
            - 'VALID': 有效数据
            - 'ZERO_FREQ': 频率为0
            - 'OUT_OF_RANGE': 超出频率范围
            - 'INVALID': 其他无效情况
    """
    if frequency is None:
        return 'INVALID'

    # 频率为0 → 传感器未连接或故障
    if frequency <= 0.01:
        logger.warning("频率为0，传感器可能未连接或故障")
        return 'ZERO_FREQ'

    # 获取有效频率范围
    freq_min = 400.0
    freq_max = 6000.0

    if calibration is not None:
        if isinstance(calibration, dict):
            freq_min = calibration.get('freq_min', 400.0)
            freq_max = calibration.get('freq_max', 6000.0)
        else:
            freq_min = getattr(calibration, 'freq_min', 400.0) or 400.0
            freq_max = getattr(calibration, 'freq_max', 6000.0) or 6000.0

    # 检查频率范围
    if frequency < freq_min or frequency > freq_max:
        logger.warning("频率超出范围: %.2fHz (有效范围: %.1f-%.1fHz)",
                      frequency, freq_min, freq_max)
        return 'OUT_OF_RANGE'

    return 'VALID'


def validate_calc_value(calc_value, calibration=None):
    """
    校验解算结果是否在传感器量程范围内。

    Args:
        calc_value (float): 解算物理量值
        calibration: 标定参数

    Returns:
        str: 数据质量标识
    """
    if calc_value is None:
        return 'INVALID'

    range_min = None
    range_max = None

    if calibration is not None:
        if isinstance(calibration, dict):
            range_min = calibration.get('range_min')
            range_max = calibration.get('range_max')
        else:
            range_min = getattr(calibration, 'range_min', None)
            range_max = getattr(calibration, 'range_max', None)

    # 检查量程
    if range_min is not None and calc_value < range_min:
        logger.warning("解算值低于量程: %.4f < %.4f", calc_value, range_min)
        return 'OVER_RANGE'
    if range_max is not None and calc_value > range_max:
        logger.warning("解算值超出量程: %.4f > %.4f", calc_value, range_max)
        return 'OVER_RANGE'

    return 'VALID'


# ============================================================================
# 振弦解算器类
# ============================================================================

class VibratingWireCalculator:
    """
    振弦传感器解算器。

    负责从数据库读取标定参数、执行解算、校验、并自动入库。

    Usage:
        calc = VibratingWireCalculator(db_manager)
        results = calc.process_channel_data(channel_data_list)
    """

    def __init__(self, db_manager):
        """
        初始化解算器。

        Args:
            db_manager (DatabaseManager): 数据库管理器实例
        """
        self.db = db_manager
        logger.info("振弦解算器初始化完成")

    def _get_calibration_params(self, udid, channel):
        """
        从数据库获取传感器的标定参数。

        如果数据库中不存在标定参数，使用默认值。

        Args:
            udid (str): 设备UDID
            channel (int): 通道号

        Returns:
            dict: 标定参数字典
        """
        default_params = {
            'sensor_type': 'strain',
            'k_coefficient': 1.0,
            'f0_initial': 0.0,
            'alpha_temp': 0.0,
            't0_initial': 20.0,
            'range_min': None,
            'range_max': None,
            'freq_min': 400.0,
            'freq_max': 6000.0,
            'thermistor_b': 3950.0,
            'thermistor_r25': 10000.0,
        }

        if not self.db or not self.db.is_connected():
            logger.warning("数据库未连接，使用默认标定参数 [UDID=%s CH=%d]", udid, channel)
            return default_params

        try:
            cal = self.db.get_calibration(udid, channel)
            if cal:
                params = {
                    'sensor_type': cal.sensor_type,
                    'k_coefficient': cal.k_coefficient,
                    'f0_initial': cal.f0_initial,
                    'alpha_temp': cal.alpha_temp,
                    't0_initial': cal.t0_initial,
                    'range_min': cal.range_min,
                    'range_max': cal.range_max,
                    'freq_min': cal.freq_min or 400.0,
                    'freq_max': cal.freq_max or 6000.0,
                    'thermistor_b': cal.thermistor_b or 3950.0,
                    'thermistor_r25': cal.thermistor_r25 or 10000.0,
                }
                logger.debug("已加载标定参数: UDID=%s CH=%d Type=%s K=%.6f f0=%.2f",
                           udid, channel, params['sensor_type'],
                           params['k_coefficient'], params['f0_initial'])
                return params
            else:
                logger.info("无标定参数 [UDID=%s CH=%d]，使用默认值", udid, channel)
                return default_params

        except Exception as e:
            logger.error("获取标定参数失败 [UDID=%s CH=%d]: %s", udid, channel, e)
            return default_params

    def calculate_single_channel(self, udid, channel, frequency, temperature=None,
                                  thermistor_resistance=None,
                                  calibration_params=None):
        """
        对单个通道执行完整的振弦解算。

        流程:
            1. 获取标定参数
            2. 热敏电阻阻值 → 温度（如果未提供温度但提供了阻值）
            3. 数据合法性校验
            4. 执行解算公式
            5. 解算结果校验（量程检查）

        Args:
            udid (str): 设备UDID
            channel (int): 通道号
            frequency (float): 频率(Hz)
            temperature (float or None): 温度(℃)
            thermistor_resistance (float or None): 热敏电阻阻值(Ω)
            calibration_params (dict or None): 标定参数，None则自动查询

        Returns:
            dict: 解算结果，包含:
                - udid: 设备UDID
                - channel: 通道号
                - sensor_type: 传感器类型
                - raw_frequency: 原始频率
                - raw_temperature: 原始温度
                - calc_value: 解算物理量值
                - calc_unit: 单位
                - temperature_corrected: 是否温度修正
                - calc_status: 解算状态
                - calc_error: 错误信息
                - calibration_used: 使用的标定参数
        """
        result = {
            'udid': udid,
            'channel': channel,
            'sensor_type': None,
            'raw_frequency': frequency,
            'raw_temperature': temperature,
            'calc_value': None,
            'calc_unit': None,
            'temperature_corrected': False,
            'calc_status': 'PENDING',
            'calc_error': None,
            'calibration_used': {},
        }

        try:
            # ---- 步骤1: 获取标定参数 ----
            if calibration_params is None:
                calibration_params = self._get_calibration_params(udid, channel)

            result['calibration_used'] = calibration_params
            sensor_type = calibration_params.get('sensor_type', 'strain')
            result['sensor_type'] = sensor_type
            result['calc_unit'] = SENSOR_UNITS.get(sensor_type, '')

            # ---- 步骤2: 温度换算 ----
            if temperature is None and thermistor_resistance is not None:
                temperature = thermistor_to_temperature(
                    thermistor_resistance,
                    r25=calibration_params.get('thermistor_r25', 10000.0),
                    b_value=calibration_params.get('thermistor_b', 3950.0),
                )
                result['raw_temperature'] = temperature
                logger.debug("UDID=%s CH=%d: 热敏电阻→温度: R=%.1fΩ → T=%.2f℃",
                           udid, channel, thermistor_resistance, temperature)

            # ---- 步骤3: 数据校验 ----
            quality = validate_channel_data(frequency, calibration_params)
            if quality != 'VALID':
                result['calc_status'] = 'ERROR'
                result['calc_error'] = f"数据质量异常: {quality}"
                logger.warning("UDID=%s CH=%d: 数据校验不通过 - %s", udid, channel, quality)
                return result

            # ---- 步骤4: 执行解算 ----
            k = calibration_params.get('k_coefficient', 1.0)
            f0 = calibration_params.get('f0_initial', 0.0)
            alpha = calibration_params.get('alpha_temp', 0.0)
            t0 = calibration_params.get('t0_initial', 20.0)

            calc_value, temp_corrected = calculate_physical_value(
                frequency=frequency,
                k_coefficient=k,
                f0_initial=f0,
                temperature=temperature,
                alpha_temp=alpha,
                t0_initial=t0,
            )

            result['calc_value'] = calc_value
            result['temperature_corrected'] = temp_corrected

            # ---- 步骤5: 量程校验 ----
            range_quality = validate_calc_value(calc_value, calibration_params)
            if range_quality != 'VALID':
                result['calc_status'] = 'OVER_RANGE'
                result['calc_error'] = f"解算值超出量程: {calc_value}"
                logger.warning("UDID=%s CH=%d: 解算值超出量程 %.4f%s",
                             udid, channel, calc_value, result['calc_unit'])
            else:
                result['calc_status'] = 'SUCCESS'
                logger.info("UDID=%s CH=%d: 水压解算 P=%.4f%s (f=%.2fHz, T=%.2f℃)",
                           udid, channel, calc_value, result['calc_unit'],
                           frequency, temperature)

        except Exception as e:
            result['calc_status'] = 'ERROR'
            result['calc_error'] = f"解算异常: {str(e)}"
            logger.error("UDID=%s CH=%d: 解算异常 - %s", udid, channel, e, exc_info=True)

        return result

    def process_channel_data(self, channel_data_list, raw_packet_id=None):
        """
        批量处理通道数据：解算 → 校验 → 入库。

        完整链路:
            解析结果 → 频率/温度 → 查询标定参数 → 解算 → 写入channel_calc_result

        Args:
            channel_data_list (list[dict]): 通道数据列表，每项包含:
                - udid
                - channel
                - frequency
                - temperature (optional)
                - thermistor_resistance (optional)
                - collect_time (optional)
            raw_packet_id (int): 关联的原始数据包ID

        Returns:
            tuple: (calc_results_list, saved_count)
                - calc_results_list: 解算结果字典列表
                - saved_count: 成功入库的数量
        """
        calc_results = []
        saved_count = 0

        if not channel_data_list:
            logger.info("通道数据列表为空，跳过解算")
            return calc_results, saved_count

        logger.info("开始批量解算: %d 个通道数据", len(channel_data_list))

        for item in channel_data_list:
            udid = item.get('udid', '')
            channel = item.get('channel', 0)

            # 执行单通道解算
            result = self.calculate_single_channel(
                udid=udid,
                channel=channel,
                frequency=item.get('frequency'),
                temperature=item.get('temp') or item.get('temperature'),
                thermistor_resistance=item.get('thermistor_r') or item.get('thermistor_resistance'),
            )

            # 补充关联信息
            result['raw_packet_id'] = raw_packet_id
            result['collect_time'] = item.get('collect_time')

            calc_results.append(result)

        # ---- 自动入库 ----
        if self.db and self.db.is_connected():
            # 需要先获取channel_raw_data的ID（在实际流程中，channel_raw_data已入库）
            # 这里构建入库数据
            db_results = []
            for cr in calc_results:
                if cr['calc_status'] in ('SUCCESS', 'OVER_RANGE', 'WARNING'):
                    db_results.append({
                        'channel_raw_id': cr.get('channel_raw_id', 0),
                        'raw_packet_id': cr.get('raw_packet_id'),
                        'udid': cr['udid'],
                        'channel': cr['channel'],
                        'sensor_type': cr['sensor_type'],
                        'k_coefficient': cr['calibration_used'].get('k_coefficient'),
                        'f0_initial': cr['calibration_used'].get('f0_initial'),
                        'alpha_temp': cr['calibration_used'].get('alpha_temp'),
                        't0_initial': cr['calibration_used'].get('t0_initial'),
                        'raw_frequency': cr['raw_frequency'],
                        'raw_temperature': cr['raw_temperature'],
                        'calc_value': cr['calc_value'],
                        'calc_unit': cr['calc_unit'],
                        'temperature_corrected': cr['temperature_corrected'],
                        'calc_status': cr['calc_status'],
                        'calc_error': cr['calc_error'],
                        'collect_time': cr.get('collect_time'),
                    })

            if db_results:
                inserted_ids = self.db.batch_insert_calc_results(db_results)
                saved_count = len(inserted_ids)

                # 回填ID
                for cr, cid in zip(calc_results, inserted_ids + [None] * (len(calc_results) - len(inserted_ids))):
                    if cid:
                        cr['calc_id'] = cid

                logger.info("解算结果入库: %d/%d 条成功", saved_count, len(db_results))
            else:
                logger.info("无成功的解算结果需要入库")
        else:
            logger.warning("数据库未连接，解算结果未入库")

        return calc_results, saved_count


# ============================================================================
# 模块自检
# ============================================================================

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                       format='%(asctime)s | %(levelname)s | %(message)s')

    print("=" * 60)
    print("calculator/vw_calculator.py 模块自检")
    print("=" * 60)

    # 测试1: 热敏电阻转温度
    print("\n[1] 热敏电阻转温度:")
    temps = [
        thermistor_to_temperature(10000.0),   # 25℃
        thermistor_to_temperature(15000.0),   # 阻值增大→温度降低
        thermistor_to_temperature(5000.0),    # 阻值减小→温度升高
        thermistor_to_temperature(0),         # 无效
    ]
    for i, t in enumerate(temps):
        print(f"  测试{i+1}: T={t}℃" if t else f"  测试{i+1}: 无效")

    # 测试2: 振弦解算
    print("\n[2] 振弦解算公式:")
    # 应变计: K=0.001, f=1500Hz, f0=1400Hz, T=25℃, T0=20℃, α=0.5
    value, corrected = calculate_physical_value(1500.0, 0.001, 1400.0, 25.0, 0.5, 20.0)
    print(f"  应变计: f=1500Hz, f0=1400Hz, K=0.001 → F={value:.4f}με (温度修正={corrected})")

    # 渗压计: K=-0.01, f=1200Hz, f0=1000Hz
    value, _ = calculate_physical_value(1200.0, -0.01, 1000.0)
    print(f"  渗压计: f=1200Hz, f0=1000Hz, K=-0.01 → F={value:.4f}kPa")

    # 钢筋计: K=0.005, f=2000Hz, f0=1800Hz
    value, _ = calculate_physical_value(2000.0, 0.005, 1800.0)
    print(f"  钢筋计: f=2000Hz, f0=1800Hz, K=0.005 → F={value:.4f}kN")

    # 测试3: 数据校验
    print("\n[3] 数据合法性校验:")
    checks = [
        (1234.5, None, 'VALID'),
        (0, None, 'ZERO_FREQ'),
        (100, None, 'OUT_OF_RANGE'),   # 低于400Hz
        (8000.0, None, 'OUT_OF_RANGE'), # 高于6000Hz
        (1500.0, {'freq_min': 1000, 'freq_max': 3000}, 'VALID'),
    ]
    for freq, cal, expected in checks:
        result = validate_channel_data(freq, cal)
        status = "PASS" if result == expected else "FAIL"
        print(f"  freq={freq}Hz → quality={result} (期望{expected}) [{status}]")

    # 测试4: 解算器
    print("\n[4] 解算器基本测试:")
    calc = VibratingWireCalculator(None)  # 无DB连接
    result = calc.calculate_single_channel(
        udid='VS001', channel=1,
        frequency=1500.0, temperature=25.0,
    )
    print(f"  UDID={result['udid']} CH={result['channel']}")
    print(f"  传感器类型: {result['sensor_type']}")
    print(f"  解算值: {result['calc_value']:.4f} {result['calc_unit']}")
    print(f"  状态: {result['calc_status']}")

    print("\n自检完成!")
