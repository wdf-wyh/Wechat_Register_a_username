"""
聊天话术模板库 — 用于自动化聊天时生成自然的对话内容。

按场景分类：问候、回复、分享、闲聊、结束等。
配合 LLM 可进一步扩展多样性。

使用方式:
    from content.chat_templates import ChatTemplateManager
    mgr = ChatTemplateManager()
    text = mgr.get_random_chat("greeting", persona)
"""

import random
from typing import Optional


class ChatTemplateManager:
    """聊天话术管理器"""

    # 按场景分类的话术模板
    TEMPLATES: dict[str, list[str]] = {
        "greeting": [
            "早啊～",
            "最近怎么样？",
            "好久没聊了",
            "周末有空吗？",
            "在干嘛呢",
            "最近怎么样，好久没聊了",
        ],
        "reply": [
            "哈哈，确实",
            "有道理",
            "没事没事",
            "我也觉得是这样",
            "那挺好啊",
            "这个可以有",
            "等一下我看下啊",
            "可以的可以的",
        ],
        "share": [
            "看到这个笑得不行 🤣",
            "刚发现的一家店，有空一起去",
            "刚发现一家不错的店，你有空吗",
            "突然想到你，上次说的事怎么样了？",
            "对了，你那边最近怎么样",
        ],
        "small_talk": [
            "今天天气真的奇怪，一会冷一会热的",
            "又到饭点了，不知道吃啥",
            "明天终于周五了",
            "最近睡眠不好，不知道是不是年纪大了",
            "地铁上刚才发生了一件社死的事",
        ],
        "ending": [
            "先不说了，我有点事",
            "晚点再聊～",
            "去吃饭了，回聊",
            "先忙一会，晚点找你",
            "好的，那就这样",
        ],
    }

    def get_random_chat(
        self,
        scene: str = "small_talk",
        persona: Optional[dict] = None,
    ) -> str:
        """
        获取随机聊天话术。

        Args:
            scene: 场景类型 (greeting/reply/share/small_talk/ending)
            persona: 人格档案（可选，用于风格调整）

        Returns:
            聊天文本
        """
        templates = self.TEMPLATES.get(scene, self.TEMPLATES["small_talk"])
        return random.choice(templates)

    def get_conversation_chain(
        self,
        turns: int = 4,
        persona: Optional[dict] = None,
    ) -> list[tuple[str, str]]:
        """
        生成一段多轮对话。

        Args:
            turns: 对话轮数
            persona: 人格档案

        Returns:
            [(scene, text), ...] 对话序列
        """
        scenes = ["greeting", "small_talk", "reply", "share", "small_talk", "ending"]
        chain = []
        for i in range(min(turns, len(scenes))):
            scene = scenes[i % len(scenes)]
            text = self.get_random_chat(scene, persona)
            chain.append((scene, text))
        return chain

    def get_scenes(self) -> list[str]:
        """获取所有场景类型"""
        return list(self.TEMPLATES.keys())
