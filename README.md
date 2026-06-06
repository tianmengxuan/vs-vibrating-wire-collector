# VS振弦数据采集器 - 使用说明

## 版本信息

- **版本**: v1.0.0
- **适配设备**: 河北稳控科技 VS系列振弦传感器无线采发仪（VS1XX/VS4XX）
- **协议版本**: 稳控科技监测设备通用通讯协议接口说明 V1.2.0

---

## 一、极简使用步骤

### 1. 双击运行
双击 `VS振弦数据采集器.exe`，程序会自动完成以下操作：
- 在exe同目录下创建 `logs` 文件夹
- 生成默认配置文件 `config.env`

### 2. 配置数据库
关闭程序，用记事本打开同目录下的 `config.env`，修改MySQL连接信息：
```
DB_HOST=192.168.1.100    # 你的MySQL服务器地址
DB_PORT=3306
DB_USER=root             # 数据库用户名
DB_PASSWORD=yourpass     # 数据库密码
DB_NAME=vs_collector     # 数据库名
```

### 3. 创建数据库
在MySQL中执行：
```sql
CREATE DATABASE vs_collector DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```
程序启动后会自动创建所有表结构，无需手动执行SQL。

### 4. 再次运行
修改完配置后，再次双击 `VS振弦数据采集器.exe`，程序将：
- 连接MySQL数据库
- 自动创建7张核心数据表
- 在9000端口启动TCP服务
- 等待设备接入

---

## 二、配置文件说明 (config.env)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| DB_HOST | 127.0.0.1 | MySQL服务器地址 |
| DB_PORT | 3306 | MySQL端口 |
| DB_USER | root | 数据库用户名 |
| DB_PASSWORD | root | 数据库密码 |
| DB_NAME | vs_collector | 数据库名称 |
| DB_CHARSET | utf8mb4 | 数据库字符集 |
| TCP_HOST | 0.0.0.0 | TCP监听地址（0.0.0.0表示所有网卡） |
| TCP_PORT | 9000 | TCP监听端口 |
| TCP_MAX_CONNECTIONS | 100 | 最大并发连接数 |
| LOG_LEVEL | INFO | 日志级别: DEBUG/INFO/WARNING/ERROR/CRITICAL |
| LOG_RETENTION_DAYS | 30 | 日志保留天数 |
| CMD_TIMEOUT | 15 | 指令应答超时(秒) |
| CMD_MAX_RETRIES | 3 | 指令最大重试次数 |

---

## 三、日志查看

所有日志文件位于exe同目录下的 `logs` 文件夹中：

| 文件 | 说明 |
|------|------|
| `VS_Collector_YYYY-MM-DD.log` | 每日全量日志 |
| `VS_Collector_ERROR_YYYY-MM-DD.log` | 每日错误日志（仅ERROR及以上级别） |

日志格式：
```
时间戳 | 日志级别 | 模块名          | 函数名         | 行号 | 日志消息
2026-01-15 10:30:00 | INFO     | tcp_server.server | _handle_client | 123  | 新设备连接: 192.168.1.100:45678
```

---

## 四、数据库表结构

程序自动创建以下7张表：

| 表名 | 说明 |
|------|------|
| device_info | 设备信息表 |
| sensor_calibration | 传感器标定参数表 |
| raw_data_packet | 原始数据包表 |
| device_status | 设备状态表 |
| channel_raw_data | 通道原始数据表 |
| channel_calc_result | 通道解算结果表 |
| command_send_log | 指令发送日志表 |

---

## 五、常见问题排查

### Q1: MySQL连接失败
**现象**: 日志中出现 "数据库连接失败"  
**解决**:
1. 检查 `config.env` 中数据库连接信息是否正确
2. 确保MySQL服务已启动
3. 检查防火墙是否放行MySQL端口(3306)
4. 确认数据库 `vs_collector` 已创建

### Q2: TCP端口被占用
**现象**: 日志中出现 "TCP服务启动失败（端口可能被占用）"  
**解决**:
1. 修改 `config.env` 中的 `TCP_PORT` 为其他端口
2. 检查是否有其他程序占用了9000端口
3. 使用 `netstat -ano | findstr 9000` 查看端口占用

### Q3: 日志未生成
**现象**: `logs` 目录下没有日志文件  
**解决**:
1. 确认程序有exe所在目录的写入权限
2. 检查 `logs` 目录是否被创建
3. 尝试以管理员身份运行

### Q4: 设备无法连接
**现象**: 设备无法连接到采集器  
**解决**:
1. 检查设备配置的服务器地址和端口是否正确
2. 检查防火墙是否放行了TCP监听端口
3. 确认设备和服务器在同一网络
4. 使用 `telnet 服务器IP 9000` 测试连通性

### Q5: 打包后的exe不工作
**现象**: exe双击后闪退  
**解决**:
1. 在命令行中运行exe查看错误信息
2. 检查是否缺少依赖（build.bat会自动安装）
3. 确认config.env编码为UTF-8

---

## 六、开发者指南

### 项目结构
```
稳控解算软件/
├── main.py            # 主程序入口
├── config.py          # 路径适配与配置管理
├── logger_config.py   # 日志系统
├── database/          # 数据库层
│   ├── models.py      # ORM模型
│   ├── crud.py        # CRUD操作
│   └── schema.sql     # 建表SQL
├── tcp_server/        # TCP服务端
│   ├── server.py      # 服务端核心
│   └── session.py     # 设备会话
├── protocol/          # 协议解析
│   ├── checksum.py    # 校验算法
│   ├── parser.py      # 数据解析
│   └── dispatcher.py  # 自动识别分发
├── calculator/        # 振弦解算
│   └── vw_calculator.py
├── remote_config/     # 指令管控
│   └── commands.py
├── simulator.py       # 模拟设备测试
├── VS_DataCollector.spec  # 打包配置
├── build.bat          # 一键打包脚本
└── requirements.txt   # 依赖文件
```

### 打包发布
```batch
# 安装依赖
pip install -r requirements.txt

# 执行打包
build.bat

# 或手动打包
pyinstaller VS_DataCollector.spec --clean
```

### 测试
```batch
# 启动主程序
python main.py

# 另开一个终端，运行模拟设备测试
python simulator.py --count 10 --interval 3
```

---

## 七、注意事项

1. **config.env编码**: 必须使用UTF-8编码，否则中文注释可能导致读取失败
2. **数据库**: 首次运行前需手动创建数据库，表结构会自动创建
3. **端口**: 确保TCP端口未被其他程序占用
4. **防火墙**: 部署在服务器上时需放行TCP监听端口
5. **RS485安全**: 多设备RS485总线场景下，严禁使用0xFF通用地址修改参数
