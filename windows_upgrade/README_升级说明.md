# VS振弦数据采集器 Windows 一键升级说明

## 本次修复

本版本修复了旧版 Windows 控制台双击升级时可能出现的 `0x1F` 写控制台失败和中文乱码。`upgrade_vs_collector.cmd`、`upgrade_vs_collector.ps1` 以及升级载荷文件名现在全部使用 ASCII，兼容 Windows PowerShell 5.1 和旧控制台；脚本不会向控制台输出中文程序名、中文路径或系统异常详情。

## 升级包文件

升级包不要包含服务器上的 `config.env`、`sites.json`、`formulas.json`。执行升级需要：

- `upgrade_vs_collector.cmd`
- `upgrade_vs_collector.ps1`
- `VSCollector_Update.exe`
- `README_升级说明.md`（仅说明，不参与执行）

升级包 ZIP 文件名可以使用中文，但 ZIP 内的 CMD、PowerShell 脚本和 EXE 载荷必须保持上述 ASCII 文件名。

## 使用方法

1. 正常关闭现有的 `VS振弦数据采集器.exe`，不要强制结束进程。
2. 将升级包内容放到旧 EXE 所在目录。
3. 双击 `upgrade_vs_collector.cmd`。
4. CMD 只显示简短英文结果；详细的 ASCII 状态记录在 `upgrade_vs_collector.log`。
5. 成功后脚本自动启动更新后的 `VS振弦数据采集器.exe`。

## 安全行为

- 脚本优先按完整路径检测目标进程，无法精确检测时按进程名保守检测；程序仍运行时返回退出码 `2`，不会强制结束。
- 升级前将旧 EXE 和已存在的 `config.env`、`sites.json`、`formulas.json` 复制到 `upgrade_backup_yyyyMMdd_HHmmss`。
- 三个配置文件只备份，绝不覆盖、移动、删除或改写。
- EXE 替换或启动失败时会尝试回滚旧 EXE。
- 备份目录在升级成功后也会保留，便于人工回退。
