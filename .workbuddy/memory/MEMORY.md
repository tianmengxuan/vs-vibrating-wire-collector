# 稳控解算软件 - 长期记忆

## 项目概述
- **项目名称**: VS振弦数据采集器
- **目标**: 河北稳控科技VS系列振弦传感器无线采发仪数据采集解算软件
- **协议**: 稳控科技监测设备通用通讯协议接口说明 V1.2.0
- **部署**: Windows单EXE，PyInstaller打包
- **数据库**: MySQL 8.0 + SQLAlchemy ORM
- **语言**: Python 3.8+

## 技术约定
- 所有代码符合PEP8规范，带中文注释和docstring
- 路径适配: PyInstaller单文件模式使用 sys.executable 获取exe真实目录
- 日志: logs/VS_Collector_YYYY-MM-DD.log + 独立ERROR日志
- 指令: 下发必须追加 "#\r\n" 结束符
- RS485安全: 禁止 0xFF 通用地址写参数
- CRC16-MODBUS: 逐位算法 + 大端字节序返回

## 文件结构（23个文件）
```
main.py                    # 主程序入口
config.py                  # 路径适配+配置管理
logger_config.py           # 日志系统
database/                  # 数据库层
  __init__.py, models.py, crud.py, schema.sql
tcp_server/                # TCP服务端
  __init__.py, server.py, session.py
protocol/                  # 协议解析
  __init__.py, checksum.py, parser.py, dispatcher.py
calculator/                # 振弦解算
  __init__.py, vw_calculator.py
remote_config/             # 指令管控
  __init__.py, commands.py
simulator.py               # 模拟设备测试
VS_DataCollector.spec      # PyInstaller打包配置
build.bat                  # 一键打包脚本
requirements.txt           # 依赖文件
README.md                  # 使用说明
```

## 关键设计决策
- 数据库连接失败不崩溃，定期重连
- TCP使用asyncio实现高并发
- 粘包拆包按协议规则识别帧边界
- 振弦解算公式: F = K × (f² - f0²) + α×(T - T0)
- 热敏电阻转温度: B参数简化方程
- 指令: 独立队列+超时重试(最多3次)

## data_sensor 表（2026-05-07 已实现）
- **表名**: data_sensor
- **用途**: 向第三方系统同步传感器解算数据
- **ORM**: models.py 第9张表 DataSensor 类
- **写入时机**: server.py 步骤5.1（本地）+ 5.2b（远程）
- **写入方法**: crud.py → `batch_insert_data_sensor()` 写本地；remote_sync.py → `batch_write_data_sensor()` 写远程

## 远程同步（2026-05-07）
- **目标**: 101.37.18.32:3306, 数据库 rtu, 用户 root, 密码 jinpu123!@#
- **组件**: `database/remote_sync.py` → `RemoteSyncManager`
- **同步内容**:
  1. `device_monitor_snapshot` - 设备级快照（每个设备/数据包1条）
  2. `data_sensor` - 通道级传感器数据（每条通道1条）
- **特性**: 惰性连接、独立事务、失败不影响本地业务
