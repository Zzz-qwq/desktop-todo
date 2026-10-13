"""Run: python -m unittest -v test_todo_v2.py (no GUI/API needed)."""
import json
import tempfile
import unittest
from pathlib import Path
from todo_v2_core import (load_tasks_safely, save_tasks_atomically,
                          archive_completed_task, parse_extraction_result,
                          build_extraction_user_prompt, equivalent_title,
                          VIEW_SELF, VIEW_OTHER, VIEW_MULTI, VIEW_UNKNOWN,
                          VIEW_OPTIONS, EXTRACT_SYSTEM_PROMPT)

CASE_TEXT = ("@小李（无）负责：今天之内需要将成绩报表发给某高校。课堂成绩的话，都是讲师打分+课堂出勤（有记录表）。"
             "结业考试学校负责，我们提供考勤和作业评分。这个需要你今天制作一下。此任务为最高优先级。@小张协助。\n"
             "合同目前是资料整理的二期回款8400未收回，还需要和某高校那边对齐一下资料的整理，@小李（无）@小张搭档一下，"
             "老师的意思是尽量对后续的课程开个会，资料也在会上对齐，建议@王老师参会。\n"
             "课程实施合同还未签，推进签署以及回款，三门理论课已经实施完了，实践课和实验课还缺学时。")


class CoreTests(unittest.TestCase):
    def test_case001_confirmed_and_pending(self):
        response = {
            "confirmed_tasks": [
                {"text": "制作并发送某高校成绩报表", "role": "主责", "priority": "高", "due": "2026-10-13",
                 "deadline_raw": "今天之内", "evidence": "今天之内需要将成绩报表发给某高校"},
                {"text": "与小张协作对齐某高校资料", "role": "共同", "priority": "", "due": "",
                 "evidence": "还需要和某高校那边对齐一下资料的整理"},
            ],
            "needs_confirmation": [
                {"text": "确认课程会议的召集人", "evidence": "尽量对后续的课程开个会"},
                {"text": "确认资料整理二期回款跟进人", "evidence": "二期回款8400未收回"},
                {"text": "确认实施合同签署及回款负责人", "evidence": "课程实施合同还未签，推进签署以及回款"},
            ],
        }
        result = parse_extraction_result(json.dumps(response, ensure_ascii=False), CASE_TEXT, "")
        self.assertEqual(len(result["confirmed_tasks"]), 2)
        self.assertEqual(len(result["needs_confirmation"]), 3)
        self.assertEqual(result["confirmed_tasks"][0]["due"], "")  # '今天' without source date
        self.assertEqual(result["confirmed_tasks"][0]["priority"], "高")

    def test_case002_assistant_role(self):
        ai_response = {"confirmed_tasks": [{"text": "协助小李完成某高校报表", "role": "协助",
                        "evidence": "@小张协助"}], "needs_confirmation": []}
        out = parse_extraction_result(ai_response, CASE_TEXT)
        self.assertEqual(out["confirmed_tasks"][0]["role"], "协助")

    def test_fake_evidence_moves_to_pending(self):
        out = parse_extraction_result({"confirmed_tasks": [{"text": "帮老板买咖啡", "role": "主责",
                                        "evidence": "老板让我买咖啡"}], "needs_confirmation": []}, CASE_TEXT)
        self.assertEqual(len(out["confirmed_tasks"]), 0)
        self.assertEqual(len(out["needs_confirmation"]), 1)

    def test_exact_duplicates_removed_but_different_contracts_preserved(self):
        obj = {"confirmed_tasks": [], "needs_confirmation": [
            {"text": "跟进合同A", "evidence": "合同"},
            {"text": "跟进合同A", "evidence": "合同"},
            {"text": "跟进合同B", "evidence": "合同"}]}
        out = parse_extraction_result(obj, "合同 A B")
        self.assertEqual(len(out["needs_confirmation"]), 2)
        self.assertFalse(equivalent_title("跟进合同A", "跟进合同B"))

    def test_name_and_message_date_validity(self):
        with self.assertRaises(ValueError):
            build_extraction_user_prompt("", "abc")
        with self.assertRaises(ValueError):
            build_extraction_user_prompt("小李", "abc", "2026-20-88")
        self.assertIn("2026-10-13", build_extraction_user_prompt("小李", "abc", "2026-10-13"))

    def test_atomic_save_preserves_previous_version(self):
        with tempfile.TemporaryDirectory() as folder:
            p = str(Path(folder) / "tasks.json")
            save_tasks_atomically(p, [{"id": 1, "text": "第一次"}])
            save_tasks_atomically(p, [{"id": 1, "text": "第二次"}])
            self.assertEqual(load_tasks_safely(p)[0]["text"], "第二次")
            self.assertEqual(load_tasks_safely(p + ".bak")[0]["text"], "第一次")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{{invalid")
            with self.assertRaises(RuntimeError):
                load_tasks_safely(p)
            with self.assertRaises(RuntimeError):
                save_tasks_atomically(p, [])
            self.assertEqual(Path(p).read_text(encoding="utf-8"), "{{invalid")

    def test_archive_once_only(self):
        with tempfile.TemporaryDirectory() as folder:
            archive_path = str(Path(folder) / "归档.md")
            task = {"id": 1, "text": "提交成绩报表", "done": False,
                    "priority": "高", "start": "", "due": ""}
            self.assertTrue(archive_completed_task(archive_path, task))
            self.assertFalse(archive_completed_task(archive_path, task))
            self.assertEqual(Path(archive_path).read_text(encoding="utf-8").count("提交成绩报表"), 1)
            self.assertTrue(task.get("archived_at"))


class PerspectiveTests(unittest.TestCase):
    """v2.1 消息视角：只验证规则有没有正确传下去，不代表真实模型效果。"""

    def test_default_is_unknown_and_not_guessed_as_other(self):
        p = build_extraction_user_prompt("小李", "原文")
        self.assertIn("未知视角", p)
        self.assertIn("无法确定发言人是谁，也无法确定人称指代关系", p)
        self.assertIn("不得默认解释成「对方在对归属人说话」", p)

    def test_each_perspective_reaches_the_prompt(self):
        cases = {
            VIEW_SELF: "外层叙述中的「我」指任务归属人本人",
            VIEW_OTHER: "「你」指任务归属人本人",
            VIEW_MULTI: "必须先逐条/逐段识别发言人",
            VIEW_UNKNOWN: "无法确定发言人是谁",
        }
        for view, needle in cases.items():
            with self.subTest(view=view):
                self.assertIn(needle, build_extraction_user_prompt("小李", "原文", "", view))

    def test_invalid_perspective_rejected(self):
        with self.assertRaises(ValueError):
            build_extraction_user_prompt("小李", "原文", "", "随便填")

    def test_options_cover_all_and_default_first(self):
        vals = [v for v, _ in VIEW_OPTIONS]
        self.assertEqual(set(vals), {VIEW_SELF, VIEW_OTHER, VIEW_MULTI, VIEW_UNKNOWN})
        self.assertEqual(vals[0], VIEW_UNKNOWN)   # 界面默认项＝未知视角

    def test_system_prompt_has_pronoun_rules_and_anti_overconservatism(self):
        self.assertIn("不得默认成「对方在对归属人说话」", EXTRACT_SYSTEM_PROMPT)
        self.assertIn("「他/她」不能简单视为其他人", EXTRACT_SYSTEM_PROMPT)
        self.assertIn("有明确证据的任务仍照常提取", EXTRACT_SYSTEM_PROMPT)
        self.assertIn("不得把所有「我」指向同一人", EXTRACT_SYSTEM_PROMPT)


class OfflineDesktopTests(unittest.TestCase):
    """离线模式的标题精简与日期传递（需要 todo_desktop 可导入）。"""

    def setUp(self):
        try:
            import todo_desktop
        except Exception as exc:      # 没装 tkinter 等环境问题时跳过
            self.skipTest("无法导入 todo_desktop：" + str(exc))
        self.td = todo_desktop

    def test_short_title_keeps_short_and_trims_long(self):
        f = self.td._short_title
        self.assertEqual(f("截止周五前提交季度报表"), "截止周五前提交季度报表")
        # 剥掉开头「@某人…：」后自己就短于 40 字的，原样返回（不动它）
        self.assertEqual(f("@小李负责：今天之内需要将成绩报表发给某高校。"),
                         "今天之内需要将成绩报表发给某高校。")
        # 真正的长句：取 40 字内最后一个句末标点断开
        self.assertEqual(
            f("@小李负责：今天之内需要将成绩报表发给某高校。课堂成绩的话，都是讲师打分+课堂出勤（有记录表）。"
              "结业考试学校负责，我们提供考勤和作业评分。这个需要你今天制作一下。"),
            "今天之内需要将成绩报表发给某高校")
        self.assertLessEqual(len(f("没有任何标点的超长句子" * 8)), 41)   # 硬截 + 省略号

    def test_offline_keeps_due_and_full_evidence(self):
        items = self.td.local_organize("今天之内把成绩报表发出去\n周五前提交课程计划")
        self.assertTrue(items[0]["due"])                    # 日期没被写死成空
        self.assertTrue(items[0]["evidence"])               # 完整原文留着
        self.assertLessEqual(len(items[0]["text"]), 40)     # 标题被精简
        self.assertIn("发出去", items[0]["evidence"])

    def test_task_pin_is_not_shadowed_by_window_pin(self):
        """回归：toggle_pin 曾被同名方法覆盖（Python 里后定义的生效），
        导致右键菜单的「📍 置顶」实际调到了不带参数的窗口置顶方法，点了没反应。"""
        import inspect
        self.assertEqual(
            list(inspect.signature(self.td.TodoApp.toggle_pin).parameters),
            ["self", "task_id"],
        )
        self.assertEqual(
            list(inspect.signature(self.td.TodoApp.toggle_window_pin).parameters),
            ["self"],
        )

        class Stub:                      # 用桩对象调真实方法，不建窗口、不碰 tasks.json
            def __init__(self):
                self.tasks = [{"id": 1, "pinned": False}]
            def save_tasks(self): pass
            def render(self): pass

        s = Stub()
        self.td.TodoApp.toggle_pin(s, 1)
        self.assertTrue(s.tasks[0]["pinned"])
        self.td.TodoApp.toggle_pin(s, 1)
        self.assertFalse(s.tasks[0]["pinned"])


if __name__ == "__main__":
    unittest.main()
