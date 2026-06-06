# -*- coding: utf-8 -*-
"""
logger_config.py - 全链路详细日志系统
=======================================
功能:
    1. 使用Python标准logging模块，配置硬编码在代码中
    2. 按日期自动分割日志文件（每天一个）
    3. 错误日志单独存储（ERROR及以上级别）
    4. 自动清理30天前的旧日志
    5. 统一的日志格式：时间戳 | 级别 | 模块名 | 函数名 | 行号 | 消息
    6. 同时输出到控制台和文件

用途:
    适配PyInstaller单EXE部署，避免配置文件路径问题。

作者: VS DataCollector Project
版本: 1.0.0
"""

import os
import sys
import logging
import logging.handlers
import time
from datetime import datetime, timedelta


# ============================================================================
# 第一部分：自定义日志格式化器
# ============================================================================

class DetailedFormatter(logging.Formatter):
    """
    自定义日志格式化器，输出：
    时间戳 | 日志级别 | 模块名 | 函数名 | 行号 | 日志消息
    """
    def __init__(self):
        super().__init__(
            fmt='%(asctime)s | %(levelname)-8s | %(name)-25s | '
                '%(funcName)-20s | %(lineno)-4d | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )


# ============================================================================
# 第二部分：错误日志过滤器
# ============================================================================

class ErrorLevelFilter(logging.Filter):
    """
    日志过滤器：只允许ERROR及以上级别的日志通过。
    用于错误日志文件的独立记录。
    """
    def filter(self, record):
        return record.levelno >= logging.ERROR


# ============================================================================
# 第三部分：旧日志清理器
# ============================================================================

def _clean_old_logs(logs_dir, retention_days=30):
    """
    清理指定目录中超过保留天数的日志文件。

    匹配所有 .log 后缀的文件，删除最后修改时间超过 retention_days 天的文件。

    Args:
        logs_dir (str): 日志目录路径
        retention_days (int): 日志保留天数，默认30天
    """
    if not os.path.exists(logs_dir):
        return

    cutoff_time = time.time() - (retention_days * 86400)  # 86400秒 = 1天
    logger = logging.getLogger(__name__)

    try:
        deleted_count = 0
        for filename in os.listdir(logs_dir):
            if not filename.endswith('.log'):
                continue

            filepath = os.path.join(logs_dir, filename)
            try:
                file_mtime = os.path.getmtime(filepath)
                if file_mtime < cutoff_time:
                    os.remove(filepath)
                    deleted_count += 1
                    logger.info("清理过期日志文件: %s", filename)
            except OSError as e:
                logger.warning("清理日志文件失败 %s: %s", filename, e)

        if deleted_count > 0:
            logger.info("共清理 %d 个过期日志文件", deleted_count)

    except Exception as e:
        logger.error("清理旧日志时发生异常: %s", e)


# ============================================================================
# 第四部分：日志系统初始化
# ============================================================================

def setup_logging(logs_dir, log_level='INFO'):
    """
    初始化全链路日志系统。

    配置说明:
        - 所有配置硬编码在代码中（避免单文件模式下的配置文件路径问题）
        - 全量日志文件: logs/VS_Collector_YYYY-MM-DD.log（每日轮转）
        - 错误日志文件: logs/VS_Collector_ERROR_YYYY-MM-DD.log（每日轮转）
        - 控制台输出: 同步输出到stdout

    Args:
        logs_dir (str): 日志文件存放目录的绝对路径
        log_level (str): 日志级别，可选 DEBUG/INFO/WARNING/ERROR/CRITICAL

    Returns:
        logging.Logger: 根日志器实例
    """
    # 确保logs目录存在
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir, exist_ok=True)

    # 获取根日志器并清空已有处理器（防止重复初始化）
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG)  # 根日志器设为DEBUG，由各处理器控制级别

    # ---- 解析日志级别 ----
    level_map = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL,
    }
    file_level = level_map.get(log_level.upper(), logging.INFO)

    # ---- 创建格式化器 ----
    formatter = DetailedFormatter()

    # ====================================================================
    # 处理器1: 全量日志文件（每日轮转）
    # ====================================================================
    all_log_file = os.path.join(logs_dir, 'VS_Collector.log')

    # 使用TimedRotatingFileHandler实现每日轮转
    all_file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=all_log_file,
        when='midnight',        # 每天午夜轮转
        interval=1,             # 间隔1天
        backupCount=30,         # 保留30天
        encoding='utf-8',
        delay=False,            # 立即创建文件
    )
    # 设置轮转文件的命名格式: VS_Collector_YYYY-MM-DD.log
    all_file_handler.suffix = '%Y-%m-%d.log'
    all_file_handler.namer = lambda name: name.replace('.log', '')  # 去除重复的.log
    # 自定义轮转后的文件名格式
    def all_rotator(source, dest):
        """自定义轮转：将文件名改为 VS_Collector_YYYY-MM-DD.log 格式"""
        import shutil
        # dest 是系统生成的带时间戳的文件名
        shutil.move(source, dest)
    # 使用自定义命名方案，直接用每日轮转处理器
    all_file_handler.setLevel(file_level)
    all_file_handler.setFormatter(formatter)
    root_logger.addHandler(all_file_handler)

    # ====================================================================
    # 处理器2: 错误日志文件（只记录ERROR及以上级别，每日轮转）
    # ====================================================================
    error_log_file = os.path.join(logs_dir, 'VS_Collector_ERROR.log')

    error_file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=error_log_file,
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8',
        delay=False,
    )
    error_file_handler.suffix = '%Y-%m-%d.log'
    error_file_handler.namer = lambda name: name.replace('.log', '')  # 去除重复的.log
    error_file_handler.setLevel(logging.ERROR)  # 只记录ERROR及以上
    error_file_handler.setFormatter(formatter)
    root_logger.addHandler(error_file_handler)

    # ====================================================================
    # 处理器3: 控制台输出
    # ====================================================================
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(file_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # ====================================================================
    # 清理30天前的旧日志
    # ====================================================================
    _clean_old_logs(logs_dir, retention_days=30)

    # 记录日志系统启动信息
    root_logger.info("=" * 60)
    root_logger.info("全链路日志系统初始化完成")
    root_logger.info("  日志目录: %s", logs_dir)
    root_logger.info("  日志级别: %s", log_level)
    root_logger.info("  全量日志: %s", all_log_file)
    root_logger.info("  错误日志: %s", error_log_file)
    root_logger.info("=" * 60)

    return root_logger


def get_module_logger(name):
    """
    获取模块级别的日志器。
    各业务模块通过此函数获取logger，确保日志来源清晰。

    用法:
        from logger_config import get_module_logger
        logger = get_module_logger(__name__)

    Args:
        name (str): 模块名，通常传入 __name__

    Returns:
        logging.Logger: 模块日志器
    """
    return logging.getLogger(name)


# ============================================================================
# 第五部分：便捷日志记录辅助函数
# ============================================================================

def log_tcp_event(logger, event_type, udid, message, extra=None):
    """
    记录TCP相关事件的便捷函数。

    Args:
        logger: 日志器实例
        event_type (str): 事件类型，如 CONNECT/DISCONNECT/DATA_RECV/CMD_SEND/CMD_REPLY
        udid (str): 设备UDID
        message (str): 事件描述
        extra (dict, optional): 额外信息
    """
    log_msg = f"[TCP:{event_type}] [UDID:{udid}] {message}"
    if extra:
        log_msg += f" | Extra: {extra}"
    logger.info(log_msg)


def log_parse_event(logger, event_type, udid, protocol, message):
    """
    记录协议解析事件的便捷函数。

    Args:
        logger: 日志器实例
        event_type (str): 事件类型
        udid (str): 设备UDID
        protocol (str): 协议类型
        message (str): 事件描述
    """
    logger.info("[PARSE:%s] [UDID:%s] [PROTO:%s] %s",
                event_type, udid, protocol, message)


def log_calc_event(logger, event_type, udid, channel, message):
    """
    记录振弦解算事件的便捷函数。

    Args:
        logger: 日志器实例
        event_type (str): 事件类型
        udid (str): 设备UDID
        channel (int): 通道号
        message (str): 事件描述
    """
    logger.info("[CALC:%s] [UDID:%s] [CH:%d] %s",
                event_type, udid, channel, message)


def log_db_event(logger, event_type, message):
    """
    记录数据库事件的便捷函数。

    Args:
        logger: 日志器实例
        event_type (str): 事件类型
        message (str): 事件描述
    """
    logger.info("[DB:%s] %s", event_type, message)


def log_cmd_event(logger, event_type, udid, command, message):
    """
    记录指令管控事件的便捷函数。

    Args:
        logger: 日志器实例
        event_type (str): 事件类型
        udid (str): 设备UDID
        command (str): 指令内容
        message (str): 事件描述
    """
    logger.info("[CMD:%s] [UDID:%s] [CMD:%s] %s",
                event_type, udid, command, message)


# ============================================================================
# 第六部分：模块自检
# ============================================================================

if __name__ == '__main__':
    import tempfile

    # 创建临时日志目录进行测试
    test_dir = os.path.join(tempfile.gettempdir(), 'vs_logger_test')
    os.makedirs(test_dir, exist_ok=True)

    # 初始化日志系统
    setup_logging(test_dir, 'DEBUG')

    # 获取测试日志器
    test_logger = get_module_logger('test_module')

    # 测试各级别日志
    print("\n" + "=" * 60)
    print("logger_config.py 模块自检")
    print("=" * 60)

    test_logger.debug("这是一条DEBUG测试日志 | 参数: %d", 42)
    test_logger.info("这是一条INFO测试日志 | 参数: %s", "hello")
    test_logger.warning("这是一条WARNING测试日志")
    test_logger.error("这是一条ERROR测试日志 | 错误码: %d", 500)
    test_logger.critical("这是一条CRITICAL测试日志 | 系统即将关闭")

    # 测试便捷函数
    log_tcp_event(test_logger, 'CONNECT', 'VS001', '设备已连接', {'ip': '192.168.1.100'})
    log_parse_event(test_logger, 'SUCCESS', 'VS001', 'STR2.0', '解析成功')
    log_calc_event(test_logger, 'SUCCESS', 'VS001', 1, '解算完成 F=123.45')
    log_db_event(test_logger, 'INSERT', '批量入库 10 条记录')
    log_cmd_event(test_logger, 'SEND', 'VS001', '@SETM', '进入设置模式')

    print(f"\n日志文件已生成在: {test_dir}")
    for f in sorted(os.listdir(test_dir)):
        print(f"  - {f}")

    print("\n自检完成!")
