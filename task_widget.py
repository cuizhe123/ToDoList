#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
轻量级任务桌面挂件
快捷键：Ctrl+Alt+Z 呼出/隐藏
功能：添加任务、完成任务、未完成任务自动顺延到次日、任务分类标签
"""

import tkinter as tk
from tkinter import ttk, messagebox
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import keyboard
import threading
import socket

SINGLE_INSTANCE_PORT = 39301

# ===== 设计令牌（Design Tokens）=====
COL_BG        = '#14171c'   # 窗口底色：暖近黑（非纯黑）
COL_PANEL     = '#1b1f26'   # 标题栏 / 面板
COL_CARD      = '#1f242e'   # 任务卡片
COL_CARD_HV   = '#272e3a'   # 卡片悬停
COL_INPUT     = '#232a35'   # 输入框
COL_BORDER    = '#2c3442'   # 卡片描边
COL_BORDER_HV = '#3c4656'   # 卡片悬停描边
COL_TXT_HI    = '#edeff3'   # 主文字
COL_TXT_MID   = '#9aa3b2'   # 次级文字
COL_TXT_LOW   = '#5c6572'   # 弱化文字
COL_ACCENT    = '#2fa8a0'   # 单一强调色（青绿，克制饱和度）
COL_ACCENT_HV = '#3cbcb3'   # 强调色悬停
COL_DANGER    = '#e06c66'   # 危险 / 删除
COL_SUCCESS   = '#4fbf8f'   # 完成态 / 进度满
COL_DONE_CARD = '#181c23'   # 完成任务卡片（更暗弱化）
COL_PROG_BG   = '#222936'   # 进度条轨道
COL_SCROLL_FG = '#3a4453'   # 滚动条滑块
COL_SCROLL_BG = '#20262f'   # 滚动条轨道

# 字体令牌（Windows 中文界面：微软雅黑 + Segoe UI Symbol 承载符号）
FONT_UI        = ('Microsoft YaHei UI', 10)
FONT_UI_BOLD   = ('Microsoft YaHei UI', 10, 'bold')
FONT_TITLE     = ('Microsoft YaHei UI', 12, 'bold')
FONT_SMALL     = ('Microsoft YaHei UI', 8)
FONT_SYMBOL    = ('Segoe UI Symbol', 13)
FONT_SYMBOL_SM = ('Segoe UI Symbol', 11)

# 间距令牌（4/8 节奏）
SP_XS, SP_SM, SP_MD, SP_LG = 4, 8, 12, 16

# 分类配置：category -> (文字, 前景色, 背景色) —— 柔和胶囊（暗底同色系，不刺眼）
CATEGORY_CONFIG = {
    None:  ('·',  COL_TXT_LOW, COL_CARD),
    '不急': ('不急', '#a8b3c2', '#2a3140'),
    '急':   ('急',  '#ff8a80', '#3a2328'),
    '长期': ('长期', '#7fd4a2', '#203530'),
}
CATEGORY_ORDER = [None, '不急', '急', '长期']


class SingleInstanceGuard:
    """单实例保护：绑定本地端口，绑定成功者为主实例；
    后来者连接端口通知主实例显示窗口后自动退出。"""

    def __init__(self, port):
        self.port = port
        self.sock = None

    def acquire(self):
        """尝试成为主实例，成功返回 True"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.bind(('127.0.0.1', self.port))
            self.sock.listen(1)
            self.sock.settimeout(0.5)
            return True
        except OSError:
            self.sock.close()
            self.sock = None
            return False

    def notify_show(self):
        """通知主实例显示窗口"""
        try:
            s = socket.create_connection(('127.0.0.1', self.port), timeout=1)
            s.sendall(b'show')
            s.close()
            return True
        except OSError:
            return False

    def listen(self, callback):
        """后台监听命令，收到 'show' 时调用 callback"""
        def _loop():
            while self.sock is not None:
                try:
                    conn, _ = self.sock.accept()
                    try:
                        data = conn.recv(16)
                        if data.strip() == b'show':
                            callback()
                    finally:
                        conn.close()
                except socket.timeout:
                    continue
                except OSError:
                    break
        threading.Thread(target=_loop, daemon=True).start()


class TaskWidget:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("今日list")
        self.root.geometry("640x560")
        self.root.configure(bg=COL_BG)

        # 无边框圆角挂件：移除系统标题栏 + DWM 系统圆角（延迟到窗口映射后设置，Win11 22000+）
        self.root.overrideredirect(True)
        self.root.after(300, self._apply_round_corner)
        
        # 数据文件路径（与程序同目录，统一存放于 D:\task_widget）
        self.data_file = Path(__file__).resolve().parent / 'task_widget.json'
        self.tasks = self.load_tasks()
        
        # 窗口状态
        self.is_visible = True
        
        # 设置窗口置顶
        self.root.attributes('-topmost', True)
        
        # 绑定关闭事件（最小化到托盘而不是退出）
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        
        self.setup_ui()
        self.refresh_task_list()
        
        # 注册全局快捷键（在新线程中）
        self.register_hotkey()
        
    def _apply_round_corner(self, window=None):
        """窗口映射后应用 DWM 系统圆角（Win11 22000+），失败自动降级为直角"""
        try:
            import ctypes
            win = window if window is not None else self.root
            hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), 2)  # GA_ROOT=2
            val = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(val), ctypes.sizeof(val))
        except Exception:
            pass
        
    def load_tasks(self):
        """加载任务数据"""
        if self.data_file.exists():
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 自动顺延未完成任务
                    return self.carryover_tasks(data)
            except:
                return {}
        return {}
    
    def carryover_tasks(self, tasks):
        """将过去日期的未完成任务顺延到今天"""
        today = datetime.now().strftime('%Y-%m-%d')
        today_tasks = tasks.get(today, [])
        
        # 收集所有过去日期的未完成任务
        for date_str in list(tasks.keys()):
            if date_str < today:
                for task in tasks[date_str]:
                    if not task['completed']:
                        # 标记为顺延任务
                        task['carried_from'] = date_str
                        today_tasks.append(task)
                # 删除过去的日期
                del tasks[date_str]
        
        if today_tasks:
            tasks[today] = today_tasks
        
        return tasks
    
    def save_tasks(self):
        """保存任务数据"""
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(self.tasks, f, ensure_ascii=False, indent=2)
    
    def setup_ui(self):
        """构建UI界面（无边框圆角挂件：自绘标题栏 + 今日进度条 + 卡片层次）"""
        # 顶部强调细线（品牌头条，克制）
        tk.Frame(self.root, bg=COL_ACCENT, height=2).pack(fill='x')

        # 自绘标题栏：日期 + 今日统计 + 退出按钮（无系统标题栏，整行为拖拽区）
        title_frame = tk.Frame(self.root, bg=COL_PANEL, height=64)
        title_frame.pack(fill='x', pady=(0, SP_SM))
        title_frame.pack_propagate(False)

        today_str = datetime.now().strftime('%Y年%m月%d日  %A')
        title_label = tk.Label(
            title_frame,
            text=today_str,
            font=FONT_TITLE,
            bg=COL_PANEL,
            fg=COL_TXT_HI
        )
        title_label.pack(side='left', padx=(SP_LG, 0), pady=8)

        self.count_label = tk.Label(
            title_frame,
            text="",
            font=FONT_SMALL,
            bg=COL_PANEL,
            fg=COL_TXT_MID
        )
        self.count_label.pack(side='left', padx=(SP_SM, 0), pady=8)

        # 标题栏右侧按钮组（惯例顺序：最小化在左 · 关闭在最右）
        # ✕ 关闭（最右）：hover 变红底白字，Windows 11 风格危险提示
        quit_btn = tk.Button(
            title_frame,
            text="✕",
            font=FONT_SYMBOL,
            bg=COL_PANEL,
            fg=COL_TXT_MID,
            activebackground=COL_DANGER,
            activeforeground='#ffffff',
            relief='flat',
            bd=0,
            cursor='hand2',
            command=self.root.destroy,
            width=3,
            pady=5
        )
        quit_btn.pack(side='right', padx=(0, SP_SM))
        quit_btn.bind('<Enter>', lambda e: quit_btn.configure(bg=COL_DANGER, fg='#ffffff'))
        quit_btn.bind('<Leave>', lambda e: quit_btn.configure(bg=COL_PANEL, fg=COL_TXT_MID))

        # — 最小化（关闭左侧）：hover 亮底亮字，等效隐藏（Ctrl+Alt+Z 恢复）
        min_btn = tk.Button(
            title_frame,
            text="—",
            font=FONT_SYMBOL,
            bg=COL_PANEL,
            fg=COL_TXT_MID,
            activebackground=COL_CARD_HV,
            activeforeground=COL_TXT_HI,
            relief='flat',
            bd=0,
            cursor='hand2',
            command=self.hide_window,
            width=3,
            pady=5
        )
        min_btn.pack(side='right', padx=(0, 2))
        min_btn.bind('<Enter>', lambda e: min_btn.configure(bg=COL_CARD_HV, fg=COL_TXT_HI))
        min_btn.bind('<Leave>', lambda e: min_btn.configure(bg=COL_PANEL, fg=COL_TXT_MID))

        # 窗口拖动（无边框窗口必须自绘拖拽手势）
        self._drag = None

        def start_drag(e):
            self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

        def do_drag(e):
            if self._drag:
                x = e.x_root - self._drag[0]
                y = e.y_root - self._drag[1]
                self.root.geometry(f"+{x}+{y}")

        for w in (title_frame, title_label, self.count_label):
            w.bind('<Button-1>', start_drag)
            w.bind('<B1-Motion>', do_drag)

        # 输入区（一体式容器：描边包裹输入框 + 圆形添加按钮）
        input_frame = tk.Frame(self.root, bg=COL_INPUT, highlightthickness=1, highlightbackground=COL_BORDER)
        input_frame.pack(fill='x', padx=SP_LG, pady=(SP_SM, SP_MD))

        self.task_entry = tk.Entry(
            input_frame,
            font=('Microsoft YaHei UI', 11),
            bg=COL_INPUT,
            fg=COL_TXT_HI,
            insertbackground=COL_TXT_HI,
            relief='flat',
            bd=0
        )
        self.task_entry.pack(side='left', fill='x', expand=True, ipady=9, ipadx=12)
        self.task_entry.bind('<Return>', lambda e: self.add_task())
        self._placeholder_active = True
        self._set_placeholder()
        self.task_entry.bind('<FocusIn>', lambda e: self._clear_placeholder())
        self.task_entry.bind('<FocusOut>', lambda e: self._set_placeholder())

        add_btn = tk.Button(
            input_frame,
            text="＋",
            font=('Microsoft YaHei UI', 15, 'bold'),
            bg=COL_ACCENT,
            fg='#ffffff',
            activebackground=COL_ACCENT_HV,
            activeforeground='#ffffff',
            relief='flat',
            bd=0,
            cursor='hand2',
            command=self.add_task,
            width=3
        )
        add_btn.pack(side='right', padx=(6, 0), ipady=7)
        add_btn.bind('<Enter>', lambda e: add_btn.configure(bg=COL_ACCENT_HV))
        add_btn.bind('<Leave>', lambda e: add_btn.configure(bg=COL_ACCENT))

        # 任务列表区域
        list_frame = tk.Frame(self.root, bg=COL_BG)
        list_frame.pack(fill='both', expand=True, padx=SP_LG, pady=(0, SP_SM))

        # 滚动条（细、与面板同色）
        scrollbar = tk.Scrollbar(list_frame, bg=COL_PANEL, troughcolor=COL_BG, relief='flat', bd=0, width=8)
        scrollbar.pack(side='right', fill='y')

        # Canvas用于滚动
        self.canvas = tk.Canvas(
            list_frame,
            bg=COL_BG,
            highlightthickness=0,
            yscrollcommand=scrollbar.set
        )
        self.canvas.pack(side='left', fill='both', expand=True)
        scrollbar.config(command=self.canvas.yview)

        # 任务容器
        self.task_container = tk.Frame(self.canvas, bg=COL_BG)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.task_container, anchor='nw')

        # 绑定容器大小变化
        self.task_container.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self.on_canvas_configure)

        # 鼠标滚轮绑定
        self.canvas.bind_all("<MouseWheel>", self.on_mousewheel)

        # 底部状态栏（原生壳：操作提示 + 统计）
        status_bar = tk.Frame(self.root, bg=COL_PANEL)
        status_bar.pack(fill='x', side='bottom')
        tk.Frame(status_bar, bg=COL_BG, height=1).pack(fill='x')
        hint_label = tk.Label(
            status_bar,
            text="Ctrl+Alt+Z 隐藏/显示  ·  右键任务更多操作",
            font=FONT_SMALL,
            bg=COL_PANEL,
            fg=COL_TXT_LOW
        )
        hint_label.pack(side='left', padx=SP_LG, pady=6)

        self.status_label = tk.Label(
            status_bar,
            text="",
            font=FONT_SMALL,
            bg=COL_PANEL,
            fg=COL_TXT_MID
        )
        self.status_label.pack(side='right', padx=SP_LG, pady=6)

    # ---- placeholder 支持 ----
    def _set_placeholder(self):
        if not self.task_entry.get() and not self._placeholder_active:
            self.task_entry.delete(0, tk.END)
            self.task_entry.insert(0, "添加任务，回车确认…")
            self.task_entry.configure(fg=COL_TXT_LOW)
            self._placeholder_active = True

    def _clear_placeholder(self):
        if self._placeholder_active:
            self.task_entry.delete(0, tk.END)
            self.task_entry.configure(fg=COL_TXT_HI)
            self._placeholder_active = False

    def on_canvas_configure(self, event):
        """调整Canvas窗口宽度"""
        self.canvas.itemconfig(self.canvas_window, width=event.width)
    
    def on_mousewheel(self, event):
        """鼠标滚轮滚动"""
        self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")
    
    def add_task(self):
        """添加新任务"""
        task_text = self.task_entry.get().strip()
        # placeholder 激活时视为空输入
        if self._placeholder_active or not task_text:
            return
        
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.tasks:
            self.tasks[today] = []
        
        new_task = {
            'text': task_text,
            'completed': False,
            'created_at': datetime.now().isoformat(),
            'category': None
        }
        
        self.tasks[today].append(new_task)
        self.save_tasks()
        self.task_entry.delete(0, tk.END)
        # 不在此处插入占位符：焦点仍在输入框，FocusIn 不会再触发，
        # 直接保持空输入框让用户继续输入；FocusOut 会自动恢复占位符。
        self._placeholder_active = False
        self.task_entry.configure(fg=COL_TXT_HI)
        self.task_entry.focus_set()
        self.refresh_task_list()
    
    def toggle_task(self, index):
        """切换任务完成状态"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today in self.tasks and index < len(self.tasks[today]):
            self.tasks[today][index]['completed'] = not self.tasks[today][index]['completed']
            self.save_tasks()
            self.refresh_task_list()
    
    def delete_task(self, index):
        """删除任务"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today in self.tasks and index < len(self.tasks[today]):
            del self.tasks[today][index]
            self.save_tasks()
            self.refresh_task_list()
    
    def edit_task(self, index):
        """弹出对话框修改任务内容（多行文本框，自动换行）"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.tasks or index >= len(self.tasks[today]):
            return
        old_text = self.tasks[today][index]['text']

        dlg = tk.Toplevel(self.root)
        dlg.title("修改任务")
        dlg.configure(bg=COL_BG)
        dlg.resizable(True, True)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.minsize(320, 180)

        # 居中于主窗口
        W, H = 340, 220
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - W) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - H) // 2
        dlg.geometry(f"{W}x{H}+{x}+{y}")

        # 顶部标签
        tk.Label(
            dlg, text="修改任务内容", font=FONT_UI_BOLD,
            bg=COL_BG, fg=COL_TXT_HI, anchor='w'
        ).pack(fill='x', padx=15, pady=(12, 4))

        # 多行文本框（wrap=word，自动换行，不超出边框）
        txt = tk.Text(
            dlg,
            wrap='word',
            font=('Microsoft YaHei UI', 10),
            bg=COL_INPUT,
            fg=COL_TXT_HI,
            insertbackground=COL_TXT_HI,
            relief='flat',
            bd=0,
            padx=10,
            pady=8,
            height=5,
            highlightthickness=1,
            highlightbackground=COL_BORDER,
            highlightcolor=COL_ACCENT,
        )
        txt.insert('1.0', old_text)
        txt.pack(padx=15, pady=(0, 4), fill='both', expand=True)
        txt.focus_set()
        txt.mark_set('insert', 'end')

        # 提示
        tk.Label(
            dlg, text="Ctrl+Enter 保存  ·  Esc 取消",
            font=FONT_SMALL, bg=COL_BG, fg=COL_TXT_LOW
        ).pack(anchor='e', padx=15)

        def do_save():
            new_text = txt.get('1.0', 'end').strip()
            if new_text:
                self.tasks[today][index]['text'] = new_text
                self.save_tasks()
                self.refresh_task_list()
            dlg.destroy()

        btn_frame = tk.Frame(dlg, bg=COL_BG)
        btn_frame.pack(pady=(4, 12))
        tk.Button(
            btn_frame, text="保存修改",
            font=('Microsoft YaHei UI', 9),
            bg=COL_ACCENT, fg='#ffffff',
            activebackground=COL_ACCENT_HV, activeforeground='#ffffff',
            relief='flat', bd=0, cursor='hand2',
            command=do_save, width=8
        ).pack(side='left', padx=5)
        tk.Button(
            btn_frame, text="取消",
            font=('Microsoft YaHei UI', 9),
            bg=COL_PANEL, fg=COL_TXT_MID,
            activebackground=COL_CARD_HV, activeforeground=COL_TXT_HI,
            relief='flat', bd=0, cursor='hand2',
            command=dlg.destroy, width=8
        ).pack(side='left', padx=5)

        txt.bind('<Control-Return>', lambda e: do_save())
        txt.bind('<Escape>', lambda e: dlg.destroy())
    
    def show_category_menu(self, index, widget):
        """点击分类标签弹出选择菜单：长期 / 急 / 不急"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.tasks or index >= len(self.tasks[today]):
            return
        current = self.tasks[today][index].get('category', None)
        menu = tk.Menu(self.root, tearoff=0, font=('Microsoft YaHei UI', 9),
                       bg=COL_PANEL, fg=COL_TXT_HI,
                       activebackground=COL_ACCENT, activeforeground='#ffffff')
        # 选项文字颜色：长期绿 / 急红 / 不急白
        menu_fg = {'长期': '#7fd4a2', '急': '#ff8a80', '不急': '#a8b3c2'}
        for cat in ['长期', '急', '不急']:
            label = f"✓ {cat}" if cat == current else cat
            menu.add_command(
                label=label,
                foreground=menu_fg[cat],
                command=lambda c=cat: self.set_category(index, c)
            )
        try:
            menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally:
            menu.grab_release()

    def set_category(self, index, category):
        """设置任务分类"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.tasks or index >= len(self.tasks[today]):
            return
        self.tasks[today][index]['category'] = category
        self.save_tasks()
        self.refresh_task_list()

    def refresh_task_list(self):
        """刷新任务列表显示"""
        # 清空现有任务
        for widget in self.task_container.winfo_children():
            widget.destroy()
        
        today = datetime.now().strftime('%Y-%m-%d')
        tasks = self.tasks.get(today, [])
        
        # 标题栏统计 + 状态栏统计（原生壳信息密度）
        done = sum(1 for t in tasks if t.get('completed'))
        pending = len(tasks) - done
        if hasattr(self, 'count_label'):
            self.count_label.config(text=f"{len(tasks)} 项 · 待办 {pending}")
        if hasattr(self, 'status_label'):
            self.status_label.config(text=f"已完成 {done} / {len(tasks)}")
        
        if not tasks:
            empty_frame = tk.Frame(self.task_container, bg=COL_BG)
            empty_frame.pack(fill='both', pady=46)
            tk.Label(
                empty_frame,
                text="暂无任务",
                font=('Microsoft YaHei UI', 13, 'bold'),
                bg=COL_BG,
                fg=COL_TXT_MID
            ).pack()
            tk.Label(
                empty_frame,
                text="在上方输入内容后按回车，创建今天的第一个任务",
                font=FONT_SMALL,
                bg=COL_BG,
                fg=COL_TXT_LOW
            ).pack(pady=(6, 0))
            return
        
        # 二级排序：未完成优先；同级无标签最前，再按 长期→急→不急（保留原始index供操作回调）
        SORT_KEY = {None: 0, '长期': 1, '急': 2, '不急': 3}
        sorted_tasks = sorted(enumerate(tasks), key=lambda x: (
            1 if x[1].get('completed') else 0,
            SORT_KEY.get(x[1].get('category'), 0)
        ))
        for i, task in sorted_tasks:
            self.create_task_widget(i, task)
    
    def create_task_widget(self, index, task):
        """创建单个任务组件（分类色条 + 圆形复选框 + 悬停层次）"""
        completed = task.get('completed', False)
        cat = task.get('category', None)
        card_bg = COL_DONE_CARD if completed else COL_CARD

        task_frame = tk.Frame(
            self.task_container,
            bg=card_bg,
            highlightthickness=1,
            highlightbackground=COL_BORDER,
            bd=0
        )
        task_frame.pack(fill='x', pady=3)

        # 分类色条（左侧 3px 竖条：类别色视觉锚点；无分类用中性灰）
        cat_text, cat_fg, cat_bg = CATEGORY_CONFIG.get(cat, CATEGORY_CONFIG[None])
        bar_color = {'急': '#e06c66', '长期': '#4fbf8f', '不急': '#8a94a6'}.get(cat, COL_CARD_HV)
        color_bar = tk.Label(task_frame, bg=bar_color, width=1)
        color_bar.pack(side='left', fill='both')

        # 圆形复选框（Canvas 自绘：圆环 → 实心+对勾，比字符 ○/✓ 更精致）
        check_canvas = tk.Canvas(task_frame, width=22, height=22, bg=card_bg, highlightthickness=0, bd=0)
        check_canvas.pack(side='left', padx=(10, 6), pady=8)
        self._draw_check(check_canvas, completed, False, card_bg)
        check_canvas.bind('<Button-1>', lambda e: self.toggle_task(index))

        # 分类胶囊（点击弹出菜单选择分类；hover 时保持自身胶囊色）
        cat_btn = tk.Button(
            task_frame,
            text=cat_text,
            font=FONT_SMALL,
            bg=cat_bg,
            fg=cat_fg,
            activebackground=cat_bg,
            activeforeground=cat_fg,
            relief='flat',
            bd=0,
            cursor='hand2',
            padx=6,
            pady=2
        )
        cat_btn.config(command=lambda idx=index, w=cat_btn: self.show_category_menu(idx, w))
        cat_btn.pack(side='left', padx=(0, 6), pady=8)

        # 任务文本（完成态：弱化 + 删除线）
        text_color = COL_TXT_LOW if completed else COL_TXT_HI
        text_style = 'overstrike' if completed else 'normal'
        task_label = tk.Label(
            task_frame,
            text=task['text'],
            font=('Microsoft YaHei UI', 10, text_style),
            bg=card_bg,
            fg=text_color,
            anchor='w',
            justify='left'
        )
        task_label.pack(side='left', fill='x', expand=True, padx=5, pady=10)

        # 修改内容按钮（位于删除按钮左侧）
        edit_btn = tk.Button(
            task_frame,
            text="✎",
            font=FONT_SYMBOL_SM,
            bg=card_bg,
            fg=COL_TXT_LOW,
            activebackground=COL_CARD_HV,
            activeforeground=COL_TXT_HI,
            relief='flat',
            bd=0,
            cursor='hand2',
            command=lambda: self.edit_task(index),
            width=2
        )
        edit_btn.pack(side='right', padx=(0, 2), pady=8)
        edit_btn.bind('<Enter>', lambda e: edit_btn.configure(fg=COL_TXT_HI))
        edit_btn.bind('<Leave>', lambda e: edit_btn.configure(fg=COL_TXT_LOW))

        # 删除按钮
        del_btn = tk.Button(
            task_frame,
            text="✕",
            font=FONT_SYMBOL_SM,
            bg=card_bg,
            fg=COL_TXT_LOW,
            activebackground=COL_CARD_HV,
            activeforeground=COL_DANGER,
            relief='flat',
            bd=0,
            cursor='hand2',
            command=lambda: self.delete_task(index),
            width=2
        )
        del_btn.pack(side='right', padx=(0, 10), pady=8)
        del_btn.bind('<Enter>', lambda e: del_btn.configure(fg=COL_DANGER))
        del_btn.bind('<Leave>', lambda e: del_btn.configure(fg=COL_TXT_LOW))

        # 鼠标悬停效果（完成态卡片保持弱化，不参与提亮）
        hover_widgets = [task_frame, task_label, edit_btn, del_btn]

        def on_enter(e):
            if completed:
                return
            task_frame.configure(bg=COL_CARD_HV, highlightbackground=COL_BORDER_HV)
            for w in hover_widgets:
                w.configure(bg=COL_CARD_HV)
            if cat is None:
                cat_btn.configure(bg=COL_CARD_HV)
            check_canvas.configure(bg=COL_CARD_HV)
            self._draw_check(check_canvas, False, True, COL_CARD_HV)

        def on_leave(e):
            if completed:
                return
            task_frame.configure(bg=COL_CARD, highlightbackground=COL_BORDER)
            for w in hover_widgets:
                w.configure(bg=COL_CARD)
            if cat is None:
                cat_btn.configure(bg=COL_CARD)
            check_canvas.configure(bg=COL_CARD)
            self._draw_check(check_canvas, False, False, COL_CARD)

        for w in hover_widgets + [cat_btn, check_canvas]:
            w.bind('<Enter>', on_enter)
            w.bind('<Leave>', on_leave)

        # 右键菜单（原生壳：任务行右键 = 完整操作）
        def on_right_click(e):
            self.show_task_menu(index, e)

        # 双击任务行打开详情页（长任务全文 + 编辑/删除/完成）
        def on_double_click(e):
            self.open_task_detail(index)

        for w in [task_frame, check_canvas, task_label, edit_btn, del_btn]:
            w.bind('<Button-3>', on_right_click)
        for w in [task_frame, task_label]:
            w.bind('<Double-Button-1>', on_double_click)

    def _draw_check(self, canvas, completed, hover, bg):
        """绘制圆形复选框：未完成=圆环，完成=实心+白色对勾"""
        canvas.delete('all')
        r = 8
        cx, cy = 11, 11
        if completed:
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=COL_SUCCESS, outline=COL_SUCCESS)
            canvas.create_line(cx - 3.5, cy - 0.5, cx - 1, cy + 2.5, cx + 4, cy - 3,
                               fill='#ffffff', width=2, capstyle='round', joinstyle='round')
        else:
            outline = COL_BORDER_HV if hover else COL_BORDER
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=bg, outline=outline, width=1.5)

    def show_task_menu(self, index, event):
        """任务右键菜单：完成/分类/编辑/删除"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.tasks or index >= len(self.tasks[today]):
            return
        task = self.tasks[today][index]
        completed = task.get('completed', False)

        menu = tk.Menu(self.root, tearoff=0, font=('Microsoft YaHei UI', 9),
                       bg=COL_PANEL, fg=COL_TXT_HI,
                       activebackground=COL_ACCENT, activeforeground='#ffffff')

        menu.add_command(
            label="☰ 打开详情",
            command=lambda: self.open_task_detail(index)
        )
        menu.add_separator()
        menu.add_command(
            label="✓ 标记完成" if not completed else "↺ 恢复未完成",
            command=lambda: self.toggle_task(index)
        )
        cat_menu = tk.Menu(menu, tearoff=0, font=('Microsoft YaHei UI', 9),
                           bg=COL_PANEL, fg=COL_TXT_HI,
                           activebackground=COL_ACCENT, activeforeground='#ffffff')
        menu_fg = {'长期': '#7fd4a2', '急': '#ff8a80', '不急': '#a8b3c2'}
        for c in ['长期', '急', '不急', None]:
            label = "无分类" if c is None else c
            if c == task.get('category'):
                label = "✓ " + label
            cat_menu.add_command(
                label=label,
                foreground=menu_fg.get(c, COL_TXT_LOW),
                command=lambda cc=c: self.set_category(index, cc)
            )
        menu.add_cascade(label="分类", menu=cat_menu)
        menu.add_command(label="✎ 编辑", command=lambda: self.edit_task(index))
        menu.add_separator()
        menu.add_command(label="✕ 删除", foreground=COL_DANGER,
                         command=lambda: self.delete_task(index))

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def open_task_detail(self, index):
        """在主窗口内叠加内联详情面板（不开新窗口）"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.tasks or index >= len(self.tasks[today]):
            return
        task = self.tasks[today][index]

        # 如果已有详情面板则先销毁（防止重复打开）
        if hasattr(self, '_detail_panel') and self._detail_panel is not None:
            try:
                self._detail_panel.destroy()
            except Exception:
                pass
            self._detail_panel = None

        # 内联面板：place 覆盖整个主窗口，z 轴最顶层
        panel = tk.Frame(self.root, bg=COL_BG, relief='flat', bd=0)
        panel.place(x=0, y=0, relwidth=1, relheight=1)
        panel.lift()
        self._detail_panel = panel

        def close_panel():
            panel.place_forget()
            panel.destroy()
            self._detail_panel = None

        # 顶部强调线
        tk.Frame(panel, bg=COL_ACCENT, height=2).pack(fill='x')

        # 标题栏
        bar = tk.Frame(panel, bg=COL_PANEL, height=40)
        bar.pack(fill='x')
        bar.pack_propagate(False)
        title_lbl = tk.Label(bar, text="任务详情", font=FONT_UI_BOLD, bg=COL_PANEL, fg=COL_TXT_HI)
        title_lbl.pack(side='left', padx=(SP_LG, 0), pady=8)
        close_btn = tk.Button(
            bar, text="✕", font=FONT_SYMBOL_SM, bg=COL_PANEL, fg=COL_TXT_MID,
            activebackground=COL_PANEL, activeforeground=COL_DANGER,
            relief='flat', bd=0, cursor='hand2', command=close_panel, width=3
        )
        close_btn.pack(side='right', padx=(0, SP_SM), pady=4)
        close_btn.bind('<Enter>', lambda e: close_btn.configure(fg=COL_DANGER))
        close_btn.bind('<Leave>', lambda e: close_btn.configure(fg=COL_TXT_MID))

        # 状态行：分类胶囊 + 完成切换
        row = tk.Frame(panel, bg=COL_BG)
        row.pack(fill='x', padx=SP_LG, pady=(SP_MD, SP_SM))
        cat = task.get('category', None)
        cat_text, cat_fg, cat_bg = CATEGORY_CONFIG.get(cat, CATEGORY_CONFIG[None])
        cat_btn = tk.Button(
            row, text=cat_text, font=FONT_SMALL, bg=cat_bg, fg=cat_fg,
            activebackground=cat_bg, activeforeground=cat_fg,
            relief='flat', bd=0, cursor='hand2', padx=8, pady=3
        )
        cat_btn.pack(side='left')
        completed = task.get('completed', False)

        def pick_cat():
            self.show_category_menu(index, cat_btn)
            c = self.tasks[today][index].get('category', None)
            ct, cf, cb = CATEGORY_CONFIG.get(c, CATEGORY_CONFIG[None])
            cat_btn.config(text=ct, fg=cf, bg=cb, activebackground=cb, activeforeground=cf)
        cat_btn.config(command=pick_cat)

        def do_toggle():
            self.toggle_task(index)
            close_panel()
        toggle_btn = tk.Button(
            row,
            text="✓ 标记完成" if not completed else "↺ 恢复未完成",
            font=FONT_SMALL, bg=COL_PANEL,
            fg=COL_SUCCESS if not completed else COL_TXT_MID,
            activebackground=COL_CARD_HV,
            activeforeground=COL_SUCCESS if not completed else COL_TXT_MID,
            relief='flat', bd=0, cursor='hand2', padx=10, pady=3,
            command=do_toggle
        )
        toggle_btn.pack(side='right')

        # 按钮行先 pack（固定底部），text 再 pack 填充剩余空间
        btns = tk.Frame(panel, bg=COL_BG)
        btns.pack(fill='x', padx=SP_LG, pady=(0, SP_MD), side='bottom')

        # 完整内容（多行可编辑，wrap 自动换行）
        text = tk.Text(
            panel, wrap='word', font=('Microsoft YaHei UI', 11),
            bg=COL_INPUT, fg=COL_TXT_HI, insertbackground=COL_TXT_HI,
            relief='flat', bd=0, padx=12, pady=10,
            highlightthickness=1, highlightbackground=COL_BORDER
        )
        text.insert('1.0', task['text'])
        text.pack(fill='both', expand=True, padx=SP_LG, pady=(0, SP_SM))

        def do_save():
            new_text = text.get('1.0', 'end').strip()
            if new_text:
                self.tasks[today][index]['text'] = new_text
                self.save_tasks()
                self.refresh_task_list()
            close_panel()

        save_btn = tk.Button(
            btns, text="保存修改", font=('Microsoft YaHei UI', 9),
            bg=COL_ACCENT, fg='#ffffff', activebackground=COL_ACCENT_HV,
            activeforeground='#ffffff', relief='flat', bd=0, cursor='hand2',
            command=do_save, width=8
        )
        save_btn.pack(side='left', padx=(0, 6))

        def do_delete():
            if messagebox.askyesno("删除任务", "确定删除该任务吗？", parent=self.root):
                self.delete_task(index)
                close_panel()
        del_btn = tk.Button(
            btns, text="删除任务", font=('Microsoft YaHei UI', 9),
            bg=COL_PANEL, fg=COL_DANGER, activebackground=COL_CARD_HV,
            activeforeground=COL_DANGER, relief='flat', bd=0, cursor='hand2',
            command=do_delete, width=8
        )
        del_btn.pack(side='left')

        close_btn2 = tk.Button(
            btns, text="✓ 完成", font=('Microsoft YaHei UI', 9, 'bold'),
            bg=COL_SUCCESS, fg='#ffffff', activebackground='#27ae60',
            activeforeground='#ffffff', relief='flat', bd=0, cursor='hand2',
            command=close_panel, width=9, pady=3
        )
        close_btn2.pack(side='right')

        text.bind('<Control-Return>', lambda e: do_save())
        text.bind('<Escape>', lambda e: close_panel())
        text.focus_set()

    def toggle_window(self):
        """切换窗口显示/隐藏"""
        if self.is_visible:
            self.hide_window()
        else:
            self.show_window()
    
    def hide_window(self):
        """隐藏窗口"""
        self.root.withdraw()
        self.is_visible = False
    
    def show_window(self):
        """显示窗口"""
        self.root.deiconify()
        self.root.attributes('-topmost', True)
        self.root.focus_force()
        self.is_visible = True
    
    def register_hotkey(self):
        """注册全局快捷键"""
        def hotkey_handler():
            self.root.after(0, self.toggle_window)
        
        try:
            keyboard.add_hotkey('ctrl+alt+z', hotkey_handler)
        except Exception as e:
            print(f"快捷键注册失败: {e}")
    
    def run(self):
        """启动应用"""
        self.root.mainloop()


if __name__ == '__main__':
    guard = SingleInstanceGuard(SINGLE_INSTANCE_PORT)
    if guard.acquire():
        # 主实例：正常运行
        app = TaskWidget()
        guard.listen(app.show_window)
        app.run()
    else:
        # 已有实例在运行：通知它显示窗口，本实例退出
        guard.notify_show()
