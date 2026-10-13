import json
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
import urllib.request
from tkinter import ttk, font
from datetime import datetime, date, timedelta
from tkinter import messagebox
from todo_v2_core import (load_tasks_safely, save_tasks_atomically,
    archive_completed_task, ensure_archive_file,
    EXTRACT_SYSTEM_PROMPT, build_extraction_user_prompt, parse_extraction_result,
    VIEW_UNKNOWN, VIEW_OPTIONS)
from todo_v2_review import CandidateReviewDialog
# TODO_V2_PATCHED_2026_10

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(APP_DIR, "tasks.json")
ARCHIVE_FILE = os.path.join(APP_DIR, "归档.md")

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

CONFIG_FILE = os.path.join(APP_DIR, "config.json")


def read_api_key():
    """找 API Key，顺序：环境变量 → 本程序目录 config.json → ~/.dsh/.credentials.yaml"""
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env and env.strip():
        return env.strip()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                k = (json.load(f).get("deepseek_api_key") or "").strip()
            if k:
                return k
        except Exception:
            pass
    cred = os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")
    if os.path.exists(cred):
        try:
            with open(cred, "r", encoding="utf-8") as f:
                m = re.search(r"DEEPSEEK_API_KEY:\s*(sk-\S+)", f.read())
            if m:
                return m.group(1)
        except Exception:
            pass
    return ""


def save_api_key(key):
    """把 key 写进程序目录的 config.json（保留文件里其他字段）；传空字符串则清除"""
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception:
            cfg = {}
    key = (key or "").strip()
    if key:
        cfg["deepseek_api_key"] = key
    else:
        cfg.pop("deepseek_api_key", None)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ---- Soft Dark 调色板（v2.2）----
# 低对比深色、轻量分层：BG 最暗作底，BG2 是任务区域，BG3 再亮一层（兼作分割线）。
BG = "#1B1F27"
BG2 = "#292F3A"
BG3 = "#353B46"
FG = "#EEF2F7"
FG_MUTED = "#A6AFBD"
ACCENT = "#89B4FA"
GREEN = "#A6DA95"
# 优先级三色刻意压低饱和度，含义不变（高/中/低），但不抢主文字。
RED = "#E78284"
YELLOW = "#E5C07B"
BLUE = "#7FB4CA"

# 强调色块上的文字 / 强调色的按下态
ON_ACCENT = "#1B1F27"
ACCENT_ACTIVE = "#A6C8FF"
LINE = BG3


class TodoApp:
    def __init__(self, root):
        self.root = root
        self.tasks = self.load_tasks()
        self.drag_data = None
        self.resize_data = None
        self.always_on_top = True
        self.expanded_ids = set()

        self.font_body = font.Font(family="Microsoft YaHei UI", size=11)
        self.font_small = font.Font(family="Microsoft YaHei UI", size=9)
        self.font_strike = font.Font(family="Microsoft YaHei UI", size=11, overstrike=1)

        self.setup_window()
        self.build_ui()
        self.render()

    def setup_window(self):
        self.root.title("桌面待办清单")
        self.root.geometry("380x460")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)
        self.root.configure(bg=BG)
        self.position_at_right()

    def position_at_right(self):
        w = self.root.winfo_screenwidth()
        h = self.root.winfo_screenheight()
        self.root.geometry(f"+{w - 410}+{h - 600}")

    def _style_ttk(self):
        """ttk 控件默认跟系统主题走（浅色），在深色窗口里非常突兀，这里显式覆盖。
        注意：没配 ttk 样式前，v2.1 的优先级下拉框是「白底黑字」，一眼出戏。"""
        style = ttk.Style()
        try:
            style.theme_use("clam")   # vista 主题下 fieldbackground 根本改不动，必须换主题
        except tk.TclError:
            pass
        style.configure(
            "TCombobox",
            fieldbackground=BG3, background=BG3, foreground=FG,
            arrowcolor=FG_MUTED, bordercolor=BG3,
            lightcolor=BG3, darkcolor=BG3,
            selectbackground=BG3, selectforeground=FG,
            padding=(8, 4),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", BG3), ("active", BG3)],
            foreground=[("readonly", FG)],
            arrowcolor=[("active", ACCENT)],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=BG3, troughcolor=BG, bordercolor=BG,
            arrowcolor=FG_MUTED, lightcolor=BG3, darkcolor=BG3,
        )
        style.map("Vertical.TScrollbar", background=[("active", FG_MUTED)])
        # 下拉候选列表是原生 Listbox，不吃 ttk 样式，只能走 option 数据库
        self.root.option_add("*TCombobox*Listbox.background", BG3)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", ON_ACCENT)

    def build_ui(self):
        self._style_ttk()
        self.title_bar = tk.Frame(self.root, bg=BG3, height=38)
        self.title_bar.pack(fill="x")
        self.title_bar.pack_propagate(False)

        self.title_label = tk.Label(
            self.title_bar, text="📌 桌面待办", bg=BG3, fg=FG, font=self.font_body
        )
        self.title_label.pack(side="left", padx=12)

        self.btn_pin = tk.Button(
            self.title_bar,
            text="📌",
            bg=BG3,
            fg=FG_MUTED,
            relief="flat",
            activebackground=BG3,
            activeforeground=FG,
            cursor="hand2",
            bd=0,
            command=self.toggle_window_pin,
        )
        self.btn_pin.pack(side="right", padx=4, pady=4)

        self.btn_ai = tk.Button(
            self.title_bar,
            text="✨",
            bg=BG3,
            fg=YELLOW,
            relief="flat",
            activebackground=BG3,
            activeforeground=YELLOW,
            cursor="hand2",
            bd=0,
            command=self.open_ai_dialog,
        )
        self.btn_ai.pack(side="right", padx=2, pady=4)

        self.btn_arch = tk.Button(
            self.title_bar,
            text="🗂",
            bg=BG3,
            fg=BLUE,
            relief="flat",
            activebackground=BG3,
            activeforeground=BLUE,
            cursor="hand2",
            bd=0,
            command=self.open_archive,
        )
        self.btn_arch.pack(side="right", padx=2, pady=4)

        self.btn_cfg = tk.Button(
            self.title_bar,
            text="⚙ 设置",
            bg=BG3,
            fg=FG_MUTED,
            relief="flat",
            activebackground=BG3,
            activeforeground=FG,
            cursor="hand2",
            bd=0,
            command=self.open_api_dialog,
        )
        self.btn_cfg.pack(side="right", padx=2, pady=4)

        self.btn_close = tk.Button(
            self.title_bar,
            text="✕",
            bg=BG3,
            fg=FG_MUTED,
            relief="flat",
            activebackground=RED,
            activeforeground="#FFFFFF",
            cursor="hand2",
            bd=0,
            command=self.root.destroy,
        )
        self.btn_close.pack(side="right", padx=6, pady=4)

        self.title_bar.bind("<Button-1>", self.start_drag)
        self.title_bar.bind("<B1-Motion>", self.on_drag)
        self.title_label.bind("<Button-1>", self.start_drag)
        self.title_label.bind("<B1-Motion>", self.on_drag)

        self.list_container = tk.Frame(self.root, bg=BG)
        self.list_container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            self.list_container, bg=BG, highlightthickness=0, bd=0
        )
        self.scrollbar = ttk.Scrollbar(
            self.list_container, orient="vertical", command=self.canvas.yview
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.task_frame = tk.Frame(self.canvas, bg=BG)
        self.task_window = self.canvas.create_window(
            (0, 0), window=self.task_frame, anchor="nw"
        )
        self.task_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self.task_window, width=e.width),
        )
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.bind_mousewheel(self.list_container)
        self.bind_mousewheel(self.task_frame)

        self.input_frame = tk.Frame(self.root, bg=BG2)
        self.input_frame.pack(fill="x", side="bottom")

        self.entry = tk.Entry(
            self.input_frame,
            bg=BG3,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=self.font_body,
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(10, 4), pady=8, ipady=5)
        self.entry.bind("<Return>", lambda e: self.add_task())

        self.btn_add = tk.Button(
            self.input_frame,
            text="＋",
            bg=ACCENT,
            fg=ON_ACCENT,
            relief="flat",
            font=self.font_body,
            activebackground=ACCENT_ACTIVE,
            activeforeground=ON_ACCENT,
            cursor="hand2",
            bd=0,
            width=3,
            command=self.add_task,
        )
        self.btn_add.pack(side="right", padx=(0, 10), pady=8)

        self.toolbar = tk.Frame(self.root, bg=BG2)
        self.toolbar.pack(fill="x", side="bottom")

        self.pri_var = tk.StringVar(value="中")
        self.pri_menu = ttk.Combobox(
            self.toolbar,
            textvariable=self.pri_var,
            values=["高", "中", "低"],
            width=3,
            state="readonly",
            font=self.font_small,
        )
        self.pri_menu.pack(side="left", padx=(10, 4), pady=6)

        self.btn_clear_done = tk.Button(
            self.toolbar,
            text="🗑 清理完成",
            bg=BG2,
            fg=FG_MUTED,
            relief="flat",
            activebackground=BG3,
            activeforeground=RED,
            cursor="hand2",
            bd=0,
            font=self.font_small,
            command=self.clear_done,
        )
        self.btn_clear_done.pack(side="right", padx=10, pady=6)

        self.status_bar = tk.Label(
            self.root, text="", bg=BG2, fg=FG_MUTED, font=self.font_small, anchor="w"
        )
        self.status_bar.pack(fill="x", side="bottom")

        grip = tk.Frame(self.root, bg=BG2, width=14, height=14, cursor="size_nw_se")
        grip.pack(side="bottom", anchor="se", padx=0, pady=0)
        grip.bind("<Button-1>", self.start_resize)
        grip.bind("<B1-Motion>", self.on_resize)

    def on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def bind_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self.on_mousewheel)
        for child in widget.winfo_children():
            self.bind_mousewheel(child)

    def load_tasks(self):
        return load_tasks_safely(DATA_FILE)

    def save_tasks(self):
        save_tasks_atomically(DATA_FILE, self.tasks)
    def add_task(self):
        text = self.entry.get().strip()
        if not text:
            return
        task = {
            "id": self.next_id(),
            "text": text,
            "done": False,
            "priority": self.pri_var.get(),
            "start": "",
            "due": "",
            "pinned": False,
        }
        self.tasks.append(task)
        self.entry.delete(0, "end")
        self.save_tasks()
        self.render()

    def next_id(self):
        ids = [t["id"] for t in self.tasks]
        return max(ids, default=0) + 1

    def build_task(self, text, item=None):
        item = item or {}
        task = {
            "id": self.next_id(),
            "text": text,
            "done": False,
            "priority": str(item.get("priority", "中")).strip() or "中",
            "start": str(item.get("start", "")).strip() or "",
            "due": str(item.get("due", "")).strip() or "",
            "note": str(item.get("note", "")).strip() or "",
            "pinned": False,
        }
        if task["start"] and not self.valid_date(task["start"]):
            task["start"] = ""
        if task["due"] and not self.valid_date(task["due"]):
            task["due"] = ""
        return task

    def valid_date(self, s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    def toggle_done(self, task_id):
        for task in self.tasks:
            if task["id"] != task_id:
                continue
            if task.get("done"):
                task["done"] = False
            else:
                try:
                    archive_completed_task(ARCHIVE_FILE, task)
                except OSError as exc:
                    messagebox.showerror("归档失败", "任务未标记完成，归档写入失败：" + str(exc), parent=self.root)
                    return
                task["done"] = True
            try:
                self.save_tasks()
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc), parent=self.root)
                return
            self.render()
            break

    def archive_task(self, task):
        """保留旧方法名，供已有脚本调用；实际写入由安全归档函数处理。"""
        return archive_completed_task(ARCHIVE_FILE, task)
    def toggle_expand(self, task_id):
        if task_id in self.expanded_ids:
            self.expanded_ids.discard(task_id)
        else:
            self.expanded_ids.add(task_id)
        self.render()

    def toggle_pin(self, task_id):
        for t in self.tasks:
            if t["id"] == task_id:
                t["pinned"] = not t.get("pinned", False)
                break
        self.save_tasks()
        self.render()

    def delete_task(self, task_id):
        self.tasks = [t for t in self.tasks if t["id"] != task_id]
        self.save_tasks()
        self.render()

    def edit_task(self, task_id):
        t = next((x for x in self.tasks if x["id"] == task_id), None)
        if not t:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("编辑任务")
        dlg.configure(bg=BG2)
        dlg.transient(self.root)
        dlg.attributes("-topmost", True)
        dlg.grab_set()
        w = max(self.root.winfo_width(), 320)
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + 60
        dlg.geometry(f"{w}x240+{x}+{y}")

        lab = tk.Label(dlg, text="任务内容", bg=BG2, fg=FG, font=self.font_small, anchor="w")
        lab.pack(fill="x", padx=12, pady=(12, 4))
        box = tk.Text(
            dlg,
            bg=BG3,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=self.font_body,
            wrap="word",
            height=6,
        )
        box.insert("1.0", t["text"])
        box.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        box.focus_set()

        def on_ok():
            val = box.get("1.0", "end-1c").strip()
            if not val:
                self.flash_status("内容不能为空")
                return
            t["text"] = val
            self.save_tasks()
            self.render()
            dlg.destroy()

        btn_frame = tk.Frame(dlg, bg=BG2)
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(
            btn_frame,
            text="保存",
            bg=ACCENT,
            fg=ON_ACCENT,
            relief="flat",
            activebackground=ACCENT_ACTIVE,
            activeforeground=ON_ACCENT,
            cursor="hand2",
            bd=0,
            font=self.font_small,
            command=on_ok,
        ).pack(side="right")
        tk.Button(
            btn_frame,
            text="取消",
            bg=BG3,
            fg=FG,
            relief="flat",
            activebackground=BG2,
            cursor="hand2",
            bd=0,
            font=self.font_small,
            command=dlg.destroy,
        ).pack(side="right", padx=(0, 8))
        dlg.bind("<Return>", lambda e: on_ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def clear_done(self):
        self.tasks = [t for t in self.tasks if not t["done"]]
        self.expanded_ids.clear()
        self.save_tasks()
        self.render()

    def open_api_dialog(self):
        ApiKeyDialog(self)

    def open_ai_dialog(self):
        AiDialog(self)

    def open_archive(self):
        try:
            ensure_archive_file(ARCHIVE_FILE)
            if os.name == "nt":
                os.startfile(ARCHIVE_FILE)
            elif os.sys.platform == "darwin":
                subprocess.Popen(["open", ARCHIVE_FILE])
            else:
                subprocess.Popen(["xdg-open", ARCHIVE_FILE])
        except OSError as exc:
            messagebox.showerror("无法打开归档", str(exc), parent=self.root)
    def change_priority(self, task_id, value):
        for t in self.tasks:
            if t["id"] == task_id:
                t["priority"] = value
                break
        self.save_tasks()
        self.render()

    def flash_status(self, msg):
        self.status_bar.config(text=msg)
        self.root.after(2500, lambda: self.status_bar.config(text=""))

    def priority_color(self, p):
        return {"高": RED, "中": YELLOW, "低": BLUE}.get(p, FG_MUTED)

    def priority_icon(self, p):
        return {"高": "🔴", "中": "🟡", "低": "🔵"}.get(p, "⚪")

    def start_label(self, start):
        if not start:
            return ""
        try:
            d = datetime.strptime(start, "%Y-%m-%d").date()
            today = date.today()
            delta = (d - today).days
            if delta == 0:
                return "今天开始"
            if delta > 0 and delta <= 3:
                return f"{delta}天后开始"
            return f"{d.month}月{d.day}日开始"
        except ValueError:
            return start

    def due_label(self, due):
        if not due:
            return ""
        try:
            d = datetime.strptime(due, "%Y-%m-%d").date()
            today = date.today()
            delta = (d - today).days
            if delta < 0:
                return f"逾期{days_label(-delta)}"
            if delta == 0:
                return "今天截止"
            if delta <= 3:
                return f"剩{delta}天"
            return f"{d.month}月{d.day}日"
        except ValueError:
            return due

    def render(self):
        for w in self.task_frame.winfo_children():
            w.destroy()

        order_map = {"高": 0, "中": 1, "低": 2}
        pinned = [t for t in self.tasks if not t["done"] and t.get("pinned")]
        active = [t for t in self.tasks if not t["done"] and not t.get("pinned")]
        done_list = [t for t in self.tasks if t["done"]]
        active.sort(key=lambda t: (order_map.get(t["priority"], 9), t.get("due", "")))
        done_list.sort(key=lambda t: t["id"], reverse=True)

        all_tasks = pinned + active + done_list

        if not all_tasks:
            empty = tk.Label(
                self.task_frame,
                text="还没有任务\n在下方输入，回车添加 ✨",
                bg=BG,
                fg=FG_MUTED,
                font=self.font_body,
                justify="center",
            )
            empty.pack(pady=40)
            self.bind_mousewheel(empty)
            self.update_status()
            return

        for t in all_tasks:
            row = tk.Frame(self.task_frame, bg=BG)
            row.pack(fill="x", padx=8, pady=3)

            card = tk.Frame(row, bg=BG2)
            card.pack(fill="x")
            card.bind("<Button-3>", lambda e, tid=t["id"]: self.show_context_menu(e, tid))

            text_font = self.font_strike if t["done"] else self.font_body
            text_color = FG_MUTED if t["done"] else FG

            # 布局顺序很关键：右侧的小按钮要「先占位」，任务文字最后 pack 去填剩下的空间。
            # v2.1 是把文字标签先 pack 了的，长标题一换行就把 ✎ / ✕ 挤成 0 宽度
            # （右侧按钮整排消失）——这是这次要修的可读性问题。
            def _bind_menu(w):
                w.bind("<Button-3>", lambda e, tid=t["id"]: self.show_context_menu(e, tid))

            pri_lbl = tk.Label(
                card,
                text=self.priority_icon(t["priority"]),
                bg=BG2,
                fg=self.priority_color(t["priority"]),
                font=self.font_small,
            )
            _bind_menu(pri_lbl)
            pri_lbl.pack(side="left", padx=(10, 6), pady=7)

            check_var = tk.StringVar(value="☑" if t["done"] else "☐")
            check_btn = tk.Button(
                card,
                textvariable=check_var,
                bg=BG2,
                fg=GREEN if t["done"] else FG_MUTED,
                relief="flat",
                activebackground=BG3,
                cursor="hand2",
                bd=0,
                font=self.font_body,
                command=lambda tid=t["id"]: self.toggle_done(tid),
            )
            check_btn.pack(side="left", padx=(0, 2), pady=7)

            # 右侧控件按「从右往左」的顺序进列表：先 pack 的在最右边
            right_side = []

            if t.get("pinned"):
                pin_lbl = tk.Label(card, text="📍", bg=BG2, fg=ACCENT, font=self.font_small)
                _bind_menu(pin_lbl)
                right_side.append((pin_lbl, (0, 4)))

            if t.get("note") or t.get("start") or t.get("due") or t.get("priority"):
                expand_lbl = tk.Label(
                    card,
                    text="▾" if t["id"] in self.expanded_ids else "▸",
                    bg=BG2,
                    fg=FG_MUTED,
                    font=self.font_small,
                    cursor="hand2",
                )
                expand_lbl.bind("<Button-1>", lambda e, tid=t["id"]: self.toggle_expand(tid))
                right_side.append((expand_lbl, (0, 6)))

            start_text = self.start_label(t.get("start", ""))
            if start_text:
                start_lbl = tk.Label(
                    card, text=f"🕐{start_text}", bg=BG2, fg=GREEN, font=self.font_small,
                )
                _bind_menu(start_lbl)
                right_side.append((start_lbl, (4, 0)))

            due_text = self.due_label(t.get("due", ""))
            if due_text:
                overdue = due_text.startswith("逾期") and not t["done"]
                due_lbl = tk.Label(
                    card, text=f"⏰{due_text}", bg=BG2,
                    fg=RED if overdue else FG_MUTED, font=self.font_small,
                )
                _bind_menu(due_lbl)
                right_side.append((due_lbl, (4, 8)))

            del_btn = tk.Button(
                card,
                text="✕",
                bg=BG2,
                fg=FG_MUTED,
                relief="flat",
                activebackground=BG3,
                activeforeground=RED,
                cursor="hand2",
                bd=0,
                font=self.font_small,
                command=lambda tid=t["id"]: self.delete_task(tid),
            )
            right_side.append((del_btn, (0, 6)))

            edit_btn = tk.Button(
                card,
                text="✎",
                bg=BG2,
                fg=BLUE,
                relief="flat",
                activebackground=BG3,
                activeforeground=BLUE,
                cursor="hand2",
                bd=0,
                font=self.font_small,
                command=lambda tid=t["id"]: self.edit_task(tid),
            )
            right_side.append((edit_btn, (0, 2)))

            for widget, pad in right_side:
                widget.pack(side="right", padx=pad, pady=7)

            text_lbl = tk.Label(
                card,
                text=t["text"],
                bg=BG2,
                fg=text_color,
                font=text_font,
                anchor="w",
                justify="left",
                wraplength=200,
            )
            _bind_menu(text_lbl)
            text_lbl.bind("<Button-1>", lambda e, tid=t["id"]: self.toggle_expand(tid))
            text_lbl.pack(side="left", fill="x", expand=True, pady=7)

            def _autowrap(ev, lbl=text_lbl):
                """让换行宽度跟着标签实际拿到的宽度走——固定 180px 会把长标题挤成好几行。"""
                want = max(90, ev.width - 8)
                if abs(int(lbl.cget("wraplength")) - want) > 2:
                    lbl.configure(wraplength=want)
            text_lbl.bind("<Configure>", _autowrap)

            if t["id"] in self.expanded_ids:
                detail = tk.Frame(row, bg=BG3)
                detail.pack(fill="x", padx=(8, 8), pady=(0, 3))

                if t.get("note"):
                    note_lbl = tk.Label(
                        detail,
                        text=t["note"],
                        bg=BG3,
                        fg=FG_MUTED,
                        font=self.font_small,
                        anchor="w",
                        justify="left",
                        wraplength=330,
                    )
                    note_lbl.pack(fill="x", padx=8, pady=(6, 2))
                    note_lbl.bind("<Button-3>", lambda e, tid=t["id"]: self.show_context_menu(e, tid))

                meta = []
                if t.get("priority"):
                    meta.append(f"优先级：{t['priority']}")
                if t.get("start"):
                    meta.append(f"开始：{t['start']}")
                if t.get("due"):
                    meta.append(f"截止：{t['due']}")
                if t.get("created"):
                    meta.append(f"创建：{t['created']}")
                if meta:
                    meta_lbl = tk.Label(
                        detail,
                        text="  |  ".join(meta),
                        bg=BG3,
                        fg=FG,
                        font=self.font_small,
                        anchor="w",
                    )
                    meta_lbl.pack(fill="x", padx=8, pady=(0, 6))
                    meta_lbl.bind("<Button-3>", lambda e, tid=t["id"]: self.show_context_menu(e, tid))

            self.bind_mousewheel(row)

        self.update_status()

    def update_status(self):
        total = len(self.tasks)
        done = sum(1 for t in self.tasks if t["done"])
        self.status_bar.config(text=f"共 {total} 项 · 完成 {done} 项" + (" · 🎉" if total and done == total else ""))

    def show_context_menu(self, event, task_id):
        menu = tk.Menu(self.root, tearoff=0)
        t = next((x for x in self.tasks if x["id"] == task_id), None)
        if not t:
            return
        if t["done"]:
            menu.add_command(label="✔ 标记未完成", command=lambda: self.toggle_done(task_id))
        else:
            menu.add_command(label="✔ 标记完成", command=lambda: self.toggle_done(task_id))
        menu.add_separator()
        pri_menu = tk.Menu(menu, tearoff=0)
        for p in ["高", "中", "低"]:
            pri_menu.add_command(
                label=f"{self.priority_icon(p)} {p}优先级",
                command=lambda v=p: self.change_priority(task_id, v),
            )
        menu.add_cascade(label="⚡ 优先级", menu=pri_menu)
        pin_text = "📍 取消置顶" if t.get("pinned") else "📍 置顶（固定在最上方，不参与排序）"
        menu.add_command(label=pin_text, command=lambda: self.toggle_pin(task_id))
        menu.add_command(label="✎ 编辑任务", command=lambda: self.edit_task(task_id))
        menu.add_separator()
        menu.add_command(label="🗑 删除任务", command=lambda: self.delete_task(task_id))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def start_drag(self, event):
        self.drag_data = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def on_drag(self, event):
        if self.drag_data:
            x = event.x_root - self.drag_data[0]
            y = event.y_root - self.drag_data[1]
            self.root.geometry(f"+{x}+{y}")

    def start_resize(self, event):
        self.resize_data = (
            event.x_root,
            event.y_root,
            self.root.winfo_width(),
            self.root.winfo_height(),
        )

    def on_resize(self, event):
        if self.resize_data:
            dx = event.x_root - self.resize_data[0]
            dy = event.y_root - self.resize_data[1]
            w = max(self.resize_data[2] + dx, 260)
            h = max(self.resize_data[3] + dy, 300)
            self.root.geometry(f"{int(w)}x{int(h)}")

    def toggle_window_pin(self):
        """标题栏 📌：窗口置顶开关。
        注意：这个方法以前也叫 toggle_pin，把上面「单个任务置顶」的同名方法覆盖掉了
        （Python 里后定义的生效），导致右键菜单的「置顶」点了没反应。改名修掉。"""
        self.always_on_top = not self.always_on_top
        self.root.attributes("-topmost", self.always_on_top)
        self.btn_pin.config(text="📌" if self.always_on_top else "📍", fg=ACCENT if self.always_on_top else FG_MUTED)


def days_label(n):
    return f"{n}天"


class ApiKeyDialog(tk.Toplevel):
    """填写 DeepSeek API Key 的小窗口——不用命令行、不用编辑器"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("设置")
        self.configure(bg=BG2)
        self.attributes("-topmost", True)
        self.geometry("470x560")
        self.transient(app.root)
        self.grab_set()

        def note(txt, fg=FG_MUTED, size=None):
            tk.Label(self, text=txt, bg=BG2, fg=fg, font=size or app.font_small,
                     anchor="w", justify="left", wraplength=430).pack(
                fill="x", padx=16, pady=(7, 0))

        note("DeepSeek API Key（可选）", fg=FG, size=app.font_body)
        note("· 不填也能用：待办增删改、优先级、到期提醒全部可用；"
             "点「✨ 整理」时会用本地规则拆分条目、识别日期。")
        note("· 填上之后，「✨ 整理」改由 AI 处理：合并同类、判优先级、补日期，更贴近原意。")
        note("· 去哪儿申请：platform.deepseek.com → API keys")

        self.var = tk.StringVar(value=read_api_key())
        # 默认遮罩：设置窗口很可能在演示/录屏里被打开，明文显示 key 会直接泄出去。
        self.show_key = tk.BooleanVar(value=False)
        key_row = tk.Frame(self, bg=BG2)
        key_row.pack(fill="x", padx=16, pady=(10, 0))
        ent = tk.Entry(key_row, textvariable=self.var, bg=BG3, fg=FG,
                       insertbackground=FG, relief="flat", font=app.font_small,
                       show="•")
        ent.pack(side="left", fill="x", expand=True, ipady=6)
        tk.Checkbutton(
            key_row, text="显示", variable=self.show_key,
            command=lambda: ent.config(show="" if self.show_key.get() else "•"),
            bg=BG2, fg=FG_MUTED, selectcolor=BG3, activebackground=BG2,
            activeforeground=FG, font=app.font_small, bd=0, highlightthickness=0,
        ).pack(side="left", padx=(6, 0))
        ent.focus_set()

        note("Key 只保存在本机 config.json 里，不上传、不进 Git。")

        row = tk.Frame(self, bg=BG2)
        row.pack(fill="x", padx=16, pady=(14, 4))
        tk.Button(row, text="保存", bg=ACCENT, fg=ON_ACCENT, relief="flat",
                  font=app.font_small, cursor="hand2", bd=0, padx=18, pady=5,
                  command=self.save).pack(side="left")
        tk.Button(row, text="清除", bg=BG3, fg=FG, relief="flat",
                  font=app.font_small, cursor="hand2", bd=0, padx=14, pady=5,
                  command=self.clear).pack(side="left", padx=(8, 0))
        self.status = tk.Label(self, text="", bg=BG2, fg=GREEN,
                               font=app.font_small, anchor="w")
        self.status.pack(fill="x", padx=16, pady=(2, 12))

        # ---- 归档文件 ----
        tk.Frame(self, bg=BG3, height=1).pack(fill="x", padx=16, pady=(0, 0))
        note("归档文件", fg=FG, size=app.font_body)
        note("每次勾选完成任务，就往这个文件追加一条记录；同一任务不会重复归档。")

        self.archive_path_var = tk.StringVar(value=ARCHIVE_FILE)
        path_ent = tk.Entry(
            self, textvariable=self.archive_path_var, bg=BG3, fg=FG_MUTED,
            insertbackground=FG, relief="flat", font=app.font_small,
            state="readonly", readonlybackground=BG3,
        )
        path_ent.pack(fill="x", padx=16, pady=(6, 0), ipady=5)
        # 只读 Entry 也能选中复制：点一下就全选
        path_ent.bind("<FocusIn>", lambda e: path_ent.select_range(0, "end"))

        arow = tk.Frame(self, bg=BG2)
        arow.pack(fill="x", padx=16, pady=(8, 12))
        tk.Button(arow, text="打开文件", bg=BG3, fg=FG, relief="flat",
                  font=app.font_small, cursor="hand2", bd=0, padx=14, pady=5,
                  command=self.open_archive_file).pack(side="left")
        tk.Button(arow, text="打开所在文件夹", bg=BG3, fg=FG, relief="flat",
                  font=app.font_small, cursor="hand2", bd=0, padx=14, pady=5,
                  command=self.open_archive_folder).pack(side="left", padx=(8, 0))
        tk.Button(arow, text="关闭", bg=BG3, fg=FG, relief="flat",
                  font=app.font_small, cursor="hand2", bd=0, padx=14, pady=5,
                  command=self.destroy).pack(side="right")

    def open_archive_file(self):
        """打开归档文件本身"""
        try:
            ensure_archive_file(ARCHIVE_FILE)
            if os.name == "nt":
                os.startfile(ARCHIVE_FILE)
            elif os.sys.platform == "darwin":
                subprocess.Popen(["open", ARCHIVE_FILE])
            else:
                subprocess.Popen(["xdg-open", ARCHIVE_FILE])
        except OSError as exc:
            self.status.config(text="打开归档失败：" + str(exc), fg=RED)

    def open_archive_folder(self):
        """打开归档文件所在的文件夹，并把它选中"""
        try:
            path = os.path.normpath(ARCHIVE_FILE)
            if os.name == "nt":
                subprocess.Popen('explorer /select,"%s"' % path)
            elif os.sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except OSError as exc:
            self.status.config(text="打开文件夹失败：" + str(exc), fg=RED)

    def save(self):
        save_api_key(self.var.get())
        self.status.config(text="已保存，下次点「✨ 整理」就走 AI 了", fg=GREEN)

    def clear(self):
        save_api_key("")
        self.var.set("")
        self.status.config(text="已清除，将使用本地规则整理", fg=FG_MUTED)


class AiDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("✨ AI 自动整理")
        self.configure(bg=BG2)
        self.attributes("-topmost", True)
        self.geometry("470x560")
        self.transient(app.root)
        self.grab_set()

        head = tk.Label(
            self,
            text=("把原始信息粘贴到下面，点击整理，AI 自动提取任务并定优先级"
                  if read_api_key() else
                  "把原始信息粘贴到下面，点击整理（未配 API Key，将用本地规则拆分；点主窗口 ⚙ 可填写）"),
            bg=BG2,
            fg=FG,
            font=app.font_small,
            anchor="w",
        )
        head.pack(fill="x", padx=12, pady=(10, 4))

        name_row = tk.Frame(self, bg=BG2)
        name_row.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(
            name_row, text="任务归属人", bg=BG2, fg=FG, font=app.font_small,
        ).pack(side="left")
        self.owner_var = tk.StringVar(value="")
        self.owner_entry = tk.Entry(
            name_row, textvariable=self.owner_var, bg=BG3, fg=FG,
            insertbackground=FG, relief="flat", font=app.font_small, width=12,
        )
        self.owner_entry.pack(side="left", padx=(6, 0))
        date_row = tk.Frame(self, bg=BG2)
        date_row.pack(fill="x", padx=12, pady=(0, 5))
        tk.Label(date_row, text="原消息日期（可选）", bg=BG2, fg=FG,
                 font=app.font_small).pack(side="left")
        self.message_date_var = tk.StringVar(value="")
        tk.Entry(date_row, textvariable=self.message_date_var, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", width=14).pack(side="left", padx=6)
        tk.Label(date_row, text="YYYY-MM-DD；不填则不换算相对时间",
                 bg=BG2, fg=FG_MUTED, font=app.font_small).pack(side="left")

        # v2.1 消息视角：决定「你/我」怎么对应到人。未知视角下不敢猜的责任会交给你确认。
        view_row = tk.Frame(self, bg=BG2)
        view_row.pack(fill="x", padx=12, pady=(0, 5))
        tk.Label(view_row, text="消息视角", bg=BG2, fg=FG,
                 font=app.font_small).pack(side="left")
        self.view_var = tk.StringVar(value=VIEW_UNKNOWN)
        for val, lab in VIEW_OPTIONS:
            tk.Radiobutton(
                view_row, text=lab, variable=self.view_var, value=val,
                bg=BG2, fg=FG, selectcolor=BG3, font=app.font_small,
                activebackground=BG2, activeforeground=FG,
            ).pack(side="left", padx=(6, 0))

        self.box = tk.Text(
            self,
            bg=BG3,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=app.font_body,
            wrap="word",
            height=14,
        )
        self.box.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        opt = tk.Frame(self, bg=BG2)
        opt.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(opt, text="v2：仅提取新任务，先人工确认；旧任务优先级不自动修改",
                 bg=BG2, fg=FG_MUTED, font=app.font_small).pack(side="left")
        self.box.bind("<Control-Return>", lambda e: self.run_ai())

        btn = tk.Frame(self, bg=BG2)
        btn.pack(fill="x", padx=12, pady=(0, 10))
        self.btn_go = tk.Button(
            btn, text="✨ 整理", bg=ACCENT, fg=ON_ACCENT, relief="flat",
            font=app.font_small, cursor="hand2", bd=0, padx=16, pady=4,
            command=self.run_ai,
        )
        self.btn_go.pack(side="left")
        tk.Button(
            btn, text="关闭", bg=BG3, fg=FG, relief="flat",
            font=app.font_small, cursor="hand2", bd=0,
            command=self.destroy,
        ).pack(side="left", padx=(8, 0))

        self.status = tk.Label(self, text="", bg=BG2, fg=GREEN, font=app.font_small, anchor="w")
        self.status.pack(fill="x", padx=12, pady=(0, 8))

    def run_ai(self):
        raw = self.box.get("1.0", "end-1c").strip()
        owner = self.owner_var.get().strip()
        msg_date = self.message_date_var.get().strip()
        view = self.view_var.get()
        if not raw:
            self.status.config(text="请先粘贴原始信息", fg=RED)
            return
        try:
            prompt = build_extraction_user_prompt(owner, raw, msg_date, view)
        except ValueError as exc:
            self.status.config(text=str(exc), fg=RED)
            return
        using_ai = bool(read_api_key())
        self.btn_go.config(state="disabled")
        self.status.config(text="AI 识别中…" if using_ai else "离线拆分中…", fg=FG_MUTED)
        threading.Thread(target=self._work, args=(raw, prompt, msg_date, using_ai), daemon=True).start()

    def _work(self, raw, prompt, msg_date, using_ai):
        try:
            if using_ai:
                reply = call_deepseek(prompt)
                candidates = parse_extraction_result(reply, raw, msg_date)
            else:
                # Rule-based offline fallback cannot infer owner; do not pretend that it can.
                items = local_organize(raw)
                candidates = {"confirmed_tasks": [], "needs_confirmation": []}
                for item in items:
                    candidates["needs_confirmation"].append({
                        "text": item.get("text", ""), "priority": item.get("priority", "中"),
                        "due": item.get("due", ""), "deadline_raw": "",
                        "evidence": item.get("evidence", item.get("text", "")),
                        "note": "", "reason": "离线规则仅能拆分文本，无法确定负责人"})
            self.after(0, lambda: self._show_review(candidates, using_ai))
        except Exception as exc:
            error = str(exc)
            self.after(0, lambda: self._fail(error))

    def _show_review(self, candidates, using_ai):
        self.btn_go.config(state="normal")
        n = len(candidates["confirmed_tasks"]) + len(candidates["needs_confirmation"])
        self.status.config(text=f"识别到 {n} 项候选；审核后才会加入待办", fg=GREEN)
        CandidateReviewDialog(self.app, candidates, "ai" if using_ai else "local",
                              self.owner_var.get().strip(), self.view_var.get())
    def _fail(self, err):
        self.btn_go.config(state="normal")
        self.status.config(text=f"出错：{err}", fg=RED)


# ---------------- 本地规则整理（没配 API Key 时的兜底，完全不联网） ----------------

BULLET_RE = re.compile(r"^\s*(?:[-*•·]|\(\d+\)|\d+[.、)）]|[①-⑳]|\[[ xX]?\]|【[^】]{0,8}】)\s*")
# 出现这些词 → 判为高优先级
DUE_WORDS = ("截止", "deadline", "最晚", "之前", "前提交", "前完成",
             "必须", "紧急", "尽快", "重点", "优先", "今晚", "今天")
# 出现这些词 → 判为低优先级（安排 / 通知 / 信息类，非行动项）
LOW_WORDS = ("会议", "例会", "周会", "通知", "安排", "提醒", "备忘",
             "待跟进", "背景", "同步", "知悉", "讨论")
WEEKDAY_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _parse_date(s, today):
    """从一段文字里认日期，返回 YYYY-MM-DD；认不出返回空字符串"""
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except Exception:
            return ""
    m = re.search(r"(\d{1,2})\s*[月/]\s*(\d{1,2})\s*[日号]?", s)
    if m:
        try:
            return date(today.year, int(m.group(1)), int(m.group(2))).isoformat()
        except Exception:
            return ""
    if "大后天" in s:
        return (today + timedelta(days=3)).isoformat()
    if "后天" in s:
        return (today + timedelta(days=2)).isoformat()
    if "明天" in s or "明日" in s:
        return (today + timedelta(days=1)).isoformat()
    if "今天" in s or "今日" in s:
        return today.isoformat()
    m = re.search(r"(下+)?(?:周|星期|礼拜)([一二三四五六日天])", s)
    if m:
        delta = (WEEKDAY_CN[m.group(2)] - today.weekday()) % 7
        if m.group(1):
            delta += 7
        return (today + timedelta(days=delta)).isoformat()
    m = re.search(r"(\d+)\s*天后", s)
    if m:
        return (today + timedelta(days=int(m.group(1)))).isoformat()
    return ""


# 离线模式没有语义能力，标题只能机械精简：先去掉开头的「@某人…：」，
# 再在 limit 内找最后一个句末标点／逗号断开，都没有就硬截。
# 截掉的信息不会丢——完整原文会进「原文依据」备注。
TITLE_LIMIT = 40
_MENTION_PREFIX_RE = re.compile(r"^\s*@[^：:\n]{1,20}?[：:]\s*")
_SENT_END_RE = re.compile(r"[。；！？!?;]")
_CLAUSE_END_RE = re.compile(r"[，,、]")


def _short_title(text, limit=TITLE_LIMIT):
    t = _MENTION_PREFIX_RE.sub("", text.strip())
    if len(t) <= limit:
        return t
    for pattern in (_SENT_END_RE, _CLAUSE_END_RE):
        cuts = [m.start() for m in pattern.finditer(t) if 6 <= m.start() <= limit]
        if cuts:
            return t[: max(cuts)]
    return t[:limit] + "…"


def local_organize(raw):
    """不联网的规则整理：拆行 → 认日期 → 按关键词判优先级。输出结构与 AI 返回一致。"""
    today = date.today()
    out, seen = [], set()
    for line in raw.splitlines():
        t = BULLET_RE.sub("", line).strip().strip("　 \t")
        if len(t) < 2:
            continue
        if t.endswith(("：", ":")) and len(t) <= 12:   # 像小标题的，跳过
            continue
        if t in seen:
            continue
        seen.add(t)
        if any(w in t for w in DUE_WORDS):
            pri = "高"
        elif any(w in t for w in LOW_WORDS):
            pri = "低"
        else:
            pri = "中"
        out.append({
            "text": _short_title(t),
            "priority": pri,
            "start": "",
            "due": _parse_date(t, today),
            "note": "",
            "evidence": t,          # 完整原文，进审核窗的「原文依据」，精简掉的不丢
        })
    order = {"高": 0, "中": 1, "低": 2}
    out.sort(key=lambda x: order.get(x["priority"], 1))
    return out


def call_deepseek(prompt):
    key = read_api_key()
    if not key:
        raise RuntimeError("未找到 DeepSeek API Key")
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    req = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]
def parse_tasks_json(text):
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("AI 返回格式无法解析，请重试")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise RuntimeError("AI 返回格式无法解析，请重试")
    out = []
    for item in data:
        if isinstance(item, dict):
            out.append(item)
    return out


def main():
    root = tk.Tk()
    try:
        app = TodoApp(root)
    except Exception as exc:
        messagebox.showerror("Todo 启动失败（任务数据未被覆盖）", str(exc), parent=root)
        root.destroy()
        return
    root.mainloop()
if __name__ == "__main__":
    main()
