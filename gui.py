# -*- coding: utf-8 -*-
"""
gui.py - VS振弦数据采集器 v4.1
=======================================
平铺表格 / 设备+通道二级公式 / 删单位列

作者: VS DataCollector Project
版本: 4.1.0
"""

import os, json, time, queue, threading, asyncio, traceback, tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

RECENT_DATA_ONLINE_SECONDS = 3600
STALE_DEVICE_CHECK_MS = 30000

class GuiLogHandler:
    def __init__(self, q):
        self.q = q
    def _push(self, lv, msg):
        try:
            self.q.put({'type': 'LOG', 'level': lv, 'time': datetime.now().strftime('%H:%M:%S'), 'message': str(msg)})
        except:
            pass
    def info(self, m):
        self._push('INFO', m)
    def error(self, m):
        self._push('ERROR', m)
    def warn(self, m):
        self._push('WARN', m)
    def dev_event(self, udid, ev, data=None):
        self.q.put({'type': ev, 'udid': udid, 'data': data or {}})

class VSCollectorGUI:
    def __init__(self, root, config, db_manager=None):
        self.root = root
        self.config = config
        self.db_manager = db_manager
        self._default_fm = {
            'K': float(config.get('CALC_DEFAULT_K', 1.0)),
            'f0': float(config.get('CALC_DEFAULT_F0', 0.0)),
            'alpha': float(config.get('CALC_DEFAULT_ALPHA', 0.0)),
            'T0': float(config.get('CALC_DEFAULT_T0', 20.0)),
            'elev': 0.0,
        }
        self.msg_queue = queue.Queue()
        self.tcp_server = None
        self.server_thread = None
        self.loop = None
        self.running = False
        self.devices = {}
        self.site_names = {}
        self.device_formulas = {}  # {udid: {str(ch): {K,f0,alpha,T0,elev}}}
        self._tree_sort_column = None
        self._tree_sort_desc = False
        self.log_count = 0
        self.data_count = 0
        # 垃圾UDID黑名单（协议名/HTTP标识等不得作为设备ID显示）
        self._GARBAGE_UDID = ('', 'UNKNOWN', 'UNKNOWN_DEVICE', 'SL651_STR', 'SL651_HEX', 'SL651_UNKNOWN')
        # 设备公式页缓存（防ComboBox取值时序异常）
        self._f_selected_udid = None
        self._f_selected_ch = None
        self._load_site_names()
        self._load_device_formulas()
        self._setup_window()
        self._build_ui()
        self._start_msg_processor()
        self._start_stale_device_checker()
        self._log('INFO', 'GUI v4.3 已启动')

    def _setup_window(self):
        self.root.title('VS振弦数据采集器 v4.1')
        self.root.geometry('1400x850')
        self.root.minsize(1100, 600)
        self.root.protocol('WM_DELETE_WINDOW', self._on_closing)
        self.style = ttk.Style()
        self.style.theme_use('clam')
        C = {'bg':'#1e1e2e','fg':'#cdd6f4','panel':'#181825','header':'#313244',
             'accent':'#89b4fa','green':'#a6e3a1','red':'#f38ba8','yellow':'#f9e2af',
             'dim':'#585b70','input':'#11111b'}
        self.C = C
        self.root.configure(bg=C['bg'])
        self.style.configure('TFrame', background=C['bg'])
        self.style.configure('TLabel', background=C['bg'], foreground=C['fg'])
        self.style.configure('TButton', background=C['header'], foreground=C['fg'], padding=4)
        self.style.configure('TEntry', fieldbackground=C['input'], foreground=C['fg'])
        self.style.configure('TCombobox', fieldbackground=C['input'], foreground=C['fg'])
        self.style.configure('TNotebook', background=C['bg'])
        self.style.configure('TNotebook.Tab', background=C['panel'], foreground=C['fg'], padding=(12, 4))
        self.style.map('TNotebook.Tab', background=[('selected', C['header'])])
        self.style.configure('TLabelframe', background=C['bg'], foreground=C['accent'])
        self.style.configure('TLabelframe.Label', background=C['bg'], foreground=C['accent'],
                             font=('Microsoft YaHei UI', 9, 'bold'))
        self.style.configure('Header.TLabel', font=('Microsoft YaHei UI', 15, 'bold'), foreground=C['accent'])
        self.style.configure('Treeview', background=C['input'], foreground=C['fg'],
                            fieldbackground=C['input'], rowheight=28)
        self.style.map('Treeview', background=[('selected', C['header'])],
                      foreground=[('selected', C['fg'])])
        self.style.configure('Treeview.Heading', background=C['header'], foreground=C['fg'],
                            font=('Microsoft YaHei UI', 9, 'bold'))

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=(12, 5))
        top.pack(fill=tk.X)
        ttk.Label(top, text='VS振弦数据采集器', style='Header.TLabel').pack(side=tk.LEFT)
        ttk.Button(top, text='⚙ 设置', command=self._open_settings).pack(side=tk.RIGHT, padx=3)
        self.btn_stop = ttk.Button(top, text='■ 停止', command=self._stop_server, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.RIGHT, padx=3)
        self.btn_start = ttk.Button(top, text='▶ 启动服务', command=self._start_server)
        self.btn_start.pack(side=tk.RIGHT, padx=3)
        self.lbl_run = ttk.Label(top, text='● 已停止', foreground=self.C['red'],
                                 font=('Microsoft YaHei UI', 10, 'bold'))
        self.lbl_run.pack(side=tk.RIGHT, padx=15)

        # === 平铺表格（无单位列）===
        tf = ttk.LabelFrame(self.root, text='设备数据监控', padding=6)
        tf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 0))

        cols = ('udid','site','status','ip','signal','voltage','packets','freq','temp','calc','water','water_elevation','quality','refresh_time')
        self._tree_columns = cols
        self._tree_heading_texts = {
            'udid': '设备ID',
            'site': '站点名称',
            'status': '状态',
            'ip': 'IP',
            'signal': '信号(dBm)',
            'voltage': '电压(V)',
            'packets': '数据包',
            'freq': '频率(Hz)',
            'temp': '温度(℃)',
            'calc': 'P水压(MPa)',
            'water': '水位(m)',
            'water_elevation': '水位高程(m)',
            'quality': '数据质量',
            'refresh_time': '刷新时间',
        }
        self._numeric_sort_columns = {'signal', 'voltage', 'packets', 'freq', 'temp', 'calc', 'water', 'water_elevation'}
        self.tree = ttk.Treeview(tf, columns=cols, show='headings', height=16)
        self._update_tree_headings()

        w = {'udid':85,'site':105,'status':42,'ip':120,'signal':55,'voltage':55,'packets':48,
             'freq':75,'temp':65,'calc':85,'water':80,'water_elevation':90,'quality':48,'refresh_time':160}
        for c, wi in w.items():
            self.tree.column(c, width=wi,
                           anchor='center' if c in ('status','signal','voltage','packets','quality') else 'w')

        vsb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tf.grid_rowconfigure(0, weight=1)
        tf.grid_columnconfigure(0, weight=1)

        self.tree.bind('<Double-1>', self._on_row_dblclick)
        self.tree.bind('<<TreeviewSelect>>', self._on_row_select)

        # === 指令栏 ===
        cf = ttk.Frame(self.root, padding=(10, 3))
        cf.pack(fill=tk.X)
        ttk.Label(cf, text='指令 设备:').pack(side=tk.LEFT)
        self.cmd_udid = ttk.Combobox(cf, width=14, state='readonly')
        self.cmd_udid.pack(side=tk.LEFT, padx=4)
        for l, c in [('SETM', '@SETM'), ('INFO', '$INFO 1'), ('SAVE', '$SAVE')]:
            ttk.Button(cf, text=l, width=6, command=lambda v=c: self._send_cmd(v)).pack(side=tk.LEFT, padx=2)
        self.cmd_entry = ttk.Entry(cf, font=('Consolas', 10))
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        self.cmd_entry.bind('<Return>', lambda e: self._send_custom_cmd())
        ttk.Button(cf, text='发送', command=self._send_custom_cmd, width=6).pack(side=tk.LEFT)

        # === 日志 ===
        lf = ttk.LabelFrame(self.root, text='实时日志', padding=5)
        lf.pack(fill=tk.X, padx=10, pady=3)
        self.log_text = tk.Text(lf, wrap=tk.WORD, state=tk.DISABLED, font=('Consolas', 9),
                                bg=self.C['input'], fg=self.C['fg'], relief=tk.FLAT, padx=8, pady=4, height=5)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        for t,c in [('INFO',self.C['fg']),('WARN',self.C['yellow']),('ERROR',self.C['red']),
                     ('CMD',self.C['accent']),('TIME',self.C['dim'])]:
            self.log_text.tag_configure(t, foreground=c)
        ttk.Button(lf, text='清空', command=self._clear_log, width=6).pack(anchor='e', pady=(2,0))

        # === 状态栏 ===
        sf = ttk.Frame(self.root, padding=(10, 3))
        sf.pack(fill=tk.X, side=tk.BOTTOM)
        self.st_run = ttk.Label(sf, text='● 已停止', foreground=self.C['red'])
        self.st_run.pack(side=tk.LEFT, padx=(0, 12))
        self.st_online = ttk.Label(sf, text='在线: 0')
        self.st_online.pack(side=tk.LEFT, padx=(0, 12))
        self.st_data = ttk.Label(sf, text='数据: 0 包')
        self.st_data.pack(side=tk.LEFT, padx=(0, 12))
        self.st_port = ttk.Label(sf, text=f"端口: {self.config.get('TCP_PORT', 9000)}")
        self.st_port.pack(side=tk.LEFT, padx=(0, 12))
        self.st_db = ttk.Label(sf, text='DB: 未连接', foreground=self.C['red'])
        self.st_db.pack(side=tk.LEFT)
        ttk.Label(sf, text='v4.1', foreground=self.C['dim']).pack(side=tk.RIGHT)

    # ================================================================
    # 设置对话框
    # ================================================================
    def _open_settings(self):
        dlg = tk.Toplevel(self.root)
        dlg.title('系统设置')
        dlg.geometry('520x640')
        dlg.configure(bg=self.C['bg'])
        dlg.transient(self.root)
        dlg.grab_set()
        self._sv = {}
        nb = ttk.Notebook(dlg)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Tab1: TCP
        t1 = ttk.Frame(nb, padding=10)
        nb.add(t1, text='  TCP  ')
        self._srow(t1, '监听端口:', 'tcp_port', self.config.get('TCP_PORT', '9000'))

        # Tab2: MySQL
        t2 = ttk.Frame(nb, padding=10)
        nb.add(t2, text='  MySQL  ')
        self._srow(t2, '主机:', 'db_host', self.config.get('DB_HOST', '127.0.0.1'))
        self._srow(t2, '端口:', 'db_port', self.config.get('DB_PORT', '3306'))
        self._srow(t2, '用户名:', 'db_user', self.config.get('DB_USER', 'root'))
        self._srow(t2, '密码:', 'db_pw', self.config.get('DB_PASSWORD', 'root'), show='*')
        self._srow(t2, '数据库:', 'db_name', self.config.get('DB_NAME', 'vs_collector'))

        # 连接/断开按钮 + 状态
        tr = ttk.Frame(t2)
        tr.pack(fill=tk.X, pady=(12, 0))
        self._db_status_label = tk.Label(tr, text='', bg=self.C['bg'], fg=self.C['dim'],
                                         font=('Microsoft YaHei UI', 9))
        self._db_status_label.pack(side=tk.LEFT, padx=5)
        self._db_btn_connect = ttk.Button(tr, text='连接', command=self._db_connect)
        self._db_btn_connect.pack(side=tk.RIGHT, padx=3)
        self._db_btn_disconnect = ttk.Button(tr, text='断开', command=self._db_disconnect)
        self._db_btn_disconnect.pack(side=tk.RIGHT, padx=3)
        self._update_db_status()

        # Tab3: 设备公式（设备+通道二级选择）
        t3 = ttk.Frame(nb, padding=10)
        nb.add(t3, text='  设备公式  ')
        tk.Label(t3, text='P = K×(f0²-f²) + Kt×(t-t0)  单位: MPa\n'
                '水位(m) = P(MPa)×1000 / 9.8       (h = P/γ, γ=9.8kN/m³)\n'
                '选择设备 → 选择通道 → 配置公式',
                bg=self.C['bg'], fg=self.C['dim'], font=('Microsoft YaHei UI', 8), justify=tk.LEFT).pack(pady=(0, 10))

        # 设备选择
        r1 = tk.Frame(t3, bg=self.C['bg'])
        r1.pack(fill=tk.X, pady=2)
        tk.Label(r1, text='设备:', bg=self.C['bg'], fg=self.C['fg'], width=6, anchor='e').pack(side=tk.LEFT, padx=(0, 8))
        self.fdev_combo = ttk.Combobox(r1, width=18, state='readonly')
        self.fdev_combo.pack(side=tk.LEFT)
        self.fdev_combo.bind('<<ComboboxSelected>>', self._on_fdev_select)
        self._f_selected_udid = None   # 缓存当前选中设备UDID，防止ComboBox取值异常
        self._f_selected_ch = None     # 缓存当前选中通道号

        # 通道选择
        r2 = tk.Frame(t3, bg=self.C['bg'])
        r2.pack(fill=tk.X, pady=2)
        tk.Label(r2, text='通道:', bg=self.C['bg'], fg=self.C['fg'], width=6, anchor='e').pack(side=tk.LEFT, padx=(0, 8))
        self.fch_combo = ttk.Combobox(r2, width=10, state='readonly')
        self.fch_combo.pack(side=tk.LEFT)
        self.fch_combo.bind('<<ComboboxSelected>>', self._on_fch_select)

        # 公式编辑
        self.ff = tk.Frame(t3, bg=self.C['bg'])
        for lab, k, dv in [('K 系数:', 'K', '1.0'), ('f0 基准(Hz):', 'f0', '0.0'),
                            ('Kt 温度系数:', 'alpha', '0.0'), ('t0 基准(℃):', 'T0', '20.0')]:
            self._srow_in(self.ff, lab, k, dv)
        # 安装高程（用于计算水位高程 = 水位 + 安装高程）
        tk.Label(self.ff, text='── 安装高程 ──', bg=self.C['bg'], fg=self.C['dim'],
                 font=('Microsoft YaHei UI', 8)).pack(pady=(10, 2))
        self._srow_in(self.ff, '安装高程(m):', 'elev', '0.0')
        self.ff.pack(fill=tk.X, pady=5)

        self._refresh_fdev()

        bf = ttk.Frame(dlg)
        bf.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(bf, text='保存设置', command=lambda: self._save_settings(dlg)).pack(side=tk.RIGHT, padx=3)
        ttk.Button(bf, text='取消', command=dlg.destroy).pack(side=tk.RIGHT, padx=3)

    def _srow(self, p, lab, k, dv, show=None):
        f = tk.Frame(p, bg=self.C['bg'])
        f.pack(fill=tk.X, pady=3)
        tk.Label(f, text=lab, bg=self.C['bg'], fg=self.C['fg'], width=12, anchor='e',
                font=('Microsoft YaHei UI', 10)).pack(side=tk.LEFT, padx=(0, 8))
        v = tk.StringVar(value=dv)
        self._sv[k] = v
        e = tk.Entry(f, textvariable=v, width=30, bg=self.C['input'], fg=self.C['fg'],
                    insertbackground=self.C['accent'], relief=tk.FLAT, borderwidth=1, font=('Consolas', 10))
        if show:
            e.configure(show=show)
        e.pack(side=tk.LEFT)

    def _srow_in(self, p, lab, k, dv):
        f = tk.Frame(p, bg=self.C['bg'])
        f.pack(fill=tk.X, pady=2)
        tk.Label(f, text=lab, bg=self.C['bg'], fg=self.C['fg'], width=14, anchor='e',
                font=('Microsoft YaHei UI', 10)).pack(side=tk.LEFT, padx=(0, 8))
        v = tk.StringVar(value=dv)
        self._sv[k] = v
        tk.Entry(f, textvariable=v, width=18, bg=self.C['input'], fg=self.C['fg'],
                insertbackground=self.C['accent'], relief=tk.FLAT, borderwidth=1, font=('Consolas', 10)).pack(side=tk.LEFT)

    def _update_db_status(self):
        """更新数据库连接状态显示"""
        # 设置对话框中的状态标签（可能不存在或已销毁）
        try:
            lbl = self._db_status_label
        except AttributeError:
            lbl = None
        if lbl is not None:
            try:
                if self.db_manager and self.db_manager.is_connected():
                    lbl.configure(text='● 已连接', fg=self.C['green'])
                else:
                    lbl.configure(text='● 未连接', fg=self.C['red'])
            except tk.TclError:
                pass  # widget 已被销毁，忽略
        # 连接/断开按钮
        try:
            if self.db_manager and self.db_manager.is_connected():
                self._db_btn_connect.configure(state=tk.DISABLED)
                self._db_btn_disconnect.configure(state=tk.NORMAL)
            else:
                self._db_btn_connect.configure(state=tk.NORMAL)
                self._db_btn_disconnect.configure(state=tk.DISABLED)
        except (AttributeError, tk.TclError):
            pass
        # 同步状态栏
        if hasattr(self, 'st_db'):
            if self.db_manager and self.db_manager.is_connected():
                self.st_db.configure(text='DB: 已连接', foreground=self.C['green'])
            else:
                self.st_db.configure(text='DB: 未连接', foreground=self.C['red'])

    def _db_connect(self):
        """连接MySQL数据库"""
        if not self.db_manager:
            from database import DatabaseManager
            from config import get_database_url
            # 用当前设置中的值更新config
            self._save_db_to_config()
            db_url = get_database_url(self.config)
            self.db_manager = DatabaseManager(database_url=db_url, pool_size=2, pool_recycle=1800)
        if self.db_manager.connect():
            self.db_manager.init_tables()
            self._log('INFO', 'MySQL 连接成功')
            # 如果TCPServer已启动，更新其db_manager
            if self.tcp_server:
                self.tcp_server.db = self.db_manager
            self._update_db_status()
        else:
            self._update_db_status()
            messagebox.showwarning('连接失败', '无法连接到 MySQL，请检查配置')

    def _db_disconnect(self):
        """断开MySQL连接"""
        if self.db_manager:
            self.db_manager.disconnect()
            self._log('INFO', 'MySQL 已断开')
        self._update_db_status()

    def _save_db_to_config(self):
        """将设置中的DB参数保存到config"""
        if hasattr(self, '_sv'):
            for k in ('db_host', 'db_port', 'db_user', 'db_pw', 'db_name'):
                if k in self._sv:
                    val = self._sv[k].get()
                    key = ('DB_HOST' if k == 'db_host' else
                           'DB_PORT' if k == 'db_port' else
                           'DB_USER' if k == 'db_user' else
                           'DB_PASSWORD' if k == 'db_pw' else
                           'DB_NAME')
                    self.config[key] = val

    def _refresh_fdev(self):
        all_devs = sorted(self.devices.keys())
        self.fdev_combo['values'] = all_devs
        cur = self.fdev_combo.get()
        if cur in all_devs:
            self._on_fdev_select()
        elif all_devs:
            self.fdev_combo.set(all_devs[0])
            self._on_fdev_select()

    def _on_fdev_select(self, e=None):
        udid = self.fdev_combo.get()
        self._f_selected_udid = udid   # 缓存当前设备UDID
        if not udid:
            return
        # 更新通道下拉
        dev = self.devices.get(udid, {})
        chs = sorted(dev.get('channels', {}).keys())
        if not chs:
            chs = list(range(1, 17))  # 默认1-16
        self.fch_combo['values'] = [str(c) for c in chs]
        if self.fch_combo.get() not in self.fch_combo['values']:
            self.fch_combo.set(str(chs[0]))
        self._on_fch_select()

    def _on_fch_select(self, e=None):
        """通道选择事件——使用缓存UDID避免ComboBox取值时序异常"""
        udid = self._f_selected_udid   # 从缓存读取，避免ComboBox.get()偶发返回空
        ch = self.fch_combo.get()
        self._f_selected_ch = ch       # 缓存当前通道号
        if not udid or not ch:
            return
        fm = self._get_formula(udid, ch)
        for k in ('K', 'f0', 'alpha', 'T0', 'elev'):
            if k in self._sv:
                self._sv[k].set(str(fm.get(k, self._default_fm.get(k, 0))))

    def _get_formula(self, udid, channel):
        """获取指定设备和通道的公式（不存在则返回默认）"""
        ch_key = str(channel)
        if udid in self.device_formulas and ch_key in self.device_formulas[udid]:
            return self.device_formulas[udid][ch_key]
        return dict(self._default_fm)

    def _save_settings(self, dlg):
        try:
            v = self._sv
            udid = self._f_selected_udid   # 从缓存读取，避免ComboBox.get()偶发返回空
            ch = self._f_selected_ch       # 从缓存读取
            if udid and ch:
                fm = {}
                for k in ('K', 'f0', 'alpha', 'T0', 'elev'):
                    if k in v:
                        fm[k] = float(v[k].get())
                if udid not in self.device_formulas:
                    self.device_formulas[udid] = {}
                self.device_formulas[udid][ch] = fm
                self._save_device_formulas()
                self._log('INFO', f'公式已保存 [{udid} CH{ch}] K={fm["K"]} f0={fm["f0"]}')
            for k in ('tcp_port',):
                if k in v:
                    self.config['TCP_PORT'] = v[k].get()
                    self.st_port.configure(text=f"端口: {v[k].get()}")
            for k in ('db_host',):
                if k in v:
                    self.config['DB_HOST'] = v[k].get()
            for k in ('db_port',):
                if k in v:
                    self.config['DB_PORT'] = v[k].get()
            for k in ('db_user',):
                if k in v:
                    self.config['DB_USER'] = v[k].get()
            for k in ('db_pw',):
                if k in v:
                    self.config['DB_PASSWORD'] = v[k].get()
            for k in ('db_name',):
                if k in v:
                    self.config['DB_NAME'] = v[k].get()
            # 持久化配置到 config.env
            from config import save_config
            save_config(self.config)
            self._log('INFO', '配置已保存到 config.env')
            dlg.destroy()
        except ValueError as exc:
            messagebox.showwarning('输入错误', str(exc))

    # ================================================================
    # 表格
    # ================================================================
    def _update_tree_headings(self):
        for col in self._tree_columns:
            text = self._tree_heading_texts[col]
            if col == self._tree_sort_column:
                text = f'{text} {"↓" if self._tree_sort_desc else "↑"}'
            self.tree.heading(col, text=text, command=lambda c=col: self._sort_tree_by_column(c))

    def _sort_tree_by_column(self, col):
        if col == self._tree_sort_column:
            self._tree_sort_desc = not self._tree_sort_desc
        else:
            self._tree_sort_column = col
            self._tree_sort_desc = False
        self._apply_tree_sort()
        self._update_tree_headings()

    def _apply_tree_sort(self):
        col = self._tree_sort_column
        if not col:
            return

        rows = []
        for iid in self.tree.get_children(''):
            raw = self.tree.set(iid, col)
            ok, value, fallback = self._tree_sort_value(col, raw)
            rows.append((iid, ok, value, fallback))

        valid_rows = [row for row in rows if row[1]]
        fallback_rows = [row for row in rows if not row[1]]
        valid_rows.sort(key=lambda row: row[2], reverse=self._tree_sort_desc)
        fallback_rows.sort(key=lambda row: row[3])

        for index, row in enumerate(valid_rows + fallback_rows):
            self.tree.move(row[0], '', index)

    def _tree_sort_value(self, col, raw):
        text = str(raw).strip()
        fallback = text.casefold()

        if col in self._numeric_sort_columns:
            try:
                return True, float(text.replace(',', '')), fallback
            except ValueError:
                return False, None, fallback

        if col == 'refresh_time':
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S',
                        '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M', '%H:%M:%S'):
                try:
                    return True, datetime.strptime(text, fmt), fallback
                except ValueError:
                    pass
            return False, None, fallback

        if col == 'status':
            if '在线' in text and '离线' not in text:
                return True, 0, fallback
            if '离线' in text:
                return True, 1, fallback
            return False, None, fallback

        return True, fallback, fallback

    def _on_row_dblclick(self, event):
        col = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if col == '#2' and item:
            self._edit_site(item)

    def _edit_site(self, udid):
        b = self.tree.bbox(udid, '#2')
        if not b:
            return
        x, y, w, h = b
        old = self.site_names.get(udid, '')
        e = tk.Entry(self.tree, font=('Microsoft YaHei UI', 9),
                    bg=self.C['input'], fg=self.C['fg'], insertbackground=self.C['accent'],
                    relief=tk.FLAT, borderwidth=1)
        e.place(x=x, y=y, width=w, height=h)
        e.insert(0, old)
        e.select_range(0, tk.END)
        e.focus_set()
        def save(ev=None):
            nw = e.get().strip()
            if nw != old:
                self.site_names[udid] = nw
                self.tree.set(udid, 'site', nw)
                self._apply_tree_sort()
                self._save_site_names()
                self._log('INFO', f'站点: {udid} → {nw}')
            e.destroy()
        e.bind('<Return>', save)
        e.bind('<FocusOut>', save)
        e.bind('<Escape>', lambda ev: e.destroy())

    def _on_row_select(self, event):
        sel = self.tree.selection()
        if sel:
            self.cmd_udid.set(sel[0])

    # ================================================================
    # 设备事件
    # ================================================================
    def _dev_online(self, udid, addr):
        udid = udid.rstrip(':;, \t') if udid else udid  # 清理末尾冒号/分号
        if udid not in self.devices:
            self.devices[udid] = {'addr': addr, 'signal': '-', 'voltage': '-',
                                  'online': True, 'packets': 0, 'channels': {}}
            site = self.site_names.get(udid, '')
            self.tree.insert('', 'end', iid=udid,
                            values=(udid, site, '● 在线', addr or '-', '-', '-', '0', '', '', '', '', '', '', '-'))
            self._log('INFO', f'设备上线: {udid}')
        else:
            self.devices[udid]['online'] = True
            self.devices[udid]['addr'] = addr or self.devices[udid].get('addr', '-')
            self.tree.set(udid, 'status', '● 在线')
            self.tree.set(udid, 'ip', addr or '-')
        self._apply_tree_sort()
        self._update_combo()
        self._update_online_count()

    def _dev_offline(self, udid):
        if udid in self.devices:
            dev = self.devices[udid]
            if self._has_recent_data(dev):
                dev['online'] = True
                self.tree.set(udid, 'status', '● 在线')
                self._apply_tree_sort()
                self._log('INFO', f'TCP断开但1小时内有数据，保持在线: {udid}')
            else:
                self._mark_device_offline(udid)
        self._update_online_count()

    def _dev_data(self, udid, data):
        # 清理末尾冒号/分号
        udid = udid.rstrip(':;, \t') if udid else udid
        # 拦截垃圾UDID（HTTP爬虫/SL651异常/未识别设备/含控制字符/乱码）
        if not udid or udid in self._GARBAGE_UDID or udid.startswith('SL651_') or udid.startswith('HTTP_'):
            return
        # 含控制字符(0x00-0x1F, 0x7F)或过短(<4)的UDID直接丢弃
        if any(ord(c) < 32 or ord(c) == 127 for c in udid) or len(udid) < 4:
            return
        if udid not in self.devices:
            self.devices[udid] = {'addr': '', 'signal': '-', 'voltage': '-',
                                  'online': False, 'packets': 0, 'channels': {}}
            site = self.site_names.get(udid, '')
            self.tree.insert('', 'end', iid=udid,
                            values=(udid, site, '○ 离线', '-', '-', '-', '0', '', '', '', '', '', '', '-'))
            self._log('INFO', f'设备发现: {udid}')
            self._update_combo()

        dev = self.devices[udid]
        dev['packets'] += 1
        dev['online'] = True
        dev['signal'] = data.get('signal', dev.get('signal', '-'))
        dev['voltage'] = data.get('voltage', dev.get('voltage', '-'))
        # 动态IP更新
        addr = data.get('addr', '')
        if addr:
            dev['addr'] = addr
            self.tree.set(udid, 'ip', addr)

        channels = data.get('channels', [])
        for ch in channels:
            cn = ch.get('channel', 0)
            freq = ch.get('frequency')
            temp = ch.get('temp')
            q = ch.get('data_quality', 'VALID')

            # 用设置中的公式参数计算水压 P = K×(f0²-f²) + Kt×(t-t0)
            fm = self._get_formula(udid, cn)
            p = None
            w = None
            if freq is not None and freq > 0 and fm['f0'] > 0:
                f2 = freq * freq
                f02 = fm['f0'] * fm['f0']
                p = fm['K'] * (f02 - f2)  # P = K × (f0² - f²)
                if temp is not None and fm['alpha'] != 0:
                    p += fm['alpha'] * (temp - fm['T0'])  # + Kt × (t - t0)
                p = round(p, 6)
                if p < 0:
                    p = None  # 负压无物理意义，不显示
                # 水位: h = P/γ,  γ=9.8kN/m³,  h(m) = P(MPa)×1000/9.8
                if p is not None:
                    w = round(p * 1000.0 / 9.8, 4)

            # 水位高程 = 水位 + 安装高程(来自设备公式)
            we = None
            elev = fm.get('elev', 0.0)
            if w is not None and elev != 0:
                we = round(w + elev, 4)

            dev['channels'][cn] = {'freq': freq, 'temp': temp,
                                   'calc': p, 'water': w, 'water_elevation': we, 'quality': q}

        # 取第一个有效通道显示在主行
        ch1 = dev['channels'].get(1) or (list(dev['channels'].values())[0] if dev['channels'] else None)

        # 实时日志: 收到完整数据（不截断）+ 解析结果
        proto = data.get('protocol', '?')
        raw_hex = data.get('raw_hex', '')
        raw_str = data.get('raw_str', '')
        dlen = data.get('data_len', 0)
        if raw_hex:
            self._log('INFO', f'收到 [{udid}] {proto} {dlen}B\n  RAW_HEX: {raw_hex}')
        elif raw_str:
            self._log('INFO', f'收到 [{udid}] {proto} {dlen}B\n  RAW_STR: {raw_str}')
        # 实时日志: 通道数据（用公式计算后的P和水位）
        for cn, ch_data in sorted(dev['channels'].items()):
            freq = ch_data.get('freq')
            temp = ch_data.get('temp')
            p_val = ch_data.get('calc')
            w_val = ch_data.get('water')
            q = ch_data.get('quality', 'VALID')
            parts = []
            if freq is not None:
                parts.append(f'f={freq}Hz')
            if temp is not None:
                parts.append(f'T={temp}℃')
            if p_val is not None:
                parts.append(f'P={p_val:.4f}MPa')
            if w_val is not None:
                parts.append(f'水位={w_val:.4f}m')
            self._log('INFO', f'  └ CH{cn}: {", ".join(parts)} [{q}]')

        # 记录刷新时间（含年月日）
        now = datetime.now()
        refresh_ts = now.strftime('%Y-%m-%d %H:%M:%S')
        dev['refresh_time'] = refresh_ts
        dev['last_data_time'] = now

        def fs(v, d=1):
            return f'{v:.{d}f}' if isinstance(v, (int, float)) else '-'

        sv = dev['signal']
        vv = dev['voltage']
        sv_s = f'{sv}' if not isinstance(sv, (int, float)) else f'{sv}'
        vv_s = f'{vv:.2f}' if isinstance(vv, (int, float)) else f'{vv}'

        vals = (
            udid,
            self.site_names.get(udid, ''),
            '● 在线' if dev['online'] else '○ 离线',
            dev.get('addr', '-'),
            sv_s, vv_s,
            str(dev['packets']),
            fs(ch1.get('freq')) if ch1 else '',
            fs(ch1.get('temp')) if ch1 else '',
            f"{fs(ch1.get('calc'),4)}" if ch1 else '',
            fs(ch1.get('water'), 4) if ch1 else '',
            fs(ch1.get('water_elevation'), 4) if ch1 else '',
            ch1.get('quality', '-') if ch1 else '',
            refresh_ts,
        )
        try:
            self.tree.item(udid, values=vals)
            self._apply_tree_sort()
        except:
            pass
        self._update_online_count()

        # 第三方监控快照入库
        if self.db_manager and self.db_manager.is_connected():
            try:
                sig = dev.get('signal')
                vol = dev.get('voltage')
                self.db_manager.insert_device_snapshot(
                    udid=udid,
                    site_name=self.site_names.get(udid, ''),
                    signal=float(sig) if isinstance(sig, (int, float)) else None,
                    voltage=float(vol) if isinstance(vol, (int, float)) else None,
                    frequency=ch1.get('freq') if ch1 else None,
                    temperature=ch1.get('temp') if ch1 else None,
                    pressure=ch1.get('calc') if ch1 else None,
                    water_level=ch1.get('water') if ch1 else None,
                    elevation=ch1.get('water_elevation') if ch1 else None,
                    status=ch1.get('quality', 'VALID') if ch1 else None,
                )
                self._log('INFO', f'入库成功: {udid} P水压={ch1.get("calc")} 水位={ch1.get("water")}')
            except Exception as e:
                self._log('ERROR', f'入库失败 {udid}: {e}')

    def _update_online_count(self):
        self.st_online.configure(text=f'在线: {sum(1 for d in self.devices.values() if d.get("online"))}')

    def _device_last_data_time(self, dev):
        last_data_time = dev.get('last_data_time')
        if isinstance(last_data_time, datetime):
            return last_data_time

        refresh_time = dev.get('refresh_time')
        if isinstance(refresh_time, str) and refresh_time and refresh_time != '-':
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S',
                        '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M', '%H:%M:%S'):
                try:
                    parsed = datetime.strptime(refresh_time, fmt)
                    if fmt == '%H:%M:%S':
                        parsed = parsed.replace(
                            year=datetime.now().year,
                            month=datetime.now().month,
                            day=datetime.now().day,
                        )
                    return parsed
                except ValueError:
                    pass
        return None

    def _has_recent_data(self, dev, now=None):
        last_data_time = self._device_last_data_time(dev)
        if last_data_time is None:
            return False
        now = now or datetime.now()
        return (now - last_data_time).total_seconds() < RECENT_DATA_ONLINE_SECONDS

    def _mark_device_offline(self, udid):
        self.devices[udid]['online'] = False
        self.tree.set(udid, 'status', '○ 离线')
        self._apply_tree_sort()
        self._log('WARN', f'设备离线: {udid}')

    def _start_stale_device_checker(self):
        self._expire_stale_devices()

    def _expire_stale_devices(self):
        now = datetime.now()
        changed = False
        for udid, dev in list(self.devices.items()):
            if dev.get('online') and self._device_last_data_time(dev) and not self._has_recent_data(dev, now):
                dev['online'] = False
                self.tree.set(udid, 'status', '○ 离线')
                self._log('WARN', f'设备超过1小时无数据，标记离线: {udid}')
                changed = True

        if changed:
            self._apply_tree_sort()
            self._update_online_count()

        self.root.after(STALE_DEVICE_CHECK_MS, self._expire_stale_devices)

    def _update_combo(self):
        self.cmd_udid['values'] = sorted(self.devices.keys())

    # ================================================================
    # 持久化
    # ================================================================
    def _path(self, fn):
        import sys
        d = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        return os.path.join(d, fn)

    def _load_site_names(self):
        try:
            p = self._path('sites.json')
            if os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    self.site_names = json.load(f)
        except:
            self.site_names = {}

    def _save_site_names(self):
        try:
            with open(self._path('sites.json'), 'w', encoding='utf-8') as f:
                json.dump(self.site_names, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log('ERROR', f'保存站点失败: {e}')

    def _load_device_formulas(self):
        try:
            p = self._path('formulas.json')
            if os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    self.device_formulas = json.load(f)
        except:
            self.device_formulas = {}

    def _save_device_formulas(self):
        try:
            with open(self._path('formulas.json'), 'w', encoding='utf-8') as f:
                json.dump(self.device_formulas, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log('ERROR', f'保存公式失败: {e}')

    # ================================================================
    # 日志
    # ================================================================
    def _log(self, lv, msg):
        self.msg_queue.put({'type': 'LOG', 'level': lv, 'time': datetime.now().strftime('%H:%M:%S'), 'message': str(msg)})

    def _append_log(self, lv, ts, msg):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f'[{ts}] ', 'TIME')
        self.log_text.insert(tk.END, f'{msg}\n', lv if lv in ('INFO', 'WARN', 'ERROR', 'CMD') else 'INFO')
        self.log_text.see(tk.END)
        self.log_count += 1
        if self.log_count > 50000:
            self.log_text.delete('1.0', '2.0')
            self.log_count -= 1
        self.log_text.configure(state=tk.DISABLED)

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete('1.0', tk.END)
        self.log_count = 0
        self.log_text.configure(state=tk.DISABLED)

    def _start_msg_processor(self):
        self._process_messages()

    def _process_messages(self):
        try:
            while True:
                m = self.msg_queue.get_nowait()
                mt = m.get('type', '')
                if mt == 'LOG':
                    self._append_log(m['level'], m['time'], m['message'])
                elif mt == 'DEVICE_ONLINE':
                    self._dev_online(m['udid'], m['data'].get('addr', ''))
                elif mt == 'DEVICE_OFFLINE':
                    self._dev_offline(m['udid'])
                elif mt == 'DEVICE_DATA':
                    self._dev_data(m['udid'], m['data'])
                    self.data_count += 1
                    self.st_data.configure(text=f'数据: {self.data_count} 包')
        except queue.Empty:
            pass
        self.root.after(100, self._process_messages)

    # ================================================================
    # 指令
    # ================================================================
    def _send_cmd(self, cmd):
        u = self.cmd_udid.get()
        if not u:
            messagebox.showwarning('提示', '请先选择设备')
            return
        self._do_send(u, cmd)

    def _send_custom_cmd(self):
        cmd = self.cmd_entry.get().strip()
        u = self.cmd_udid.get()
        if not cmd or not u:
            return
        self._do_send(u, cmd)
        self.cmd_entry.delete(0, tk.END)

    def _do_send(self, udid, cmd):
        if not self.tcp_server or not self.running:
            messagebox.showwarning('提示', '服务未启动')
            return
        if not cmd.endswith('#\r\n'):
            cmd = cmd.rstrip('\r\n')
            cmd += '\r\n' if cmd.endswith('#') else '#\r\n'
        self._log('CMD', f'→ {udid}: {cmd.strip()}')
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_send(udid, cmd.encode('ascii')), self.loop)

    async def _async_send(self, udid, b):
        try:
            ok, r = await self.tcp_server.send_command_to_device(udid, b, timeout=10)
            if ok:
                reply_str = (r.decode('ascii', errors='replace') if r else '(空)').strip()
                self._log('CMD', f'← {udid}: {reply_str}')
            else:
                self._log('WARN', f'← {udid}: 无应答')
        except Exception as e:
            self._log('ERROR', f'指令异常: {e}')

    # ================================================================
    # 启停
    # ================================================================
    def _start_server(self):
        if self.running:
            return
        from tcp_server import TCPServer
        from calculator import VibratingWireCalculator
        try:
            port = int(self.config.get('TCP_PORT', 9000))
            calc = VibratingWireCalculator(self.db_manager)
            self.tcp_server = TCPServer(host='0.0.0.0', port=port, db_manager=self.db_manager, calculator=calc)
            gh = GuiLogHandler(self.msg_queue)
            def on_gui(udid, dd):
                gh.dev_event(udid, 'DEVICE_DATA', dd)
            self.tcp_server.gui_data_callback = on_gui
            def on_online(udid, addr):
                gh.dev_event(udid, 'DEVICE_ONLINE', {'addr': addr})
            self.tcp_server._on_device_connect = on_online
            _odc = self.tcp_server._on_device_disconnect
            async def pdc(session):
                u = session.udid or f'IP_{session.addr[0]}'
                gh.dev_event(u, 'DEVICE_OFFLINE')
                await _odc(session)
            self.tcp_server._on_device_disconnect = pdc
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            def run():
                loop = self.loop
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self.tcp_server.start())
                    loop.run_forever()
                except Exception as e:
                    self._log('ERROR', f'服务异常: {e}')
            self.server_thread = threading.Thread(target=run, daemon=True)
            self.server_thread.start()
            time.sleep(0.5)
            self.running = True
            self.btn_start.configure(state=tk.DISABLED)
            self.btn_stop.configure(state=tk.NORMAL)
            self.lbl_run.configure(text='● 运行中', foreground=self.C['green'])
            self.st_run.configure(text='● 运行中', foreground=self.C['green'])
            self._log('INFO', f'TCP 已启动 0.0.0.0:{port}')
            self._update_db_status()
        except Exception as e:
            self._log('ERROR', f'启动失败: {e}')
            messagebox.showerror('启动失败', str(e))

    def _stop_server(self):
        if not self.running:
            return
        self._log('INFO', '正在停止...')
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=3)
        self.running = False
        self.btn_start.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)
        self.lbl_run.configure(text='● 已停止', foreground=self.C['red'])
        self.st_run.configure(text='● 已停止', foreground=self.C['red'])
        self._log('INFO', '服务已停止')

    def _on_closing(self):
        if self.running:
            if messagebox.askyesno('确认', '服务正在运行，确定退出？'):
                self._stop_server()
                self.root.destroy()
        else:
            self.root.destroy()

def run_gui(config, db_manager=None):
    root = tk.Tk()
    VSCollectorGUI(root, config, db_manager)
    root.mainloop()
    return 0
