"""
搜索关键词库 — 用于模拟真实用户的搜索行为。

按类别组织，配合不同人格画像选择对应的搜索主题。

使用方式:
    from content.search_keywords import SearchKeywordManager
    mgr = SearchKeywordManager()
    keyword = mgr.get_random_keyword(persona)
"""

import random
from typing import Optional


class SearchKeywordManager:
    """搜索关键词管理器"""

    KEYWORDS: dict[str, list[str]] = {
        "生活": [
            "天气预报",
            "快递查询",
            "附近美食",
            "地铁线路图",
            "油价",
        ],
        "资讯": [
            "今日热点",
            "财经新闻",
            "科技资讯",
            "体育新闻",
            "娱乐新闻",
        ],
        "学习": [
            "英语学习",
            "Excel 教程",
            "摄影技巧",
            "菜谱大全",
            "健身教程",
        ],
        "娱乐": [
            "好看的电影推荐",
            "热门综艺",
            "今晚开播的剧",
            "减肥食谱",
            "周末去哪玩",
        ],
        "小程序": [
            "美团外卖",
            "滴滴出行",
            "拼多多",
            "京东购物",
            "大众点评",
        ],
        "小游戏": [
            "跳一跳",
            "欢乐斗地主",
            "天天象棋",
            "羊了个羊",
        ],
        "购物": [
            "最近有什么好用的",
            "手机最新款",
            "耳机推荐",
            "性价比高的",
        ],
    }

    def get_random_keyword(
        self,
        persona: Optional[dict] = None,
        category: str = None,
    ) -> str:
        """
        获取随机搜索关键词。

        Args:
            persona: 人格档案
            category: 指定类别，None 自动匹配人格

        Returns:
            搜索关键词
        """
        if category is None and persona:
            # 根据人格 topics 选择类别
            topics = persona.get("topics", [])
            for cat in self.KEYWORDS:
                for topic in topics:
                    if cat in topic or topic in cat:
                        category = cat
                        break
                if category:
                    break

        if category is None:
            category = random.choice(list(self.KEYWORDS.keys()))

        keywords = self.KEYWORDS.get(category, self.KEYWORDS["生活"])
        return random.choice(keywords)

    def get_keywords_batch(self, count: int = 3) -> list[str]:
        """批量获取关键词"""
        all_keywords = []
        for kw_list in self.KEYWORDS.values():
            all_keywords.extend(kw_list)
        return random.sample(all_keywords, min(count, len(all_keywords)))
