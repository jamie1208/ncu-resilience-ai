"""示範模式的虛構資料（P9-T01，ADR-0022）。

所有內容都是虛構的：不含真實人名、店家、電話或學號。日期以「種子日往前幾天」（`ago`）表示，
每次播種都以當天為準，讓「近 90 天」「近 4 週」這類時間窗永遠有資料。

涵蓋的情境：
- 主系列：腳踏車失竊（41 件）。改善前宿舍區車棚密集失竊；56 天前開始加裝監視器與照明、
  35 天前完成，前後 8 週的每週件數明顯下降；竊案轉移到總圖前車棚，9 天前一晚 5 件觸發突增。
  另含地點別名「女十四舍車棚」→「宿舍區車棚」、匿名回報、改善後再發生、推測原因經人工確認、
  其餘建議的效益評估與 What-if；
- 系列 B 宿舍熱水中斷（改善完成後再發生，觸發再發生提醒）；
- 系列 C 自習室財物失竊（同一事件三篇來源：官方公告與兩則目擊）；
- 系列 D 餐廳用餐後腸胃不適（未查證與官方查證並存）；
- 系列 E 校園網路中斷（官方公告，改善措施走完整個流程）；
- 單一事件：火災傳言經官方澄清為不實、颱風停課、車禍封路、道路坍方、化學品外洩、
  疑似指控店家（公開時提醒並附一鍵下架）。

模型的回應不寫在這裡：`responder.py` 依這份資料與 Skill 的輸入組出合法的輸出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Risk = Literal["high", "medium", "low"]
Claim = Literal["official", "first_hand", "hearsay", "rumor"]
Stance = Literal["supports", "refutes"]

RISK_SCORES: dict[Risk, dict[str, int]] = {
    "high": {
        "personal_safety": 70, "operations": 60, "property": 60, "reputation": 50, "scope": 60,
    },
    "medium": {
        "personal_safety": 30, "operations": 30, "property": 50, "reputation": 20, "scope": 30,
    },
    "low": {"personal_safety": 5, "operations": 30, "property": 5, "reputation": 5, "scope": 20},
}  # fmt: skip


@dataclass(frozen=True)
class EventSpec:
    """一個虛構事件：S02 抽取、S03 分類、S06 風險、S15 文案與 S11 系列的劇本。

    事件建立時間取第一篇文章的發布時間，與 `occurred_ago` 的差距就是 CRI 的發現延遲。
    """

    key: str
    title: str
    """S02 抽出的標題（S15 不執行時使用）。"""
    bulletin_title: str
    bulletin_summary: str
    category: str
    location: str | None
    occurred_ago: int
    risk: Risk
    series: str | None = None
    related_to: str | None = None
    """與哪個事件是「相關但不同」的事件（S04 related_event）。"""
    mentions_person: bool = False


@dataclass(frozen=True)
class Article:
    """一篇虛構的來源文章；`event` 為 None 時 S02 判斷不是事件。"""

    key: str
    source: str
    ago: int
    text: str
    event: str | None
    claim: Claim = "first_hand"
    stance: Stance = "supports"


@dataclass(frozen=True)
class Finding:
    """系列分析的一項規律或推測原因；`events` 為 None 表示系列中的全部事件。"""

    description: str
    events: tuple[str, ...] | None = None
    to_verify: str | None = None


@dataclass(frozen=True)
class Measure:
    """系列分析的一項改善建議。"""

    action: str
    audience: Literal["students", "school", "units"]
    horizon: Literal["short", "mid", "long"]
    priority: Literal["high", "medium", "low"]
    kind: Literal["respond", "investigate", "prevent"]
    cause: str | None = None
    unit: str | None = None


@dataclass(frozen=True)
class SeriesSpec:
    """一個系列的 S12 劇本。標題不可含地點與日期（業務驗證）。"""

    key: str
    title: str
    summary: str
    patterns: tuple[Finding, ...] = ()
    causes: tuple[Finding, ...] = ()
    measures: tuple[Measure, ...] = ()
    burst_explanation: str = "這幾週通報集中，件數明顯高於平常。"


@dataclass(frozen=True)
class ActionSpec:
    """事件層級的改善措施與狀態時間軸：（狀態, 種子日往前幾天）。"""

    event: str
    title: str
    owner: str
    steps: tuple[tuple[str, int], ...]
    created_ago: int


@dataclass(frozen=True)
class SeriesActionSpec:
    """系列層級的改善措施；`measure_index` 有值時從系列分析的第幾項建議建立（標題用建議）。"""

    series: str
    title: str | None
    owner: str
    steps: tuple[tuple[str, int], ...]
    created_ago: int
    measure_index: int | None = None


@dataclass(frozen=True)
class AnonymousSpec:
    """一則匿名回報；`article` 是它進入管線後對應的文章劇本。"""

    ago: int
    content: str
    location: str | None
    article: Article


@dataclass(frozen=True)
class Dataset:
    events: tuple[EventSpec, ...]
    articles: tuple[Article, ...]
    series: tuple[SeriesSpec, ...]
    actions: tuple[ActionSpec, ...]
    anonymous: tuple[AnonymousSpec, ...]
    aliases: dict[str, str] = field(default_factory=dict)
    """S13：新地點名稱 → 既有代表地點名稱。"""


# ---------- 主系列：腳踏車失竊 ----------

_BIKE = "bike"
DORM = "宿舍區車棚"
LIBRARY = "總圖前車棚"

IMPROVEMENT_STARTED_AGO = 56
"""主系列的改善措施「宿舍區車棚加裝監視器與照明」開始處理的日子（前後各 8 週比較的分界）。"""


@dataclass(frozen=True)
class _Bike:
    """一件腳踏車失竊：代號、發生日、發文日、地點、來源、標題、快報說明、原文。"""

    key: str
    occurred_ago: int
    ago: int
    location: str
    source: str
    title: str
    summary: str
    text: str
    risk: Risk = "medium"
    related_to: str | None = None
    extra: tuple[tuple[str, str, int, str], ...] = ()
    """同一事件的其他來源：（文章代號, 來源, 發文日, 原文）。"""


def _claim(source: str) -> Claim:
    return "official" if source.startswith("ncu-") else "first_hand"


# 改善前（56 天前以前）宿舍區車棚密集失竊，每週約 2–3 件；56 天前開始加裝監視器與照明、
# 35 天前完成後，宿舍區車棚明顯減少，但竊案轉移到總圖前車棚（9 天前一晚 5 件，觸發突增）。
BIKES: tuple[_Bike, ...] = (
    _Bike("k1", 118, 117, DORM, "facebook", "宿舍區車棚腳踏車失竊（藍色淑女車）",
          "宿舍區車棚一台藍色淑女車不見了，車主只鎖了前輪。",
          "我停在宿舍區車棚兩天的藍色淑女車不見了，那時候只鎖前輪，有人看到嗎？"),
    _Bike("k2", 114, 113, DORM, "facebook", "宿舍區車棚又有腳踏車被偷",
          "宿舍區車棚又有腳踏車在夜裡被偷，車棚的燈好像是壞的。",
          "昨晚停在宿舍區車棚的腳踏車早上就不見了，車棚那盞燈根本不會亮。"),
    _Bike("k3", 110, 109, "工程五館車棚", "facebook", "工程五館車棚腳踏車失竊",
          "工程五館車棚有腳踏車在上課期間被偷。",
          "上完課出來，停在工程五館車棚的腳踏車就不見了，鎖還丟在地上。"),
    _Bike("k4", 107, 106, DORM, "facebook", "宿舍區車棚腳踏車整台被搬走",
          "宿舍區車棚一台只鎖輪子的腳踏車，被整台搬走。",
          "宿舍區車棚我的車只鎖輪子，結果整台被搬走，只剩地上的鎖。"),
    _Bike("k5", 104, 102, DORM, "ncu-osa-life", "宿舍區車棚腳踏車失竊，生輔組提醒",
          "生輔組公告宿舍區車棚近期腳踏車失竊增加，提醒把車鎖在固定物上。",
          "生活輔導組公告：宿舍區車棚近期腳踏車失竊案件增加，請將車輛鎖在固定物上。"),
    _Bike("k6", 101, 100, "女十四舍車棚", "facebook", "女十四舍車棚腳踏車失竊",
          "女十四舍旁的車棚有腳踏車被偷，車主早上才發現。",
          "女十四舍車棚我的腳踏車不見了，早上要去上課才發現，好煩。"),
    _Bike("k7", 99, 98, DORM, "facebook", "宿舍區車棚腳踏車連車燈一起不見",
          "宿舍區車棚一台裝了車燈的腳踏車整台不見。",
          "宿舍區車棚又一台，連剛裝的車燈一起不見，這個月第幾台了。"),
    _Bike("k8", 96, 95, "依仁堂前", "facebook", "依仁堂前腳踏車失竊",
          "依仁堂前停放的腳踏車在晚上被偷。",
          "晚上去依仁堂打球，出來停在門口的腳踏車就沒了。"),
    _Bike("k9", 94, 93, DORM, "facebook", "宿舍區車棚兩台腳踏車同晚失竊",
          "宿舍區車棚同一晚有兩台腳踏車被偷，兩位車主都有發文。",
          "宿舍區車棚昨晚我的車被偷了，旁邊室友那台也不見。",
          extra=(("k9-2", "facebook", 93,
                  "我也是，昨晚停在宿舍區車棚的車早上就不見，跟樓上同一排。"),)),
    _Bike("k10", 91, 90, DORM, "facebook", "宿舍區車棚腳踏車被剪鎖",
          "宿舍區車棚有腳踏車的鎖被剪斷，車被騎走。",
          "宿舍區車棚我的鎖被剪斷丟在旁邊，車直接被騎走了。"),
    _Bike("k11", 88, 87, "中大湖畔", "facebook", "中大湖畔腳踏車失竊",
          "中大湖畔散步時停放的腳踏車被偷。",
          "在中大湖畔散步半小時，回來腳踏車就不見了。"),
    _Bike("k12", 86, 84, DORM, "ncu-osa-life", "宿舍區車棚腳踏車接連失竊，生輔組加強巡查",
          "生輔組公告宿舍區車棚腳踏車接連失竊，將加強夜間巡查。",
          "生活輔導組公告：宿舍區車棚腳踏車接連失竊，本組將加強夜間巡查，請做好防竊。"),
    _Bike("k13", 83, 82, DORM, "facebook", "宿舍區車棚新買的腳踏車被偷",
          "宿舍區車棚一台剛買不久的腳踏車被偷。",
          "上禮拜才買的腳踏車，停在宿舍區車棚一個晚上就被偷了。"),
    _Bike("k14", 80, 79, "女十四舍車棚", "facebook", "女十四舍車棚腳踏車又被偷",
          "女十四舍旁的車棚又有腳踏車被偷。",
          "女十四舍車棚又有人的車不見，晚上那邊黑漆漆的。"),
    _Bike("k15", 77, 76, DORM, "facebook", "宿舍區車棚腳踏車半夜被牽走",
          "宿舍區車棚有人半夜牽走腳踏車，被路過的人看到。",
          "半夜回宿舍看到有人在宿舍區車棚牽車，以為是車主，早上才知道是被偷。"),
    _Bike("k16", 74, 73, "工程五館車棚", "facebook", "工程五館車棚腳踏車被剪鎖",
          "工程五館車棚有腳踏車的鎖被剪斷。",
          "工程五館車棚我的車鎖被剪了，車不見，車棚完全沒有監視器。"),
    _Bike("k17", 71, 70, DORM, "facebook", "宿舍區車棚腳踏車鎖在柱子上還是被偷",
          "宿舍區車棚一台鎖在柱子上的腳踏車還是被偷。",
          "我已經把車鎖在宿舍區車棚的柱子上了，還是被偷，鎖被剪斷。"),
    _Bike("k18", 68, 66, DORM, "facebook", "宿舍區車棚腳踏車連續兩晚失竊",
          "宿舍區車棚連續兩個晚上都有腳踏車被偷。",
          "宿舍區車棚連續兩個晚上都有人掉車，這樣下去誰還敢停。"),
    _Bike("k19", 65, 64, DORM, "facebook", "宿舍區車棚腳踏車失竊（黑色登山車）",
          "宿舍區車棚一台黑色登山車不見了。",
          "宿舍區車棚我的黑色登山車不見了，有看到的拜託跟我說。"),
    _Bike("k20", 62, 61, "依仁堂前", "facebook", "依仁堂前腳踏車被牽走",
          "依仁堂前有腳踏車在晚上被牽走。",
          "依仁堂門口我的車被牽走了，打完球出來才發現。"),
    _Bike("k21", 60, 58, DORM, "ncu-osa-life", "宿舍區車棚腳踏車失竊，總務處將加裝監視器",
          "生輔組公告宿舍區車棚失竊未減，總務處將加裝監視器與照明。",
          "生活輔導組公告：宿舍區車棚腳踏車失竊仍多，總務處將於近日加裝監視器與照明設備。"),
    _Bike("k22", 58, 57, DORM, "facebook", "宿舍區車棚腳踏車鎖頭被破壞",
          "宿舍區車棚有腳踏車的鎖頭被破壞，車被偷走。",
          "宿舍區車棚我的U型鎖被破壞，車被偷，聽說要裝監視器了。"),
    _Bike("k35", 112, 111, DORM, "facebook", "宿舍區車棚腳踏車失竊（灰色變速車）",
          "宿舍區車棚一台灰色變速車在夜裡不見。",
          "灰色變速車停在宿舍區車棚，昨晚還在，早上就沒了。"),
    _Bike("k36", 103, 103, "女十四舍車棚", "facebook", "女十四舍車棚腳踏車鎖被剪",
          "女十四舍旁的車棚有腳踏車的鎖被剪斷。",
          "女十四舍車棚我的鎖被剪成兩段，車也不見了。"),
    _Bike("k37", 92, 92, DORM, "facebook", "宿舍區車棚腳踏車失竊（小孩座椅）",
          "宿舍區車棚一台裝了後座椅的腳踏車被偷。",
          "宿舍區車棚那台有後座椅的腳踏車是我的，昨晚被偷了。"),
    _Bike("k38", 85, 85, DORM, "facebook", "宿舍區車棚腳踏車輪子被拆走",
          "宿舍區車棚有腳踏車的前輪被拆走。",
          "宿舍區車棚我的車前輪被整個拆走，只剩車架鎖在那邊。"),
    _Bike("k39", 75, 75, DORM, "facebook", "宿舍區車棚腳踏車失竊（剛換新鎖）",
          "宿舍區車棚一台剛換新鎖的腳踏車還是被偷。",
          "才剛換了新鎖，停在宿舍區車棚一晚還是被偷，好無力。"),
    _Bike("k40", 63, 63, DORM, "facebook", "宿舍區車棚腳踏車被騎走，有人目擊",
          "宿舍區車棚有人目擊腳踏車被騎走，車主隔天才發現。",
          "朋友說半夜看到有人從宿舍區車棚騎走我的車，那時候還以為是我。"),
    # 改善後
    _Bike("k23", 50, 49, DORM, "facebook", "宿舍區車棚監視器施工期間腳踏車失竊",
          "宿舍區車棚監視器施工期間，仍有一台腳踏車被偷。",
          "宿舍區車棚在裝監視器，結果施工那幾天我的車還是被偷了。"),
    _Bike("k24", 38, 37, "工程五館車棚", "facebook", "工程五館車棚腳踏車又被偷",
          "工程五館車棚又有腳踏車被偷。",
          "工程五館車棚又有人掉車，那邊也該裝監視器了吧。"),
    _Bike("k25", 30, 29, "中大湖畔", "facebook", "中大湖畔腳踏車被牽走",
          "中大湖畔停放的腳踏車被牽走。",
          "停在中大湖畔的車被牽走，那邊晚上幾乎沒有人。"),
    _Bike("k26", 22, 21, DORM, "facebook", "宿舍區車棚腳踏車失竊，監視器有拍到",
          "宿舍區車棚有腳踏車被偷，新裝的監視器拍到過程。",
          "宿舍區車棚我的車被偷，還好新裝的監視器有拍到，已經去調畫面了。"),
    _Bike("k27", 16, 15, "依仁堂前", "facebook", "依仁堂前腳踏車半夜不見",
          "依仁堂前停了一晚的腳踏車不見。",
          "依仁堂前停一個晚上，早上腳踏車就不見了。"),
    _Bike("k28", 9, 9, LIBRARY, "facebook", "總圖前車棚腳踏車一晚多台失竊",
          "總圖前車棚一晚有多台腳踏車被偷，圖書館已公告提醒。",
          "總圖前車棚昨晚好幾台腳踏車都不見了，我的也是，鎖全被剪斷。", "high",
          extra=(("k28-2", "ncu-lib", 8,
                  "圖書館公告：總圖前車棚昨晚發生多起腳踏車失竊，請勿將車輛長時間停放。"),)),
    _Bike("k29", 9, 9, LIBRARY, "facebook", "總圖前車棚腳踏車失竊（白色小折）",
          "總圖前車棚一台白色小折被偷。",
          "總圖前車棚我的白色小折不見了，昨晚十點停的。", "high", related_to="k28"),
    _Bike("k30", 9, 9, LIBRARY, "facebook", "總圖前車棚腳踏車失竊（紅色公路車）",
          "總圖前車棚一台紅色公路車被偷。",
          "紅色公路車停在總圖前車棚，早上來就剩一個前輪。", "high"),
    _Bike("k31", 9, 8, LIBRARY, "facebook", "總圖前車棚腳踏車被剪鎖騎走",
          "總圖前車棚一台腳踏車的鎖被剪斷，車被騎走。",
          "總圖前車棚我的鎖被剪斷，車被騎走，旁邊好幾台也是。", "high"),
    _Bike("k32", 9, 8, LIBRARY, "facebook", "總圖前車棚腳踏車失竊（綠色淑女車）",
          "總圖前車棚一台綠色淑女車被偷。",
          "我的綠色淑女車也在總圖前車棚被偷，這裡是不是被盯上了。", "high"),
    _Bike("k34", 3, 2, LIBRARY, "facebook", "總圖前車棚腳踏車座墊被拆",
          "總圖前車棚有腳踏車的座墊與車燈被拆走。",
          "總圖前車棚我的座墊跟車燈被拆走，車還在但不能騎。"),
)  # fmt: skip

BIKE_EVENTS: tuple[EventSpec, ...] = (
    *(
        EventSpec(b.key, b.title, b.title, b.summary, "security", b.location, b.occurred_ago,
                  b.risk, _BIKE, related_to=b.related_to)
        for b in BIKES
    ),
    EventSpec("k33", "總圖前車棚腳踏車又被偷（匿名回報）", "總圖前車棚腳踏車又被偷",
              "有人匿名回報總圖前車棚又有腳踏車被偷，鎖被剪斷。", "security", LIBRARY, 5, "high",
              _BIKE),
)  # fmt: skip

BIKE_ARTICLES: tuple[Article, ...] = tuple(
    a
    for b in BIKES
    for a in (
        Article(b.key, b.source, b.ago, b.text, b.key, _claim(b.source)),
        *(Article(k, src, ago, text, b.key, _claim(src)) for k, src, ago, text in b.extra),
    )
)


# ---------- 其他事件 ----------

_HOTWATER = "hotwater"
_STUDY = "study"
_CANTEEN = "canteen"
_NETWORK = "network"

EVENTS: tuple[EventSpec, ...] = (
    *BIKE_EVENTS,
    # 系列 B：宿舍熱水中斷
    EventSpec(
        "b1",
        "男九舍熱水中斷",
        "男九舍沒熱水",
        "男九舍晚上洗澡時突然沒熱水，整層都一樣。",
        "facility",
        "男九舍",
        100,
        "low",
        _HOTWATER,
    ),
    EventSpec(
        "b2",
        "男九舍熱水器故障，宿服組搶修",
        "男九舍熱水器故障，宿服組搶修",
        "宿服組公告男九舍熱水器故障，正在搶修。",
        "facility",
        "男九舍",
        88,
        "medium",
        _HOTWATER,
    ),
    EventSpec(
        "b3",
        "女十四舍熱水忽冷忽熱",
        "女十四舍熱水忽冷忽熱",
        "女十四舍這兩天熱水忽冷忽熱，晚上尖峰時間特別明顯。",
        "facility",
        "女十四舍",
        72,
        "low",
        _HOTWATER,
    ),
    EventSpec(
        "b4",
        "男九舍熱水再度中斷，宿服組更換設備",
        "男九舍熱水再度中斷，宿服組更換設備",
        "宿服組公告男九舍熱水再度中斷，將更換老舊熱水設備。",
        "facility",
        "男九舍",
        55,
        "medium",
        _HOTWATER,
    ),
    EventSpec(
        "b5",
        "男九舍又沒熱水",
        "男九舍又沒熱水",
        "男九舍晚上又沒熱水了，換過設備還是發生。",
        "facility",
        "男九舍",
        20,
        "medium",
        _HOTWATER,
    ),
    EventSpec(
        "b6",
        "男九舍熱水停了一晚",
        "男九舍熱水停了一晚",
        "男九舍昨晚熱水停了一整晚，早上才恢復。",
        "facility",
        "男九舍",
        6,
        "medium",
        _HOTWATER,
    ),
    # 系列 C：自習室財物失竊
    EventSpec(
        "c1",
        "總圖自習室筆電失竊",
        "總圖自習室筆電失竊",
        "總圖自習室有人離座吃飯，回來筆電就不見了。",
        "security",
        "總圖書館",
        88,
        "medium",
        _STUDY,
    ),
    EventSpec(
        "c2",
        "總圖自習室財物失竊，圖書館提醒",
        "總圖自習室財物失竊，圖書館提醒",
        "圖書館公告自習室接連有財物失竊，提醒離座時隨身攜帶貴重物品。",
        "security",
        "總圖書館",
        67,
        "medium",
        _STUDY,
    ),
    EventSpec(
        "c3",
        "總圖自習室錢包被拿走",
        "總圖自習室錢包被拿走",
        "總圖自習室座位上的錢包在離座十分鐘內被拿走。",
        "security",
        "總圖書館",
        27,
        "medium",
        _STUDY,
    ),
    # 系列 D：餐廳用餐後腸胃不適
    EventSpec(
        "d1",
        "學生餐廳用餐後腸胃不適",
        "學生餐廳用餐後有人肚子痛",
        "有人在學生餐廳吃完午餐後肚子痛，詢問是否有人一樣。",
        "health",
        "學生餐廳",
        75,
        "low",
        _CANTEEN,
    ),
    EventSpec(
        "d2",
        "學生餐廳多人腸胃不適，環安中心抽驗",
        "學生餐廳多人腸胃不適，環安中心抽驗",
        "環安中心公告學生餐廳有多人用餐後腸胃不適，已抽驗食材並暫停一個攤位。",
        "health",
        "學生餐廳",
        43,
        "medium",
        _CANTEEN,
    ),
    EventSpec(
        "d3",
        "學生餐廳晚餐後腹瀉",
        "學生餐廳晚餐後腹瀉",
        "有人說在學生餐廳吃完晚餐後腹瀉，還不確定原因。",
        "health",
        "學生餐廳",
        15,
        "low",
        _CANTEEN,
    ),
    # 系列 E：校園網路中斷
    EventSpec(
        "e1",
        "校園網路中斷",
        "校園網路中斷，計中搶修",
        "計中公告校園網路中斷約兩小時，原因是核心設備故障。",
        "facility",
        "全校",
        106,
        "medium",
        _NETWORK,
    ),
    EventSpec(
        "e2",
        "宿舍網路斷線",
        "宿舍網路斷線，計中處理中",
        "計中公告宿舍區網路斷線，正在更換線路。",
        "facility",
        "全校",
        60,
        "medium",
        _NETWORK,
    ),
    EventSpec(
        "e3",
        "校園無線網路異常",
        "校園無線網路異常",
        "計中公告校園無線網路連線不穩，已調整設定。",
        "facility",
        "全校",
        36,
        "low",
        _NETWORK,
    ),
    EventSpec(
        "e4",
        "校園網路短暫中斷",
        "校園網路短暫中斷",
        "計中公告今天下午校園網路短暫中斷二十分鐘，已恢復。",
        "facility",
        "全校",
        11,
        "low",
        _NETWORK,
    ),
    # 單一事件
    EventSpec(
        "f1",
        "工程四館火災傳言",
        "工程四館冒煙，官方澄清是演練",
        "有人看到工程四館冒煙，環安中心澄清是消防演練。",
        "fire",
        "工程四館",
        50,
        "high",
    ),
    EventSpec(
        "f2",
        "颱風停班停課",
        "颱風來襲，全校停班停課",
        "颱風來襲，學校宣布明天全校停班停課。",
        "weather",
        "全校",
        37,
        "high",
    ),
    EventSpec(
        "f3",
        "中大路與校門口車禍封路",
        "中大路校門口車禍，暫時封路",
        "中大路校門口發生多車擦撞，暫時封閉一個車道。",
        "traffic",
        "校門口",
        22,
        "high",
    ),
    EventSpec(
        "f4",
        "環校道路坍方封閉",
        "環校道路坍方，部分路段封閉",
        "總務處公告環校道路一段邊坡坍方，該路段暫時封閉。",
        "traffic",
        "環校道路",
        30,
        "medium",
    ),
    EventSpec(
        "f5",
        "化學館實驗室化學品外洩",
        "化學館實驗室化學品外洩",
        "環安中心公告化學館一間實驗室有少量化學品外洩，已完成清理。",
        "fire",
        "化學館",
        70,
        "high",
    ),
    EventSpec(
        "f6",
        "宵夜街店家疑似使用黑心原料",
        "宵夜街店家疑似使用黑心原料",
        "有人發文說宵夜街一家飲料店用過期原料，尚未查證。",
        "health",
        "宵夜街",
        9,
        "medium",
        mentions_person=True,
    ),
)


# ---------- 來源文章 ----------

ARTICLES: tuple[Article, ...] = (
    *BIKE_ARTICLES,
    Article(
        "b1", "facebook", 99, "男九舍晚上洗澡突然沒熱水，整層都一樣，有人知道怎麼回事嗎？", "b1"
    ),
    Article(
        "b2",
        "ncu-osa-life",
        85,
        "宿舍服務組公告：男九舍熱水器故障，目前正在搶修，造成不便敬請見諒。",
        "b2",
        "official",
    ),
    Article(
        "b3",
        "facebook",
        68,
        "女十四舍的熱水是怎樣，這兩天洗到一半突然變冷水，晚上九點後更嚴重",
        "b3",
    ),
    Article(
        "b4",
        "ncu-osa-life",
        52,
        "宿舍服務組公告：男九舍熱水再度中斷，將更換老舊熱水設備。",
        "b4",
        "official",
    ),
    Article("b5", "facebook", 19, "男九舍晚上又沒熱水了，不是才換過設備嗎？", "b5"),
    Article(
        "b6", "facebook", 5, "男九舍昨天整晚沒熱水，到早上七點多才有，冬天洗冷水真的受不了", "b6"
    ),
    Article("c1", "facebook", 87, "總圖自習室離座去吃飯，回來筆電就不見了，請大家注意。", "c1"),
    Article(
        "c2-1",
        "ncu-lib",
        64,
        "圖書館公告：自習室接連發生財物失竊，離座時請隨身攜帶貴重物品。",
        "c2",
        "official",
    ),
    Article("c2-2", "facebook", 64, "總圖自習室今天又有人的平板不見，館員說最近好幾起。", "c2"),
    Article("c2-3", "facebook", 64, "我也是今天在總圖自習室掉東西，已經跟櫃台登記。", "c2"),
    Article("c3", "facebook", 23, "總圖自習室座位上的錢包，離座十分鐘就被拿走了。", "c3"),
    Article("d1", "facebook", 74, "在學生餐廳吃完午餐後肚子痛，有人一樣嗎？", "d1", "hearsay"),
    Article(
        "d2-1", "facebook", 41, "今天學生餐廳好多人吃完都不舒服，朋友也在跑廁所。", "d2", "hearsay"
    ),
    Article(
        "d2-2",
        "ncu-epsc",
        40,
        "環安中心公告：學生餐廳有多人用餐後腸胃不適，已抽驗食材並暫停一個攤位營業。",
        "d2",
        "official",
    ),
    Article(
        "d3",
        "facebook",
        13,
        "昨天在學生餐廳吃完晚餐後腹瀉，不確定是不是餐點的問題。",
        "d3",
        "hearsay",
    ),
    Article(
        "e1",
        "ncu-cc",
        105,
        "電算中心公告：校園網路中斷約兩小時，原因為核心交換器故障，已完成搶修。",
        "e1",
        "official",
    ),
    Article(
        "e2",
        "ncu-cc",
        58,
        "電算中心公告：宿舍區網路斷線，正在更換線路，預計今晚恢復。",
        "e2",
        "official",
    ),
    Article(
        "e3",
        "ncu-cc",
        33,
        "電算中心公告：校園無線網路連線不穩，已調整設定並持續觀察。",
        "e3",
        "official",
    ),
    Article(
        "e4",
        "ncu-cc",
        10,
        "電算中心公告：今天下午校園網路短暫中斷二十分鐘，目前已恢復。",
        "e4",
        "official",
    ),
    Article("f1-1", "facebook", 50, "工程四館一直冒煙，是不是失火了？", "f1", "hearsay"),
    Article(
        "f1-2",
        "ncu-epsc",
        50,
        "環安中心澄清：今日工程四館為消防演練施放煙霧，並無火災發生。",
        "f1",
        "official",
        "refutes",
    ),
    Article(
        "f2-1", "cwa-alerts", 36, "中央氣象署發布颱風警報，桃園地區明日風雨增強。", "f2", "official"
    ),
    Article(
        "f2-2",
        "ncu-announcements",
        35,
        "本校公告：因颱風來襲，明日全校停班停課。",
        "f2",
        "official",
    ),
    Article(
        "f3-1", "facebook", 22, "中大路校門口剛剛多車擦撞，封了一個車道，經過的人請改道。", "f3"
    ),
    Article("f3-2", "facebook", 22, "校門口那邊車禍，警察在指揮交通，中大路很塞。", "f3"),
    Article(
        "f4",
        "ncu-announcements",
        27,
        "總務處公告：環校道路一段邊坡坍方，該路段暫時封閉，請改道通行。",
        "f4",
        "official",
    ),
    Article(
        "f5",
        "ncu-epsc",
        66,
        "環安中心公告：化學館一間實驗室有少量化學品外洩，已完成清理，無人受傷。",
        "f5",
        "official",
    ),
    Article(
        "f6",
        "facebook",
        9,
        "聽說宵夜街某家飲料店用過期原料，喝完好幾個人不舒服，店家都不承認。",
        "f6",
        "hearsay",
    ),
)


# ---------- 匿名回報 ----------

ANONYMOUS: tuple[AnonymousSpec, ...] = (
    AnonymousSpec(
        4,
        "總圖前車棚又有腳踏車被偷，鎖被剪斷，上禮拜才一晚掉好幾台。",
        LIBRARY,
        Article("k33", "anonymous-report", 4, "", "k33"),
    ),
    AnonymousSpec(
        2,
        "想請問圖書館期末考週會延長開放時間嗎？",
        None,
        Article("q1", "anonymous-report", 2, "", None),
    ),
)


# ---------- 系列分析（S12） ----------

SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec(
        _BIKE,
        "腳踏車失竊",
        "改善前失竊集中在宿舍區車棚，加裝監視器與照明後明顯減少，但近期轉移到總圖前車棚。",
        patterns=(
            Finding("多發生在夜間到清晨，車主隔天早上才發現。"),
            Finding(
                "改善前大多集中在宿舍區車棚。",
                (
                    "k2",
                    "k4",
                    "k5",
                    "k7",
                    "k9",
                    "k10",
                    "k12",
                    "k13",
                    "k15",
                    "k17",
                    "k18",
                    "k19",
                    "k21",
                    "k22",
                ),
            ),
            Finding("最近一週集中在總圖前車棚，一晚多台。", ("k28", "k29", "k30", "k31", "k32")),
        ),
        causes=(
            Finding(
                "車棚照明不足、沒有監視器。",
                ("k2", "k16", "k21", "k22", "k28"),
                "請總務處確認各車棚的照明與監視器位置。",
            ),
            Finding(
                "只鎖輪子或鎖頭強度不足，整台車容易被帶走。",
                ("k1", "k4", "k10", "k17", "k31"),
                "請生輔組統計失竊車輛的上鎖方式。",
            ),
        ),
        measures=(
            Measure(
                "宿舍區車棚加裝監視器與照明。", "school", "mid", "high", "prevent", "c1", "總務處"
            ),
            Measure(
                "增設可鎖車架的固定式停車架。", "school", "mid", "medium", "prevent", "c2", "總務處"
            ),
            Measure("鎖車時連車架一起鎖在固定物上。", "students", "short", "high", "respond", "c2"),
            Measure(
                "總圖前車棚比照加裝監視器。", "school", "short", "high", "prevent", "c1", "總務處"
            ),
        ),
        burst_explanation="總圖前車棚一晚接連傳出多台腳踏車失竊，件數明顯高於平常。",
    ),
    SeriesSpec(
        _HOTWATER,
        "宿舍熱水中斷",
        "熱水中斷多在晚上用水尖峰發生，更換設備後仍有再發生。",
        patterns=(Finding("多在晚上用水尖峰時段發生。"),),
        causes=(
            Finding("熱水設備老舊，尖峰時段負載不足。", None, "請宿服組確認設備年份與負載紀錄。"),
        ),
        measures=(
            Measure(
                "全面檢查宿舍熱水設備的負載與年份。",
                "school",
                "mid",
                "high",
                "investigate",
                "c1",
                "宿舍服務組",
            ),
            Measure("尖峰時段錯開洗澡時間。", "students", "short", "low", "respond"),
        ),
    ),
    SeriesSpec(
        _STUDY,
        "自習室財物失竊",
        "多發生在離座用餐的空檔，失竊物品以筆電與錢包為主。",
        patterns=(Finding("多在離座用餐時發生。"),),
        causes=(Finding("自習室沒有可上鎖的置物空間。", None, "請圖書館確認置物櫃的需求。"),),
        measures=(
            Measure(
                "自習室增設可上鎖的置物櫃。", "school", "mid", "medium", "prevent", "c1", "圖書館"
            ),
            Measure("離座時隨身攜帶貴重物品。", "students", "short", "high", "respond"),
        ),
    ),
    SeriesSpec(
        _CANTEEN,
        "餐廳用餐後腸胃不適",
        "三次通報都在用餐後數小時內出現症狀，只有一次經官方抽驗。",
        patterns=(Finding("症狀多在用餐後數小時內出現。"),),
        causes=(Finding("部分攤位食材保存溫度可能不足。", ("d2",), "請環安中心公布抽驗結果。"),),
        measures=(
            Measure(
                "定期抽驗餐廳食材與保存溫度。", "school", "mid", "high", "prevent", "c1", "環安中心"
            ),
            Measure("出現症狀時盡早就醫並保留收據。", "students", "short", "medium", "respond"),
        ),
    ),
    SeriesSpec(
        _NETWORK,
        "校園網路中斷",
        "多次中斷都和網路設備或線路有關，每次都在當天恢復。",
        patterns=(Finding("每次中斷都在當天恢復。"),),
        causes=(Finding("部分網路設備使用年限較長。", None, "請電算中心確認設備汰換計畫。"),),
        measures=(
            Measure(
                "汰換使用年限較長的網路設備。",
                "school",
                "long",
                "medium",
                "prevent",
                "c1",
                "電算中心",
            ),
        ),
    ),
)


# ---------- 改善措施（CRI 的回應、恢復、調適） ----------

ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec("b2", "更換男九舍熱水器", "宿舍服務組",
               (("handling", 80), ("recovered", 68), ("improved", 60)), 85),
    ActionSpec("b4", "加裝熱水備援機組", "宿舍服務組",
               (("handling", 45), ("recovered", 31), ("improved", 25)), 52),
    ActionSpec("e1", "更換核心交換器", "電算中心",
               (("handling", 105), ("recovered", 104), ("improved", 90)), 105),
    ActionSpec("e2", "建立宿舍斷線通報流程", "電算中心",
               (("handling", 57), ("recovered", 54), ("improved", 40)), 58),
    ActionSpec("c2", "自習室加裝置物櫃", "圖書館",
               (("handling", 55), ("recovered", 30), ("improved", 22)), 63),
    ActionSpec("d2", "餐廳攤位衛生稽查", "環安中心",
               (("handling", 38), ("recovered", 33), ("improved", 28)), 40),
    ActionSpec("f2", "颱風後校園巡檢", "總務處",
               (("handling", 35), ("recovered", 33), ("improved", 31)), 35),
    ActionSpec("f4", "環校道路邊坡加固", "總務處", (("handling", 23), ("recovered", 13)), 27),
    ActionSpec("k28", "總圖前車棚張貼防竊提醒", "圖書館", (("handling", 6),), 8),
    ActionSpec("k33", "調閱總圖前車棚周邊監視器", "總務處", (), 3),
    ActionSpec("d3", "張貼用餐衛生提醒", "環安中心", (("dropped", 10),), 12),
)  # fmt: skip


# 主系列的系列層級改善措施（前後比較只看系列層級的措施）
SERIES_ACTIONS: tuple[SeriesActionSpec, ...] = (
    SeriesActionSpec(
        _BIKE, "宿舍區車棚加裝監視器與照明", "總務處",
        (("handling", IMPROVEMENT_STARTED_AGO), ("recovered", 45), ("improved", 35)), 58,
    ),
    # 從今天的系列分析建立：剛開始處理，前後比較會照實顯示「資料還不夠」
    SeriesActionSpec(_BIKE, None, "總務處", (("handling", 2),), 3, measure_index=3),
)  # fmt: skip

DATASET = Dataset(
    events=EVENTS,
    articles=ARTICLES,
    series=SERIES,
    actions=ACTIONS,
    anonymous=ANONYMOUS,
    aliases={"女十四舍車棚": DORM},
)

# 主系列的人工確認原因與效益評估（評估還沒做的兩項建議）
CONFIRMED_CAUSE = (
    _BIKE,
    "c1",
    "總務處回覆：宿舍區車棚原本只有一盞燈、沒有監視器，已於改善時補上。",
)
IMPACT_SELECTION = (_BIKE, ("m2", "m4"))
"""從系列 A 的分析建立追蹤的建議（第幾項，從 0 起）。"""
