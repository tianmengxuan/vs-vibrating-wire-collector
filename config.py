# -*- coding: utf-8 -*-
"""
config.py - 路径适配与配置管理模块
=====================================
功能:
    1. 获取exe真实运行目录（PyInstaller单文件模式兼容）
    2. 自动创建logs目录
    3. 配置文件加载：优先读取exe同目录下的config.env，不存在则生成默认配置
    4. 内置默认配置，无配置文件也能启动

用途:
    适配单EXE部署场景，确保日志、配置文件生成在exe同目录下，
    而非PyInstaller解压的临时目录。

作者: VS DataCollector Project
版本: 1.0.0
"""

import os
import sys
import logging
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)


# ============================================================================
# 第一部分：路径适配（PyInstaller单文件模式核心）
# ============================================================================

def get_exe_dir():
    """
    获取exe文件所在的真实目录。

    PyInstaller单文件模式（--onefile）会将exe解压到临时目录（sys._MEIPASS）运行，
    而sys.executable指向实际的exe文件路径。
    因此需要根据运行模式判断：
        - 打包模式：使用 os.path.dirname(sys.executable) 获取exe所在目录
        - 脚本模式：使用 os.path.dirname(os.path.abspath(__file__)) 获取脚本目录

    Returns:
        str: exe文件所在目录的绝对路径
    """
    # PyInstaller打包后，sys.frozen为True，_MEIPASS为临时解压目录
    if getattr(sys, 'frozen', False):
        # 单文件打包模式：sys.executable是exe的实际路径
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        # Python脚本模式：使用当前文件的目录
        exe_dir = os.path.dirname(os.path.abspath(__file__))

    return exe_dir


def get_logs_dir():
    """
    获取logs日志目录的绝对路径。
    自动创建目录（如果不存在）。

    Returns:
        str: logs目录的绝对路径
    """
    exe_dir = get_exe_dir()
    logs_dir = os.path.join(exe_dir, 'logs')
    _ensure_dir(logs_dir)
    return logs_dir


def _ensure_dir(dir_path):
    """
    创建目录（如果不存在）。

    Args:
        dir_path (str): 目标目录路径
    """
    if not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)


# ============================================================================
# 第二部分：配置文件管理
# ============================================================================

# 内置默认配置（硬编码，确保无config.env也能启动）
_DEFAULT_CONFIG = {
    # ---- 数据库配置 ----
    'DB_HOST': '127.0.0.1',
    'DB_PORT': '3306',
    'DB_USER': 'root',
    'DB_PASSWORD': 'jinpu123!@#',
    'DB_NAME': 'rtu',
    'DB_CHARSET': 'utf8mb4',

    # ---- TCP服务配置 ----
    'TCP_HOST': '0.0.0.0',       # 监听所有网卡
    'TCP_PORT': '9000',           # 默认监听端口
    'TCP_MAX_CONNECTIONS': '100', # 最大并发连接数

    # ---- 数据解算配置 ----
    'CALC_DEFAULT_K': '1.0',      # 默认K系数（无标定参数时使用）
    'CALC_DEFAULT_F0': '0.0',     # 默认初始频率
    'CALC_DEFAULT_ALPHA': '0.0',  # 默认温度系数
    'CALC_DEFAULT_T0': '20.0',    # 默认初始温度(℃)

    # ---- 日志配置 ----
    'LOG_LEVEL': 'INFO',          # 日志级别: DEBUG/INFO/WARNING/ERROR/CRITICAL
    'LOG_RETENTION_DAYS': '30',   # 日志保留天数

    # ---- 指令超时配置 ----
    'CMD_TIMEOUT': '15',          # 指令应答超时(秒)
    'CMD_MAX_RETRIES': '3',       # 指令最大重试次数
}


def _get_config_file_path():
    """
    获取config.env配置文件的完整路径（在exe同目录下）。

    Returns:
        str: config.env文件路径
    """
    exe_dir = get_exe_dir()
    return os.path.join(exe_dir, 'config.env')


def save_default_config():
    """
    在exe同目录下生成默认config.env配置文件。

    仅在config.env不存在时生成，避免覆盖用户修改的配置。
    文件使用UTF-8编码，包含所有配置项的中文注释。

    Returns:
        str: 生成的配置文件路径，若已存在则返回None
    """
    config_path = _get_config_file_path()

    if os.path.exists(config_path):
        logger.info("配置文件已存在，跳过生成: %s", config_path)
        return None

    # 生成带详细中文注释的配置文件
    config_content = """# ============================================================
# VS振弦数据采集器 - 配置文件
# ============================================================
# 说明：修改此文件后，重新启动程序即可生效。
#       若不修改，程序将使用下方的默认值运行。
#       文件编码必须为 UTF-8。
# ============================================================

# ---- 数据库配置 ----
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=root
DB_NAME=rtu
DB_CHARSET=utf8mb4

# ---- TCP服务配置 ----
TCP_HOST=0.0.0.0
TCP_PORT=9000
TCP_MAX_CONNECTIONS=100

# ---- 日志配置 ----
LOG_LEVEL=INFO
LOG_RETENTION_DAYS=30

# ---- 指令超时配置 ----
CMD_TIMEOUT=15
CMD_MAX_RETRIES=3
"""

    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(config_content.strip())
        logger.info("已生成默认配置文件: %s", config_path)
        return config_path
    except OSError as e:
        logger.error("生成配置文件失败: %s", e)
        return None


def load_config():
    """
    加载配置：优先读取exe同目录下的config.env，不存在则使用内置默认配置。

    首次运行时config.env不存在，会先尝试生成默认配置文件。
    后续运行用户可修改config.env中的参数。

    Returns:
        dict: 配置字典，所有值为字符串类型

    Note:
        即使config.env读取失败（如编码问题），也会回退到默认配置，
        确保程序始终可以启动。
    """
    config = dict(_DEFAULT_CONFIG)  # 复制一份默认配置

    # 尝试读取config.env
    config_path = _get_config_file_path()

    if not os.path.exists(config_path):
        logger.info("配置文件不存在，正在生成默认配置...")
        save_default_config()
        logger.info("使用内置默认配置启动")
        return config

    # 解析config.env文件（支持空行和注释）
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()

                # 跳过空行和注释行（#开头）
                if not line or line.startswith('#'):
                    continue

                # 解析 KEY=VALUE 格式
                if '=' not in line:
                    logger.warning("配置文件第%d行格式错误（缺少=）: %s", line_no, line)
                    continue

                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()

                if key in config:
                    config[key] = value
                    logger.debug("加载配置项: %s = %s", key,
                                 value if 'PASSWORD' not in key.upper() else '***')

        logger.info("配置文件加载成功: %s (共 %d 项)", config_path, len(config))

    except UnicodeDecodeError as e:
        logger.error("配置文件编码错误（不是UTF-8）: %s, 使用默认配置", e)
        config = dict(_DEFAULT_CONFIG)
    except OSError as e:
        logger.error("读取配置文件失败: %s, 使用默认配置", e)
        config = dict(_DEFAULT_CONFIG)

    return config


def save_config(config):
    """
    将当前配置写回 config.env 文件（持久化）。
    保留原有注释行，仅更新 KEY=VALUE 部分。

    Args:
        config (dict): 当前配置字典

    Returns:
        bool: 写入是否成功
    """
    config_path = _get_config_file_path()

    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                old_lines = f.readlines()
            updated = []
            replaced_keys = set()
            for line in old_lines:
                stripped = line.strip()
                if stripped and not stripped.startswith('#') and '=' in stripped:
                    key = stripped.split('=', 1)[0].strip()
                    if key in config:
                        updated.append(f"{key}={config[key]}\n")
                        replaced_keys.add(key)
                        continue
                updated.append(line)
            for k, v in config.items():
                if k not in replaced_keys:
                    updated.append(f"{k}={v}\n")
        else:
            updated = [
                "# VS振弦数据采集器 - 配置文件\n",
                "# 修改后重启程序生效\n\n",
            ]
            for k, v in config.items():
                updated.append(f"{k}={v}\n")

        with open(config_path, 'w', encoding='utf-8') as f:
            f.writelines(updated)
        logger.info("配置已保存: %s (%d项)", config_path, len(config))
        return True
    except OSError as e:
        logger.error("保存配置文件失败: %s", e)
        return False


def get_config_value(config, key, default=None):
    """
    安全获取配置值，带类型转换的便捷方法。

    Args:
        config (dict): 配置字典
        key (str): 配置键名
        default: 默认值

    Returns:
        str: 配置值（字符串类型）
    """
    return config.get(key, default)


def get_config_int(config, key, default=0):
    """
    获取整型配置值。

    Args:
        config (dict): 配置字典
        key (str): 配置键名
        default (int): 默认值

    Returns:
        int: 整型配置值

    Raises:
        ValueError: 如果值无法转换为整数，返回默认值并记录警告
    """
    try:
        return int(config.get(key, str(default)))
    except (ValueError, TypeError):
        logger.warning("配置项 %s 的值 '%s' 无法转换为整数，使用默认值 %d",
                       key, config.get(key), default)
        return default


# ============================================================================
# 第三部分：数据库连接字符串构建
# ============================================================================

def get_database_url(config):
    """
    根据配置构建SQLAlchemy数据库连接URL。

    Args:
        config (dict): 配置字典

    Returns:
        str: MySQL连接URL，格式为 mysql+pymysql://user:pass@host:port/db?charset=utf8mb4
    """
    db_host = config.get('DB_HOST', '127.0.0.1')
    db_port = config.get('DB_PORT', '3306')
    db_user = config.get('DB_USER', 'root')
    db_password = config.get('DB_PASSWORD', 'jinpu123!@#')
    db_name = config.get('DB_NAME', 'rtu')
    db_charset = config.get('DB_CHARSET', 'utf8mb4')

    # 构建SQLAlchemy连接URL
    # 对密码进行URL编码（防止特殊字符如 @ # 导致URL解析错误）
    encoded_password = quote_plus(db_password)
    url = (
        f"mysql+pymysql://{db_user}:{encoded_password}"
        f"@{db_host}:{db_port}/{db_name}"
        f"?charset={db_charset}"
    )
    return url


# ============================================================================
# 第四部分：模块自检（仅用于调试）
# ============================================================================

if __name__ == '__main__':
    # 配置基本日志以便自检
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s | %(lineno)d | %(message)s'
    )

    print("=" * 60)
    print("config.py 模块自检")
    print("=" * 60)

    # 1. 路径测试
    exe_dir = get_exe_dir()
    print(f"exe运行目录: {exe_dir}")
    print(f"logs目录:    {get_logs_dir()}")

    # 2. 配置加载测试
    config = load_config()
    print(f"\n当前配置 (共{len(config)}项):")
    for k, v in config.items():
        display_v = '***' if 'PASSWORD' in k.upper() else v
        print(f"  {k} = {display_v}")

    # 3. 数据库URL测试
    db_url = get_database_url(config)
    # 隐藏密码
    safe_url = db_url
    if '@' in db_url:
        parts = db_url.split('@')
        if ':' in parts[0]:
            auth_parts = parts[0].rsplit(':', 1)
            safe_url = f"{auth_parts[0]}:****@{parts[1]}"
    print(f"\n数据库URL: {safe_url}")

    print("\n自检完成!")
