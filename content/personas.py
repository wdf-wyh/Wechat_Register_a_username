"""
账号人格档案 — 每个微信号有独立的"人设"，用于指导行为风格和内容生成。

不同账号拥有不同的人格特征，确保内容风格差异化，避免同质化检测。
"""

import random

# ================================================================
# 预设人格模板
# ================================================================

PERSONAS = [
    {
        "id": "p01",
        "name": "职场白领-北京",
        "industry": "互联网科技",
        "age": 28,
        "city": "北京",
        "gender": "男",
        "hobbies": ["跑步", "咖啡", "科技资讯", "股票"],
        "post_style": "简洁干练，偶尔吐槽通勤和工作",
        "comment_style": "直接简短，偶尔正经",
        "daily_routine": "早通勤刷朋友圈，午休看公众号，晚上偶尔发心情",
        "topics": ["互联网", "跑步打卡", "咖啡探店", "通勤吐槽", "数码产品"],
        # 冷启动种子资源（人工预填；空则对应动作自动跳过）
        # seed_friends 可直接填写手机号/微信号列表，Day1-3 会按 1/2/2 节奏依次添加。
        "seed_friends": [
            "13346396313",
            "furenlanjie118",
            "yqflanjie666",
            "furenlanjie711",
            "dajiang3790",
        ],
        "seed_groups": [],
        "moments_big_v": ["课程小助手"],
        "industry_public_accounts": ["课程小助手", "极客公园", "爱范儿", "少数派", "虎嗅", "InfoQ"],
        "public_accounts": ["人民日报", "央视新闻", "36氪", "极客公园", "爱范儿", "少数派"],
    },
    {
        "id": "p02",
        "name": "文艺青年-上海",
        "industry": "内容消费与生活方式",
        "age": 26,
        "city": "上海",
        "gender": "女",
        "hobbies": ["美食", "摄影", "看展", "旅行"],
        "post_style": "图片为主，文字文艺但不矫情",
        "comment_style": "热情赞美，喜欢用表情",
        "daily_routine": "上午刷朋友圈，下午分享美食照，晚上看展/社交",
        "topics": ["美食探店", "展览打卡", "胶片摄影", "周末旅行", "咖啡拉花"],
        "seed_friends": [],
        "seed_groups": [],
        "industry_public_accounts": ["一条", "三联生活周刊", "理想国", "豆瓣", "Lens", "凤凰WEEKLY"],
        "public_accounts": ["下厨房", "一条", "理想国", "豆瓣", "小红书精选", "三联生活周刊"],
    },
    {
        "id": "p03",
        "name": "宅男-广州",
        "industry": "游戏动漫",
        "age": 24,
        "city": "广州",
        "gender": "男",
        "hobbies": ["游戏", "动漫", "宠物", "外卖评测"],
        "post_style": "随意口语化，偶尔发猫图",
        "comment_style": "搞笑调侃，爱用梗",
        "daily_routine": "上午消失了，下午刷视频号，晚上打游戏间歇刷朋友圈",
        "topics": ["猫", "新游戏", "外卖踩雷", "熬夜", "二次元"],
        "seed_friends": [],
        "seed_groups": [],
        "industry_public_accounts": ["机核网", "游研社", "电玩巴士", "动漫之家", "手游那点事", "JumpxSwitch"],
        "public_accounts": ["机核网", "游研社", "电玩巴士", "哔哩哔哩弹幕网", "动漫之家", "猫奴日常"],
    },
    {
        "id": "p04",
        "name": "宝妈-成都",
        "industry": "母婴育儿",
        "age": 32,
        "city": "成都",
        "gender": "女",
        "hobbies": ["育儿", "美食", "追剧", "DIY手工"],
        "post_style": "带娃日常，有人间烟火气",
        "comment_style": "关心型，习惯性夸别人家的娃",
        "daily_routine": "娃午睡时刷手机，晚上娃睡后追剧+朋友圈",
        "topics": ["带娃日常", "家常菜", "追剧推荐", "亲子游", "幼儿园"],
        "seed_friends": [],
        "seed_groups": [],
        "industry_public_accounts": ["丁香妈妈", "年糕妈妈", "宝宝树", "凯叔讲故事", "妈妈网", "父母堂"],
        "public_accounts": ["丁香妈妈", "父母必读", "下厨房", "年糕妈妈", "凯叔讲故事", "宝宝树"],
    },
    {
        "id": "p05",
        "name": "大学生-武汉",
        "industry": "校园成长",
        "age": 20,
        "city": "武汉",
        "gender": "女",
        "hobbies": ["追星", "奶茶", "拍照", "综艺"],
        "post_style": "活泼有趣，emoji 用得多但不堆砌",
        "comment_style": "热情互动，经常评论好友自拍",
        "daily_routine": "课间必刷手机，晚上刷视频号 + 和同学聊天",
        "topics": ["考试吐槽", "奶茶测评", "综艺安利", "校园日常", "自拍"],
        "seed_friends": [],
        "seed_groups": [],
        "industry_public_accounts": ["武大青年", "考研政治", "GQ实验室", "新世相", "槽值", "中国大学生在线"],
        "public_accounts": ["毒舌电影", "GQ实验室", "新世相", "丁香医生", "考研政治", "武大青年"],
    },
    {
        "id": "p06",
        "name": "中年商务-深圳",
        "industry": "财经管理",
        "age": 42,
        "city": "深圳",
        "gender": "男",
        "hobbies": ["喝茶", "财经", "高尔夫", "职场管理"],
        "post_style": "成熟稳重，基本只转发行业文章，少发原创",
        "comment_style": "得体简短，基本不评论私人内容",
        "daily_routine": "早上看公众号，午间刷朋友圈，晚上偶尔点赞",
        "topics": ["行业趋势", "管理心得", "经济观察", "茶文化", "健康"],
        "seed_friends": [],
        "seed_groups": [],
        "industry_public_accounts": ["华尔街见闻", "第一财经", "虎嗅", "清华管理评论", "笔记侠", "创业邦"],
        "public_accounts": ["华尔街见闻", "第一财经", "虎嗅", "得到", "清华管理评论", "南方周末"],
    },
    {
        "id": "p07",
        "name": "自由职业-杭州",
        "industry": "摄影文旅",
        "age": 29,
        "city": "杭州",
        "gender": "男",
        "hobbies": ["摄影", "户外徒步", "音乐节", "精酿啤酒"],
        "post_style": "随性自由，照片质感和文案不刻意",
        "comment_style": "自然随意，喜欢互动但不频繁",
        "daily_routine": "作息不固定，活动分布在全天，周末更活跃",
        "topics": ["徒步路线", "摄影作品", "音乐节现场", "小众酒吧", "vlog"],
        "seed_friends": [],
        "seed_groups": [],
        "industry_public_accounts": ["摄影之友", "穷游网", "音乐先声", "周末去哪儿", "户外探险outdoor", "Feekr旅行"],
        "public_accounts": ["摄影之友", "穷游网", "音乐先声", "杭州本地宝", "周末去哪儿", "精酿啤酒"],
    },
    {
        "id": "p08",
        "name": "退休阿姨-南京",
        "industry": "健康生活",
        "age": 58,
        "city": "南京",
        "gender": "女",
        "hobbies": ["广场舞", "养生", "旅游团", "晒娃(孙辈)"],
        "post_style": "热情淳朴，喜欢发早安鸡汤和风景照",
        "comment_style": "热情点赞型，每条都赞，偶尔评论'真好看'",
        "daily_routine": "早上 6 点发早安，上午刷群聊，晚上广场舞完发合照",
        "topics": ["养生知识", "广场舞", "旅游打卡", "孙辈照片", "菜谱分享"],
        "seed_friends": [],
        "seed_groups": [],
        "industry_public_accounts": ["养生中国", "丁香医生", "美食天下", "旅游卫视", "健康时报", "南京发布"],
        "public_accounts": ["养生中国", "丁香医生", "央视新闻", "南京发布", "美食天下", "旅游卫视"],
    },
]


def get_persona(persona_id: str) -> dict | None:
    """
    根据 ID 获取人格档案。

    Args:
        persona_id: 人格 ID

    Returns:
        人格字典，未找到返回 None
    """
    for p in PERSONAS:
        if p["id"] == persona_id:
            return p
    return None


def random_persona(seed: int = None) -> dict:
    """
    随机选择一个未使用的人格。

    Args:
        seed: 随机种子（用于可复现的选择）

    Returns:
        人格字典
    """
    rng = random.Random(seed) if seed else random
    return rng.choice(PERSONAS).copy()


def get_moments_big_v_candidates(persona: dict) -> list[str]:
    """
    朋友圈优先互动的大 V 名单（秒评目标）。

    合并 persona.moments_big_v、public_accounts、industry_public_accounts，去重保序。
    """
    persona = persona or {}
    merged: list[str] = []
    for key in ("moments_big_v", "industry_public_accounts", "public_accounts"):
        for name in persona.get(key, []) or []:
            text = str(name).strip()
            if text and text not in merged:
                merged.append(text)
    return merged


def get_public_account_candidates(persona: dict, count: int | None = None) -> list[str]:
    """
    返回公众号候选名单。

    优先行业垂类账号，再补通用账号，自动去重并保序。
    """
    persona = persona or {}
    merged: list[str] = []
    for key in ("industry_public_accounts", "public_accounts"):
        for name in persona.get(key, []) or []:
            text = str(name).strip()
            if text and text not in merged:
                merged.append(text)
    if count is None:
        return merged
    return merged[: max(0, int(count))]
