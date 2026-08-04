#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
今日 list — 桌面任务挂件 v2
快捷键：Ctrl+Alt+Z 呼出/隐藏
功能：添加任务、分类标签（急/不急/长期）、未完成任务自动顺延到次日、
      长期任务次日重置、手动排序、分类过滤、已完成折叠、撤销删除、
      完成率动画进度条、窗口拖拽移动
数据文件：task_widget.json（与 v1 格式完全兼容）
"""

import tkinter as tk
from tkinter import messagebox
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
COL_PROG_BG   = '#222936'   # 进度条轨道（已停用，保留兼容）
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
        self.root.title("今日 list")
        self.root.configure(bg=COL_BG)
        self.root.overrideredirect(True)  # 无边框
        self.root.attributes('-topmost', True)

        # 尝试设置图标（相对路径，兼容整理后结构）
        try:
            icon_path = Path(__file__).resolve().parent / 'assets' / 'icon.ico'
            if icon_path.exists():
                self.root.iconbitmap(str(icon_path))
        except Exception:
            pass

        # 窗口默认位置：右上角
        W, H = 640, 560
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self._win_x, self._win_y = sw - W - 24, 64
        self.root.geometry(f"{W}x{H}+{self._win_x}+{self._win_y}")

        # 拖拽窗口状态
        self._drag_win = None

        # 数据
        self.base_dir = Path(__file__).resolve().parent
        self.data_file = self.base_dir / 'task_widget.json'
        self.tasks = self.load_tasks()
        self.schedule = self._load_schedule()   # 今日安排数据
        self._schedule_win = None               # 今日安排面板引用
        self.timeline = self._load_timeline()   # 每日时间线数据
        self._timeline_win = None               # 每日时间线面板引用

        # 视图状态
        self._filter = 'all'        # all / 急 / 长期 / 不急 / done
        self._done_folded = False   # 已完成分区是否折叠
        self._undo_stack = []       # 撤销删除栈
        self._default_cat = None    # 输入框预设分类（快捷添加）
        # 增量渲染状态：卡片widget与数据顺序保持一致（操作只改单张卡，不整表重建）
        self._cards = []            # 每项: {'f': frame, 'data': task, 'label':..., 'cat_btn':...}
        self._done_header = None    # 已完成折叠header（常驻）
        self._done_fold_btn = None
        self._done_count_lbl = None
        self._empty_frame = None    # 空状态frame（常驻）
        self._empty_msg_lbl = None

        # UI
        self._build_ui()
        self._build_all_cards()
        self.register_hotkey()

        # 圆角（Win11 可选）
        self._try_round_corner()

    # ---------- 数据层 ----------

    def load_tasks(self):
        """加载任务并执行跨日迁移：未完成顺延、长期任务重置"""
        if not self.data_file.exists():
            return {}
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                tasks = json.load(f)
        except Exception:
            return {}

        today = datetime.now().strftime('%Y-%m-%d')
        today_tasks = tasks.get(today, [])

        for date_str in list(tasks.keys()):
            if date_str < today:
                for task in tasks[date_str]:
                    if not task.get('completed') or task.get('category') == '长期':
                        # 长期任务已完成：新的一天重置为未完成，重新打卡
                        if task.get('completed') and task.get('category') == '长期':
                            task['completed'] = False
                        # 标记为顺延任务
                        task['carried_from'] = date_str
                        today_tasks.append(task)
                # 删除过去的日期
                del tasks[date_str]

        if today_tasks:
            tasks[today] = today_tasks
        return tasks

    def save_tasks(self):
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(self.tasks, f, ensure_ascii=False, indent=2)

    # ---------- UI 构建 ----------

    def _build_ui(self):
        # ── 标题栏（可拖拽）──
        title_bar = tk.Frame(self.root, bg=COL_PANEL, height=44)
        title_bar.pack(fill='x')
        title_bar.pack_propagate(False)

        today = datetime.now()
        self.title_lbl = tk.Label(
            title_bar,
            text=f"今日 · {today.month}月{today.day}日 {self._weekday_cn(today.weekday())}",
            font=FONT_TITLE, bg=COL_PANEL, fg=COL_TXT_HI
        )
        self.title_lbl.pack(side='left', padx=(SP_LG, 0), pady=8)

        # 今日安排按钮（紧贴标题文字）
        sched_btn = tk.Button(
            title_bar, text="📋", font=FONT_SYMBOL_SM,
            bg=COL_PANEL, fg=COL_TXT_MID,
            activebackground=COL_PANEL, activeforeground=COL_ACCENT,
            relief='flat', bd=0, cursor='hand2', takefocus=0,
            command=self.open_schedule_panel
        )
        sched_btn.pack(side='left', padx=(SP_XS, 0), pady=8)
        sched_btn.bind('<Enter>', lambda e: sched_btn.configure(fg=COL_ACCENT))
        sched_btn.bind('<Leave>', lambda e: sched_btn.configure(fg=COL_TXT_MID))

        # 每日时间线按钮（紧贴今日安排按钮）
        tl_btn = tk.Button(
            title_bar, text="⏱", font=FONT_SYMBOL_SM,
            bg=COL_PANEL, fg=COL_TXT_MID,
            activebackground=COL_PANEL, activeforeground=COL_ACCENT,
            relief='flat', bd=0, cursor='hand2', takefocus=0,
            command=self.open_timeline_panel
        )
        tl_btn.pack(side='left', padx=(SP_XS, 0), pady=8)
        tl_btn.bind('<Enter>', lambda e: tl_btn.configure(fg=COL_ACCENT))
        tl_btn.bind('<Leave>', lambda e: tl_btn.configure(fg=COL_TXT_MID))

        self.count_label = tk.Label(
            title_bar, text="", font=FONT_SMALL,
            bg=COL_PANEL, fg=COL_TXT_LOW
        )
        self.count_label.pack(side='right', padx=(0, SP_SM), pady=8)

        # 窗口控制按钮：— 最小化（隐藏到后台） / ✕ 退出
        win_ctrl = tk.Frame(title_bar, bg=COL_PANEL)
        win_ctrl.pack(side='right', padx=(0, SP_XS), pady=6)
        quit_btn = tk.Button(
            win_ctrl, text="✕", font=FONT_SYMBOL_SM, bg=COL_PANEL, fg=COL_TXT_MID,
            activebackground='#3a1f1f', activeforeground='#e06c66',
            relief='flat', bd=0, cursor='hand2', takefocus=0, command=self.quit_app, width=2
        )
        quit_btn.pack(side='right')
        hide_btn = tk.Button(
            win_ctrl, text="—", font=FONT_SYMBOL_SM, bg=COL_PANEL, fg=COL_TXT_MID,
            activebackground=COL_PANEL, activeforeground=COL_TXT_HI,
            relief='flat', bd=0, cursor='hand2', takefocus=0, command=self.hide_window, width=2
        )
        hide_btn.pack(side='right')

        # 标题栏拖拽
        for w in [title_bar, self.title_lbl]:
            w.bind('<Button-1>', self._start_drag)
            w.bind('<B1-Motion>', self._on_drag)

        # ── 输入区 ──
        input_frame = tk.Frame(self.root, bg=COL_BG)
        input_frame.pack(fill='x', padx=SP_LG, pady=(SP_XS, SP_SM))

        entry_frame = tk.Frame(input_frame, bg=COL_INPUT, highlightthickness=1,
                               highlightbackground=COL_BORDER)
        entry_frame.pack(fill='x')
        self.task_entry = tk.Entry(
            entry_frame, font=FONT_UI, bg=COL_INPUT, fg=COL_TXT_HI,
            insertbackground=COL_TXT_HI, relief='flat', bd=0
        )
        self.task_entry.pack(fill='x', side='left', expand=True, ipadx=8, ipady=6)
        self.task_entry.bind('<Return>', lambda e: self.add_task())
        self.task_entry.bind('<FocusIn>', lambda e: self._clear_placeholder())
        self.task_entry.bind('<FocusOut>', lambda e: self._set_placeholder())
        self.task_entry.bind('<Control-z>', lambda e: self.undo_delete())

        add_btn = tk.Button(
            entry_frame, text="＋", font=FONT_SYMBOL, bg=COL_INPUT, fg=COL_ACCENT,
            activebackground=COL_INPUT, activeforeground=COL_ACCENT_HV,
            relief='flat', bd=0, cursor='hand2', takefocus=0, command=self.add_task, width=3
        )
        add_btn.pack(side='right', pady=2)

        # 快捷分类按钮行（预设分类，输入后回车自动带该分类）
        cat_row = tk.Frame(input_frame, bg=COL_BG)
        cat_row.pack(fill='x', pady=(SP_XS, 0))
        tk.Label(cat_row, text="添加分类:", font=FONT_SMALL, bg=COL_BG,
                 fg=COL_TXT_LOW).pack(side='left')
        self._cat_btns = {}
        for c in ['急', '长期', '不急']:
            _t, _fg, _bg = CATEGORY_CONFIG[c]
            b = tk.Button(
                cat_row, text=_t, font=FONT_SMALL, bg=_bg, fg=_fg,
                activebackground=_bg, activeforeground=_fg,
                relief='flat', bd=0, cursor='hand2', takefocus=0, padx=8, pady=1
            )
            b.config(command=lambda cc=c: self._toggle_default_cat(cc))
            b.pack(side='left', padx=(SP_XS, 0))
            self._cat_btns[c] = b
        self._cat_hint = tk.Label(cat_row, text="", font=FONT_SMALL, bg=COL_BG, fg=COL_TXT_LOW)
        self._cat_hint.pack(side='left', padx=(SP_MD, 0))

        # ── 过滤栏 ──
        filter_bar = tk.Frame(self.root, bg=COL_BG)
        filter_bar.pack(fill='x', padx=SP_LG, pady=(SP_XS, SP_SM))
        self._filter_btns = {}
        for key, label in [('all', '全部'), ('急', '急'), ('长期', '长期'),
                           ('不急', '不急'), ('done', '已完成')]:
            b = tk.Button(
                filter_bar, text=label, font=FONT_SMALL,
                bg=COL_CARD, fg=COL_TXT_MID,
                activebackground=COL_CARD_HV, activeforeground=COL_TXT_HI,
                relief='flat', bd=0, cursor='hand2', takefocus=0, padx=10, pady=2
            )
            b.config(command=lambda k=key: self.set_filter(k))
            b.pack(side='left', padx=(0, SP_XS))
            self._filter_btns[key] = b

        undo_btn = tk.Button(
            filter_bar, text="↩ 撤销", font=FONT_SMALL,
            bg=COL_CARD, fg=COL_TXT_LOW,
            activebackground=COL_CARD_HV, activeforeground=COL_TXT_HI,
            relief='flat', bd=0, cursor='hand2', takefocus=0, command=self.undo_delete
        )
        undo_btn.pack(side='right')
        self._undo_btn = undo_btn

        # ── 任务列表 ──
        list_frame = tk.Frame(self.root, bg=COL_BG)
        list_frame.pack(fill='both', expand=True, padx=SP_LG, pady=(0, SP_SM))

        scrollbar = tk.Scrollbar(list_frame, bg=COL_PANEL, troughcolor=COL_SCROLL_BG,
                                 relief='flat', bd=0, width=8,
                                 activebackground=COL_SCROLL_FG)
        scrollbar.pack(side='right', fill='y')

        self.canvas = tk.Canvas(
            list_frame, bg=COL_BG, highlightthickness=0, bd=0,
            yscrollcommand=scrollbar.set
        )
        self.canvas.pack(side='left', fill='both', expand=True)
        scrollbar.config(command=self.canvas.yview)

        self.task_container = tk.Frame(self.canvas, bg=COL_BG)
        self._canvas_window = self.canvas.create_window(
            (0, 0), window=self.task_container, anchor='nw'
        )
        self.task_container.bind('<Configure>',
                                 lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfig(
            self._canvas_window, width=e.width))
        self.canvas.bind_all('<MouseWheel>', self._on_mousewheel)

        # 占位符
        self._placeholder_active = False
        self._set_placeholder()

    @staticmethod
    def _weekday_cn(w):
        return ['一', '二', '三', '四', '五', '六', '日'][w]

    # ---------- 窗口拖拽 ----------

    def _start_drag(self, e):
        self._drag_win = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _on_drag(self, e):
        if self._drag_win:
            dx, dy = self._drag_win
            self.root.geometry(f'+{e.x_root - dx}+{e.y_root - dy}')

    def _try_round_corner(self):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetAncestor(self.root.winfo_id(), 2)
            val = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(val), ctypes.sizeof(val))
        except Exception:
            pass

    # ---------- 输入占位 ----------

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

    # ---------- 过滤与视图 ----------

    def set_filter(self, key):
        self._filter = key
        self._apply_visibility()
        self._refresh_counters()

    def _toggle_default_cat(self, cat):
        """预设快捷分类：再次点击取消"""
        self._default_cat = None if self._default_cat == cat else cat
        for c, b in self._cat_btns.items():
            _t, _fg, _bg = CATEGORY_CONFIG[c]
            if c == self._default_cat:
                b.configure(bg=_fg, fg='#14171c', activebackground=_fg, activeforeground='#14171c')
            else:
                b.configure(bg=_bg, fg=_fg, activebackground=_bg, activeforeground=_fg)
        self._cat_hint.config(text=f"回车后自动标为「{self._default_cat}」" if self._default_cat else "")

    # ---------- 任务操作 ----------

    def add_task(self):
        task_text = self.task_entry.get().strip()
        if self._placeholder_active or not task_text:
            return

        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.tasks:
            self.tasks[today] = []

        new_task = {
            'text': task_text,
            'completed': False,
            'created_at': datetime.now().isoformat(),
            'category': self._default_cat,   # 支持快捷分类
        }
        self.tasks[today].append(new_task)
        self.save_tasks()
        self.task_entry.delete(0, tk.END)
        self._placeholder_active = False
        self.task_entry.configure(fg=COL_TXT_HI)
        self.task_entry.focus_set()
        # 增量：只追加新卡片，不重建整个列表
        self._cards.append(self._render_task(len(self.tasks[today]) - 1, new_task))
        self._apply_visibility()
        self._refresh_counters()
        self.canvas.yview_moveto(1.0)

    def toggle_task(self, index):
        """勾选/取消勾选（零重建：只改样式+按过滤重排，不销毁任何widget）"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today in self.tasks and index < len(self.tasks[today]):
            self.tasks[today][index]['completed'] = not self.tasks[today][index]['completed']
            self.save_tasks()
            self._update_card_style(self._cards[index])
            self._apply_visibility()
            self._refresh_counters()

    def toggle_card(self, card):
        """卡片引用版勾选（动态查index，删除/移动后依然准确）"""
        try:
            self.toggle_task(self._cards.index(card))
        except ValueError:
            pass

    def delete_task(self, index):
        """删除任务（增量：只销毁该卡片，后续卡片pack自动上移不重建）"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today in self.tasks and index < len(self.tasks[today]):
            removed = self.tasks[today].pop(index)
            self._undo_stack.append((today, removed, index))
            if len(self._undo_stack) > 10:
                self._undo_stack.pop(0)
            self.save_tasks()
            if 0 <= index < len(self._cards):
                try:
                    self._cards[index]['f'].destroy()
                except Exception:
                    pass
                self._cards.pop(index)
            self._apply_visibility()
            self._refresh_counters()
            self._flash_undo()

    def delete_card(self, card):
        """卡片引用版删除"""
        try:
            self.delete_task(self._cards.index(card))
        except ValueError:
            pass

    def undo_delete(self):
        if not self._undo_stack:
            return
        today, removed, index = self._undo_stack.pop()
        if today not in self.tasks:
            self.tasks[today] = []
        self.tasks[today].insert(min(index, len(self.tasks[today])), removed)
        self.save_tasks()
        self._build_all_cards()  # 低频操作：全量重建最稳妥

    def _flash_undo(self):
        """短暂高亮撤销按钮提示"""
        self._undo_btn.configure(bg=COL_ACCENT, fg='#14171c')
        self.root.after(1500, lambda: self._undo_btn.configure(
            bg=COL_CARD, fg=COL_TXT_LOW))

    def move_task(self, index, delta):
        """上移/下移任务（增量：数据+卡片顺序同步交换，只重排位置不重建）"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.tasks or index < 0 or index >= len(self.tasks[today]):
            return
        tasks = self.tasks[today]
        new_index = index + delta
        if new_index < 0 or new_index >= len(tasks):
            return
        if tasks[index].get('completed') or tasks[new_index].get('completed'):
            return  # 不跨完成区移动
        tasks[index], tasks[new_index] = tasks[new_index], tasks[index]
        if index < len(self._cards) and new_index < len(self._cards):
            self._cards[index], self._cards[new_index] = self._cards[new_index], self._cards[index]
        self.save_tasks()
        self._apply_visibility()

    def move_card(self, card, delta):
        """卡片引用版上移/下移"""
        try:
            self.move_task(self._cards.index(card), delta)
        except ValueError:
            pass

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

        W, H = 340, 220
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - W) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - H) // 2
        dlg.geometry(f"{W}x{H}+{x}+{y}")

        tk.Label(
            dlg, text="修改任务内容", font=FONT_UI_BOLD,
            bg=COL_BG, fg=COL_TXT_HI, anchor='w'
        ).pack(fill='x', padx=15, pady=(12, 4))

        txt = tk.Text(
            dlg, wrap='word', font=FONT_UI, bg=COL_INPUT, fg=COL_TXT_HI,
            insertbackground=COL_TXT_HI, relief='flat', bd=0, padx=10, pady=8,
            height=5, highlightthickness=1, highlightbackground=COL_BORDER,
            highlightcolor=COL_ACCENT,
        )
        txt.insert('1.0', old_text)
        txt.pack(padx=15, pady=(0, 4), fill='both', expand=True)
        txt.focus_set()
        txt.mark_set('insert', 'end')

        tk.Label(
            dlg, text="Ctrl+Enter 保存  ·  Esc 取消",
            font=FONT_SMALL, bg=COL_BG, fg=COL_TXT_LOW
        ).pack(anchor='e', padx=15)

        def do_save():
            new_text = txt.get('1.0', 'end').strip()
            if new_text:
                self.tasks[today][index]['text'] = new_text
                self.save_tasks()
                self._rebuild_one(index)
            dlg.destroy()

        btn_frame = tk.Frame(dlg, bg=COL_BG)
        btn_frame.pack(pady=(4, 12))
        tk.Button(
            btn_frame, text="保存修改", font=FONT_UI,
            bg=COL_ACCENT, fg='#ffffff',
            activebackground=COL_ACCENT_HV, activeforeground='#ffffff',
            relief='flat', bd=0, cursor='hand2', command=do_save, width=8
        ).pack(side='left', padx=5)
        tk.Button(
            btn_frame, text="取消", font=FONT_UI,
            bg=COL_PANEL, fg=COL_TXT_MID,
            activebackground=COL_CARD_HV, activeforeground=COL_TXT_HI,
            relief='flat', bd=0, cursor='hand2', command=dlg.destroy, width=8
        ).pack(side='left', padx=5)

        txt.bind('<Control-Return>', lambda e: do_save())
        txt.bind('<Escape>', lambda e: dlg.destroy())

    def show_category_menu(self, index, widget):
        """点击分类标签弹出选择菜单：长期 / 急 / 不急"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.tasks or index >= len(self.tasks[today]):
            return
        current = self.tasks[today][index].get('category', None)
        menu = tk.Menu(self.root, tearoff=0, font=FONT_UI,
                       bg=COL_PANEL, fg=COL_TXT_HI,
                       activebackground=COL_ACCENT, activeforeground='#ffffff')
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
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.tasks or index >= len(self.tasks[today]):
            return
        self.tasks[today][index]['category'] = category
        self.save_tasks()
        self._rebuild_one(index)
        self._refresh_counters()

    # ---------- 渲染 ----------

    def _build_all_cards(self):
        """全量重建（仅启动/跨日/撤销/恢复窗口时调用）。日常操作全部走增量路径。"""
        # 记录滚动位置
        try:
            self._yview_frac = self.canvas.yview()[0]
        except Exception:
            self._yview_frac = 0.0
        # 隐藏旧内容（重建过程用户不可见）
        try:
            self.canvas.itemconfig(self._canvas_window, state='hidden')
        except Exception:
            pass

        # 清空卡片与容器（常驻header/空状态由_apply_visibility管理）
        self._cards = []
        for widget in self.task_container.winfo_children():
            widget.destroy()
        self._done_header = None
        self._done_fold_btn = None
        self._done_count_lbl = None
        self._empty_frame = None
        self._empty_msg_lbl = None

        today = datetime.now().strftime('%Y-%m-%d')
        tasks = self.tasks.get(today, [])
        for i, t in enumerate(tasks):
            self._cards.append(self._render_task(i, t))

        self._ensure_empty_frame()
        self._apply_visibility()
        self._refresh_counters()
        self._restore_after_refresh()

    def _restore_after_refresh(self):
        """全量重建后恢复显示与滚动位置（双缓冲收尾）"""
        try:
            self.canvas.itemconfig(self._canvas_window, state='normal')
        except Exception:
            pass
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        except Exception:
            pass
        frac = getattr(self, '_yview_frac', 0.0)
        if frac:
            try:
                self.canvas.yview_moveto(frac)
            except Exception:
                pass

    def _ensure_empty_frame(self):
        """创建常驻空状态frame（仅首次调用时创建一次，之后只切换显示）"""
        if self._empty_frame is not None:
            return
        self._empty_frame = tk.Frame(self.task_container, bg=COL_BG)
        tk.Label(
            self._empty_frame, text="🗒", font=('Segoe UI Emoji', 26),
            bg=COL_BG, fg=COL_TXT_LOW
        ).pack()
        self._empty_msg_lbl = tk.Label(
            self._empty_frame, text="", font=FONT_UI_BOLD,
            bg=COL_BG, fg=COL_TXT_MID
        )
        self._empty_msg_lbl.pack(pady=(8, 0))
        tk.Label(
            self._empty_frame, text="在上方输入内容后按回车，创建今天的第一个任务",
            font=FONT_SMALL, bg=COL_BG, fg=COL_TXT_LOW
        ).pack(pady=(6, 0))

    def _show_empty(self, msg="暂无任务"):
        self._ensure_empty_frame()
        if self._empty_msg_lbl is not None:
            self._empty_msg_lbl.config(text=msg)
        self._empty_frame.pack(fill='both', pady=46)

    def _hide_empty(self):
        if self._empty_frame is not None:
            self._empty_frame.pack_forget()

    def _apply_visibility(self):
        """增量核心：按过滤/折叠重排卡片与分区显示。
        widget全程不销毁，只pack/pack_forget，彻底消除重建闪烁。"""
        today = datetime.now().strftime('%Y-%m-%d')
        tasks = self.tasks.get(today, [])
        f = self._filter
        # 待办在前、已完成在后（与旧渲染顺序一致）
        order = [i for i, t in enumerate(tasks) if not t.get('completed')] \
              + [i for i, t in enumerate(tasks) if t.get('completed')]
        done_count = sum(1 for t in tasks if t.get('completed'))

        # 先全部脱离布局（不销毁）
        for card in self._cards:
            card['f'].pack_forget()
        if self._done_header is not None:
            self._done_header.pack_forget()
        self._hide_empty()

        if not tasks:
            self._show_empty("暂无任务")
            return

        shown_any = False
        if f != 'done':
            for i in order:
                t = tasks[i]
                if t.get('completed'):
                    continue
                if f == 'all' or t.get('category') == f:
                    self._cards[i]['f'].pack(fill='x', pady=(0, SP_MD))
                    shown_any = True

        if f == 'done':
            for i in order:
                t = tasks[i]
                if t.get('completed'):
                    self._cards[i]['f'].pack(fill='x', pady=(0, SP_MD))
                    shown_any = True
            if not shown_any:
                self._show_empty("暂无已完成任务")
        elif done_count:
            # 常驻已完成折叠header
            if self._done_header is None:
                self._done_header = tk.Frame(self.task_container, bg=COL_BG)
                self._done_fold_btn = tk.Button(
                    self._done_header, text="▾ 已完成", font=FONT_SMALL, bg=COL_BG, fg=COL_TXT_LOW,
                    activebackground=COL_BG, activeforeground=COL_TXT_MID,
                    relief='flat', bd=0, cursor='hand2', takefocus=0,
                    command=self._toggle_done_fold, anchor='w', padx=0
                )
                self._done_fold_btn.pack(side='left')
                self._done_count_lbl = tk.Label(
                    self._done_header, text="", font=FONT_SMALL, bg=COL_BG, fg=COL_TXT_LOW
                )
                self._done_count_lbl.pack(side='left', padx=(2, 0))
            self._done_header.pack(fill='x', pady=(SP_MD, SP_XS))
            self._done_fold_btn.config(text="▾ 已完成" if not self._done_folded else "▸ 已完成")
            self._done_count_lbl.config(text=f"({done_count})")
            if not self._done_folded:
                for i in order:
                    t = tasks[i]
                    if t.get('completed') and (f == 'all' or t.get('category') == f):
                        self._cards[i]['f'].pack(fill='x', pady=(0, SP_MD))

        if not shown_any and not done_count:
            self._show_empty("暂无任务")

        # 更新滚动区
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        except Exception:
            pass

    def _refresh_counters(self):
        """更新统计与过滤高亮（轻量，不碰列表）"""
        today = datetime.now().strftime('%Y-%m-%d')
        tasks = self.tasks.get(today, [])
        done = sum(1 for t in tasks if t.get('completed'))
        total = len(tasks)
        self.count_label.config(text=f"{total} 项 · 完成 {done}" if total else "0 项")
        for key, b in self._filter_btns.items():
            if key == self._filter:
                b.configure(bg=COL_ACCENT, fg='#14171c')
            else:
                b.configure(bg=COL_CARD, fg=COL_TXT_MID)

    def _update_card_style(self, card):
        """零重建样式更新：勾选/取消勾选只改颜色/删除线/勾选图形，不销毁重建任何widget"""
        task = card['data']
        completed = task.get('completed', False)
        bg = COL_DONE_CARD if completed else COL_CARD
        frame = card['f']
        frame.configure(bg=bg, highlightbackground=COL_BORDER)
        card['check'].configure(bg=bg)
        self._draw_check(card['check'], completed, False, bg)
        card['label'].configure(
            fg=COL_TXT_LOW if completed else COL_TXT_HI,
            font=('Microsoft YaHei UI', 10, 'overstrike' if completed else 'normal'),
            bg=bg
        )
        text_block = card['label'].master
        text_block.configure(bg=bg)
        meta = text_block.winfo_children()[1] if len(text_block.winfo_children()) > 1 else None
        if meta is not None:
            meta.configure(bg=bg)
            for w in meta.winfo_children():
                try:
                    w.configure(bg=bg)
                except Exception:
                    pass
        btn_box = card['up'].master
        btn_box.configure(bg=bg)
        for key in ('up', 'down', 'edit', 'del'):
            card[key].configure(bg=bg)
        if task.get('category') is None:
            card['cat_btn'].configure(bg=bg)

    def _rebuild_one(self, index):
        """单卡重建（编辑/分类/勾选后样式变更）：只销毁重建该卡片，其余widget不动"""
        if 0 <= index < len(self._cards):
            try:
                self._cards[index]['f'].destroy()
            except Exception:
                pass
            today = datetime.now().strftime('%Y-%m-%d')
            tasks = self.tasks.get(today, [])
            if index < len(tasks):
                self._cards[index] = self._render_task(index, tasks[index])
            else:
                self._cards.pop(index)
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        except Exception:
            pass

    def _toggle_done_fold(self):
        self._done_folded = not self._done_folded
        self._apply_visibility()

    def _render_task(self, index, task, done=False):
        """创建单个任务组件（分类色条 + 圆形复选框 + 悬停层次 + 排序按钮）。
        返回card dict供增量操作；按钮一律绑定card引用，动态查index，
        删除/移动后引用依然准确，且本方法不pack（由_apply_visibility统一布局）。"""
        completed = task.get('completed', False) or done
        cat = task.get('category', None)
        card_bg = COL_DONE_CARD if completed else COL_CARD

        task_frame = tk.Frame(
            self.task_container, bg=card_bg,
            highlightthickness=1, highlightbackground=COL_BORDER, bd=0
        )
        card = {'f': task_frame, 'data': task}

        # 分类色条（左侧 3px 竖条）
        bar_color = {'急': '#e06c66', '长期': '#4fbf8f', '不急': '#8a94a6'}.get(cat, COL_CARD_HV)
        color_bar = tk.Label(task_frame, bg=bar_color, width=1)
        color_bar.pack(side='left', fill='both')

        # 圆形复选框（Canvas 自绘）
        check_canvas = tk.Canvas(task_frame, width=22, height=22, bg=card_bg,
                                 highlightthickness=0, bd=0)
        check_canvas.pack(side='left', padx=(10, 6), pady=8)
        self._draw_check(check_canvas, completed, False, card_bg)
        check_canvas.bind('<Button-1>', lambda e: self.toggle_card(card))

        # 分类胶囊
        cat_text, cat_fg, cat_bg = CATEGORY_CONFIG.get(cat, CATEGORY_CONFIG[None])
        cat_btn = tk.Button(
            task_frame, text=cat_text, font=FONT_SMALL, bg=cat_bg, fg=cat_fg,
            activebackground=cat_bg, activeforeground=cat_fg,
            relief='flat', bd=0, cursor='hand2', takefocus=0, padx=6, pady=2
        )
        cat_btn.config(command=lambda w=cat_btn: self.show_category_menu(self._cards.index(card), w))
        cat_btn.pack(side='left', padx=(0, 6), pady=8)

        # 任务文本 + 创建时间小字
        text_block = tk.Frame(task_frame, bg=card_bg)
        text_block.pack(side='left', fill='x', expand=True, padx=5, pady=8)

        text_color = COL_TXT_LOW if completed else COL_TXT_HI
        text_style = 'overstrike' if completed else 'normal'
        task_label = tk.Label(
            text_block, text=task['text'], font=('Microsoft YaHei UI', 10, text_style),
            bg=card_bg, fg=text_color, anchor='w', justify='left', wraplength=420
        )
        task_label.pack(fill='x')

        # 顺延徽章 + 创建时间
        meta = tk.Frame(text_block, bg=card_bg)
        meta.pack(anchor='w', pady=(2, 0))
        if task.get('carried_from'):
            cf = task['carried_from'][5:].replace('-', '/')
            tk.Label(
                meta, text=f"↩ 顺延自 {cf}", font=('Microsoft YaHei UI', 7),
                bg=COL_CARD_HV if not completed else COL_DONE_CARD,
                fg=COL_TXT_LOW, padx=4, pady=1
            ).pack(side='left')
        if task.get('created_at'):
            try:
                ct = datetime.fromisoformat(task['created_at']).strftime('%H:%M')
            except Exception:
                ct = ""
            if ct:
                tk.Label(
                    meta, text=ct, font=FONT_SMALL,
                    bg=card_bg, fg=COL_TXT_LOW
                ).pack(side='left', padx=(6, 0))

        # 右侧操作区
        btn_box = tk.Frame(task_frame, bg=card_bg)
        btn_box.pack(side='right', padx=(0, 6), pady=8)

        up_btn = tk.Button(
            btn_box, text="↑", font=FONT_SYMBOL_SM, bg=card_bg, fg=COL_TXT_LOW,
            activebackground=COL_CARD_HV, activeforeground=COL_TXT_HI,
            relief='flat', bd=0, cursor='hand2', takefocus=0,
            command=lambda: self.move_card(card, -1), width=2
        )
        up_btn.pack(side='left', padx=1)
        down_btn = tk.Button(
            btn_box, text="↓", font=FONT_SYMBOL_SM, bg=card_bg, fg=COL_TXT_LOW,
            activebackground=COL_CARD_HV, activeforeground=COL_TXT_HI,
            relief='flat', bd=0, cursor='hand2', takefocus=0,
            command=lambda: self.move_card(card, 1), width=2
        )
        down_btn.pack(side='left', padx=1)
        edit_btn = tk.Button(
            btn_box, text="✎", font=FONT_SYMBOL_SM, bg=card_bg, fg=COL_TXT_LOW,
            activebackground=COL_CARD_HV, activeforeground=COL_TXT_HI,
            relief='flat', bd=0, cursor='hand2', takefocus=0,
            command=lambda: self.edit_task(self._cards.index(card)), width=2
        )
        edit_btn.pack(side='left', padx=1)
        del_btn = tk.Button(
            btn_box, text="✕", font=FONT_SYMBOL_SM, bg=card_bg, fg=COL_TXT_LOW,
            activebackground=COL_CARD_HV, activeforeground=COL_DANGER,
            relief='flat', bd=0, cursor='hand2', takefocus=0,
            command=lambda: self.delete_card(card), width=2
        )
        del_btn.pack(side='left', padx=1)

        # 悬停效果（动态版：toggle后自动按最新completed状态生效，无需重建绑定）
        hover_widgets = [task_frame, task_label, text_block, meta, btn_box]

        def on_enter(e):
            if card['data'].get('completed'):
                return
            task_frame.configure(bg=COL_CARD_HV, highlightbackground=COL_BORDER_HV)
            for w in hover_widgets:
                w.configure(bg=COL_CARD_HV)
            if cat is None:
                cat_btn.configure(bg=COL_CARD_HV)
            check_canvas.configure(bg=COL_CARD_HV)
            self._draw_check(check_canvas, False, True, COL_CARD_HV)

        def on_leave(e):
            if card['data'].get('completed'):
                return
            task_frame.configure(bg=COL_CARD, highlightbackground=COL_BORDER)
            for w in hover_widgets:
                w.configure(bg=COL_CARD)
            if cat is None:
                cat_btn.configure(bg=COL_CARD)
            check_canvas.configure(bg=COL_CARD)
            self._draw_check(check_canvas, False, False, COL_CARD)

        for w in hover_widgets:
            w.bind('<Enter>', on_enter)
            w.bind('<Leave>', on_leave)

        # 右键菜单（任务行右键 = 完整操作）
        def on_right_click(e):
            try:
                self.show_task_menu(self._cards.index(card), e)
            except ValueError:
                pass

        # 双击任务行打开详情面板
        def on_double_click(e):
            try:
                self.open_task_detail(self._cards.index(card))
            except ValueError:
                pass

        for w in [task_frame, task_label, text_block]:
            w.bind('<Button-3>', on_right_click)
            w.bind('<Double-Button-1>', on_double_click)
        # 子部件引用（供零重建样式更新使用）
        card.update({'check': check_canvas, 'label': task_label, 'cat_btn': cat_btn,
                     'up': up_btn, 'down': down_btn, 'edit': edit_btn, 'del': del_btn})
        return card

    @staticmethod
    def _draw_check(canvas, completed, hover, bg):
        canvas.delete('all')
        r = 8
        cx, cy = 11, 11
        if completed:
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                               fill=COL_SUCCESS, outline=COL_SUCCESS)
            canvas.create_line(cx - 3.5, cy - 0.5, cx - 1, cy + 2.5, cx + 4, cy - 3,
                               fill='#ffffff', width=2, capstyle='round', joinstyle='round')
        else:
            outline = COL_BORDER_HV if hover else COL_BORDER
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                               fill=bg, outline=outline, width=1.5)

    def open_task_detail(self, index):
        """双击任务行 → 内联详情面板：完成切换 / 分类 / 全文 / 编辑 / 删除"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.tasks or index >= len(self.tasks[today]):
            return
        task = self.tasks[today][index]

        # 若已有详情面板先销毁（防重复）
        if hasattr(self, '_detail_panel') and self._detail_panel is not None:
            try:
                self._detail_panel.destroy()
            except Exception:
                pass
            self._detail_panel = None

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
        tk.Label(bar, text="任务详情", font=FONT_UI_BOLD,
                 bg=COL_PANEL, fg=COL_TXT_HI).pack(side='left', padx=(SP_LG, 0), pady=8)
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
            relief='flat', bd=0, cursor='hand2', command=do_toggle, padx=10, pady=3
        )
        toggle_btn.pack(side='right')

        # 任务全文（只读大文本）
        text = tk.Text(
            panel, wrap='word', font=('Microsoft YaHei UI', 11),
            bg=COL_BG, fg=COL_TXT_HI, insertbackground=COL_TXT_HI,
            relief='flat', bd=0, padx=SP_LG, pady=SP_SM, height=10,
            highlightthickness=0
        )
        text.insert('1.0', task['text'])
        text.config(state='disabled')
        text.pack(fill='both', expand=True)

        # 元信息
        meta = tk.Frame(panel, bg=COL_BG)
        meta.pack(fill='x', padx=SP_LG, pady=(0, SP_SM))
        if task.get('carried_from'):
            tk.Label(meta, text=f"↩ 顺延自 {task['carried_from'][5:].replace('-', '/')}",
                     font=FONT_SMALL, bg=COL_BG, fg=COL_TXT_LOW).pack(anchor='w')
        if task.get('created_at'):
            try:
                ct = datetime.fromisoformat(task['created_at']).strftime('%Y-%m-%d %H:%M')
                tk.Label(meta, text=f"创建于 {ct}", font=FONT_SMALL,
                         bg=COL_BG, fg=COL_TXT_LOW).pack(anchor='w', pady=(2, 0))
            except Exception:
                pass

        # 底部操作按钮
        btn_frame = tk.Frame(panel, bg=COL_BG)
        btn_frame.pack(fill='x', padx=SP_LG, pady=(0, SP_LG))
        del_btn2 = tk.Button(
            btn_frame, text="删除任务", font=FONT_UI,
            bg=COL_PANEL, fg=COL_DANGER,
            activebackground=COL_CARD_HV, activeforeground=COL_DANGER,
            relief='flat', bd=0, cursor='hand2',
            command=lambda: (close_panel(), self.delete_task(index)), width=9, pady=3
        )
        del_btn2.pack(side='left')
        close_btn2 = tk.Button(
            btn_frame, text="关闭", font=FONT_UI,
            bg=COL_ACCENT, fg='#ffffff',
            activebackground=COL_ACCENT_HV, activeforeground='#ffffff',
            relief='flat', bd=0, cursor='hand2',
            command=close_panel, width=9, pady=3
        )
        close_btn2.pack(side='right')

    def show_task_menu(self, index, event):
        """任务右键菜单：完成/分类/编辑/删除"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today not in self.tasks or index >= len(self.tasks[today]):
            return
        task = self.tasks[today][index]
        completed = task.get('completed', False)

        menu = tk.Menu(self.root, tearoff=0, font=FONT_UI,
                       bg=COL_PANEL, fg=COL_TXT_HI,
                       activebackground=COL_ACCENT, activeforeground='#ffffff')

        menu.add_command(
            label="✓ 标记完成" if not completed else "↺ 恢复未完成",
            command=lambda: self.toggle_task(index)
        )
        cat_menu = tk.Menu(menu, tearoff=0, font=FONT_UI,
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
        menu.add_command(label="↑ 上移", command=lambda: self.move_task(index, -1))
        menu.add_command(label="↓ 下移", command=lambda: self.move_task(index, 1))
        menu.add_command(label="✎ 编辑", command=lambda: self.edit_task(index))
        menu.add_separator()
        menu.add_command(label="✕ 删除", foreground=COL_DANGER,
                         command=lambda: self.delete_task(index))
        menu.add_separator()
        menu.add_command(label="退出应用", foreground=COL_TXT_LOW,
                         command=self.quit_app)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ---------- 滚轮 ----------

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ---------- 窗口控制 ----------

    def toggle_window(self):
        if self.root.state() == 'withdrawn':
            self.show_window()
        else:
            self.hide_window()

    def hide_window(self):
        self.root.withdraw()
        if self._schedule_win is not None:
            try:
                self._schedule_win.withdraw()
            except tk.TclError:
                self._schedule_win = None
        if self._timeline_win is not None:
            try:
                self._timeline_win.withdraw()
            except tk.TclError:
                self._timeline_win = None

    def quit_app(self):
        """真正退出应用：清理全局热键后销毁窗口"""
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def show_window(self):
        self.root.deiconify()
        self.root.attributes('-topmost', True)
        self.root.focus_force()
        # 刷新日期（跨天后标题栏日期更新）
        today = datetime.now()
        self.title_lbl.config(
            text=f"今日 · {today.month}月{today.day}日 {self._weekday_cn(today.weekday())}")
        self._build_all_cards()
        # 同步恢复今日安排面板
        if self._schedule_win is not None:
            try:
                self._schedule_win.deiconify()
                self._schedule_win.lift()
            except tk.TclError:
                self._schedule_win = None
        # 同步恢复每日时间线面板
        if self._timeline_win is not None:
            try:
                self._timeline_win.deiconify()
                self._timeline_win.lift()
            except tk.TclError:
                self._timeline_win = None

    # ---------- 今日安排 ----------

    def _load_schedule(self):
        """从 task_widget.json 的 _schedule 键加载今日安排（自由文本）"""
        if not self.data_file.exists():
            return ''
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            today = datetime.now().strftime('%Y-%m-%d')
            return raw.get('_schedule', {}).get(today, '')
        except Exception:
            return ''

    def _save_schedule(self, text: str):
        """把自由文本写回 task_widget.json 的 _schedule 键（不影响任务数据）"""
        try:
            raw = {}
            if self.data_file.exists():
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
            today = datetime.now().strftime('%Y-%m-%d')
            if '_schedule' not in raw:
                raw['_schedule'] = {}
            raw['_schedule'][today] = text
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存今日安排失败: {e}")

    def open_schedule_panel(self):
        """打开/聚焦/隐藏今日安排面板（自由文本编辑器）"""
        if self._schedule_win is not None:
            try:
                if self._schedule_win.state() == 'normal' and self._schedule_win.winfo_viewable():
                    # 已显示 → 隐藏
                    self._schedule_win.withdraw()
                    return
                # 已隐藏 → 重新显示
                self._schedule_win.deiconify()
                self._schedule_win.lift()
                self._schedule_win.focus_force()
                return
            except tk.TclError:
                self._schedule_win = None

        win = tk.Toplevel(self.root)
        self._schedule_win = win
        win.title("今日安排")
        win.configure(bg=COL_BG)
        win.overrideredirect(True)
        win.attributes('-topmost', True)

        # 窗口尺寸 & 位置（主窗口左侧）
        W, H = 340, 500
        rx = self.root.winfo_x()
        ry = self.root.winfo_y()
        win.geometry(f"{W}x{H}+{max(0, rx - W - 8)}+{ry}")

        # ── 标题栏 ──
        title_bar = tk.Frame(win, bg=COL_PANEL, height=40)
        title_bar.pack(fill='x')
        title_bar.pack_propagate(False)

        title_lbl = tk.Label(
            title_bar, text="📋 今日安排",
            font=FONT_TITLE, bg=COL_PANEL, fg=COL_TXT_HI
        )
        title_lbl.pack(side='left', padx=SP_LG, pady=6)

        # 字数统计
        char_lbl = tk.Label(
            title_bar, text="", font=FONT_SMALL,
            bg=COL_PANEL, fg=COL_TXT_LOW
        )
        char_lbl.pack(side='left', padx=(0, SP_SM), pady=6)

        close_btn = tk.Button(
            title_bar, text="✕", font=FONT_SYMBOL_SM,
            bg=COL_PANEL, fg=COL_TXT_MID,
            activebackground='#3a1f1f', activeforeground=COL_DANGER,
            relief='flat', bd=0, cursor='hand2', takefocus=0,
            command=win.destroy
        )
        close_btn.pack(side='right', padx=SP_SM, pady=6)
        close_btn.bind('<Enter>', lambda e: close_btn.configure(fg=COL_DANGER))
        close_btn.bind('<Leave>', lambda e: close_btn.configure(fg=COL_TXT_MID))

        # 拖拽
        _drag = {'x': 0, 'y': 0}
        def _start(e): _drag['x'], _drag['y'] = e.x_root, e.y_root
        def _move(e):
            dx, dy = e.x_root - _drag['x'], e.y_root - _drag['y']
            _drag['x'], _drag['y'] = e.x_root, e.y_root
            win.geometry(f"+{win.winfo_x()+dx}+{win.winfo_y()+dy}")
        for w in (title_bar, title_lbl):
            w.bind('<Button-1>', _start)
            w.bind('<B1-Motion>', _move)

        # ── 文本编辑区 ──
        text_frame = tk.Frame(win, bg=COL_INPUT, highlightthickness=1,
                              highlightbackground=COL_BORDER)
        text_frame.pack(fill='both', expand=True, padx=SP_LG, pady=(SP_SM, 0))

        scrollbar = tk.Scrollbar(text_frame, orient='vertical',
                                 troughcolor=COL_SCROLL_BG, width=6)
        scrollbar.pack(side='right', fill='y')

        text_box = tk.Text(
            text_frame,
            font=FONT_UI, bg=COL_INPUT, fg=COL_TXT_HI,
            insertbackground=COL_ACCENT,
            selectbackground=COL_ACCENT, selectforeground='#ffffff',
            relief='flat', bd=8, wrap='word',
            yscrollcommand=scrollbar.set,
            undo=True  # Ctrl+Z 撤销
        )
        text_box.pack(side='left', fill='both', expand=True)
        scrollbar.config(command=text_box.yview)

        # 载入已有内容
        saved = self._load_schedule()
        if saved:
            text_box.insert('1.0', saved)
        text_box.focus_set()
        # 拦截 Ctrl+Alt+Z：阻止 tk 把它当 undo 处理（OS层已有全局热键处理）
        text_box.bind('<Control-Alt-z>', lambda e: 'break')
        text_box.bind('<Control-Alt-Z>', lambda e: 'break')

        # ── 状态栏 ──
        status_bar = tk.Frame(win, bg=COL_PANEL, height=28)
        status_bar.pack(fill='x', padx=0, pady=(2, 0))
        status_bar.pack_propagate(False)

        status_lbl = tk.Label(
            status_bar, text="Ctrl+Z 撤销  |  自动保存",
            font=FONT_SMALL, bg=COL_PANEL, fg=COL_TXT_LOW
        )
        status_lbl.pack(side='left', padx=SP_LG)

        save_lbl = tk.Label(
            status_bar, text="", font=FONT_SMALL,
            bg=COL_PANEL, fg=COL_TXT_LOW
        )
        save_lbl.pack(side='right', padx=SP_LG)

        # ── 自动保存（停止输入 800ms 后触发）──
        _save_timer = [None]

        def _auto_save(event=None):
            if _save_timer[0]:
                win.after_cancel(_save_timer[0])
            _save_timer[0] = win.after(800, _do_save)

        def _do_save():
            content = text_box.get('1.0', 'end-1c')
            self._save_schedule(content)
            # 更新字数
            chars = len(content.replace('\n', ''))
            char_lbl.config(text=f"{chars}字" if chars else "")
            save_lbl.config(text="已保存 ✓", fg=COL_TXT_LOW)
            win.after(1500, lambda: save_lbl.config(text=""))

        text_box.bind('<KeyRelease>', _auto_save)

        # 初始字数
        init_chars = len(saved.replace('\n', '')) if saved else 0
        if init_chars:
            char_lbl.config(text=f"{init_chars}字")

        # 关闭时保存并清理引用
        def _on_close():
            _do_save()
            win.destroy()

        win.protocol('WM_DELETE_WINDOW', _on_close)
        win.bind('<Destroy>', lambda e: setattr(self, '_schedule_win', None)
                 if e.widget is win else None)

    # ---------- 每日时间线 ----------

    def _load_timeline(self):
        """从 task_widget.json 的 _timeline 键加载今日时间线（按起始时间排序）"""
        if not self.data_file.exists():
            return []
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            today = datetime.now().strftime('%Y-%m-%d')
            items = raw.get('_timeline', {}).get(today, [])
            return sorted(items, key=lambda x: (x.get('sh', 0), x.get('sm', 0)))
        except Exception:
            return []

    def _save_timeline(self, items):
        """把时间线写回 task_widget.json 的 _timeline 键（不影响任务数据）"""
        try:
            raw = {}
            if self.data_file.exists():
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
            today = datetime.now().strftime('%Y-%m-%d')
            if '_timeline' not in raw:
                raw['_timeline'] = {}
            raw['_timeline'][today] = sorted(
                items, key=lambda x: (x.get('sh', 0), x.get('sm', 0)))
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存每日时间线失败: {e}")

    def open_timeline_panel(self):
        """打开/聚焦/隐藏每日时间线面板"""
        if self._timeline_win is not None:
            try:
                if self._timeline_win.state() == 'normal' and self._timeline_win.winfo_viewable():
                    # 已显示 → 隐藏
                    self._timeline_win.withdraw()
                    return
                # 已隐藏 → 重新显示
                self._timeline_win.deiconify()
                self._timeline_win.lift()
                self._timeline_win.focus_force()
                return
            except tk.TclError:
                self._timeline_win = None

        win = tk.Toplevel(self.root)
        self._timeline_win = win
        win.title("每日时间线")
        win.configure(bg=COL_BG)
        win.overrideredirect(True)
        win.attributes('-topmost', True)

        # 窗口尺寸 & 位置（今日安排左侧）
        W, H = 340, 500
        rx = self.root.winfo_x()
        ry = self.root.winfo_y()
        win.geometry(f"{W}x{H}+{max(0, rx - W - 8)}+{ry}")

        # ── 标题栏 ──
        title_bar = tk.Frame(win, bg=COL_PANEL, height=40)
        title_bar.pack(fill='x')
        title_bar.pack_propagate(False)

        title_lbl = tk.Label(
            title_bar, text="⏱ 每日时间线",
            font=FONT_TITLE, bg=COL_PANEL, fg=COL_TXT_HI
        )
        title_lbl.pack(side='left', padx=SP_LG, pady=6)

        close_btn = tk.Button(
            title_bar, text="✕", font=FONT_SYMBOL_SM,
            bg=COL_PANEL, fg=COL_TXT_MID,
            activebackground='#3a1f1f', activeforeground=COL_DANGER,
            relief='flat', bd=0, cursor='hand2', takefocus=0,
            command=win.destroy
        )
        close_btn.pack(side='right', padx=SP_SM, pady=6)
        close_btn.bind('<Enter>', lambda e: close_btn.configure(fg=COL_DANGER))
        close_btn.bind('<Leave>', lambda e: close_btn.configure(fg=COL_TXT_MID))

        # 拖拽
        _drag = {'x': 0, 'y': 0}
        def _start(e): _drag['x'], _drag['y'] = e.x_root, e.y_root
        def _move(e):
            dx, dy = e.x_root - _drag['x'], e.y_root - _drag['y']
            _drag['x'], _drag['y'] = e.x_root, e.y_root
            win.geometry(f"+{win.winfo_x()+dx}+{win.winfo_y()+dy}")
        for w in (title_bar, title_lbl):
            w.bind('<Button-1>', _start)
            w.bind('<B1-Motion>', _move)

        # ── 输入区：起始时 : 起始分 - 终止时 : 终止分 ，任务 ──
        input_frame = tk.Frame(win, bg=COL_PANEL, highlightthickness=1,
                               highlightbackground=COL_BORDER)
        input_frame.pack(fill='x', padx=SP_LG, pady=(SP_SM, 0))

        row1 = tk.Frame(input_frame, bg=COL_PANEL)
        row1.pack(fill='x', pady=(SP_SM, 0), padx=SP_SM)

        sh_var, sm_var = tk.StringVar(), tk.StringVar()
        eh_var, em_var = tk.StringVar(), tk.StringVar()

        def _mk_entry(var):
            e = tk.Entry(row1, textvariable=var, width=3, justify='center',
                         font=FONT_UI, bg=COL_INPUT, fg=COL_TXT_HI,
                         insertbackground=COL_ACCENT, relief='flat')
            e.pack(side='left', ipady=3, padx=1)
            return e

        _mk_entry(sh_var)
        tk.Label(row1, text=":", font=FONT_UI, bg=COL_PANEL, fg=COL_TXT_MID).pack(side='left')
        _mk_entry(sm_var)
        tk.Label(row1, text="  —  ", font=FONT_UI, bg=COL_PANEL, fg=COL_TXT_MID).pack(side='left')
        _mk_entry(eh_var)
        tk.Label(row1, text=":", font=FONT_UI, bg=COL_PANEL, fg=COL_TXT_MID).pack(side='left')
        _mk_entry(em_var)

        row2 = tk.Frame(input_frame, bg=COL_PANEL)
        row2.pack(fill='x', pady=(SP_XS, SP_SM), padx=SP_SM)

        task_var = tk.StringVar()
        task_entry = tk.Entry(row2, textvariable=task_var,
                              font=FONT_UI, bg=COL_INPUT, fg=COL_TXT_HI,
                              insertbackground=COL_ACCENT, relief='flat')
        task_entry.pack(side='left', fill='x', expand=True, ipady=3, padx=(0, SP_SM))

        add_btn = tk.Button(
            row2, text="＋ 添加", font=FONT_SMALL,
            bg=COL_ACCENT, fg='#ffffff',
            activebackground=COL_ACCENT_HV, activeforeground='#ffffff',
            relief='flat', bd=0, cursor='hand2', takefocus=0
        )
        add_btn.pack(side='right')

        # ── 列表区（Canvas 滚动）──
        list_frame = tk.Frame(win, bg=COL_BG)
        list_frame.pack(fill='both', expand=True, padx=SP_LG, pady=(SP_SM, 0))

        canvas = tk.Canvas(list_frame, bg=COL_BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(list_frame, orient='vertical',
                                 troughcolor=COL_SCROLL_BG, width=6)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        container = tk.Frame(canvas, bg=COL_BG)
        canvas_win = canvas.create_window((0, 0), window=container, anchor='nw')

        def _on_resize(e):
            canvas.itemconfig(canvas_win, width=e.width)
        canvas.bind('<Configure>', _on_resize)

        def _refresh_scroll():
            container.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox('all'))

        # 载入今日数据（跨日自动取新的一天）
        items = self._load_timeline()

        def _render():
            for w in container.winfo_children():
                w.destroy()
            if not items:
                tk.Label(container, text="今日还没有时间线，添加一条吧",
                         font=FONT_SMALL, bg=COL_BG, fg=COL_TXT_LOW
                         ).pack(pady=30)
            for i, it in enumerate(items):
                row = tk.Frame(container, bg=COL_CARD, highlightthickness=1,
                               highlightbackground=COL_BORDER)
                row.pack(fill='x', pady=3)

                t = it.get('task', '')
                t_str = f"{it.get('sh',0):02d}:{it.get('sm',0):02d} - " \
                        f"{it.get('eh',0):02d}:{it.get('em',0):02d}，{t}"
                tk.Label(row, text=t_str, font=FONT_UI, bg=COL_CARD, fg=COL_TXT_HI,
                         anchor='w', justify='left'
                         ).pack(side='left', fill='x', expand=True, padx=SP_SM, pady=6)

                del_btn = tk.Button(
                    row, text="✕", font=FONT_SYMBOL_SM,
                    bg=COL_CARD, fg=COL_TXT_LOW,
                    activebackground=COL_CARD, activeforeground=COL_DANGER,
                    relief='flat', bd=0, cursor='hand2', takefocus=0,
                    command=lambda idx=i: _delete(idx)
                )
                del_btn.pack(side='right', padx=(0, SP_SM))
                del_btn.bind('<Enter>', lambda e, b=del_btn: b.configure(fg=COL_DANGER))
                del_btn.bind('<Leave>', lambda e, b=del_btn: b.configure(fg=COL_TXT_LOW))

        def _parse_int(s):
            s = s.strip()
            if not s.isdigit():
                return None
            return int(s)

        def _add():
            sh, sm = _parse_int(sh_var.get()), _parse_int(sm_var.get())
            eh, em = _parse_int(eh_var.get()), _parse_int(em_var.get())
            task = task_var.get().strip()
            if None in (sh, sm, eh, em):
                status_lbl.config(text="时间请填 0-23 时 / 0-59 分", fg=COL_DANGER)
                return
            if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
                status_lbl.config(text="时间范围：时 0-23，分 0-59", fg=COL_DANGER)
                return
            if (sh, sm) > (eh, em):
                status_lbl.config(text="终止时间需晚于起始时间", fg=COL_DANGER)
                return
            if not task:
                status_lbl.config(text="任务内容不能为空", fg=COL_DANGER)
                return
            items.append({'sh': sh, 'sm': sm, 'eh': eh, 'em': em, 'task': task})
            items.sort(key=lambda x: (x['sh'], x['sm']))
            for v in (sh_var, sm_var, eh_var, em_var, task_var):
                v.set('')
            task_entry.focus_set()
            _save()
            _render()
            _refresh_scroll()
            status_lbl.config(text=f"已添加：{sh:02d}:{sm:02d} - {eh:02d}:{em:02d}，{task}",
                              fg=COL_SUCCESS)

        def _delete(idx):
            items.pop(idx)
            _save()
            _render()
            _refresh_scroll()

        def _save():
            self._save_timeline(items)
            self.timeline = items

        add_btn.configure(command=_add)
        task_entry.bind('<Return>', lambda e: _add())

        # ── 状态栏 ──
        status_bar = tk.Frame(win, bg=COL_PANEL, height=28)
        status_bar.pack(fill='x', pady=(2, 0))
        status_bar.pack_propagate(False)

        status_lbl = tk.Label(
            status_bar, text="按起始时间自动排序 · 每日刷新",
            font=FONT_SMALL, bg=COL_PANEL, fg=COL_TXT_LOW
        )
        status_lbl.pack(side='left', padx=SP_LG)

        _render()
        _refresh_scroll()

        # 关闭时清理引用
        win.protocol('WM_DELETE_WINDOW', win.destroy)
        win.bind('<Destroy>', lambda e: setattr(self, '_timeline_win', None)
                 if e.widget is win else None)

    def register_hotkey(self):
        """注册全局快捷键 Ctrl+Alt+Z"""
        def hotkey_handler():
            self.root.after(0, self.toggle_window)

        try:
            keyboard.add_hotkey('ctrl+alt+z', hotkey_handler)
        except Exception as e:
            print(f"快捷键注册失败: {e}")

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    guard = SingleInstanceGuard(SINGLE_INSTANCE_PORT)
    if guard.acquire():
        app = TaskWidget()
        guard.listen(app.show_window)
        app.run()
    else:
        guard.notify_show()
