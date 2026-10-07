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

BG = "#1e1e2e"
BG2 = "#27293d"
BG3 = "#313244"
FG = "#cdd6f4"
FG_MUTED = "#7f849c"
ACCENT = "#cba6f7"
GREEN = "#a6e3a1"
RED = "#f38ba8"
YELLOW = "#f9e2af"
BLUE = "#89b4fa"


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

    def build_ui(self):
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
            command=self.toggle_pin,
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
            text="⚙",
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
            activeforeground="#ffffff",
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
            fg="#1e1e2e",
            relief="flat",
            font=self.font_body,
            activebackground="#b4befe",
            activeforeground="#1e1e2e",
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
        if not os.path.exists(DATA_FILE):
            return []
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_tasks(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.tasks, f, ensure_ascii=False, indent=2)

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
        for t in self.tasks:
            if t["id"] == task_id:
                t["done"] = not t["done"]
                if t["done"]:
                    self.archive_task(t)
                break
        self.save_tasks()
        self.render()

    def archive_task(self, t):
        try:
            now = datetime.now()
            head = f"\n## {now.strftime('%Y-%m-%d')}\n"
            line = f"- [x] {t['text']} ｜ 优先级：{t.get('priority', '中')}"
            if t.get("due"):
                line += f" ｜ 截止：{t['due']}"
            if t.get("start"):
                line += f" ｜ 开始：{t['start']}"
            line += f" ｜ 完成于 {now.strftime('%H:%M')}\n"
            if not os.path.exists(ARCHIVE_FILE):
                with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
                    f.write("# 桌面待办 · 归档\n")
            with open(ARCHIVE_FILE, "a", encoding="utf-8") as f:
                f.write(head + line)
            self.flash_status(f"已归档：{t['text'][:20]}")
        except OSError:
            self.flash_status("归档写入失败，请检查 E:\工作 权限")

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
            fg="#1e1e2e",
            relief="flat",
            activebackground="#b4befe",
            activeforeground="#1e1e2e",
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
            if os.name == "nt":
                os.startfile(ARCHIVE_FILE)
            else:
                subprocess.Popen(["xdg-open", ARCHIVE_FILE])
        except OSError:
            self.flash_status("无法打开归档文件")

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

            pri_lbl = tk.Label(
                card,
                text=self.priority_icon(t["priority"]),
                bg=BG2,
                fg=self.priority_color(t["priority"]),
                font=self.font_small,
            )
            pri_lbl.pack(side="left", padx=(8, 4), pady=6)
            pri_lbl.bind("<Button-3>", lambda e, tid=t["id"]: self.show_context_menu(e, tid))

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
            check_btn.pack(side="left", padx=(0, 2), pady=6)

            text_lbl = tk.Label(
                card,
                text=t["text"],
                bg=BG2,
                fg=text_color,
                font=text_font,
                anchor="w",
                justify="left",
                wraplength=180,
            )
            text_lbl.pack(side="left", fill="x", expand=True, pady=6)
            text_lbl.bind("<Button-3>", lambda e, tid=t["id"]: self.show_context_menu(e, tid))
            text_lbl.bind("<Button-1>", lambda e, tid=t["id"]: self.toggle_expand(tid))
            expansion_c = False
            if t.get("pinned"):
                pin_lbl = tk.Label(
                    card,
                    text="📍",
                    bg=BG2,
                    fg=ACCENT,
                    font=self.font_small,
                )
                pin_lbl.pack(side="right", padx=(0, 4), pady=6)
                pin_lbl.bind("<Button-3>", lambda e, tid=t["id"]: self.show_context_menu(e, tid))
                expansion_c = True
            if t.get("note") or t.get("start") or t.get("due") or t.get("priority"):
                expand_mark = "▾" if t["id"] in self.expanded_ids else "▸"
                expand_lbl = tk.Label(
                    card,
                    text=expand_mark,
                    bg=BG2,
                    fg=FG_MUTED,
                    font=self.font_small,
                    cursor="hand2",
                )
                expand_lbl.pack(side="right", padx=(0, 6), pady=6)
                expand_lbl.bind("<Button-1>", lambda e, tid=t["id"]: self.toggle_expand(tid))
                expansion_c = True
            if not expansion_c:
                spacer = tk.Label(card, text="", bg=BG2, width=2)
                spacer.pack(side="right", padx=(0, 4), pady=6)

            start_text = self.start_label(t.get("start", ""))
            if start_text:
                start_lbl = tk.Label(
                    card,
                    text=f"🕐{start_text}",
                    bg=BG2,
                    fg=GREEN,
                    font=self.font_small,
                )
                start_lbl.pack(side="right", padx=(4, 0), pady=6)
                start_lbl.bind("<Button-3>", lambda e, tid=t["id"]: self.show_context_menu(e, tid))

            due_text = self.due_label(t.get("due", ""))
            if due_text:
                overdue = due_text.startswith("逾期") and not t["done"]
                due_lbl = tk.Label(
                    card,
                    text=f"⏰{due_text}",
                    bg=BG2,
                    fg=RED if overdue else FG_MUTED,
                    font=self.font_small,
                )
                due_lbl.pack(side="right", padx=(4, 8), pady=6)
                due_lbl.bind("<Button-3>", lambda e, tid=t["id"]: self.show_context_menu(e, tid))

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
            del_btn.pack(side="right", padx=(0, 6), pady=6)

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
            edit_btn.pack(side="right", padx=(0, 2), pady=6)

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

    def toggle_pin(self):
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
        self.geometry("470x380")
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
        ent = tk.Entry(self, textvariable=self.var, bg=BG3, fg=FG,
                       insertbackground=FG, relief="flat", font=app.font_small)
        ent.pack(fill="x", padx=16, pady=(10, 0), ipady=6)
        ent.focus_set()

        note("Key 只保存在本机 config.json 里，不上传、不进 Git。")

        row = tk.Frame(self, bg=BG2)
        row.pack(fill="x", padx=16, pady=(14, 4))
        tk.Button(row, text="保存", bg=ACCENT, fg="#1e1e2e", relief="flat",
                  font=app.font_small, cursor="hand2", bd=0, padx=18, pady=5,
                  command=self.save).pack(side="left")
        tk.Button(row, text="清除", bg=BG3, fg=FG, relief="flat",
                  font=app.font_small, cursor="hand2", bd=0, padx=14, pady=5,
                  command=self.clear).pack(side="left", padx=(8, 0))
        tk.Button(row, text="关闭", bg=BG3, fg=FG, relief="flat",
                  font=app.font_small, cursor="hand2", bd=0, padx=14, pady=5,
                  command=self.destroy).pack(side="right")

        self.status = tk.Label(self, text="", bg=BG2, fg=GREEN,
                               font=app.font_small, anchor="w")
        self.status.pack(fill="x", padx=16, pady=(2, 12))

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
        self.geometry("460x520")
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
        self.owner_var = tk.StringVar(value="我")
        self.owner_entry = tk.Entry(
            name_row, textvariable=self.owner_var, bg=BG3, fg=FG,
            insertbackground=FG, relief="flat", font=app.font_small, width=12,
        )
        self.owner_entry.pack(side="left", padx=(6, 0))

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

        self.opt_mode = tk.StringVar(value="both")
        opt = tk.Frame(self, bg=BG2)
        opt.pack(fill="x", padx=12, pady=(0, 4))
        for val, lab in [
            ("new", "只生成新任务"),
            ("repri", "重新评估现有任务优先级"),
            ("both", "两者都要"),
        ]:
            tk.Radiobutton(
                opt, text=lab, variable=self.opt_mode, value=val,
                bg=BG2, fg=FG, selectcolor=BG3, font=app.font_small,
                activebackground=BG2, activeforeground=FG,
            ).pack(side="left", padx=(0, 10))
        self.box.bind("<Control-Return>", lambda e: self.run_ai())

        btn = tk.Frame(self, bg=BG2)
        btn.pack(fill="x", padx=12, pady=(0, 10))
        self.btn_go = tk.Button(
            btn, text="✨ 整理", bg=ACCENT, fg="#1e1e2e", relief="flat",
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
        if not raw:
            self.status.config(text="请先粘贴原始信息", fg=RED)
            return
        self.btn_go.config(state="disabled")
        self.status.config(text="AI 整理中…", fg=FG_MUTED)
        threading.Thread(target=self._work, args=(raw,), daemon=True).start()

    def _work(self, raw):
        try:
            if read_api_key():
                tasks_in = parse_tasks_json(call_deepseek(self._build_prompt(raw)))
                note = ""
            else:
                tasks_in = local_organize(raw)          # 没配 key：本地规则整理，不联网
                note = "（未配 API Key，本次为本地规则整理）"
            self.app.root.after(0, lambda: self._done(tasks_in, note))
        except Exception as e:
            self.app.root.after(0, lambda: self._fail(str(e)))

    def _build_prompt(self, raw):
        mode = self.opt_mode.get()
        owner = self.owner_var.get().strip() or "我"
        lines = []
        lines.append(f"任务归属人：{owner}。请以 {owner} 为视角，提取他本人负责、参与或需要完成的【任务】以及与 {owner} 相关的【安排/信息类事项】（如会议、通知、待跟进、事务背景）。{owner} 未参与、与其无关的内容不输出；优先提取涉及 {owner} 的部分。")
        if mode in ("repri", "both"):
            lines.append("当前全部任务列表（含已完成；done=true 表示已完成，pinned=true 表示已置顶、优先级不可动，id 供你对应）：")
            for t in self.app.tasks:
                lines.append(
                    f"- id={t['id']} done={str(bool(t.get('done', False))).lower()} "
                    f"pinned={str(bool(t.get('pinned', False))).lower()} "
                    f"text={t['text']} priority={t['priority']} "
                    f"start={t.get('start', '') or '-'} due={t.get('due', '') or '-'}"
                )
            lines.append("")
        lines.append("以下是需要处理的信息：")
        lines.append(raw)
        return "\n".join(lines)

    def _done(self, tasks_in, note=""):
        self.btn_go.config(state="normal")
        new_n = 0
        repri_n = 0
        for item in tasks_in:
            rid = item.get("id")
            text = str(item.get("text", "")).strip()
            if not text and rid is None:
                continue
            if rid is not None:
                t = next((x for x in self.app.tasks if x["id"] == rid), None)
                if t and not t.get("pinned", False):
                    if item.get("priority"):
                        t["priority"] = str(item["priority"]).strip() or t["priority"]
                    if item.get("due"):
                        new_due = str(item["due"]).strip()
                        if self.app.valid_date(new_due):
                            t["due"] = new_due
                    if item.get("start"):
                        new_start = str(item["start"]).strip()
                        if self.app.valid_date(new_start):
                            t["start"] = new_start
                    if item.get("note"):
                        t["note"] = str(item["note"]).strip()
                    repri_n += 1
                elif t:
                    repri_n += 1
            else:
                self.app.tasks.append(self.app.build_task(text, item))
                new_n += 1
        order_map = {"高": 0, "中": 1, "低": 2}
        pinned = [t for t in self.app.tasks if not t["done"] and t.get("pinned")]
        active = [t for t in self.app.tasks if not t["done"] and not t.get("pinned")]
        done_list = [t for t in self.app.tasks if t["done"]]
        active.sort(key=lambda t: (order_map.get(t["priority"], 9), t.get("due", "")))
        done_list.sort(key=lambda t: t["id"], reverse=True)
        self.app.tasks = pinned + active + done_list

        self.app.save_tasks()
        self.app.render()
        self.status.config(
            text=f"完成：新增 {new_n} 条，重评估 {repri_n} 条，已按优先级重排{note}", fg=GREEN
        )

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
            "text": t[:120],
            "priority": pri,
            "start": "",
            "due": _parse_date(t, today),
            "note": "",
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
            {
                "role": "system",
                "content": (
                    "你是任务整理助手。用户会给你一段原始信息（可能含任务、会议纪要、聊天记录、邮件原始文本），并指定一个\"任务归属人\"。"
                    "请：\n"
                    "1. 以归属人为视角，把他本人负责、参与或需要完成的【任务】以及与归属人相关的【安排】"
                    "（如会议、通知、事务背景、开放事项、待跟进事项）都提取为一条条清单项。"
                    "同一件事、同一批次的零散子项合并成一条更完整项，不要拆太细，控制在最精简的条数；"
                    "归属人并未参与、与其完全无关的内容不输出。\n"
                    "2. 每一条项：若是需要归属人行动的【任务】，按其内容重要度给 高/中/低 优先级；"
                    "若是【安排/通知/信息类】非行动项，自动降为 低 优先级。"
                    "将合并后的项按优先级从高到低排序，并逐一给出 高/中/低 优先级（最重要的给高，可暂缓、事务性的给低）；\n"
                    "3. 若提供现有任务列表做重评估：把所有任务（含已完成）当作整体上下文考虑——"
                    "已完成的任务（done=true）不要输出也不要改动；已置顶的任务（pinned=true）优先级固定、不要输出也不要改动；"
                    "只基于新信息重新判断其余未完成任务每条的优先级与排序，id 必须对应；\n"
                    "4. 输出纯 JSON 数组，不要 Markdown 代码块，不要多余文字。数组顺序即展示顺序（最重要的在最前）。每个元素形如：\n"
                    "{\"id\": 数字或省略, \"text\": \"合并精简后的项文本\", \"priority\": \"高|中|低\", "
                    "\"start\": \"YYYY-MM-DD或空\", \"due\": \"YYYY-MM-DD或空\", "
                    "\"note\": \"保留该项对应的原文摘录或备注\"}\n"
                    "规则：text 用简体中文、完整可读、尽量合并同类；只依据信息里出现的日期，不要编造；"
                    "没把握的优先级给中；id 仅用于重评估现有任务，新任务不要带 id。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 4000,
    }
    req = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
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
    app = TodoApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
