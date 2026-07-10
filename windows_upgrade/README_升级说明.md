# VS振弦数据采集器 Windows 一键升级说明

## 升级包文件

升级包不要包含服务器上的 `config.env`、`sites.json`、`formulas.json`。
`upgrade_vs_collector.ps1` 已保存为 UTF-8 BOM 编码，兼容 Windows PowerShell 5.1 读取中文 EXE 文件名。

执行升级至少需要以下文件：

- `upgrade_vs_collector.cmd`
- `upgrade_vs_collector.ps1`
- `VS振弦数据采集器_新版.exe`

可同时附带本说明文件 `README_升级说明.md`，说明文件不参与升级逻辑。

其中 `VS振弦数据采集器_新版.exe` 是本次新编译的 Windows EXE，必须使用这个不同文件名，不能直接叫 `VS振弦数据采集器.exe`。

## 使用方法

1. 在服务器上正常关闭 `VS振弦数据采集器.exe`。
2. 将上述 3 个升级文件解压或复制到现有程序目录，也就是旧版 `VS振弦数据采集器.exe` 所在目录。
3. 双击 `upgrade_vs_collector.cmd`。
4. 脚本会先备份旧 EXE、`config.env`、`sites.json`、`formulas.json` 到 `upgrade_backup_yyyyMMdd_HHmmss` 目录。
5. 脚本只替换 `VS振弦数据采集器.exe`，不会覆盖或修改三个配置文件。
6. 升级成功后会自动启动新版 `VS振弦数据采集器.exe`。

## 失败处理

- 如果检测到程序仍在运行，脚本会安全退出并提示先关闭程序，不会强制结束进程。
- 如果缺少 `VS振弦数据采集器_新版.exe`，或该文件大小为 0，脚本会停止升级。
- 如果 EXE 替换失败，脚本会自动尝试恢复旧版 EXE。
- 历史数据保存在 MySQL 中，不随 EXE 替换。
- 每次升级都会生成带时间戳的备份目录，成功后也会保留；需要回退时可从该目录取回旧 EXE 和配置备份。
- `VS振弦数据采集器_新版.exe` 只是升级载荷，脚本不会把它当成 `config.env`、`sites.json`、`formulas.json` 这类正式配置文件。
