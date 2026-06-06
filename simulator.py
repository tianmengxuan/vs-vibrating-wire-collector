# -*- coding: utf-8 -*-
"""
simulator.py - 模拟设备测试工具（可选）
========================================
功能:
    模拟VS振弦传感器设备向TCP服务端上报数据。
    用于测试主程序的完整业务链路，无需真实设备。

用法:
    python simulator.py --host 127.0.0.1 --port 9000 --count 10 --interval 5

支持模拟的协议格式:
    - STR2.0 格式
    - STR3.0 格式（含BATV=）
    - 补发数据（含@时间戳）
    - 多设备并发测试

作者: VS DataCollector Project
版本: 1.0.0
"""

import socket
import time
import random
import argparse
import threading
from datetime import datetime, timedelta


# ============================================================================
# 模拟数据生成
# ============================================================================

def generate_str20_data(udid):
    """
    生成 STR2.0 格式的模拟数据。

    Args:
        udid (str): 设备UDID

    Returns:
        str: 模拟数据帧
    """
    channels = []
    for ch in range(1, 5):
        freq = random.uniform(1200, 1800)
        channels.append(f"CH{ch}={freq:.1f}")

    temp = random.uniform(20, 30)
    volt = random.uniform(11.5, 13.0)
    signal = random.randint(20, 31)

    data = f"{udid}>{','.join(channels)},TEMP={temp:.1f},VOLT={volt:.1f},SIGNAL={signal}\r\n"
    return data


def generate_str30_data(udid):
    """
    生成 STR3.0 格式的模拟数据（含BATV=）。

    Args:
        udid (str): 设备UDID

    Returns:
        str: 模拟数据帧
    """
    channels = []
    for ch in range(1, 5):
        freq = random.uniform(1200, 1800)
        temp = random.uniform(20, 30)
        freq_module = (freq * freq) / 1000.0
        thermistor_r = random.uniform(10000, 12000)
        channels.append(f"CH{ch}={freq:.1f},T{ch}={temp:.1f},M{ch}={freq_module:.1f},R{ch}={thermistor_r:.0f}")

    batv = random.uniform(11.5, 13.0)
    solarv = random.uniform(12.0, 14.5) if random.random() > 0.3 else 0
    devtemp = random.uniform(20, 35)
    signal = random.randint(20, 31)

    data = (
        f"{udid}>{','.join(channels)},"
        f"BATV={batv:.1f},SOLARV={solarv:.1f},"
        f"TEMP={devtemp:.1f},SIGNAL={signal},MODE=AUTO\r\n"
    )
    return data


def generate_retransmit_data(udid, original_time):
    """
    生成带补发标识的模拟数据。

    Args:
        udid (str): 设备UDID
        original_time (datetime): 原始采集时间

    Returns:
        str: 补发数据帧
    """
    base_data = generate_str20_data(udid).rstrip('\r\n')
    time_str = original_time.strftime('%Y-%m-%d %H:%M:%S')
    return f"{base_data}@{time_str}\r\n"


# ============================================================================
# 模拟设备客户端
# ============================================================================

class SimulatedDevice:
    """
    模拟设备客户端。

    每个实例模拟一个设备，定时向服务端发送数据。
    """

    def __init__(self, udid, host='127.0.0.1', port=9000,
                 interval=5, protocol='STR2.0', retransmit_rate=0.1):
        """
        初始化模拟设备。

        Args:
            udid (str): 设备UDID
            host (str): 服务端地址
            port (int): 服务端端口
            interval (int): 上报间隔(秒)
            protocol (str): 协议格式 STR2.0/STR3.0
            retransmit_rate (float): 补发数据概率(0-1)
        """
        self.udid = udid
        self.host = host
        self.port = port
        self.interval = interval
        self.protocol = protocol
        self.retransmit_rate = retransmit_rate
        self.sock = None
        self.running = False
        self.send_count = 0

    def connect(self):
        """建立TCP连接"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(10)
            self.sock.connect((self.host, self.port))
            print(f"[{self.udid}] 已连接到 {self.host}:{self.port}")
            return True
        except Exception as e:
            print(f"[{self.udid}] 连接失败: {e}")
            return False

    def disconnect(self):
        """断开TCP连接"""
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        print(f"[{self.udid}] 已断开连接（共发送 {self.send_count} 条数据）")

    def run(self, count=None):
        """
        运行模拟设备（阻塞模式）。

        Args:
            count (int or None): 发送数据条数，None为无限发送
        """
        if not self.connect():
            return

        self.running = True
        sent = 0

        try:
            while self.running:
                # 决定是否发送补发数据
                if random.random() < self.retransmit_rate:
                    # 补发数据：原始采集时间为1-10分钟前
                    original_time = datetime.now() - timedelta(
                        minutes=random.randint(1, 10),
                        seconds=random.randint(0, 59)
                    )
                    data = generate_retransmit_data(self.udid, original_time)
                    data_type = "补发"
                else:
                    if self.protocol == 'STR3.0':
                        data = generate_str30_data(self.udid)
                    else:
                        data = generate_str20_data(self.udid)
                    data_type = "实时"

                # 发送数据
                try:
                    self.sock.sendall(data.encode('ascii'))
                    self.send_count += 1
                    sent += 1
                    now = datetime.now().strftime('%H:%M:%S')
                    print(f"[{now}] [{self.udid}] 发送{data_type}数据 #{sent}: {data[:60].strip()}...")

                    # 尝试接收响应（如有）
                    try:
                        self.sock.settimeout(0.5)
                        resp = self.sock.recv(1024)
                        if resp:
                            print(f"[{self.udid}] 收到响应: {resp.decode('ascii', errors='ignore')[:100]}")
                    except socket.timeout:
                        pass

                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                    print(f"[{self.udid}] 连接断开，尝试重连...")
                    self.disconnect()
                    time.sleep(3)
                    if not self.connect():
                        print(f"[{self.udid}] 重连失败，停止发送")
                        break

                # 检查发送条数限制
                if count is not None and sent >= count:
                    print(f"[{self.udid}] 已完成 {count} 条数据发送")
                    break

                # 等待下次发送
                time.sleep(self.interval)

        except KeyboardInterrupt:
            print(f"[{self.udid}] 收到中断信号")
        finally:
            self.disconnect()

    def stop(self):
        """停止模拟设备"""
        self.running = False


# ============================================================================
# 命令行入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='VS振弦数据采集器 - 模拟设备测试工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单设备测试，发送10条数据
  python simulator.py --count 10

  # 多设备并发测试
  python simulator.py --devices VS001,VS002,VS003 --count 20 --interval 3

  # 连接远程服务端
  python simulator.py --host 192.168.1.100 --port 9000

  # STR3.0协议测试
  python simulator.py --protocol STR3.0
        """
    )
    parser.add_argument('--host', default='127.0.0.1', help='服务端地址 (默认: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=9000, help='服务端端口 (默认: 9000)')
    parser.add_argument('--devices', default='VS_SIM_001', help='设备UDID列表，逗号分隔 (默认: VS_SIM_001)')
    parser.add_argument('--count', type=int, default=None, help='每设备发送数据条数 (默认: 无限)')
    parser.add_argument('--interval', type=int, default=5, help='发送间隔秒数 (默认: 5)')
    parser.add_argument('--protocol', choices=['STR2.0', 'STR3.0'], default='STR2.0',
                       help='协议格式 (默认: STR2.0)')
    parser.add_argument('--retransmit', type=float, default=0.1,
                       help='补发数据概率 0-1 (默认: 0.1)')

    args = parser.parse_args()

    # 解析设备列表
    devices = [d.strip() for d in args.devices.split(',') if d.strip()]

    print("=" * 60)
    print("模拟设备测试工具")
    print("=" * 60)
    print(f"  服务端: {args.host}:{args.port}")
    print(f"  设备数: {len(devices)} ({', '.join(devices)})")
    print(f"  协议:   {args.protocol}")
    print(f"  间隔:   {args.interval}秒")
    print(f"  条数:   {'无限' if args.count is None else args.count}")
    print(f"  补发率: {args.retransmit * 100:.0f}%")
    print("=" * 60)

    # 创建并启动模拟设备
    threads = []
    for udid in devices:
        sim = SimulatedDevice(
            udid=udid,
            host=args.host,
            port=args.port,
            interval=args.interval,
            protocol=args.protocol,
            retransmit_rate=args.retransmit,
        )
        t = threading.Thread(target=sim.run, args=(args.count,), daemon=True)
        threads.append((sim, t))
        t.start()
        time.sleep(0.5)  # 错开启动时间

    # 等待所有线程完成
    try:
        for _, t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\n所有模拟设备已停止")

    print("\n测试完成！")


if __name__ == '__main__':
    main()
