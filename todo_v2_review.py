"""Todo v2 human-in-the-loop review window. Standard-library Tkinter only."""
import copy
import re
import tkinter as tk
from tkinter import messagebox, ttk
from datetime import date
from todo_v2_core import equivalent_title, VIEW_UNKNOWN


class CandidateReviewDialog(tk.Toplevel):
    """AI does NOT insert tasks until the user explicitly selects and confirms them."""

    def __init__(self, app, result, mode, owner, perspective=VIEW_UNKNOWN):
        super().__init__(app.root)
        self.app = app
        self.rows = []
        self.title("审核候选任务 · " + owner)
        self.configure(bg="#27293d")
        self.geometry("680x620")
        self.minsize(530, 420)
        self.attributes("-topmost", True)
        self.transient(app.root)

        color_bg, color_card, color_text = "#27293d", "#313244", "#cdd6f4"
        heading = "AI 提取结果 · 先确认再保存" if mode == "ai" else "离线逐行整理 · 需要逐项确认"
        tk.Label(self, text=heading, font=("Microsoft YaHei UI", 12, "bold"),
                 bg=color_bg, fg=color_text).pack(fill="x", padx=14, pady=(14, 5))

        # 结果概览。这是正常审核状态的一部分，不是错误提示：
        # 「0 条可直接确认」既可能是正常结果，也可能是发言人信息不足，所以只做说明、不报警。
        n_conf = len(result.get("confirmed_tasks", []))
        n_pend = len(result.get("needs_confirmation", []))
        tk.Label(self, text=f"{n_conf} 条可直接确认 · {n_pend} 条待确认",
                 bg=color_bg, fg="#89b4fa", font=("Microsoft YaHei UI", 10, "bold"),
                 anchor="w").pack(fill="x", padx=14, pady=(0, 4))

        if n_conf == 0 and n_pend == 0:
            tip = "未识别到需要你处理的任务。"
        elif n_conf == 0 and mode == "local":
            tip = ("离线整理只做逐行拆分，判断不了负责人和责任归属，所以没有可直接确认的任务。\n"
                   "请核对待确认事项；想要更准确的判断，可以在主窗口 ⚙ 里配好 API Key 后再整理。")
        elif n_conf == 0 and perspective == VIEW_UNKNOWN:
            tip = ("这段消息的发言者尚不明确，部分任务无法确定责任归属。\n"
                   "请核对待确认事项，或补充消息视角后重新整理。")
        elif n_conf == 0:
            tip = "本次没有可以直接确认的任务，责任依据不足的事项都列在下面，请逐条核对。"
        elif mode == "ai":
            tip = "有原文依据的明确任务默认勾选；待确认事项默认不勾选。"
        else:
            tip = "离线模式无法判断负责人或理解语义；以下仅是原文片段，全部默认不勾选。"
        tk.Label(self, text=tip, bg=color_bg, fg="#a6adc8", anchor="w",
                 justify="left", wraplength=630).pack(fill="x", padx=14, pady=(0, 10))

        container = tk.Frame(self, bg=color_bg)
        container.pack(fill="both", expand=True, padx=10)
        canvas = tk.Canvas(container, bg=color_bg, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=color_bg)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def make_group(title, items, selected):
            tk.Label(inner, text=f"{title}（{len(items)}）", bg=color_bg, fg="#cba6f7",
                     anchor="w", font=("Microsoft YaHei UI", 10, "bold")
                     ).pack(fill="x", padx=8, pady=(9, 3))
            if not items:
                tk.Label(inner, text="无", bg=color_bg, fg="#7f849c", anchor="w").pack(fill="x", padx=12)
            for item in items:
                card = tk.Frame(inner, bg=color_card)
                card.pack(fill="x", padx=8, pady=4)
                check = tk.BooleanVar(value=selected)
                title_var = tk.StringVar(value=item.get("text", ""))
                due_var = tk.StringVar(value=item.get("due", ""))
                pri_var = tk.StringVar(value=item.get("priority", "中"))
                header = tk.Frame(card, bg=color_card)
                header.pack(fill="x", padx=8, pady=(8, 3))
                tk.Checkbutton(header, variable=check, bg=color_card, activebackground=color_card,
                               selectcolor="#45475a").pack(side="left")
                tk.Entry(header, textvariable=title_var, bg="#1e1e2e", fg=color_text,
                         insertbackground=color_text, relief="flat", font=("Microsoft YaHei UI", 10)
                         ).pack(side="left", fill="x", expand=True, ipady=5, padx=(3, 0))
                fields = tk.Frame(card, bg=color_card)
                fields.pack(fill="x", padx=12, pady=3)
                tk.Label(fields, text="截止 YYYY-MM-DD：", bg=color_card, fg="#a6adc8").pack(side="left")
                tk.Entry(fields, textvariable=due_var, bg="#1e1e2e", fg=color_text,
                         insertbackground=color_text, width=12).pack(side="left")
                tk.Label(fields, text="  优先级：", bg=color_card, fg="#a6adc8").pack(side="left")
                ttk.Combobox(fields, textvariable=pri_var, state="readonly",
                             values=["高", "中", "低"], width=4).pack(side="left")
                role = item.get("role", "") or "未确定"
                why = item.get("reason", "")
                excerpt = item.get("evidence", "")
                meta = "角色：" + role
                if item.get("deadline_raw"):
                    meta += "  · 原文时间：" + item["deadline_raw"]
                if why:
                    meta += "  · 待核实：" + why
                tk.Label(card, text=meta, bg=color_card, fg="#f9e2af", anchor="w",
                         justify="left", wraplength=600).pack(fill="x", padx=12, pady=(4, 0))
                tk.Label(card, text="原文依据：" + (excerpt or "无（离线拆行）"),
                         bg=color_card, fg="#a6adc8", justify="left", anchor="w",
                         wraplength=600).pack(fill="x", padx=12, pady=(3, 10))
                self.rows.append((check, title_var, due_var, pri_var, item))

        make_group("明确责任的候选任务", result.get("confirmed_tasks", []), mode == "ai")
        make_group("责任待确认 / 需人工判断", result.get("needs_confirmation", []), False)
        footer = tk.Frame(self, bg=color_bg)
        footer.pack(fill="x", padx=12, pady=12)
        tk.Button(footer, text="取消", bg="#45475a", fg=color_text, relief="flat",
                  command=self.destroy, padx=14, pady=7).pack(side="right")
        tk.Button(footer, text="确认所选并保存", bg="#cba6f7", fg="#1e1e2e", relief="flat",
                  command=self.save_selected, padx=14, pady=7).pack(side="right", padx=(0, 8))

        # 滚轮：内容放在自绘 Canvas 里，Tk 不会把滚轮事件自动转给 Canvas，
        # 必须递归绑到每个子控件上（主窗口也是这么做的）。
        # 返回 "break" 让事件到此为止，避免同一个滚轮被父级再处理一次。
        def on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"

        def bind_wheel(widget):
            widget.bind("<MouseWheel>", on_wheel)
            for child in widget.winfo_children():
                bind_wheel(child)

        bind_wheel(self)
        bind_wheel(inner)   # 保险：确保卡片区一定被绑上

        # Toplevel grabs input only while visible. Doesn't destroy the AI source dialog.
        self.grab_set()
        self.focus_set()

    def save_selected(self):
        selected = []
        seen = []
        existing = [t.get("text", "") for t in self.app.tasks if not t.get("done")]
        for check, text_var, due_var, pri_var, original in self.rows:
            if not check.get():
                continue
            title = text_var.get().strip()
            due = due_var.get().strip()
            if not title:
                messagebox.showwarning("无法保存", "勾选的任务标题不能为空", parent=self)
                return
            if due:
                try:
                    date.fromisoformat(due)
                except ValueError:
                    messagebox.showwarning("日期格式有误", "请使用 YYYY-MM-DD，或留空", parent=self)
                    return
            if any(equivalent_title(title, x) for x in existing + seen):
                # Duplicate is intentionally skipped, not merged with an unrelated task.
                continue
            seen.append(title)
            note_parts = []
            if original.get("role"):
                note_parts.append("责任角色：" + original["role"])
            if original.get("evidence"):
                note_parts.append("原文依据：" + original["evidence"])
            if original.get("deadline_raw") and not due:
                note_parts.append("未解析原文时间：" + original["deadline_raw"])
            if original.get("note"):
                note_parts.append("备注：" + original["note"])
            selected.append({"text": title, "priority": pri_var.get(), "due": due,
                             "start": "", "note": "\n".join(note_parts)})
        if not selected:
            messagebox.showinfo("没有保存", "未选中可新增任务，或所选任务已经存在。", parent=self)
            return
        old_tasks = copy.deepcopy(self.app.tasks)
        try:
            for item in selected:
                self.app.tasks.append(self.app.build_task(item["text"], item))
            self.app.save_tasks()
        except Exception as exc:
            self.app.tasks = old_tasks
            messagebox.showerror("保存失败", "没有写入新任务：" + str(exc), parent=self)
            return
        self.app.render()
        messagebox.showinfo("已保存", f"新增 {len(selected)} 条任务（已跳过相同标题的未完成任务）", parent=self)
        self.destroy()
