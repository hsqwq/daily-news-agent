"""
邮件发送工具 — 通过 SMTP 发送 HTML 格式的新闻摘要
"""
import logging
import re
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from datetime import datetime, timezone
from pathlib import Path

import yaml
import aiosmtplib
from bs4 import BeautifulSoup, NavigableString

logger = logging.getLogger(__name__)


def _looks_like_email(value: str) -> bool:
    return bool(value and "@" in value and "." in value.split("@")[-1])


def _replace_email_unfriendly_symbols(text: str) -> str:
    replacements = {
        "📰": "",
        "🤖": "",
        "📌": "",
        "📊": "",
        "🕐": "",
        "🏢": "",
        "💰": "",
        "🔬": "",
        "💻": "",
        "🔹": "",
        "🔸": "",
        "▪": "",
        "▫": "",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    # Strip remaining non-BMP symbols that many mobile mail clients render as boxes.
    return re.sub(r"[\U00010000-\U0010ffff]", "", text)


def _normalize_body_html(body_html: str) -> str:
    """Convert model-produced HTML into a safe fragment for our email shell."""
    soup = BeautifulSoup(body_html or "", "html.parser")

    for tag in soup(["script", "style", "meta", "title", "head"]):
        tag.decompose()

    root = soup.body if soup.body else soup
    fragment = BeautifulSoup("".join(str(child) for child in root.contents), "html.parser")

    # The outer email template already provides the purple header. Remove model-made
    # duplicate hero/header blocks before insertion.
    for tag in list(fragment.find_all(True)):
        if tag.parent is None or tag.attrs is None:
            continue
        style = (tag.get("style") or "").lower().replace(" ", "")
        text = tag.get_text(" ", strip=True)
        has_gradient = "linear-gradient" in style or ("#667eea" in style and "#764ba2" in style)
        is_duplicate_header = "每日新闻" in text and ("aiagent" in text.lower() or "自动生成" in text or has_gradient)
        if has_gradient and is_duplicate_header:
            tag.decompose()

    for text_node in list(fragment.find_all(string=True)):
        if isinstance(text_node, NavigableString):
            cleaned = _replace_email_unfriendly_symbols(str(text_node))
            text_node.replace_with(cleaned)

    normalized = "".join(str(child) for child in fragment.contents).strip()
    return normalized or "<p>邮件正文生成失败，请重新运行任务。</p>"

# 工具定义 — 用于 DeepSeek function calling
SEND_EMAIL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_email",
        "description": (
            "将新闻摘要通过邮件发送到指定邮箱。"
            "支持 HTML 格式，包含完整的标题、正文、样式。"
            "用于将整理好的新闻摘要投递到用户的电子邮箱。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {
                    "type": "string",
                    "description": "收件人邮箱地址",
                    "minLength": 3,
                },
                "subject": {
                    "type": "string",
                    "description": "邮件主题，例如「每日新闻摘要 - 2024年6月17日」",
                    "minLength": 2,
                },
                "body_html": {
                    "type": "string",
                    "description": "必填且不能为空。邮件正文 HTML 片段，不要包含 <!DOCTYPE>、html、head、body 或顶部横幅。"
                    "必须包括今日概览、新闻分类标题、每条新闻的标题+摘要+来源链接+发布时间。",
                    "minLength": 200,
                },
            },
            "required": ["recipient", "subject", "body_html"],
        },
    },
}


def _load_email_config() -> dict:
    """加载邮件配置，解析环境变量"""
    import os
    import re

    config_path = Path(__file__).parent.parent / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        raw = f.read()

    # 替换 ${VAR:-default} 占位符
    def replace_env(match):
        expr = match.group(1)
        if ":-" in expr:
            var, default = expr.split(":-", 1)
            return os.environ.get(var.strip(), default.strip())
        else:
            return os.environ.get(expr.strip(), "")

    raw = re.sub(r"\$\{([^}]+)\}", replace_env, raw)
    config = yaml.safe_load(raw)
    return config.get("email", {})


async def send_email(
    recipient: str,
    subject: str,
    body_html: str,
) -> dict:
    """
    发送 HTML 格式的邮件

    Args:
        recipient: 收件人邮箱
        subject: 邮件主题
        body_html: HTML 正文

    Returns:
        {"success": bool, "message": str}
    """
    config = _load_email_config()

    smtp_host = config.get("smtp_host", "smtp.qq.com")
    smtp_port = int(config.get("smtp_port", 465))
    username = config.get("username", "")
    password = config.get("password", "")
    from_name = config.get("from_name", "每日新闻摘要")
    configured_from_email = config.get("from_email", "")
    from_email = configured_from_email if _looks_like_email(configured_from_email) else username

    if not username or not password:
        return {
            "success": False,
            "message": "邮件配置不完整，请设置 SMTP_USERNAME 和 SMTP_PASSWORD 环境变量",
        }
    if not _looks_like_email(username):
        return {
            "success": False,
            "message": "SMTP_USERNAME 必须是有效邮箱地址，QQ 邮箱要求 From 与登录邮箱一致",
        }
    if not _looks_like_email(from_email):
        return {
            "success": False,
            "message": "发件邮箱无效，请设置 SMTP_FROM_EMAIL 或使用邮箱格式的 SMTP_USERNAME",
        }

    body_html = _normalize_body_html(body_html)

    # 构建完整 HTML
    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,'PingFang SC','Microsoft YaHei',sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f5f5f5;padding:20px 0;">
        <tr>
            <td align="center">
                <table width="640" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
                    <!-- Header -->
                    <tr>
                        <td style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:32px 40px;text-align:center;">
                            <h1 style="color:#ffffff;font-size:24px;margin:0 0 8px 0;">每日新闻智能摘要</h1>
                            <p style="color:rgba(255,255,255,0.85);font-size:13px;margin:0;">
                                由 AI Agent 自动生成 · {datetime.now(timezone.utc).strftime('%Y年%m月%d日')}
                            </p>
                        </td>
                    </tr>
                    <!-- Body -->
                    <tr>
                        <td style="padding:32px 40px;">
                            {body_html}
                        </td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="background-color:#fafafa;padding:20px 40px;border-top:1px solid #eee;text-align:center;">
                            <p style="color:#999;font-size:12px;margin:0;">
                                本邮件由 AI 新闻摘要 Agent 自动生成并发送<br>
                                数据来源: 60+ 全球 RSS 新闻源 | 生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((Header(from_name, "utf-8").encode(), from_email))
    msg["To"] = recipient
    msg["Subject"] = Header(subject, "utf-8").encode()
    msg.attach(MIMEText(full_html, "html", "utf-8"))

    try:
        if smtp_port == 465:
            # SSL 直连
            await aiosmtplib.send(
                msg,
                hostname=smtp_host,
                port=smtp_port,
                username=username,
                password=password,
                use_tls=True,
                timeout=30,
            )
        else:
            # STARTTLS
            await aiosmtplib.send(
                msg,
                hostname=smtp_host,
                port=smtp_port,
                username=username,
                password=password,
                start_tls=True,
                timeout=30,
            )

        logger.info(f"邮件发送成功: {recipient}")
        return {"success": True, "message": f"邮件已成功发送至 {recipient}"}

    except aiosmtplib.SMTPAuthenticationError:
        return {"success": False, "message": "SMTP 认证失败，请检查邮箱用户名和授权码"}
    except aiosmtplib.SMTPConnectError:
        return {"success": False, "message": f"无法连接 SMTP 服务器 {smtp_host}:{smtp_port}"}
    except aiosmtplib.SMTPTimeoutError:
        return {"success": False, "message": "SMTP 连接超时"}
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        return {"success": False, "message": f"邮件发送失败: {str(e)}"}
