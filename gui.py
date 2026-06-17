"""
PyQt6 desktop interface for the news summary agent.

The GUI is intentionally more than a thin wrapper around the CLI: it exposes
runtime configuration, task composition, execution telemetry, and local data
stats in one operator-friendly surface.
"""
from __future__ import annotations

import asyncio
import html
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QProgressBar,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent import NewsAgent
from tools import get_available_categories
from tools.rss_fetcher import fetch_rss_feeds
from utils.db import NewsDatabase


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
DB_PATH = ROOT / "data" / "news.db"


FIELD_GROUPS = [
    (
        "DeepSeek / LLM",
        [
            ("DEEPSEEK_API_KEY", "API Key", "password", "sk-..."),
            ("DEEPSEEK_BASE_URL", "Base URL", "text", "https://api.deepseek.com"),
            ("DEEPSEEK_MODEL", "Model", "text", "deepseek-chat"),
            ("LLM_TEMPERATURE", "Temperature", "text", "0.3"),
            ("LLM_MAX_TOKENS", "Max Tokens", "text", "4096"),
            ("MAX_TOOL_CALLS", "Max Tool Calls", "text", "15"),
        ],
    ),
    (
        "SMTP / Email",
        [
            ("SMTP_HOST", "SMTP Host", "text", "smtp.qq.com"),
            ("SMTP_PORT", "SMTP Port", "text", "465"),
            ("SMTP_USERNAME", "Username", "text", "your-email@qq.com"),
            ("SMTP_PASSWORD", "Password / Auth Code", "password", ""),
            ("SMTP_FROM_NAME", "Sender Name", "text", "每日新闻摘要"),
            ("SMTP_FROM_EMAIL", "Sender Email", "text", "your-email@qq.com"),
            ("DEFAULT_RECIPIENT", "Default Recipient", "text", "your-email@qq.com"),
        ],
    ),
]


def read_env_values() -> dict[str, str]:
    source = ENV_PATH if ENV_PATH.exists() else ENV_EXAMPLE_PATH
    values: dict[str, str] = {}
    if not source.exists():
        return values

    for raw_line in source.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_values(values: dict[str, str]) -> None:
    lines = [
        "# DeepSeek API 配置",
        f"DEEPSEEK_API_KEY={values.get('DEEPSEEK_API_KEY', '')}",
        f"DEEPSEEK_BASE_URL={values.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')}",
        f"DEEPSEEK_MODEL={values.get('DEEPSEEK_MODEL', 'deepseek-chat')}",
        "",
        "# LLM / Agent 运行参数",
        f"LLM_TEMPERATURE={values.get('LLM_TEMPERATURE', '0.3')}",
        f"LLM_MAX_TOKENS={values.get('LLM_MAX_TOKENS', '4096')}",
        f"MAX_TOOL_CALLS={values.get('MAX_TOOL_CALLS', '15')}",
        "",
        "# 邮件配置 (SMTP)",
        f"SMTP_HOST={values.get('SMTP_HOST', 'smtp.qq.com')}",
        f"SMTP_PORT={values.get('SMTP_PORT', '465')}",
        f"SMTP_USERNAME={values.get('SMTP_USERNAME', '')}",
        f"SMTP_PASSWORD={values.get('SMTP_PASSWORD', '')}",
        f"SMTP_FROM_NAME={values.get('SMTP_FROM_NAME', '每日新闻摘要')}",
        f"SMTP_FROM_EMAIL={values.get('SMTP_FROM_EMAIL', '')}",
        "",
        "# 默认收件邮箱",
        f"DEFAULT_RECIPIENT={values.get('DEFAULT_RECIPIENT', '')}",
        "",
    ]
    ENV_PATH.write_text("\n".join(lines), encoding="utf-8")


def mask_secret(value: str) -> str:
    if not value:
        return "未配置"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def usable_email(value: str) -> bool:
    value = value.strip()
    return bool(value and "@" in value and "your-" not in value and not value.endswith("@example.com"))


class StatCard(QFrame):
    def __init__(self, title: str, value: str, caption: str = ""):
        super().__init__()
        self.setObjectName("StatCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        self.title = QLabel(title)
        self.title.setObjectName("StatTitle")
        self.value = QLabel(value)
        self.value.setObjectName("StatValue")
        self.caption = QLabel(caption)
        self.caption.setObjectName("Muted")
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)

    def set_value(self, value: str, caption: str = "") -> None:
        self.value.setText(value)
        self.caption.setText(caption)


class RssSmokeThread(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def run(self) -> None:
        async def task() -> dict:
            db = NewsDatabase(str(DB_PATH))
            await db.init()
            self.log.emit("开始执行 RSS 工具真实测试：分类 ai，单源最多 2 篇。")
            return await fetch_rss_feeds(categories=["ai"], max_per_feed=2, since_hours=72, db=db)

        try:
            self.done.emit(asyncio.run(task()))
        except Exception as exc:
            self.failed.emit(str(exc))


class AgentRunThread(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, prompt: str, model: Optional[str] = None):
        super().__init__()
        self.prompt = prompt
        self.model = model
        self._progress_value = 0
        self._delivery_success = False
        self._last_log_message = ""

    def emit_progress(self, value: int, label: str) -> None:
        self._progress_value = max(self._progress_value, min(value, 99))
        self.progress.emit(self._progress_value, label)

    def emit_log_once(self, message: str) -> None:
        if message != self._last_log_message:
            self._last_log_message = message
            self.log.emit(message)

    def run(self) -> None:
        async def task() -> str:
            self.emit_progress(4, "载入配置")
            load_dotenv(ENV_PATH, override=True)
            db = NewsDatabase(str(DB_PATH))
            self.emit_progress(7, "初始化数据库")
            await db.init()
            await db.cleanup_old_articles(7)
            agent = NewsAgent(db=db, model=self.model or None)

            original_execute = agent._execute_tool

            async def traced_execute(tool_name: str, arguments: dict) -> dict:
                stage_progress = {
                    "fetch_rss_feeds": 18,
                    "search_news": 46,
                    "fetch_article_content": 58,
                    "send_email": 88,
                }.get(tool_name, 30)
                status_text = {
                    "fetch_rss_feeds": "正在收集 RSS 新闻",
                    "search_news": "正在检索新闻库",
                    "fetch_article_content": "正在抓取重点正文",
                    "send_email": "正在发送邮件",
                }.get(tool_name, f"正在调用 {tool_name}")
                self.emit_progress(stage_progress, status_text)
                self.emit_log_once(status_text)
                result = await original_execute(tool_name, arguments)
                if tool_name == "fetch_rss_feeds":
                    self.emit_progress(38, "RSS 收集完成")
                    self.emit_log_once(
                        "RSS 完成："
                        f"{result.get('total_fetched', 0)} 篇，"
                        f"成功源 {result.get('feeds_success', 0)}，"
                        f"失败源 {result.get('feeds_failed', 0)}。"
                    )
                elif tool_name == "fetch_article_content":
                    self.emit_progress(68, "正文抓取完成")
                    self.emit_log_once(
                        "正文抓取完成："
                        f"成功 {result.get('success', 0)} / 总计 {result.get('total', 0)}。"
                    )
                elif tool_name == "send_email":
                    self._delivery_success = bool(result.get("success"))
                    self.emit_progress(94 if self._delivery_success else 90, "邮件工具返回")
                    message = result.get("message") or result.get("error") or "邮件工具已返回。"
                    if "body_html" in message or "正文为空" in message or "缺少" in message:
                        message = "邮件正文为空，等待 Agent 自动重试。"
                    self.emit_log_once(message)
                elif tool_name == "search_news":
                    self.emit_progress(54, "检索补充完成")
                return result

            agent._execute_tool = traced_execute  # type: ignore[method-assign]
            self.emit_log_once(f"Agent 启动：{agent.model}")
            self.emit_progress(10, "Agent 运行中")
            result = await agent.run(self.prompt, dry_run=False)
            if not self._delivery_success:
                raise RuntimeError("真实发送未确认完成：send_email 未成功返回。请检查执行日志中的 SMTP 错误。")
            self.emit_progress(98, "整理最终响应")
            return result

        try:
            self.done.emit(asyncio.run(task()))
        except Exception as exc:
            self.failed.emit(str(exc))


class ConfigPage(QWidget):
    status_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.inputs: dict[str, QLineEdit] = {}
        self._build()
        self.load_values()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        header = QFrame()
        header.setObjectName("PageHeader")
        header_layout = QVBoxLayout(header)
        title = QLabel("配置中心")
        title.setObjectName("PageTitle")
        subtitle = QLabel("集中管理 DeepSeek、SMTP、模型和 Agent 运行参数；保存后立即写入根目录 .env。")
        subtitle.setObjectName("Muted")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setSpacing(16)

        status_row = QHBoxLayout()
        self.api_card = StatCard("DeepSeek", "未配置", "API Key")
        self.smtp_card = StatCard("SMTP", "未配置", "真实发送能力")
        self.env_card = StatCard(".env", "未保存", str(ENV_PATH))
        status_row.addWidget(self.api_card)
        status_row.addWidget(self.smtp_card)
        status_row.addWidget(self.env_card)
        body_layout.addLayout(status_row)

        for group_title, fields in FIELD_GROUPS:
            group = QGroupBox(group_title)
            grid = QGridLayout(group)
            grid.setContentsMargins(22, 38, 22, 18)
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(12)
            for row, (key, label, kind, placeholder) in enumerate(fields):
                label_widget = QLabel(label)
                edit = QLineEdit()
                edit.setPlaceholderText(placeholder)
                if kind == "password":
                    edit.setEchoMode(QLineEdit.EchoMode.Password)
                self.inputs[key] = edit
                grid.addWidget(label_widget, row, 0)
                grid.addWidget(edit, row, 1)
            body_layout.addWidget(group)

        actions = QHBoxLayout()
        self.reveal = QCheckBox("显示密钥")
        self.reveal.toggled.connect(self._toggle_secrets)
        save_button = QPushButton("保存配置")
        save_button.setObjectName("PrimaryButton")
        save_button.clicked.connect(self.save_values)
        reload_button = QPushButton("重新载入")
        reload_button.clicked.connect(self.load_values)
        validate_button = QPushButton("检查配置")
        validate_button.clicked.connect(self.show_validation)
        actions.addWidget(self.reveal)
        actions.addStretch(1)
        actions.addWidget(reload_button)
        actions.addWidget(validate_button)
        actions.addWidget(save_button)
        body_layout.addLayout(actions)

        notes = QTextBrowser()
        notes.setObjectName("InfoBox")
        notes.setMaximumHeight(150)
        notes.setHtml(
            "<b>运行策略</b><br>"
            "真实发送需要 SMTP_USERNAME、SMTP_PASSWORD 和 DEFAULT_RECIPIENT。"
            "如果只想测试 RSS 工具，可以在任务工作台点击 RSS 工具测试。"
        )
        body_layout.addWidget(notes)
        body_layout.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll)

    def _toggle_secrets(self, checked: bool) -> None:
        for key in ("DEEPSEEK_API_KEY", "SMTP_PASSWORD"):
            self.inputs[key].setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)

    def collect_values(self) -> dict[str, str]:
        return {key: edit.text().strip() for key, edit in self.inputs.items()}

    def load_values(self) -> None:
        values = read_env_values()
        for key, edit in self.inputs.items():
            edit.setText(values.get(key, ""))
        self.refresh_cards()

    def save_values(self, silent: bool = False) -> None:
        write_env_values(self.collect_values())
        load_dotenv(ENV_PATH, override=True)
        self.refresh_cards()
        self.status_changed.emit()
        if not silent:
            QMessageBox.information(self, "已保存", f"配置已写入 {ENV_PATH}")

    def refresh_cards(self) -> None:
        values = self.collect_values()
        api_key = values.get("DEEPSEEK_API_KEY", "")
        smtp_ready = all(values.get(k) for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD"))
        self.api_card.set_value("已配置" if api_key and "your-" not in api_key else "未配置", mask_secret(api_key))
        self.smtp_card.set_value("可发送" if smtp_ready else "未完整配置", values.get("SMTP_HOST", "smtp.qq.com"))
        if ENV_PATH.exists():
            timestamp = datetime.fromtimestamp(ENV_PATH.stat().st_mtime).strftime("%m-%d %H:%M")
            self.env_card.set_value("已存在", f"上次修改 {timestamp}")
        else:
            self.env_card.set_value("未创建", "保存后生成")

    def show_validation(self) -> None:
        values = self.collect_values()
        issues = []
        if not values.get("DEEPSEEK_API_KEY") or "your-" in values.get("DEEPSEEK_API_KEY", ""):
            issues.append("DeepSeek API Key 未配置，完整 Agent 任务无法调用 LLM。")
        if values.get("SMTP_PASSWORD") and not values.get("SMTP_USERNAME"):
            issues.append("已填写 SMTP 密码，但缺少 SMTP_USERNAME。")
        if values.get("LLM_TEMPERATURE"):
            try:
                temp = float(values["LLM_TEMPERATURE"])
                if not 0 <= temp <= 2:
                    issues.append("LLM_TEMPERATURE 建议在 0 到 2 之间。")
            except ValueError:
                issues.append("LLM_TEMPERATURE 需要是数字。")
        if values.get("MAX_TOOL_CALLS"):
            try:
                if int(values["MAX_TOOL_CALLS"]) < 3:
                    issues.append("MAX_TOOL_CALLS 小于 3 会违背当前系统提示词的工具调用流程。")
            except ValueError:
                issues.append("MAX_TOOL_CALLS 需要是整数。")

        if issues:
            QMessageBox.warning(self, "配置检查", "\n".join(issues))
        else:
            QMessageBox.information(self, "配置检查", "关键配置看起来可用。")


class WorkbenchPage(QWidget):
    def __init__(self, config_page: ConfigPage):
        super().__init__()
        self.config_page = config_page
        self.agent_thread: Optional[AgentRunThread] = None
        self.rss_thread: Optional[RssSmokeThread] = None
        self.category_checks: dict[str, QCheckBox] = {}
        self._build()
        self.refresh_stats()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        header = QFrame()
        header.setObjectName("PageHeader")
        header_layout = QHBoxLayout(header)
        heading_box = QVBoxLayout()
        title = QLabel("任务工作台")
        title.setObjectName("PageTitle")
        subtitle = QLabel("输入提示词、选择新闻范围、运行 Agent，并在右侧查看执行日志与最终响应。")
        subtitle.setObjectName("Muted")
        heading_box.addWidget(title)
        heading_box.addWidget(subtitle)
        header_layout.addLayout(heading_box)
        header_layout.addStretch(1)
        self.run_button = QPushButton("运行 Agent")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.clicked.connect(self.run_agent)
        header_layout.addWidget(self.run_button)
        outer.addWidget(header)

        progress_panel = QFrame()
        progress_panel.setObjectName("ProgressPanel")
        progress_layout = QHBoxLayout(progress_panel)
        progress_layout.setContentsMargins(18, 12, 18, 12)
        self.progress_label = QLabel("等待任务")
        self.progress_label.setObjectName("ProgressLabel")
        self.progress_label.setFixedWidth(132)
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar, 1)
        outer.addWidget(progress_panel)
        self.progress_animation: Optional[QPropertyAnimation] = None

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 12, 0)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left)
        splitter.addWidget(left_scroll)
        splitter.addWidget(right)
        splitter.setSizes([560, 520])
        outer.addWidget(splitter, 1)

        prompt_group = QGroupBox("提示词")
        prompt_layout = QVBoxLayout(prompt_group)
        prompt_layout.setContentsMargins(22, 38, 22, 18)
        prompt_layout.setSpacing(12)
        preset_row = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.addItems([
            "总结今天 AI 领域最重要的 10 条新闻，并生成中文邮件摘要",
            "筛选最近 48 小时科技和编程领域值得关注的产品与开源动态",
            "整理今日财经与 AI 公司相关的重要新闻，突出投资与行业影响",
            "给我一份适合课程实验截图展示的完整 Agent 执行摘要",
        ])
        use_preset = QPushButton("套用")
        use_preset.clicked.connect(lambda: self.prompt_edit.setPlainText(self.preset_combo.currentText()))
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(use_preset)
        prompt_layout.addLayout(preset_row)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMinimumHeight(150)
        self.prompt_edit.setPlaceholderText("例如：总结今天 AI 与科技领域最重要的新闻，按重要性排序，并发送邮件。")
        self.prompt_edit.setPlainText(self.preset_combo.currentText())
        prompt_layout.addWidget(self.prompt_edit)
        left_layout.addWidget(prompt_group)

        category_group = QGroupBox("新闻范围")
        category_layout = QGridLayout(category_group)
        category_layout.setContentsMargins(22, 38, 22, 18)
        category_layout.setVerticalSpacing(8)
        category_layout.setHorizontalSpacing(24)
        categories = get_available_categories()
        for index, (key, info) in enumerate(categories.items()):
            check = QCheckBox(f"{info['label']}  ({info['feed_count']} 源)")
            check.setToolTip(info["description"])
            check.setChecked(key in {"ai", "tech"})
            self.category_checks[key] = check
            category_layout.addWidget(check, index // 2, index % 2)
        left_layout.addWidget(category_group)

        options_group = QGroupBox("执行选项")
        options_layout = QGridLayout(options_group)
        options_layout.setContentsMargins(22, 38, 22, 18)
        options_layout.setVerticalSpacing(12)
        options_layout.setHorizontalSpacing(18)
        self.mode_label = QLabel("当前模式：真实发送邮件")
        self.mode_label.setObjectName("ModeLabelSend")
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("收件邮箱（留空使用 DEFAULT_RECIPIENT）")
        self.email_input.textChanged.connect(lambda: self._refresh_mode_label())
        self.depth_spin = QSpinBox()
        self.depth_spin.setRange(3, 15)
        self.depth_spin.setValue(8)
        options_layout.addWidget(QLabel("重点深读篇数建议"), 0, 0)
        options_layout.addWidget(self.depth_spin, 0, 1)
        options_layout.addWidget(QLabel("收件邮箱"), 1, 0)
        options_layout.addWidget(self.email_input, 1, 1)
        options_layout.addWidget(self.mode_label, 2, 0, 1, 2)
        left_layout.addWidget(options_group)

        action_row = QHBoxLayout()
        rss_button = QPushButton("RSS 工具测试")
        rss_button.clicked.connect(self.run_rss_smoke)
        stats_button = QPushButton("刷新数据")
        stats_button.clicked.connect(self.refresh_stats)
        action_row.addWidget(rss_button)
        action_row.addWidget(stats_button)
        action_row.addStretch(1)
        left_layout.addLayout(action_row)

        stats_row = QHBoxLayout()
        self.article_card = StatCard("文章库", "0", "SQLite")
        self.feed_card = StatCard("RSS 分类", str(len(get_available_categories())), "可用分类")
        stats_row.addWidget(self.article_card)
        stats_row.addWidget(self.feed_card)
        left_layout.addLayout(stats_row)
        left_layout.addStretch(1)

        log_group = QGroupBox("执行日志")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(22, 38, 22, 18)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(300)
        log_layout.addWidget(self.log_view)
        right_layout.addWidget(log_group, 1)

        result_group = QGroupBox("最终响应")
        result_layout = QVBoxLayout(result_group)
        result_layout.setContentsMargins(22, 38, 22, 18)
        self.result_view = QTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setMinimumHeight(240)
        result_layout.addWidget(self.result_view)
        right_layout.addWidget(result_group, 1)
        self._refresh_mode_label()

    def _refresh_mode_label(self) -> None:
        recipient = self.email_input.text().strip() or self.config_page.collect_values().get("DEFAULT_RECIPIENT", "").strip()
        target = recipient if usable_email(recipient) else "未设置有效收件人"
        self.mode_label.setText(f"当前模式：真实发送邮件，目标邮箱：{target}")
        self.mode_label.setObjectName("ModeLabelSend")
        if hasattr(self, "run_button"):
            self.run_button.setText("发送邮件")
        self.mode_label.style().unpolish(self.mode_label)
        self.mode_label.style().polish(self.mode_label)

    def set_progress(self, value: int, label: str) -> None:
        self.progress_label.setText(label)
        if self.progress_animation:
            self.progress_animation.stop()
        self.progress_animation = QPropertyAnimation(self.progress_bar, b"value", self)
        self.progress_animation.setDuration(420)
        self.progress_animation.setStartValue(self.progress_bar.value())
        self.progress_animation.setEndValue(max(0, min(value, 100)))
        self.progress_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.progress_animation.start()

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"<span style='color:#7b8794'>{timestamp}</span> {html.escape(message)}")

    def selected_categories(self) -> list[str]:
        return [key for key, check in self.category_checks.items() if check.isChecked()]

    def compose_prompt(self) -> str:
        prompt = self.prompt_edit.toPlainText().strip()
        categories = self.selected_categories()
        values = self.config_page.collect_values()
        default_recipient = values.get("DEFAULT_RECIPIENT", "").strip()
        if categories:
            prompt += "\n\n请优先获取这些 RSS 分类: " + ", ".join(categories)
        prompt += f"\n请从候选新闻中深读约 {self.depth_spin.value()} 篇重点文章。"
        recipient = self.email_input.text().strip() or default_recipient
        if recipient:
            prompt += f"\n邮件收件人固定为: {recipient}"
        prompt += (
            "\n当前是真实发送模式：必须调用 send_email 工具发送邮件，不要只输出预览，也不要追问邮箱地址。"
            "调用 send_email 前必须先生成非空完整 body_html HTML 片段，且不要包含整页 HTML 或顶部横幅；"
            "并在同一次工具调用中传入 recipient、subject、body_html；"
            "不要先空调用 send_email。如果发送工具提示缺少 body_html，必须立刻补齐后重试。"
        )
        return prompt

    def run_agent(self) -> None:
        prompt = self.compose_prompt()
        if not prompt.strip():
            QMessageBox.warning(self, "缺少提示词", "请先输入任务提示词。")
            return
        values = self.config_page.collect_values()
        api_key = values.get("DEEPSEEK_API_KEY", "")
        if not api_key or "your-" in api_key:
            QMessageBox.warning(self, "缺少 API Key", "请先在配置中心填写 DeepSeek API Key。")
            return
        recipient = self.email_input.text().strip() or values.get("DEFAULT_RECIPIENT", "").strip()
        if not usable_email(recipient):
            QMessageBox.warning(self, "缺少收件人", "真实发送需要填写收件邮箱，或在配置中心设置有效的 DEFAULT_RECIPIENT。")
            return
        smtp_ready = all(values.get(k) for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD"))
        if not smtp_ready:
            QMessageBox.warning(self, "SMTP 未配置", "真实发送需要完整 SMTP_HOST、SMTP_PORT、SMTP_USERNAME 和 SMTP_PASSWORD。")
            return

        self.config_page.save_values(silent=True)
        self.result_view.clear()
        self.append_log("提交 Agent 任务。")
        self.set_progress(2, "提交任务")
        self.run_button.setEnabled(False)
        self.agent_thread = AgentRunThread(
            prompt=prompt,
            model=values.get("DEEPSEEK_MODEL") or None,
        )
        self.agent_thread.log.connect(self.append_log)
        self.agent_thread.progress.connect(self.set_progress)
        self.agent_thread.done.connect(self._agent_done)
        self.agent_thread.failed.connect(self._agent_failed)
        self.agent_thread.start()

    def _agent_done(self, result: str) -> None:
        self.result_view.setPlainText(result)
        self.append_log("Agent 任务完成。")
        self.set_progress(100, "任务完成")
        self.run_button.setEnabled(True)
        self.refresh_stats()

    def _agent_failed(self, error: str) -> None:
        self.append_log(f"Agent 任务失败：{error}")
        self.set_progress(0, "任务失败")
        self.run_button.setEnabled(True)
        QMessageBox.critical(self, "运行失败", error)

    def run_rss_smoke(self) -> None:
        self.append_log("启动 RSS 工具测试。")
        self.rss_thread = RssSmokeThread()
        self.rss_thread.log.connect(self.append_log)
        self.rss_thread.done.connect(self._rss_done)
        self.rss_thread.failed.connect(lambda err: self.append_log(f"RSS 工具测试失败：{err}"))
        self.rss_thread.start()

    def _rss_done(self, result: dict) -> None:
        self.append_log(
            "RSS 工具测试完成："
            f"{result.get('total_fetched', 0)} 篇，"
            f"新增 {result.get('new_articles', 0)} 篇，"
            f"分类 {result.get('by_category', {})}。"
        )
        errors = result.get("errors") or []
        if errors:
            self.append_log("部分源失败：" + "; ".join(errors[:3]))
        self.refresh_stats()

    def refresh_stats(self) -> None:
        article_total = 0
        category_caption = "尚无数据"
        if DB_PATH.exists():
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    article_total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
                    rows = conn.execute(
                        "SELECT category, COUNT(*) FROM articles GROUP BY category ORDER BY COUNT(*) DESC LIMIT 3"
                    ).fetchall()
                    if rows:
                        category_caption = " / ".join(f"{cat}:{count}" for cat, count in rows)
            except sqlite3.Error:
                category_caption = "数据库未初始化"
        self.article_card.set_value(str(article_total), category_caption)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("每日新闻智能摘要 Agent")
        self.resize(1280, 840)
        self.setMinimumSize(1100, 720)
        self._build()

    def _build(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(root)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(250)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(20, 22, 20, 22)
        brand = QLabel("Daily News Agent")
        brand.setObjectName("Brand")
        tagline = QLabel("RSS · Function Calling · Email")
        tagline.setObjectName("SidebarMuted")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(tagline)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        for label in ("配置中心", "任务工作台"):
            item = QListWidgetItem(label)
            item.setSizeHint(item.sizeHint())
            self.nav.addItem(item)
        self.nav.setCurrentRow(0)
        sidebar_layout.addWidget(self.nav)
        sidebar_layout.addStretch(1)

        footer = QLabel(f"Workspace\n{ROOT}")
        footer.setObjectName("SidebarMuted")
        footer.setWordWrap(True)
        sidebar_layout.addWidget(footer)

        self.stack = QStackedWidget()
        self.config_page = ConfigPage()
        self.workbench_page = WorkbenchPage(self.config_page)
        self.config_page.status_changed.connect(self.workbench_page.refresh_stats)
        self.stack.addWidget(self.config_page)
        self.stack.addWidget(self.workbench_page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        content = QFrame()
        content.setObjectName("Content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.addWidget(self.stack)

        root_layout.addWidget(sidebar)
        root_layout.addWidget(content, 1)


def apply_style(app: QApplication) -> None:
    app.setFont(QFont("Arial", 13))
    app.setStyleSheet(
        """
        QMainWindow { background: #f4f6f8; }
        #Sidebar { background: #19212b; color: #ffffff; }
        #Brand { color: #ffffff; font-size: 22px; font-weight: 700; }
        #SidebarMuted { color: #9ba7b4; font-size: 12px; line-height: 1.4; }
        #Content { background: #f4f6f8; }
        QScrollArea {
            background: transparent;
            border: none;
        }
        #PageHeader {
            background: #ffffff;
            border: 1px solid #dde3ea;
            border-radius: 8px;
            margin-bottom: 16px;
        }
        #ProgressPanel {
            background: #ffffff;
            border: 1px solid #dde3ea;
            border-radius: 8px;
            margin-bottom: 16px;
        }
        #ProgressLabel {
            color: #405166;
            font-weight: 700;
            min-width: 96px;
        }
        QProgressBar {
            background: #edf2f7;
            border: 1px solid #d7e1ea;
            border-radius: 6px;
            height: 14px;
            color: #253241;
            text-align: center;
            font-size: 11px;
            font-weight: 700;
        }
        QProgressBar::chunk {
            background: #2c86d1;
            border-radius: 5px;
        }
        #PageTitle { color: #17202a; font-size: 26px; font-weight: 700; }
        #Muted, QLabel { color: #526071; }
        #ModeLabel {
            color: #526071;
            background: #f7fafc;
            border: 1px solid #d7e1ea;
            border-radius: 6px;
            padding: 8px 10px;
        }
        #ModeLabelSend {
            color: #14532d;
            background: #eefbf3;
            border: 1px solid #b7e4c7;
            border-radius: 6px;
            padding: 8px 10px;
            font-weight: 700;
        }
        #StatCard {
            background: #ffffff;
            border: 1px solid #dde3ea;
            border-radius: 8px;
        }
        #StatTitle { color: #657386; font-size: 12px; font-weight: 700; text-transform: uppercase; }
        #StatValue { color: #17202a; font-size: 24px; font-weight: 700; }
        #InfoBox {
            background: #f7fafc;
            border: 1px solid #d7e1ea;
            border-radius: 8px;
            padding: 10px;
        }
        QListWidget#NavList {
            background: transparent;
            border: none;
            color: #dbe3ec;
            outline: none;
            margin-top: 22px;
        }
        QListWidget#NavList::item {
            padding: 12px 14px;
            border-radius: 8px;
            margin: 4px 0;
        }
        QListWidget#NavList::item:selected {
            background: #2c86d1;
            color: #ffffff;
        }
        QGroupBox {
            background: #ffffff;
            border: 1px solid #dde3ea;
            border-radius: 8px;
            margin-top: 0;
            padding: 0;
            font-weight: 700;
            color: #253241;
        }
        QGroupBox::title {
            subcontrol-origin: border;
            subcontrol-position: top left;
            left: 16px;
            top: 11px;
            padding: 0 8px;
            background: #ffffff;
        }
        QLineEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox {
            background: #ffffff;
            border: 1px solid #cdd6e0;
            border-radius: 6px;
            padding: 8px;
            color: #17202a;
            selection-background-color: #2c86d1;
        }
        QLineEdit, QComboBox, QSpinBox {
            min-height: 30px;
        }
        QTextEdit:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
            border: 1px solid #2c86d1;
        }
        QPushButton {
            background: #ffffff;
            border: 1px solid #c8d3df;
            border-radius: 6px;
            padding: 9px 14px;
            color: #223043;
            font-weight: 600;
        }
        QPushButton:hover { background: #eef4f8; }
        QPushButton:disabled { color: #9aa6b2; background: #f1f3f5; }
        QPushButton#PrimaryButton {
            background: #256fbe;
            border: 1px solid #256fbe;
            color: #ffffff;
        }
        QPushButton#PrimaryButton:hover { background: #1f62aa; }
        QCheckBox { color: #2d3b4d; spacing: 8px; }
        QSplitter::handle { background: #dbe3ea; width: 1px; }
        """
    )


def launch_gui() -> None:
    os.chdir(ROOT)
    load_dotenv(ENV_PATH, override=True)
    app = QApplication.instance() or QApplication(sys.argv)
    apply_style(app)
    window = MainWindow()
    window.show()
    app.exec()
