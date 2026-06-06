import os, sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# clean
import shutil
for d in ['build', 'dist']:
    if os.path.exists(d): shutil.rmtree(d, ignore_errors=True)
for f in ['VS振弦数据采集器.spec']:
    if os.path.exists(f): os.remove(f)

# build
import PyInstaller.__main__
PyInstaller.__main__.run([
    '--onefile', '--console',
    '--name', 'VS振弦数据采集器',
    '--hidden-import', 'pymysql',
    '--hidden-import', 'tkinter',
    '--hidden-import', 'tkinter.ttk',
    '--hidden-import', 'tkinter.messagebox',
    '--hidden-import', '_tkinter',
    '--hidden-import', 'queue',
    '--hidden-import', 'threading',
    '--hidden-import', 'asyncio',
    '--hidden-import', 'json',
    '--hidden-import', 'argparse',
    '--hidden-import', 'signal',
    '--hidden-import', 'traceback',
    '--hidden-import', 'config',
    '--hidden-import', 'logger_config',
    '--collect-submodules', 'database',
    '--collect-submodules', 'calculator',
    '--collect-submodules', 'protocol',
    '--collect-submodules', 'tcp_server',
    '--collect-submodules', 'remote_config',
    'main.py',
])

exe = 'dist/VS振弦数据采集器.exe'
if os.path.exists(exe):
    mb = os.path.getsize(exe) / 1024 / 1024
    print(f'OK: {mb:.1f}MB')
else:
    print('FAILED')
