"""
LLM 客户端 — 调用 LLM API 生成差异化的聊天内容和朋友圈文案。

支持的提供商:
    - DeepSeek（推荐，性价比高，中文能力强）
    - OpenAI 兼容接口（可接入任何兼容的 API）
    - 本地模型（Ollama 等，完全离线）

安全措施:
    - 每次调用使用不同的 prompt 注入人格信息
    - temperature 随机微调，确保内容多样性
    - 记录生成历史哈希，定期检查同质化趋势

使用方式:
    from content.llm_client import LLMClient
    client = LLMClient()
    post = client.generate_post_text(persona)
    chat = client.generate_chat_text(persona, context="和朋友闲聊")
"""

import hashlib
import base64
import json
import random
import re
import time
from typing import Optional

from openai import OpenAI

from config.settings import settings
from utils.logger import get_logger

from scripts.chat_history_reader import (
    _VOICE_PLACEHOLDER,
    _VOICE_UNTRANSCRIBED_LLM,
    sanitize_chat_history_for_llm,
)

logger = get_logger("llm_client")

# 深聊防幻觉：检测虚构的分享/视频话术
_SHARE_VIDEO_PATTERNS = (
    re.compile(r"视频"),
    re.compile(r"链接"),
    re.compile(r"分享给你"),
    re.compile(r"给你看"),
    re.compile(r"刚刷到"),
    re.compile(r"刚看到.{0,6}(搞笑|好玩|有意思)"),
    re.compile(r"推荐你看"),
)

_CHAT_AUTHENTICITY_RULES = """\
- 只聊聊天记录里真实出现的话题；对方没提视频/链接/分享，就不要主动提
- 禁止说「给你看个视频」「刚刷到一个」等无法在微信里真正发送的内容
- 禁止假装分享媒体；可聊日常、工作、天气、兴趣
- 若对方问了具体问题，先直接回答，再延伸"""

_FRIEND_SHARE_KEYWORDS = ("视频", "链接", "分享", "刷到", "推荐你看")


def _friend_mentioned_share_topic(history: list[dict]) -> bool:
    for item in history:
        if item.get("role") != "friend":
            continue
        text = str(item.get("text", ""))
        if any(k in text for k in _FRIEND_SHARE_KEYWORDS):
            return True
    return False


def _looks_like_fake_share(reply: str) -> bool:
    return any(p.search(reply) for p in _SHARE_VIDEO_PATTERNS)


def _self_messages(history: list[dict]) -> list[str]:
    return [
        str(h.get("text", "")).strip()
        for h in history
        if h.get("role") == "self" and str(h.get("text", "")).strip()
    ]


def _reply_contradicts_self_role(history: list[dict], reply: str) -> bool:
    """检测回复是否把己方立场说成对方立场（如「我转了」却回「钱收到了」）。"""
    reply = str(reply or "").strip()
    if not reply:
        return False
    self_text = " ".join(_self_messages(history))
    if not self_text:
        return False

    if ("转了" in self_text or "转给" in self_text) and (
        "收到了" in reply or "到账" in reply
    ):
        return True
    if ("你先去" in self_text or "你把" in self_text or "你给娃" in self_text) and (
        "这就去买" in reply or "我去买" in reply or "我这就" in reply
    ):
        return True
    if "我转" in self_text and "钱收到" in reply:
        return True
    return False


def _reply_invents_voice_content(reply: str, history: list[dict]) -> bool:
    if not reply:
        return False
    voice_claim_patterns = (
        "语音里说了",
        "刚语音",
        "语音说",
        "你语音",
        "我语音",
        "听了你的语音",
        "语音讲了",
        "刚才语音",
        "语音里讲",
    )
    has_untranscribed = any(
        "未转写" in str(h.get("text", "")) and "不知道内容" in str(h.get("text", ""))
        for h in history
    )
    if has_untranscribed and any(p in reply for p in voice_claim_patterns):
        return True
    return False


_TOPIC_KEYWORDS = (
    "外卖",
    "肯德基",
    "蛋挞",
    "测评",
    "KFC",
    "kfc",
)


def _friend_text_plain(item: dict) -> str:
    text = str(item.get("text", "")).strip()
    for prefix in ("【语音转写】", "【语音-未转写】"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text


def _safe_reply_from_friend_focus(friend_focus: list[dict]) -> str:
    """校验失败时，按对方最新消息生成不含外卖等误拼接的短回复。"""
    if not friend_focus:
        return ""
    text = _friend_text_plain(friend_focus[-1])
    if not text:
        return ""
    if "帅" in text and ("吗" in text or "?" in text or "？" in text):
        return random.choice(["帅啊，必须帅", "你帅得很", "还行吧挺帅的"])
    if "转钱" in text or "转账" in text:
        return random.choice(["等等哈", "行，回头说", "知道了"])
    if text in ("好", "好的", "嗯", "行", "OK", "ok"):
        return random.choice(["嗯", "好", "行"])
    return ""


def _reply_conflates_voice_with_unrelated_text(reply: str, history: list[dict]) -> bool:
    """
    回复把【语音转写】与无关文字话题拼在一起（如语音是「测试语音」却回外卖）。
    """
    if not reply:
        return False
    if not any(k in reply for k in _TOPIC_KEYWORDS):
        return False
    voice_texts = _voice_transcript_texts(history)
    if not voice_texts:
        return False
    voice_blob = "".join(voice_texts)
    if any(k in voice_blob for k in _TOPIC_KEYWORDS):
        return False
    return True


def _voice_transcript_texts(history: list[dict]) -> list[str]:
    texts: list[str] = []
    for item in history:
        text = str(item.get("text", "")).strip()
        if not text or ("未转写" in text and "不知道内容" in text):
            continue
        if item.get("type") == "voice":
            texts.append(text)
    return texts


def _format_history_line(item: dict, contact: str, self_name: str) -> str:
    text = str(item.get("text", "")).strip()
    if not text:
        return ""
    if item.get("type") == "voice":
        if "未转写" in text and "不知道内容" in text:
            body = f"【语音-未转写】{text}"
        else:
            body = f"【语音转写】{text}"
    elif "未转写" in text and "发来语音" in text:
        body = f"【语音-未转写】{text}"
    else:
        body = text
    if item.get("role") == "self":
        return f"【我-{self_name}】{body}"
    return f"【对方-{contact}】{body}"


def _format_history_for_prompt(history: list[dict], contact: str, self_name: str) -> str:
    lines: list[str] = []
    for item in history[-16:]:
        line = _format_history_line(item, contact, self_name)
        if line:
            lines.append(line)
    return "\n".join(lines)


def _last_nonempty_index(history: list[dict], role: str) -> int:
    for i in range(len(history) - 1, -1, -1):
        item = history[i]
        if item.get("role") != role:
            continue
        if str(item.get("text", "")).strip():
            return i
    return -1


def _analyze_chat_reply_context(
    history: list[dict],
) -> tuple[str, list[dict]]:
    """
    判断下一条该怎么回。

    Returns:
        mode: opening | wait | reply
        friend_focus: 对方最近几条消息（用于强调权重，非唯一上下文）
    """
    cleaned = [
        h for h in history if str(h.get("text", "")).strip()
    ]
    if not cleaned:
        return "opening", []

    friend_msgs = [h for h in cleaned if h.get("role") == "friend"]
    if not friend_msgs:
        return "opening", []

    last_friend_i = _last_nonempty_index(cleaned, "friend")
    last_self_i = _last_nonempty_index(cleaned, "self")

    if last_self_i > last_friend_i:
        return "wait", []

    focus = friend_msgs[-3:] if len(friend_msgs) >= 3 else friend_msgs
    return "reply", focus


class LLMClient:
    """
    LLM 内容生成客户端。

    封装了朋友圈文案、聊天内容、评论的生成逻辑。
    """

    def __init__(self):
        self.client: Optional[OpenAI] = None
        self.vision_client: Optional[OpenAI] = None
        provider = str(getattr(settings, "LLM_PROVIDER", "deepseek") or "deepseek").strip().lower()

        # 文本主通道可显式切到混元；其余提供方继续走通用 LLM_* 配置。
        if provider == "hunyuan" and getattr(settings, "HUNYUAN_API_KEY", ""):
            self.client = OpenAI(
                api_key=settings.HUNYUAN_API_KEY,
                base_url=settings.HUNYUAN_BASE_URL,
                max_retries=0,
            )
        elif settings.LLM_API_KEY:
            self.client = OpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_BASE_URL,
                # 避免 SDK 内置重试导致 30s timeout 被放大成 60~90s 卡顿
                max_retries=0,
            )
        # 混元 TokenHub：优先作为 Vision 识图客户端
        if getattr(settings, "HUNYUAN_API_KEY", ""):
            self.vision_client = OpenAI(
                api_key=settings.HUNYUAN_API_KEY,
                base_url=settings.HUNYUAN_BASE_URL,
                max_retries=0,
            )
        self._history_hashes: list[str] = []

    @property
    def available(self) -> bool:
        """检查 LLM 是否可用"""
        return self.client is not None

    def _text_model(self) -> str:
        """返回当前文本生成所用模型名。"""
        provider = str(getattr(settings, "LLM_PROVIDER", "deepseek") or "deepseek").strip().lower()
        if provider == "hunyuan" and getattr(settings, "HUNYUAN_MODEL", "").strip():
            return settings.HUNYUAN_MODEL.strip()
        return settings.LLM_MODEL

    def _vision_client(self) -> Optional[OpenAI]:
        """Vision 优先用混元，否则回退到主 LLM client。"""
        return self.vision_client or self.client

    def _vision_model(self) -> str:
        if getattr(settings, "HUNYUAN_API_KEY", "") and getattr(
            settings, "HUNYUAN_VISION_MODEL", ""
        ):
            return settings.HUNYUAN_VISION_MODEL.strip()
        if getattr(settings, "LLM_VISION_MODEL", "").strip():
            return settings.LLM_VISION_MODEL.strip()
        return settings.LLM_MODEL

    # ================================================================
    # 朋友圈文案
    # ================================================================

    def generate_post_text(
        self,
        persona: dict,
        topic: str = "日常",
    ) -> str:
        """
        生成朋友圈文案。

        Args:
            persona: 人格档案
            topic: 文案主题

        Returns:
            朋友圈文案（30-120字）
        """
        prompt = self._build_post_prompt(persona, topic)
        text = self._call_api(prompt, temperature=0.85, max_tokens=200)
        return self._ensure_variety(text, persona)

    def generate_post_from_photos(
        self,
        persona: dict,
        photo_descriptions: list[str],
        topic: str = "日常",
    ) -> str:
        """
        根据选中照片的画面描述生成朋友圈配文。

        Args:
            persona: 人格档案
            photo_descriptions: Vision 输出的各张照片描述
            topic: 备用主题

        Returns:
            与图片相关的朋友圈文案
        """
        desc_lines = "\n".join(
            f"- 照片{i + 1}：{d.strip()}"
            for i, d in enumerate(photo_descriptions)
            if d and d.strip()
        )
        if not desc_lines:
            return self.generate_post_text(persona, topic=topic)

        prompt = f"""你是一个真实微信用户，以下是你的个人画像：
- 年龄：{persona.get('age', '25-35')}岁
- 城市：{persona.get('city', '北京')}
- 兴趣爱好：{', '.join(persona.get('hobbies', ['美食', '旅行', '阅读']))}
- 发圈风格：{persona.get('post_style', '随性简短')}

你刚从手机相册里挑了几张照片准备发朋友圈，照片内容如下：
{desc_lines}

请根据这些照片写一条朋友圈文案（30-100字）。

要求：
- 文案必须和照片内容相关，不要写与画面无关的话
- 像普通人随手发的，口语化、自然
- 不要用 emoji 堆砌（最多2个）
- 不要提敏感话题
- 多张照片时可以概括整体氛围，不必逐张描述
"""
        text = self._call_api(prompt, temperature=0.85, max_tokens=200)
        return self._ensure_variety(text, persona)

    def classify_moment_thumbnail(
        self,
        jpeg_bytes: bytes,
        preferred_categories: list[str] | None = None,
        topic: str = "日常",
    ) -> dict:
        """
        判断相册缩略图是否适合作为朋友圈配图。

        Args:
            jpeg_bytes: 缩略图 JPEG
            preferred_categories: 本次发圈偏好类别（如 美食/旅行/日常）
            topic: 发圈主题，用于引导分类侧重点

        Returns:
            {suitable, category, description, reject_reason}
        """
        fallback = {
            "suitable": True,
            "category": "日常",
            "description": "",
            "reject_reason": "",
        }
        if not jpeg_bytes:
            return {**fallback, "suitable": False, "reject_reason": "空图"}

        if not self.vision_available:
            return fallback

        client = self._vision_client()
        if not client:
            return fallback

        prefs = preferred_categories or ["日常", "美食", "旅行", "风景"]
        pref_text = "、".join(prefs)
        b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        prompt = f"""判断这张手机相册缩略图是否适合发微信朋友圈。

本次发圈主题：{topic or "日常"}
优先想发的内容类型：{pref_text}

重要说明：
- 图源是微信相册网格裁剪，右上角未选中的空心圆是相册勾选控件，不算系统界面
- 不要因为有勾选圆、网格边框就判 suitable=false
- 若画面上下拼接了两种内容（例如上半美食、下半聊天列表），一律 suitable=false
- 聊天列表、桌面图标墙、纯截图即使局部有食物色块，也一律 suitable=false

不适合（suitable=false）：
- 聊天/朋友圈/网页截图（整屏 UI 列表）
- 画面中同时出现「食物特写 + 聊天/桌面/系统列表」的串图
- 二维码、条形码、付款码
- 证件、银行卡、发票、营业执照
- 黑屏、白屏、严重模糊到无法辨认主体
- 纯桌面图标墙、文件夹图标墙（不是照片）
- 纯奢侈品 logo/首饰特写且无生活场景（除非主题就是自拍/穿搭）

适合（suitable=true），并按真实内容归类：
- 美食：食物、菜品、饮料、餐厅餐桌、火锅烧烤等（优先识别）
- 旅行：景点、旅途、行李、交通出行
- 风景：自然风光、城市天际线、日落
- 日常：街头、居家、工作学习、生活片段
- 宠物：猫狗等
- 自拍：大头照、对镜自拍、妆造特写
- 其他：其余可发圈内容

分类要求：
- category 必须从：风景|美食|宠物|自拍|日常|旅行|其他 中选一个
- 若画面同时有人像和场景，优先按场景归为日常/旅行/美食，不要轻易标成自拍
- 纯侧脸/项链胸针等妆造特写才标自拍
- description 用中文客观描述画面（10-30字）

只输出 JSON，不要 markdown：
{{"suitable": true, "category": "风景|美食|宠物|自拍|日常|旅行|其他", "description": "10-30字画面描述", "reject_reason": ""}}"""

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        raw = self._call_api_vision(
            messages,
            model=self._vision_model(),
            temperature=0.1,
            max_tokens=180,
            client=client,
        )
        parsed = self._parse_json_object(raw)
        if not parsed:
            logger.warning("Vision 缩略图分类 JSON 解析失败，保守接受")
            return fallback

        return {
            "suitable": bool(parsed.get("suitable", True)),
            "category": str(parsed.get("category") or "日常"),
            "description": str(parsed.get("description") or "").strip(),
            "reject_reason": str(parsed.get("reject_reason") or "").strip(),
        }

    # ================================================================
    # 聊天内容
    # ================================================================

    def generate_chat_text(
        self,
        persona: dict,
        context: str = "和朋友闲聊",
        scene: str = "small_talk",
    ) -> str:
        """
        生成聊天消息。

        Args:
            persona: 人格档案
            context: 对话背景
            scene: 对话场景

        Returns:
            聊天文本（10-50字）
        """
        prompt = f"""你正在模拟一个真实微信用户的聊天对话。

你的个人画像：
- 年龄：{persona.get('age', '25-35')}
- 城市：{persona.get('city', '北京')}
- 兴趣爱好：{', '.join(persona.get('hobbies', ['日常']))}
- 聊天风格：{persona.get('comment_style', '自然随意')}

当前场景：{context}
对话类型：{scene}

请用真实自然的口吻，发一条微信聊天消息（10-50字）。
要求：
- 像是随手打的，不要太正式
- 不用句号结尾（微信聊天习惯）
- 偶尔出现语气词（哈哈、嗯、哦）
- 不要用 emoji 堆砌，可以不用 emoji
"""
        text = self._call_api(prompt, temperature=0.9, max_tokens=100)
        return text

    # ================================================================
    # 评论
    # ================================================================

    def generate_comment(
        self,
        persona: dict,
        post_text: str = "",
    ) -> str:
        """
        生成朋友圈评论。

        Args:
            persona: 人格档案
            post_text: 被评论的朋友圈内容

        Returns:
            评论文本（5-25字）
        """
        post_context = f'\n朋友圈内容："{post_text}"' if post_text else ""
        prompt = f"""你是一个微信用户，看到了朋友发的朋友圈。{post_context}

你的个人画像：
- 年龄：{persona.get('age', '25-35')}
- 评论风格：{persona.get('comment_style', '简洁真诚')}

请用朋友间的自然口吻写一条评论（5-25字）。
要求：简短、自然、像随手敲的。
"""
        text = self._call_api(prompt, temperature=0.8, max_tokens=80)
        return text

    def generate_channel_comment(
        self,
        persona: dict,
        video_context: str = "",
    ) -> str:
        """
        根据视频号画面 OCR 到的标题/作者/简介生成评论。

        Args:
            persona: 人格档案
            video_context: OCR 提取的作者、标题、简介等文本

        Returns:
            评论文本（5-25字）；LLM 不可用时返回空串
        """
        ctx = (video_context or "").strip()[:280]
        context_line = (
            f'\n当前视频相关文字（OCR，可能不完整）："{ctx}"'
            if ctx
            else "\n（未能识别到视频文案，请发一句通用、自然的短评）"
        )
        prompt = f"""你正在刷视频号，准备给当前视频留一条评论。{context_line}

你的个人画像：
- 年龄：{persona.get('age', '25-35')}
- 兴趣：{', '.join(persona.get('hobbies', ['日常']))}
- 评论风格：{persona.get('comment_style', '简洁真诚')}

请写一条视频号评论（5-25字）。
要求：
- 简短自然，像随手敲的，不要官话
- 若有视频文案，尽量贴合内容，但不要复述整段
- 不要用话题标签，少用或不用 emoji
- 只输出评论正文，不要引号或解释
"""
        text = self._call_api(prompt, temperature=0.85, max_tokens=80)
        return (text or "").strip().strip('"\'「」')

    def generate_channel_comment_from_image(
        self,
        persona: dict,
        image_jpeg: bytes,
        video_context: str = "",
    ) -> str:
        """
        根据视频号当前画面截图生成评论（Vision 优先，OCR 文本作补充）。

        Args:
            persona: 人格档案
            image_jpeg: 视频画面 JPEG（建议裁掉右侧互动栏）
            video_context: OCR 提取的作者/标题/简介，供模型参考

        Returns:
            评论文本（5-25字）；Vision 不可用或失败时返回空串
        """
        if not image_jpeg or not self.vision_available:
            return ""

        client = self._vision_client()
        if not client:
            return ""

        ctx = (video_context or "").strip()[:280]
        ocr_hint = (
            f"\n补充文字（OCR，可能不完整或有误）：「{ctx}」"
            if ctx
            else ""
        )
        prompt = f"""这是一张微信视频号播放页截图（已裁掉右侧点赞/评论按钮栏）。
请先看画面和字幕/简介，再写一条你要发的评论。{ocr_hint}

你的个人画像：
- 年龄：{persona.get('age', '25-35')}
- 兴趣：{', '.join(persona.get('hobbies', ['日常']))}
- 评论风格：{persona.get('comment_style', '简洁真诚')}

请写一条视频号评论（5-25字）。
要求：
- 简短自然，像随手敲的，不要官话
- 贴合画面/字幕主题，但不要复述整段文案
- 看不清内容时，只发情绪向泛评（如「哈哈哈」「牛」），不要编造具体事实
- 不要用话题标签，少用或不用 emoji
- 只输出评论正文，不要引号或解释
"""
        b64 = base64.b64encode(image_jpeg).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        raw = self._call_api_vision(
            messages,
            model=self._vision_model(),
            temperature=0.55,
            max_tokens=80,
            client=client,
        )
        return (raw or "").strip().strip('"\'「」')

    def generate_article_comment(
        self,
        persona: dict,
        title: str = "",
        article_context: str = "",
    ) -> str:
        """
        根据公众号文章标题和正文片段生成评论。

        Args:
            persona: 人格档案
            title: 文章标题
            article_context: OCR 提取的正文/小标题摘要

        Returns:
            评论文本（5-30字）；LLM 不可用时返回空串
        """
        title = (title or "").strip()[:80]
        ctx = (article_context or "").strip()[:320]
        title_line = f'\n文章标题："{title}"' if title else ""
        context_line = (
            f'\n文章摘录（OCR，可能不完整）："{ctx}"'
            if ctx
            else "\n（未能识别到正文，请根据标题写一句自然短评）"
        )
        prompt = f"""你刚看完一篇微信公众号文章，准备留一条评论。{title_line}{context_line}

你的个人画像：
- 年龄：{persona.get('age', '25-35')}
- 兴趣：{', '.join(persona.get('hobbies', ['日常']))}
- 评论风格：{persona.get('comment_style', '简洁真诚')}

请写一条公众号文章评论（5-30字）。
要求：
- 结合标题或正文内容，像真人刚看完后的随手评论
- 简短自然，不要官话，不要复述整段原文
- 不要加书名号、引号、解释或序号
"""
        text = self._call_api(prompt, temperature=0.82, max_tokens=90)
        return (text or "").strip().strip('"\'「」')

    def generate_post_from_article(
        self,
        persona: dict,
        title: str = "",
        article_context: str = "",
    ) -> str:
        """
        根据公众号文章标题和正文片段生成一条读后感朋友圈文案。
        """
        title = (title or "").strip()[:80]
        ctx = (article_context or "").strip()[:320]
        prompt = f"""你刚看完一篇微信公众号文章，想发一条朋友圈分享感受。

你的个人画像：
- 年龄：{persona.get('age', '25-35')}
- 城市：{persona.get('city', '北京')}
- 兴趣爱好：{', '.join(persona.get('hobbies', ['阅读']))}
- 发圈风格：{persona.get('post_style', '随性简短')}

文章标题：{title or '未识别'}
文章摘录（OCR，可能不完整）：{ctx or '未识别到正文'}

请写一条 18-60 字的朋友圈文案，像普通人转化成自己的感受：
- 不要像摘要，不要长篇复述
- 允许提一句“刚看到/午休刷到”之类生活化语气
- 自然口语化，不要太正式
- 只输出正文，不要引号或解释
"""
        text = self._call_api(prompt, temperature=0.86, max_tokens=120)
        return (text or "").strip().strip('"\'「」')

    # ================================================================
    # 深聊多轮 / 上帝视角编排
    # ================================================================

    def generate_chat_reply_from_history(
        self,
        persona: dict,
        contact: str,
        history: list[dict],
    ) -> str:
        """
        根据完整聊天记录生成下一条回复（单条）。

        Args:
            persona: 人设
            contact: 好友昵称
            history: [{"role": "self"|"friend", "text": "..."}, ...] 按时间排序

        Returns:
            下一条要发送的消息；失败或应等待对方时返回空串
        """
        if not history:
            history = []

        history = sanitize_chat_history_for_llm(history)
        mode, friend_focus = _analyze_chat_reply_context(history)
        if mode == "wait":
            logger.debug(
                f"chat_reply: 己方已最后发言，等待 {contact} 回复"
            )
            return ""

        self_name = str(persona.get("name", "我")).strip() or "我"
        system = f"""你是微信用户「{self_name}」，正在和好友「{contact}」1v1 聊天。

身份（非常重要，不可搞反）：
- 你 = 屏幕右侧绿色气泡 = 「{self_name}」
- 对方 = 屏幕左侧白色气泡 = 「{contact}」
- 永远以你的立场回复，禁止模仿对方口吻，禁止把自己说过的话换个说法再说一遍
- 例：若你说过「钱我转了，你去买蛋挞」，对方回「好」，你不能说「钱收到了我去买」——钱是你转的，买蛋挞是对方的事

你的个人画像：
- 年龄：{persona.get('age', '25-35')}
- 城市：{persona.get('city', '北京')}
- 兴趣：{', '.join(persona.get('hobbies', ['日常']))}
- 聊天风格：{persona.get('comment_style', '自然随意')}

请结合完整聊天记录生成你的下一条微信消息。
要求：
- 上文所有消息都是上下文；对方提起更早的事要能接上
- 自然权重：对方越新的消息越优先回应；最新一条必须接住
- 保持立场一致：不要推翻自己刚说过的话，不要把己方动作说成对方动作
- 不要自言自语或重复自己刚说过的话
- 对方问了问题要先回答，再延伸
- 记录里写「发来语音，未转写，不知道内容」时，严禁编造语音里说了什么；不要写「刚语音里说了」等
- 对方发来未转写语音时，可简短回应语气，但不要捏造语音具体内容
- 标记为【语音转写】的内容按字面理解，不要与前后无关的文字消息拼接成一件事
- 例：【语音转写】「测试语音，测试语音」只是在测试录音，与上文文字「外卖测评搞起」完全无关，禁止把测试语音理解成在说外卖
- 10-50 字，口语化，像随手打的
- 少用或不用 emoji，不要句号结尾
- 只输出消息正文，不要引号、序号或解释
{_CHAT_AUTHENTICITY_RULES}
"""
        messages: list[dict] = [{"role": "system", "content": system}]
        transcript = _format_history_for_prompt(history, contact, self_name)
        if transcript:
            messages.append(
                {
                    "role": "user",
                    "content": f"当前聊天记录（按时间）：\n{transcript}",
                }
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": "好的，我已理解完整对话和我的身份立场。",
                }
            )

        if mode == "opening":
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "请生成你的下一条消息：对话刚开始或对方还没说话，"
                        "用问候或问近况自然开场，禁止分享视频/链接/媒体。"
                    ),
                }
            )
        else:
            latest = friend_focus[-1] if friend_focus else {}
            latest_text = _format_history_line(latest, contact, self_name) if latest else ""
            recent_lines = "\n".join(
                _format_history_line(h, contact, self_name)
                for h in friend_focus
                if str(h.get("text", "")).strip()
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "请生成你的下一条回复。\n"
                        f"对方最近几句（权重更高，最新一条必须回应）：\n"
                        f"{recent_lines}\n\n"
                        f"最新一条：{latest_text or '（见上文）'}\n\n"
                        "注意：结合全文理解；优先回应最新话题；"
                        "保持你的立场一致；不要模仿对方口吻；"
                        "不要重复自己刚说过的内容；"
                        "禁止把【语音转写】与无关文字（如外卖测评）混为一谈。"
                    ),
                }
            )

        text = self._call_api_messages(messages, temperature=0.72, max_tokens=120)
        reply = (text or "").strip().strip('"\'「」')
        if reply and _reply_conflates_voice_with_unrelated_text(reply, history):
            logger.warning(
                f"chat_reply: 语音与文字话题混淆，触发重写: {reply[:30]}"
            )
            latest_friend = friend_focus[-1] if friend_focus else {}
            latest_plain = _friend_text_plain(latest_friend)
            retry_messages = list(messages) + [
                {
                    "role": "user",
                    "content": (
                        f"（上一条不合适：禁止提外卖/肯德基/测评/蛋挞。"
                        f"只回复对方最新一句「{latest_plain or '见上文'}」，"
                        "10-30字，口语化。）"
                    ),
                }
            ]
            text = self._call_api_messages(retry_messages, temperature=0.5, max_tokens=80)
            reply = (text or "").strip().strip('"\'「」')
        if reply and _reply_conflates_voice_with_unrelated_text(reply, history):
            safe = _safe_reply_from_friend_focus(friend_focus)
            if safe:
                logger.info(f"chat_reply: 使用对方焦点兜底: {safe}")
                reply = safe
            else:
                logger.warning("chat_reply: 重写后仍混淆语音与文字话题，放弃发送")
                reply = ""
        if reply and _reply_invents_voice_content(reply, history):
            logger.warning(f"chat_reply: 编造语音内容，触发重写: {reply[:30]}")
            retry_messages = list(messages) + [
                {
                    "role": "user",
                    "content": (
                        "（上一条不合适：聊天记录里未转写的语音你不知道内容，"
                        "严禁编造「语音里说了xxx」。请按文字消息正常回复，"
                        "或简短说没听清/等会听。）"
                    ),
                }
            ]
            text = self._call_api_messages(retry_messages, temperature=0.55, max_tokens=120)
            reply = (text or "").strip().strip('"\'「」')
        if reply and _reply_invents_voice_content(reply, history):
            logger.warning("chat_reply: 重写后仍编造语音，放弃发送")
            reply = ""
        if reply and _reply_contradicts_self_role(history, reply):
            logger.warning(f"chat_reply: 立场矛盾，触发重写: {reply[:30]}")
            reply = ""
        if reply and _looks_like_fake_share(reply) and not _friend_mentioned_share_topic(
            history
        ):
            retry_messages = list(messages) + [
                {
                    "role": "user",
                    "content": "（上一条不合适，重写：不要提视频/链接/分享，只聊日常）",
                }
            ]
            text = self._call_api_messages(retry_messages, temperature=0.65, max_tokens=120)
            reply = (text or "").strip().strip('"\'「」')
        if reply and _reply_contradicts_self_role(history, reply):
            retry_messages = list(messages) + [
                {
                    "role": "user",
                    "content": (
                        "（立场错了：不要模仿对方口吻，不要把自己说过的话换个说法。"
                        "若你转过钱让对方买东西，对方说「好」，你应简短回应如「嗯你去吧」"
                        "而不是说「钱收到了我去买」。请重写。）"
                    ),
                }
            ]
            text = self._call_api_messages(retry_messages, temperature=0.6, max_tokens=120)
            reply = (text or "").strip().strip('"\'「」')
        if reply and not (
            _looks_like_fake_share(reply) and not _friend_mentioned_share_topic(history)
        ) and not _reply_contradicts_self_role(history, reply):
            if not _reply_conflates_voice_with_unrelated_text(reply, history):
                return reply

        if mode == "reply":
            safe = _safe_reply_from_friend_focus(friend_focus)
            if safe and not _reply_conflates_voice_with_unrelated_text(safe, history):
                logger.info(f"chat_reply: reply 模式兜底: {safe}")
                return safe
            return ""

        fallback = [
            "在呢，最近怎么样",
            "哈喽，在忙吗",
            "在吗，聊两句",
        ]
        return random.choice(fallback)

    @property
    def vision_available(self) -> bool:
        """是否具备可用的 Vision 识图配置。"""
        if getattr(settings, "HUNYUAN_API_KEY", "") and getattr(
            settings, "HUNYUAN_VISION_MODEL", ""
        ).strip():
            return True
        return bool(getattr(settings, "LLM_VISION_MODEL", "").strip())

    def probe_vision(self) -> tuple[bool, str]:
        """
        快速探测 Vision endpoint 是否可用（小图 + 短超时）。

        Returns:
            (ok, detail)
        """
        client = self._vision_client()
        if not client:
            return False, "LLM/混元 均未配置"
        if not self.vision_available:
            return False, (
                "未配置 Vision 模型。"
                "请在 .env 设置 HUNYUAN_VISION_MODEL=hy-vision-2.0-instruct "
                "或 LLM_VISION_MODEL=ep-xxx"
            )

        # 1x1 白图探针，避免大图超时
        tiny_b64 = (
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
            "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwh"
            "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAAR"
            "CAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAn/xAAUEAEAAAAAAAAAAAAAAAAA"
            "AAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMB"
            "AAIRAxEAPwCwAA//2Q=="
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{tiny_b64}"},
                    },
                    {"type": "text", "text": "回复 OK"},
                ],
            }
        ]
        model = self._vision_model()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=8,
                timeout=20,
            )
            _ = response.choices[0].message.content
            provider = "hunyuan" if self.vision_client else "llm"
            return True, f"{provider}/{model}"
        except Exception as e:
            err = str(e)
            if "NotFound" in err or "InvalidEndpointOrModel" in err or "400004" in err:
                return False, f"Vision endpoint 不存在或未开通: {model}"
            if "timed out" in err.lower() or "504" in err:
                return False, f"Vision endpoint 超时: {model}"
            return False, err[:160]

    def read_chat_messages_from_image(
        self,
        image_jpeg: bytes,
        contact_name: str = "",
    ) -> list[dict]:
        """
        多模态识图：从聊天页截图提取消息列表。

        Args:
            image_jpeg: 聊天区域 JPEG 字节（与 OCR 使用同一裁剪）
            contact_name: 好友昵称，用于过滤顶部标题

        Returns:
            [{"role": "self"|"friend", "text": "..."}, ...]
        """
        client = self._vision_client()
        if not client or not image_jpeg:
            return []

        b64 = base64.b64encode(image_jpeg).decode("ascii")
        contact_hint = f"好友昵称是「{contact_name}」。" if contact_name else ""
        prompt = f"""这是一张微信 1v1 聊天页截图（已裁剪掉输入栏）。
{contact_hint}
请识别屏幕上所有**聊天气泡里的文字消息**，从上到下按时间顺序输出。

规则：
1. 右侧绿色/白色气泡 → role 为 "self"（自己）
2. 左侧气泡 → role 为 "friend"（对方）
3. 忽略顶部标题栏、时间戳、系统提示、按钮文字（发送/语音/表情等）
4. 同一气泡内文字合并为一条
5. 只输出 JSON 数组，不要 markdown，例如：
[{{"role":"friend","text":"在吗"}},{{"role":"self","text":"在呢"}}]
"""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        model = self._vision_model()
        raw = self._call_api_vision(
            messages,
            model=model,
            temperature=0.2,
            max_tokens=800,
            client=client,
        )
        return self._parse_chat_messages_json(raw)

    def detect_voice_bubbles_from_image(
        self,
        image_jpeg: bytes,
        focus_side: str = "",
    ) -> list[dict]:
        """
        多模态识图：检测聊天截图中的语音气泡位置。

        Args:
            focus_side: 空=两侧都找；"friend"=只找左侧对方语音

        Returns:
            [{"role": "self"|"friend", "y_percent": 0.0~1.0, "duration": int|None}, ...]
        """
        client = self._vision_client()
        if not client or not image_jpeg:
            return []

        b64 = base64.b64encode(image_jpeg).decode("ascii")
        if focus_side == "friend":
            prompt = """这是一张微信 1v1 聊天页截图（已裁剪掉输入栏）。
请**只**找出左侧（对方）的**语音消息气泡**（白色气泡，左侧波形图标，右侧秒数 1-60 如 2''、4''），不要右侧自己的绿色语音。

规则：
1. 只输出左侧对方语音，role 必须为 "friend"
2. 忽略右侧绿色气泡、居中时间戳、纯文字气泡
3. y_percent 为气泡垂直中心在图片高度上的比例（0=顶部，1=底部）
4. 只输出 JSON 数组，无则 []，例如：
[{"role":"friend","y_percent":0.82,"duration":2}]
"""
        else:
            prompt = """这是一张微信 1v1 聊天页截图（已裁剪掉输入栏）。
请找出所有**语音消息气泡**（左侧或右侧带波形图标和秒数 1-60，如 3''、5''），不是纯文字气泡。

规则：
1. 左侧白色/绿色气泡 → role 必须为 "friend"（对方语音，重点找！）
2. 右侧绿色气泡 → role 为 "self"（自己语音）
3. 忽略居中灰色时间戳（如 晚上6:15、下午5:52）
4. y_percent 为气泡垂直中心在图片高度上的比例（0=顶部，1=底部）
5. 只输出 JSON 数组，无语音则 []，例如：
[{"role":"friend","y_percent":0.55,"duration":4},{"role":"self","y_percent":0.68,"duration":3}]
"""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        model = self._vision_model()
        raw = self._call_api_vision(
            messages,
            model=model,
            temperature=0.1,
            max_tokens=400,
            client=client,
        )
        return self._parse_voice_bubbles_json(raw)

    @staticmethod
    def _parse_voice_bubbles_json(raw: str) -> list[dict]:
        if not raw:
            return []
        text = raw.strip()
        try:
            data = json.loads(text)
        except Exception:
            m = re.search(r"\[[\s\S]*\]", text)
            if not m:
                return []
            try:
                data = json.loads(m.group(0))
            except Exception:
                return []
        if not isinstance(data, list):
            return []
        out: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "friend")
            if role not in ("self", "friend"):
                role = "friend"
            try:
                y_pct = float(item.get("y_percent", 0))
            except (TypeError, ValueError):
                y_pct = 0.0
            y_pct = max(0.0, min(1.0, y_pct))
            duration = item.get("duration")
            if duration is not None:
                try:
                    duration = int(duration)
                    if not (1 <= duration <= 60):
                        duration = None
                except (TypeError, ValueError):
                    duration = None
            out.append(
                {"role": role, "y_percent": y_pct, "duration": duration}
            )
        return out

    @staticmethod
    def _parse_chat_messages_json(raw: str) -> list[dict]:
        if not raw:
            return []
        text = raw.strip()
        try:
            data = json.loads(text)
        except Exception:
            m = re.search(r"\[[\s\S]*\]", text)
            if not m:
                return []
            try:
                data = json.loads(m.group(0))
            except Exception:
                return []
        if not isinstance(data, list):
            return []
        out: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                # youtu-vita 等可能返回纯字符串数组，当作 friend 兜底
                if isinstance(item, str) and item.strip():
                    out.append({"role": "friend", "text": item.strip()})
                continue
            role = item.get("role", "friend")
            msg = str(item.get("text", "")).strip()
            if not msg:
                continue
            if role not in ("self", "friend"):
                role = "friend"
            out.append({"role": role, "text": msg})
        return out

    def generate_deep_chat_turns(
        self,
        persona: dict,
        contact: str = "朋友",
        rounds: int = 5,
    ) -> list[str]:
        """生成多轮连续聊天内容（JSON 数组）。"""
        prompt = f"""你正在模拟微信用户与「{contact}」的一段自然聊天。

个人画像：
- 年龄：{persona.get('age', '25-35')}
- 城市：{persona.get('city', '北京')}
- 兴趣：{', '.join(persona.get('hobbies', ['日常']))}
- 风格：{persona.get('comment_style', '自然随意')}

请生成 {rounds} 条连续发送的消息，像真人断续聊天（不是一口气长文）。
只输出 JSON 数组，例如：["在吗", "最近忙啥", "周末有啥安排"]
要求：每条 5-40 字，口语化，不用句号结尾。
{_CHAT_AUTHENTICITY_RULES}
"""
        raw = self._call_api(prompt, temperature=0.82, max_tokens=400)
        if not raw:
            return []
        import json
        import re
        try:
            return [str(x) for x in json.loads(raw) if str(x).strip()]
        except Exception:
            pass
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            try:
                return [str(x) for x in json.loads(m.group(0)) if str(x).strip()]
            except Exception:
                pass
        # 按行兜底
        lines = [ln.strip("- •\t ") for ln in raw.splitlines() if ln.strip()]
        return lines[:rounds]

    def plan_daily_nurture(self, context: dict, persona: dict) -> str:
        """
        上帝视角：根据账号上下文输出当日养号动作 JSON。

        Returns:
            LLM 原始文本（期望含 actions 数组）
        """
        prompt = f"""你是微信养号系统的「上帝视角」调度器。根据账号状态编排**今天**的自动化动作。

## 账号上下文
{json.dumps(context, ensure_ascii=False, indent=2)}

## 人设
- 名称：{persona.get('name', '普通用户')}
- 行业偏好：{persona.get('industry', '通用生活')}
- 年龄：{persona.get('age')}
- 城市：{persona.get('city')}
- 兴趣：{', '.join(persona.get('hobbies', []))}
- 日常节奏：{persona.get('daily_routine', '')}

## 硬性规则（必须遵守）
1. 只能使用 allowed_actions 中的动作 type
2. 禁止 manual_forbidden / behavior_forbidden 中的动作（尤其群发、自动回复）
3. 遵守 hard_limits 与 add_friend_cap_today 当日上限（超过的不要排）
4. 活跃时间仅 07:00-23:00；必须包含一条 sleep；禁止凌晨频繁操作
5. 新号前期（day1_3）以社交种子培育为主；仅可按 seed_friends/手机号名单加好友，节奏固定为 Day1=1、Day2=2、Day3=2
5.0 Day1 专项只排三项核心：add_friend(count=1)、follow_public_account(count=2, industry_only)、read_article(duration≈600, comment_rate≥0.5)；不要额外排视频号/搜索/小程序/支付页
5.0a Day2-3 只排三项核心：add_friend(count=2)、follow_public_account(count=2, industry_only)、read_article(duration≈600, comment_rate≥0.5, require_comment=true)
5.0b Day4-7 只排 post_moment(topic=生活, smart_select=true) + play_mini_game(duration≈180, 不指定 game)；不要加好友/群聊/读文/视频号
5.0c Day8-10 只排 add_friend(count=3) + deep_chat×5(duration≥320) + moments_daily_interact(target_count=20)；不要群发/发圈
5.0d Day11 只排 scroll_channels(duration≈600, finish_watch=true, comment_rate≥0.15)
5.0e Day12-14 维持周活跃：browse_mini_program + 适量 post_moment（生活/工作 3:2）；每周小程序约 3 次
5.1 如果排 follow_public_account，优先 industry_public_accounts（行业相关），不要选泛新闻号，除非行业名单不足
6. 首周加好友不得超过 3 人/天；day4_7 相位不要排 add_friend；day1_3 只能排当日上限内的 add_friend，且不得出现发圈、深聊、群聊、朋友圈点赞评论
7. 前 14 天禁止群发（含 send_message 多目标/mass/broadcast）、禁止自动回复
8. 动作数量建议 8-14 个，时间窗不要全部重叠在同一小时
9. 如果 mode=consume_only 或 state=cooldown，只排浏览类（刷朋友圈/视频号/读文章/搜索/收藏/小程序/小游戏/打开支付页）；视频号 params 须 comment_rate=0
10. 如果 recent_fails 里某动作连续失败，今天减少或避开该动作
11. 视频号 scroll_channels 每日合计约 10 分钟：params 建议 {{"duration": 600, "finish_watch": true, "like_rate": 0.2, "comment_rate": 0.18}}（前期 day1_3 的 comment_rate 用 0）
12. day4_7 / day11_14 可排 play_mini_game：走发现→游戏→找游戏→立即玩，params 建议 {{"duration": 180}}（不要填 game，避免改走游戏名搜索）
13. 遵守 behavior_taboos 列表中的全部禁忌

## 输出格式
只输出 JSON（不要 markdown）：
{{
  "phase": "day1_3|day4_7|day8_10|day11_14|post_14",
  "rationale": "一句话说明今天策略",
  "actions": [
    {{
      "type": "scroll_moments",
      "window": ["08:00", "09:00"],
      "duration": [300, 600],
      "params": {{}}
    }}
  ]
}}
"""
        return self._call_api(prompt, temperature=0.55, max_tokens=1800)

    # ================================================================
    # 内部方法
    # ================================================================

    def _parse_json_object(self, raw: str) -> dict:
        """从 LLM 输出中提取 JSON 对象。"""
        text = (raw or "").strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            pass
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _build_post_prompt(self, persona: dict, topic: str) -> str:
        """构建朋友圈文案 prompt"""
        return f"""你是一个真实微信用户，以下是你的个人画像：
- 年龄：{persona.get('age', '25-35')}岁
- 城市：{persona.get('city', '北京')}
- 兴趣爱好：{', '.join(persona.get('hobbies', ['美食', '旅行', '阅读']))}
- 发圈风格：{persona.get('post_style', '随性简短')}
- 常发主题：{', '.join(persona.get('topics', ['日常']))}

请用真实自然的口吻，写一条朋友圈文案（30-100字），主题是"{topic}"。

要求：
- 不要用 emoji 堆砌（最多2个）
- 不要太正式或太文艺
- 像普通人随手发的，有口语化表达
- 不要提任何敏感话题或政治内容
- 可以偶尔有小错别字（体现真实感，但不要太刻意）
"""

    def _call_api_messages(
        self,
        messages: list[dict],
        temperature: float = 0.9,
        max_tokens: int = 200,
    ) -> str:
        """多轮 messages 调用 LLM。"""
        if not self.client:
            logger.warning("LLM 客户端未配置 API Key，返回空字符串")
            return ""

        adjusted_temp = temperature + random.uniform(-0.05, 0.05)
        adjusted_temp = max(0.1, min(1.5, adjusted_temp))

        timeout_s = getattr(settings, "LLM_TIMEOUT", 45)
        retry_times = getattr(settings, "LLM_RETRY_TIMES", 0)
        attempts = max(1, int(retry_times) + 1)

        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self._text_model(),
                    messages=messages,
                    temperature=adjusted_temp,
                    max_tokens=max_tokens,
                    timeout=timeout_s,
                )
                text = response.choices[0].message.content.strip()
                self._record_hash(text)
                return text
            except Exception as e:
                last_error = e
                err = str(e).lower()
                # 只对“超时/请求超时”这类波动重试，避免把参数/鉴权错误拖长
                should_retry = ("timed out" in err) or ("timeout" in err) or ("request timed out" in err)
                if attempt < attempts - 1 and should_retry:
                    # 轻微指数退避，抖动更容易恢复
                    sleep_s = min(10.0, 2.0 * (attempt + 1) + random.uniform(0, 1.0))
                    logger.warning(f"LLM 调用超时，准备重试 {attempt+1}/{attempts}，等待 {sleep_s:.1f}s: {e}")
                    time.sleep(sleep_s)
                    continue

                logger.error(f"LLM API 调用失败: {e}")
                return ""

        # 理论上走不到这里；兜底返回空串
        logger.error(f"LLM API 调用失败（多次重试后仍失败）: {last_error}")
        return ""

    def _call_api_vision(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 800,
        client: Optional[OpenAI] = None,
    ) -> str:
        """多模态 vision 调用。"""
        api = client or self._vision_client()
        if not api:
            return ""

        adjusted_temp = max(0.1, min(1.0, temperature))
        timeout_s = getattr(settings, "LLM_VISION_TIMEOUT", getattr(settings, "LLM_TIMEOUT", 60))
        retry_times = getattr(settings, "LLM_RETRY_TIMES", 0)
        attempts = max(1, int(retry_times) + 1)

        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = api.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=adjusted_temp,
                    max_tokens=max_tokens,
                    timeout=timeout_s,
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                last_error = e
                err = str(e).lower()
                should_retry = ("timed out" in err) or ("timeout" in err) or ("504" in err)
                if attempt < attempts - 1 and should_retry:
                    sleep_s = min(10.0, 2.0 * (attempt + 1) + random.uniform(0, 1.0))
                    logger.warning(f"Vision 调用超时，重试 {attempt+1}/{attempts}: {e}")
                    time.sleep(sleep_s)
                    continue
                logger.error(f"Vision API 调用失败: {e}")
                return ""

        logger.error(f"Vision API 调用失败（多次重试后仍失败）: {last_error}")
        return ""

    def _call_api(
        self,
        prompt: str,
        temperature: float = 0.9,
        max_tokens: int = 200,
    ) -> str:
        """
        调用 LLM API。

        Args:
            prompt: 完整 prompt
            temperature: 温度参数（含微小随机调整）
            max_tokens: 最大输出 token

        Returns:
            LLM 返回的文本，失败返回空字符串
        """
        if not self.client:
            logger.warning("LLM 客户端未配置 API Key，返回空字符串")
            return ""

        # temperature 微调，进一步增加多样性
        adjusted_temp = temperature + random.uniform(-0.05, 0.05)
        adjusted_temp = max(0.1, min(1.5, adjusted_temp))

        timeout_s = getattr(settings, "LLM_TIMEOUT", 45)
        retry_times = getattr(settings, "LLM_RETRY_TIMES", 0)
        attempts = max(1, int(retry_times) + 1)

        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self._text_model(),
                    messages=[{"role": "user", "content": prompt}],
                    temperature=adjusted_temp,
                    max_tokens=max_tokens,
                    timeout=timeout_s,
                )
                text = response.choices[0].message.content.strip()

                # 记录哈希用于同质化检测
                self._record_hash(text)

                return text
            except Exception as e:
                last_error = e
                err = str(e).lower()
                should_retry = ("timed out" in err) or ("timeout" in err) or ("request timed out" in err)
                if attempt < attempts - 1 and should_retry:
                    sleep_s = min(10.0, 2.0 * (attempt + 1) + random.uniform(0, 1.0))
                    logger.warning(f"LLM 调用超时，准备重试 {attempt+1}/{attempts}，等待 {sleep_s:.1f}s: {e}")
                    time.sleep(sleep_s)
                    continue

                logger.error(f"LLM API 调用失败: {e}")
                return ""

        logger.error(f"LLM API 调用失败（多次重试后仍失败）: {last_error}")
        return ""

    def _record_hash(self, text: str):
        """记录内容哈希，用于后续同质化检测"""
        if text:
            h = hashlib.md5(text.encode()).hexdigest()
            self._history_hashes.append(h)
            # 只保留最近 500 条记录
            if len(self._history_hashes) > 500:
                self._history_hashes = self._history_hashes[-500:]

    def check_homogeneity(self) -> dict:
        """
        检查生成内容的同质化程度。

        Returns:
            {"total": int, "unique": int, "dup_ratio": float}
        """
        if not self._history_hashes:
            return {"total": 0, "unique": 0, "dup_ratio": 0.0}

        total = len(self._history_hashes)
        unique = len(set(self._history_hashes))
        dup_ratio = 1.0 - (unique / total)
        return {"total": total, "unique": unique, "dup_ratio": dup_ratio}

    def _ensure_variety(self, text: str, persona: dict) -> str:
        """检查内容是否过于重复，必要时回退到模板"""
        if not text:
            return ""
        h = hashlib.md5(text.encode()).hexdigest()
        recent_count = sum(1 for past in self._history_hashes[-20:] if past == h)
        if recent_count >= 2:
            logger.debug("检测到内容重复，但保留（LLM 多样性在合理范围）")
        return text
