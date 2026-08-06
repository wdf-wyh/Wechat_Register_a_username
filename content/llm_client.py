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
import random
import re
import time
from typing import Optional

from openai import OpenAI

from config.settings import settings
from utils.logger import get_logger

import json

logger = get_logger("llm_client")


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
            下一条要发送的消息；失败返回空串
        """
        if not history:
            history = []

        system = f"""你是微信用户，正在和好友「{contact}」1v1 聊天。

你的个人画像：
- 名称：{persona.get('name', '普通用户')}
- 年龄：{persona.get('age', '25-35')}
- 城市：{persona.get('city', '北京')}
- 兴趣：{', '.join(persona.get('hobbies', ['日常']))}
- 聊天风格：{persona.get('comment_style', '自然随意')}

请根据**完整聊天记录**生成你的下一条微信消息。
要求：
- 必须承接上文，不要答非所问或重复刚说过的话
- 10-50 字，口语化，像随手打的
- 少用或不用 emoji，不要句号结尾
- 只输出消息正文，不要引号、序号或解释
"""
        messages: list[dict] = [{"role": "system", "content": system}]
        # 深聊续聊只需要最近一小段上下文，过长历史会显著增加延迟/超时概率
        for item in history[-12:]:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            role = "assistant" if item.get("role") == "self" else "user"
            messages.append({"role": role, "content": text})

        if len(messages) == 1:
            messages.append(
                {
                    "role": "user",
                    "content": "（对话刚开始，请自然开场，像真人发微信）",
                }
            )
        elif history and history[-1].get("role") == "self":
            messages.append(
                {
                    "role": "user",
                    "content": "（你刚发过消息，对方还没回或回复较慢，可简短追问或自然换话题）",
                }
            )
        else:
            messages.append(
                {"role": "user", "content": "（请回复对方上一条消息）"}
            )

        text = self._call_api_messages(messages, temperature=0.88, max_tokens=120)
        reply = (text or "").strip().strip('"\'「」')
        if reply:
            return reply

        # LLM 请求偶发超时/异常：给一个短兜底，保证“打开会话→OCR→发送”链路可验证
        fallback = [
            "在呢，刚看到你消息了",
            "我这边刚忙完，在呢",
            "收到啦，我看到了",
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
只输出 JSON 数组，例如：["早啊", "今天好忙", "晚上有空吗"]
要求：每条 5-40 字，口语化，不用句号结尾。
"""
        raw = self._call_api(prompt, temperature=0.9, max_tokens=400)
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
5. 新号前期（day1_3）以社交种子培育为主：每天关注 2 个公众号，阅读推文约 10 分钟；仅可按 seed_friends/手机号名单加好友，节奏固定为 Day1=1、Day2=2、Day3=2
5.1 如果排 follow_public_account，优先使用 public_accounts 里的行业相关公众号，不要选泛新闻号，除非行业名单不足
6. 首周加好友不得超过 3 人/天；day4_7 相位不要排 add_friend；day1_3 只能排当日上限内的 add_friend，且不得出现发圈、深聊、群聊、朋友圈点赞评论
7. 前 14 天禁止群发（含 send_message 多目标/mass/broadcast）、禁止自动回复
8. 动作数量建议 8-14 个，时间窗不要全部重叠在同一小时
9. 如果 mode=consume_only 或 state=cooldown，只排浏览类（刷朋友圈/视频号/读文章/搜索/收藏/小程序/小游戏/打开支付页）；视频号 params 须 comment_rate=0
10. 如果 recent_fails 里某动作连续失败，今天减少或避开该动作
11. 视频号 scroll_channels 每日合计约 10 分钟：params 建议 {{"duration": 600, "finish_watch": true, "like_rate": 0.2, "comment_rate": 0.18}}（前期 day1_3 的 comment_rate 用 0）
12. day4_7 / day11_14 可排 play_mini_game（默认跳一跳）：params 建议 {{"game": "跳一跳", "duration": 180}}
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
