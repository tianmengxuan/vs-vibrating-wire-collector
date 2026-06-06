# -*- coding: utf-8 -*-
"""
main.py - VS振弦数据采集器 主程序入口
=======================================
功能:
    1. 路径适配（PyInstaller单EXE兼容）
    2. 自动创建logs目录
    3. 配置加载（config.env → 内置默认）
    4. 全链路日志初始化
    5. 数据库连接（失败不崩溃，等待重试）
    6. TCP服务端启动
    7. 完整业务链路: 设备接入→数据接收→粘包拆包→协议解析→振弦解算→数据入库
    8. 优雅停机: Ctrl+C / Windows关闭事件

运行方式:
    python main.py
    或
    VS振弦数据采集器.exe（打包后双击运行）

作者: VS DataCollector Project
版本: 1.0.0
"""

import os
import sys
import time
import asyncio
import signal
import logging

# ============================================================================
# 初始步骤：在导入其他模块前先确定工作目录
# ============================================================================

def _get_exe_dir():
    """
    获取exe文件所在的真实目录。

    必须在任何模块导入之前调用，确保后续的日志、配置
    路径都基于exe真实目录而非PyInstaller临时目录。

    Returns:
        str: exe文件所在目录
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller单文件模式: sys.executable 是实际exe路径
        return os.path.dirname(os.path.abspath(sys.executable))
    else:
        # Python脚本模式: __file__ 是脚本路径
        return os.path.dirname(os.path.abspath(__file__))


# 获取exe目录（在任何其他操作之前）
EXE_DIR = _get_exe_dir()

# 将exe目录添加到Python路径（确保能找到包内其他模块）
if EXE_DIR not in sys.path:
    sys.path.insert(0, EXE_DIR)

# 切换到exe所在目录（保证相对路径正确）
os.chdir(EXE_DIR)

# ============================================================================
# 模块导入
# ============================================================================

from config import get_exe_dir, get_logs_dir, load_config, save_default_config, get_database_url, get_config_int
from logger_config import setup_logging, get_module_logger
from database import DatabaseManager
from calculator import VibratingWireCalculator
from tcp_server import TCPServer


# ============================================================================
# 全局变量
# ============================================================================

logger = None           # 根日志器
config = None           # 配置字典
db_manager = None       # 数据库管理器
tcp_server = None       # TCP服务端
calculator = None       # 振弦解算器
shutdown_event = None   # 停机信号


# ============================================================================
# 优雅停机处理
# ============================================================================

def _signal_handler(signum, frame):
    """信号处理函数（Ctrl+C 或 Windows关闭事件）"""
    global shutdown_event
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else f"信号{signum}"
    print(f"\n收到停机信号: {sig_name}")
    if logger:
        logger.info("收到停机信号: %s, 开始优雅停机...", sig_name)
    if shutdown_event:
        shutdown_event.set()


async def _graceful_shutdown():
    """
    优雅停机流程。

    按顺序执行:
        1. 停止TCP服务端（关闭所有设备连接）
        2. 断开数据库连接
        3. 记录停机日志
    """
    global tcp_server, db_manager, logger

    logger.info("=" * 60)
    logger.info("正在执行优雅停机...")
    logger.info("=" * 60)

    # 步骤1: 停止TCP服务端
    if tcp_server and tcp_server.is_running:
        logger.info("[1/2] 正在停止TCP服务端...")
        try:
            await tcp_server.stop()
            logger.info("[1/2] TCP服务端已停止")
        except Exception as e:
            logger.error("[1/2] 停止TCP服务端异常: %s", e)

    # 步骤2: 断开数据库连接
    if db_manager and db_manager.is_connected():
        logger.info("[2/2] 正在断开数据库连接...")
        try:
            db_manager.disconnect()
            logger.info("[2/2] 数据库连接已断开")
        except Exception as e:
            logger.error("[2/2] 断开数据库异常: %s", e)

    logger.info("优雅停机完成。再见！")
    logger.info("=" * 60)


# ============================================================================
# 主程序入口
# ============================================================================

async def main():
    """
    主程序异步入口。

    完整的启动流程:
        1. 路径适配
        2. 创建logs目录
        3. 加载/生成配置文件
        4. 初始化日志系统
        5. 记录启动信息
        6. 初始化数据库
        7. 初始化TCP服务端
        8. 进入事件循环等待
    """
    global logger, config, db_manager, tcp_server, calculator, shutdown_event

    # ================================================================
    # 步骤1: 路径适配
    # ================================================================
    exe_dir = get_exe_dir()
    print(f"工作目录: {exe_dir}")

    # ================================================================
    # 步骤2: 创建logs目录
    # ================================================================
    logs_dir = get_logs_dir()
    print(f"日志目录: {logs_dir}")

    # ================================================================
    # 步骤3: 加载/生成配置文件
    # ================================================================
    config = load_config()

    # ================================================================
    # 步骤4: 初始化日志系统
    # ================================================================
    log_level = config.get('LOG_LEVEL', 'INFO')
    setup_logging(logs_dir, log_level)
    logger = get_module_logger('MAIN')

    # ================================================================
    # 步骤5: 记录启动信息
    # ================================================================
    logger.info("=" * 60)
    logger.info("VS振弦数据采集器 v1.0.0 启动")
    logger.info("=" * 60)
    logger.info("运行环境:")
    logger.info("  Python: %s", sys.version.split()[0])
    logger.info("  exe目录: %s", exe_dir)
    logger.info("  日志目录: %s", logs_dir)
    logger.info("  是否打包: %s", '是' if getattr(sys, 'frozen', False) else '否（脚本模式）')
    logger.info("  配置文件: %s", os.path.join(exe_dir, 'config.env'))

    # 隐藏密码后输出配置
    logger.info("当前配置:")
    for k, v in config.items():
        display_v = '***' if 'PASSWORD' in k.upper() else v
        logger.info("  %s = %s", k, display_v)

    # ================================================================
    # 步骤6: 初始化数据库
    # ================================================================
    db_url = get_database_url(config)
    # 隐藏密码的URL用于日志
    safe_url = db_url
    if '@' in db_url:
        parts = db_url.split('@')
        if ':' in parts[0]:
            auth_parts = parts[0].rsplit(':', 1)
            safe_url = f"{auth_parts[0]}:****@{parts[1]}"

    logger.info("正在连接数据库: %s", safe_url)

    db_manager = DatabaseManager(
        database_url=db_url,
        pool_size=10,       # 长连接复用：保持 10 个连接，支撑多设备并发写入
        pool_recycle=1800,  # 30分钟自动刷新连接，防止 MySQL wait_timeout
    )

    db_connected = db_manager.connect()
    if db_connected:
        logger.info("数据库连接成功（长连接复用模式: pool_size=%d, pool_pre_ping=True）", db_manager._pool_size)
        # 初始化表结构
        if db_manager.init_tables():
            logger.info("数据库表结构已就绪")
    else:
        logger.warning("数据库连接失败！程序将继续运行，等待数据库可用后自动恢复。")

    # ================================================================
    # 步骤7: 初始化振弦解算器
    # ================================================================
    calculator = VibratingWireCalculator(db_manager)
    logger.info("振弦解算器已初始化")

    # ================================================================
    # 步骤8: 初始化并启动TCP服务端
    # ================================================================
    tcp_host = config.get('TCP_HOST', '0.0.0.0')
    tcp_port = get_config_int(config, 'TCP_PORT', 9000)

    logger.info("正在启动TCP服务: %s:%d", tcp_host, tcp_port)

    tcp_server = TCPServer(
        host=tcp_host,
        port=tcp_port,
        db_manager=db_manager,
        calculator=calculator,
    )

    tcp_started = await tcp_server.start()
    if not tcp_started:
        logger.critical("TCP服务启动失败，程序退出")
        return 1

    logger.info("所有模块初始化完成，进入运行状态。")
    logger.info("等待设备连接...")

    # ================================================================
    # 步骤9: 注册信号处理 & 进入事件循环
    # ================================================================
    shutdown_event = asyncio.Event()

    # Windows下使用signal处理
    try:
        loop = asyncio.get_running_loop()
        # 注册 SIGINT (Ctrl+C) 和 SIGTERM
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: _signal_handler(s, None))
            except NotImplementedError:
                # Windows 不支持 add_signal_handler for SIGTERM
                pass
    except Exception as e:
        logger.warning("注册信号处理失败: %s (Windows环境可忽略)", e)

    # 等待停机信号
    try:
        # 长连接复用模式下，pool_pre_ping 自动检测断连并重建，无需手动保活
        # 仅在初始连接失败时定期重试
        db_retry_interval = 30  # 初始连接失败时，每30秒重试

        while not shutdown_event.is_set():
            # 如果数据库引擎未就绪（初始连接失败），尝试重连
            if db_manager and not db_manager.is_connected():
                logger.info("尝试连接数据库...")
                if db_manager.connect():
                    logger.info("数据库连接成功!")
                    db_manager.init_tables()

            # 在线设备统计
            online_count = len(tcp_server.device_sessions) if tcp_server else 0
            if online_count > 0:
                logger.debug("当前在线设备: %d", online_count)

            # 等待停机信号或超时
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=db_retry_interval)
                break  # 收到停机信号
            except asyncio.TimeoutError:
                pass  # 超时，继续循环检查

    except KeyboardInterrupt:
        logger.info("收到键盘中断信号(Ctrl+C)")
    except Exception as e:
        logger.error("主循环异常: %s", e, exc_info=True)
    finally:
        await _graceful_shutdown()

    return 0


# ============================================================================
# 程序入口点
# ============================================================================

def _run_console():
    """控制台模式入口"""
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code or 0)
    except KeyboardInterrupt:
        print("\n程序已被用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n程序异常终止: {e}")
        if logger:
            logger.critical("程序异常终止: %s", e, exc_info=True)
        sys.exit(1)


def _run_gui():
    """GUI模式入口 - 在初始化基础设施后启动GUI"""
    global config, db_manager

    # 步骤1-6: 初始化基础设施（与console模式共用）
    exe_dir = get_exe_dir()
    logs_dir = get_logs_dir()

    config = load_config()

    log_level = config.get('LOG_LEVEL', 'INFO')
    setup_logging(logs_dir, log_level)

    # 全局logger引用
    global logger
    import logging as _logging
    logger = _logging.getLogger('MAIN')

    logger.info("=" * 60)
    logger.info("VS振弦数据采集器 v4.1 启动 (GUI模式)")
    logger.info("=" * 60)
    logger.info("exe目录: %s", exe_dir)
    logger.info("日志目录: %s", logs_dir)

    # 初始化数据库
    db_url = get_database_url(config)
    db_manager = DatabaseManager(
        database_url=db_url,
        pool_size=10,       # 长连接复用：保持 10 个连接，支撑多设备并发写入
        pool_recycle=1800,  # 30分钟自动刷新连接，防止 MySQL wait_timeout
    )
    if db_manager.connect():
        logger.info("数据库连接成功（长连接复用模式: pool_size=%d, pool_pre_ping=True）", db_manager._pool_size)
        db_manager.init_tables()
        logger.info("数据库表结构已就绪")
    else:
        logger.warning("数据库连接失败，GUI仍可启动但数据不会入库")

    # 启动GUI（GUI内部管理TCP服务）
    from gui import run_gui
    try:
        run_gui(config, db_manager)
    except Exception as e:
        logger.critical("GUI启动异常: %s", e, exc_info=True)
        # 弹窗提示用户
        try:
            import tkinter.messagebox as mb
            mb.showerror("启动失败", f"GUI 启动异常:\n{e}\n\n请查看日志文件获取详情。")
        except:
            pass
        sys.exit(1)


if __name__ == '__main__':
    """
    程序启动入口。

    运行模式:
        - 默认（双击/无参数）: GUI图形界面模式
        - --console: 纯控制台模式（适合无桌面环境的服务器）

    示例:
        VS振弦数据采集器.exe            # GUI模式（默认）
        VS振弦数据采集器.exe --console  # 控制台模式
    """
    import argparse

    parser = argparse.ArgumentParser(description='VS振弦数据采集器')
    parser.add_argument('--console', action='store_true', help='以控制台模式运行（默认GUI模式）')
    args = parser.parse_args()

    if args.console:
        _run_console()
    else:
        _run_gui()
