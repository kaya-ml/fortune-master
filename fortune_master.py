"""
統合命占マスター版（fortune_master.py）v2
=========================================
v2 追加点：
  ① 相談者登録に「血液型」「婚姻状況」「交際状況」「別れた時期」「別れた理由」を追加。
     入力された項目は画面・PDF鑑定書に反映（占術スコアには影響させない）。
  ② 表示モード切替「一人用／二人用／多人数用（最大5人）」。占い内容は同一で人数分を表示。
  ③ 多人数用は各人を expander（折りたたみ）で表示し、その場で開閉可能。
  (a) 計算コア(compute_person)と表示(render_person)を分離。将来のアプリ化(Web SaaS等)へ移行しやすい構造。

実行方法:
  streamlit run fortune_master.py

必要ライブラリ:
  pip install streamlit sxtwl lunar-python plotly lunardate ephem reportlab kaleido
"""

import datetime
import io
import json
import os
import sxtwl
import streamlit as st
import plotly.graph_objects as go
from lunar_python import Solar
from datetime import date as _date, timedelta as _timedelta
from lunardate import LunarDate as _LunarDate
import ephem as _ephem

# PDF生成
from reportlab.lib.pagesizes import A4, A5
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image as RLImage,
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# フォント登録
_FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "C:/Windows/Fonts/msgothic.ttc",   # Windows フォールバック
    "C:/Windows/Fonts/meiryo.ttc",
]

def _register_pdf_fonts():
    """利用可能な日本語フォントを登録する"""
    for path in _FONT_PATHS:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("PDFJPRegular", path, subfontIndex=0))
                pdfmetrics.registerFont(TTFont("PDFJPBold",    path, subfontIndex=0))
                return True
            except Exception:
                continue
    return False

_PDF_FONT_OK = _register_pdf_fonts()
_F  = "PDFJPRegular" if _PDF_FONT_OK else "Helvetica"
_FB = "PDFJPBold"    if _PDF_FONT_OK else "Helvetica-Bold"

# サーバ環境（Streamlit Cloud等）でkaleidoのChromiumが/dev/shm不足で固まるのを回避。
# kaleido v1 では scope API が無いため try/except で握りつぶす（その場合は無害）。
try:
    import plotly.io as _pio
    _pio.kaleido.scope.chromium_args = tuple(
        a for a in _pio.kaleido.scope.chromium_args if a != "--disable-dev-shm-usage"
    )
except Exception:
    pass

# PDF カラー定義
_C_BG      = colors.HexColor("#0d1526")
_C_GOLD    = colors.HexColor("#c9952a")
_C_SILVER  = colors.HexColor("#a0b8d0")
_C_WHITE   = colors.HexColor("#f0e6d3")
_C_ACCENT  = colors.HexColor("#66ccaa")
_C_WARN    = colors.HexColor("#ff9eb5")
_C_SECTION = colors.HexColor("#1a2840")
_C_ROW1    = colors.HexColor("#0f1a2e")
_C_ROW2    = colors.HexColor("#111e35")
_C_PROFILE = colors.HexColor("#b08cd9")   # 相談者プロフィール用（紫）

# ============================================================
# JSON データ読み込み
# ============================================================

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

@st.cache_data
def load_json(filename: str) -> dict:
    path = os.path.join(_DATA_DIR, filename)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}

def get_shukuyo_desc(xiu: str) -> dict:
    data = load_json("shukuyo.json")
    return data.get(xiu, {})

def get_kyusei_desc(star_name: str) -> dict:
    data = load_json("kyusei.json")
    return data.get(star_name, {})

def get_numerology_desc(num: int) -> dict:
    data = load_json("general.json")
    return data.get("numerology", {}).get(str(num), {})

def get_zodiac_desc(sign: str) -> dict:
    data = load_json("general.json")
    return data.get("zodiac", {}).get(sign, {})

def get_tsuhen_desc(name: str) -> dict:
    data = load_json("general.json")
    return data.get("tsuhen", {}).get(name, {})

# ============================================================
# 相談者管理
# ============================================================

_CLIENTS_FILE = os.path.join(_DATA_DIR, "clients.json")

# ① 選択肢定義
BLOOD_OPTIONS    = ["（未選択）", "A", "B", "O", "AB"]
MARITAL_OPTIONS  = ["（未選択）", "未婚", "既婚"]
RELATION_OPTIONS = ["（未選択）", "交際なし", "交際あり"]
_UNSET = "（未選択）"

def _clean_opt(v: str) -> str:
    """未選択は空文字に正規化"""
    return "" if (v is None or v == _UNSET) else v

def load_clients() -> list:
    if os.path.exists(_CLIENTS_FILE):
        with open(_CLIENTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def save_clients(clients: list):
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_CLIENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(clients, f, ensure_ascii=False, indent=2)

def add_client(name: str, birth: datetime.date, memo: str = "",
               blood_type: str = "", marital: str = "", relationship: str = "",
               breakup_when: str = "", breakup_why: str = ""):
    """① 追加項目を含めて相談者を登録"""
    clients = load_clients()
    for c in clients:
        if c["name"] == name and c["birth"] == birth.isoformat():
            return False  # 重複
    clients.append({
        "name": name,
        "birth": birth.isoformat(),
        "memo": memo,
        "blood_type": _clean_opt(blood_type),
        "marital": _clean_opt(marital),
        "relationship": _clean_opt(relationship),
        "breakup_when": breakup_when.strip() if breakup_when else "",
        "breakup_why": breakup_why.strip() if breakup_why else "",
        "registered": datetime.date.today().isoformat(),
    })
    save_clients(clients)
    return True

def _client_profile_rows(meta: dict) -> list:
    """① メタ情報から表示すべき(項目, 内容)のリストを作る（空欄は除外）"""
    if not meta:
        return []
    rows = []
    bt = _clean_opt(meta.get("blood_type", ""))
    ma = _clean_opt(meta.get("marital", ""))
    rl = _clean_opt(meta.get("relationship", ""))
    bw = (meta.get("breakup_when", "") or "").strip()
    by = (meta.get("breakup_why", "") or "").strip()
    if bt: rows.append(("血液型", f"{bt}型"))
    if ma: rows.append(("婚姻状況", ma))
    if rl: rows.append(("交際状況", rl))
    if bw: rows.append(("別れた時期", bw))
    if by: rows.append(("別れた理由", by))
    return rows

# ============================================================
# 基本定数（fortune_app.py と同一）
# ============================================================

TENKAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
CHISHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

_GOKEI_NEXT  = {0: 1, 1: 2, 2: 3, 3: 4, 4: 0}
_GOKEI_PREV  = {0: 4, 1: 0, 2: 1, 3: 2, 4: 3}
_GOKOKU_NEXT = {0: 2, 2: 4, 4: 1, 1: 3, 3: 0}
_GOKOKU_PREV = {0: 3, 3: 1, 1: 4, 4: 2, 2: 0}

_TSUHEN_TABLE = {
    "同":   {True: "比肩",  False: "劫財"},
    "我生": {True: "食神",  False: "傷官"},
    "生我": {True: "偏印",  False: "印綬"},
    "我克": {True: "偏財",  False: "正財"},
    "克我": {True: "偏官",  False: "正官"},
}

TSUHEN_DESC_BASIC = {
    "比肩": "独立心・自主性が強く、協調よりも自己主張を重んじる",
    "劫財": "競争心旺盛で行動力があるが、感情の起伏が激しい面も",
    "食神": "楽天的で表現力豊か。芸術・食・享楽への才能",
    "傷官": "鋭敏な感性と批判精神。芸術・技術の才に恵まれる",
    "偏財": "社交的で金銭感覚が鋭い。投機・商才・異性運",
    "正財": "堅実・勤勉。着実に財を蓄える安定志向",
    "偏官": "行動力と決断力。競争に勝つ強さを持つ一方で波乱も",
    "正官": "責任感・誠実さ・秩序を重んじる。出世・地位運",
    "偏印": "直感・発想力・学問。孤独を好み精神世界に深い",
    "印綬": "知性・品格・学問運。母性的な保護と深い思慮",
}

_SHUKU_27_ORDER = [
    "角", "亢", "氐", "房", "心", "尾", "箕",
    "斗", "女", "虚", "危", "室", "壁",
    "奎", "婁", "胃", "昴", "畢", "觜", "参",
    "井", "鬼", "柳", "星", "張", "翼", "軫",
]
_SHUKU_27_INDEX = {s: i for i, s in enumerate(_SHUKU_27_ORDER)}

SHUKU_27_YOMI = {
    "角": "角宿（かくしゅく）",  "亢": "亢宿（こうしゅく）",
    "氐": "氐宿（ていしゅく）",  "房": "房宿（ぼうしゅく）",
    "心": "心宿（しんしゅく）",  "尾": "尾宿（びしゅく）",
    "箕": "箕宿（きしゅく）",    "斗": "斗宿（としゅく）",
    "女": "女宿（じょしゅく）",  "虚": "虚宿（きょしゅく）",
    "危": "危宿（きしゅく）",    "室": "室宿（しつしゅく）",
    "壁": "壁宿（へきしゅく）",  "奎": "奎宿（けいしゅく）",
    "婁": "婁宿（ろうしゅく）",  "胃": "胃宿（いしゅく）",
    "昴": "昴宿（ぼうしゅく）",  "畢": "畢宿（ひつしゅく）",
    "觜": "觜宿（しゅくしゅく）","参": "参宿（しんしゅく）",
    "井": "井宿（せいしゅく）",  "鬼": "鬼宿（きしゅく）",
    "柳": "柳宿（りゅうしゅく）","星": "星宿（せいしゅく）",
    "張": "張宿（ちょうしゅく）","翼": "翼宿（よくしゅく）",
    "軫": "軫宿（しんしゅく）",
}

KYUSEI_ALIAS = {
    "一白水天枢": "一白水星", "二黒土天璇": "二黒土星",
    "三碧木天玑": "三碧木星", "四绿木天权": "四緑木星",
    "五黄土玉衡": "五黄土星", "六白金开阳": "六白金星",
    "七赤金摇光": "七赤金星", "八白土洞明": "八白土星",
    "九紫火隐元": "九紫火星",
}
KYUSEI_NAMES = [
    "一白水星", "二黒土星", "三碧木星", "四緑木星", "五黄土星",
    "六白金星", "七赤金星", "八白土星", "九紫火星",
]
KYUSEI_MAP = {name: i + 1 for i, name in enumerate(KYUSEI_NAMES)}

_KEISHA_MONTH = {
    1: ("一白傾斜", "柔軟・内省・流動性。水のように環境に適応し、深く感じとる"),
    2: ("二黒傾斜", "忍耐・勤勉・大地の安定。縁の下の力持ちとして周囲を支える"),
    3: ("三碧傾斜", "行動力・積極性・新しいものへの感受性。スタートの星"),
    4: ("四緑傾斜", "穏和・信頼・コツコツ型。風のように広く人脈を結ぶ"),
    5: ("五黄傾斜", "強烈なエネルギーと支配力。中心的存在になる宿命"),
    6: ("六白傾斜", "完璧主義・リーダー気質・高い志と誇り"),
    7: ("七赤傾斜", "愉快・社交・弁舌の才。金の星が示す喜びと享楽"),
    8: ("八白傾斜", "変化・革新・蓄積。山のどっしりした安定と突然の変革"),
    9: ("九紫傾斜", "直感・名誉・表現力。火のように輝き人々を照らす"),
}

_KEISHA_COMBO = {
    (1, 1): "自己完結型の深い感受性。孤独を力に変える内向の賢者",
    (1, 6): "知性と意志力が融合。論理的思考とリーダー性を兼ね備える",
    (1, 7): "社交的な感受性派。人の輪の中で直感を活かす",
    (2, 3): "行動力に支えられた奉仕精神。動きながら人を育てる",
    (2, 8): "堅実さの中に改革の芽。時が来れば大きく動く",
    (3, 4): "積極性と協調性の融合。広い人脈を行動力で活かす",
    (3, 9): "直感と行動が合体。閃きを即実行する天才肌",
    (4, 3): "信頼と行動力の組み合わせ。着実に前進する実行者",
    (5, 5): "最強のエネルギー。王者の資質と孤高の宿命",
    (6, 1): "指導力の中に柔軟性。強さと適応力を兼ねる",
    (7, 2): "明るさと忍耐が共存。笑顔で粘り抜く実力者",
    (8, 3): "革新と行動力。変化を恐れず突き進む開拓者",
    (9, 6): "輝きとリーダー性。カリスマ的存在感を放つ",
}

_KYUSEI_BASE_SCORE = {1:-6, 2:-2, 3:2, 4:6, 5:3, 6:7, 7:4, 8:1, 9:-4,}
_KYUSEI_GOGYO      = {1:0, 2:1, 3:2, 4:2, 5:1, 6:3, 7:3, 8:1, 9:4}
_KQ_GOKEI_NEXT     = {0:2, 2:4, 4:1, 1:3, 3:0}
_KQ_GOKOKU_NEXT    = {0:4, 4:3, 3:2, 2:1, 1:0}

_TSUHEN_SCORE = {
    "比肩":  3, "劫財": -6, "食神":  9, "傷官": -2,
    "偏財":  8, "正財":  6, "偏官":  1, "正官":  8,
    "偏印":  2, "印綬":  7, "不明":  0,
}

JUNI_UN_NAMES = ["長生","沐浴","冠帯","建禄","帝旺","衰","病","死","墓","絶","胎","養"]
_JUNI_UN_SCORE = {
    "長生":  7, "沐浴":  1, "冠帯":  8, "建禄":  9, "帝旺": 10,
    "衰":   -1, "病":   -3, "死":   -5, "墓":   -2, "絶":  -6,
    "胎":    3, "養":    5,
}
_JUNI_TABLE = {
    0:[1,2,3,4,5,6,7,8,9,10,11,0],  1:[10,11,0,1,2,3,4,5,6,7,8,9],
    2:[10,11,0,1,2,3,4,5,6,7,8,9],  3:[7,8,9,10,11,0,1,2,3,4,5,6],
    4:[4,5,6,7,8,9,10,11,0,1,2,3],  5:[6,5,4,3,2,1,0,11,10,9,8,7],
    6:[9,8,7,6,5,4,3,2,1,0,11,10],  7:[9,8,7,6,5,4,3,2,1,0,11,10],
    8:[0,11,10,9,8,7,6,5,4,3,2,1],  9:[3,2,1,0,11,10,9,8,7,6,5,4],
}
_KIKI_SHINOH = {
    "財":("偏財","正財"), "官":("偏官","正官"),
    "食傷":("食神","傷官"), "比劫":("比肩","劫財"), "印":("偏印","印綬"),
}
_PERSONAL_YEAR_SCORE = {
    1:6, 2:-2, 3:8, 4:-4, 5:4, 6:6, 7:-2, 8:10, 9:-6, 11:8, 22:10, 33:8,
}
_SHUKU_SCORE = {
    "角":  5, "亢":  4, "氐":  8, "房":  9,
    "心": -2, "尾":  7, "箕":  4, "斗":  3,
    "女": -3, "虚": -5, "危": -6, "室":  8,
    "壁":  7, "奎":  4, "婁":  7, "胃": -1,
    "昴":  5, "畢":  9, "觜": -2, "参":  6,
    "井":  8, "鬼":  2, "柳": -4, "星":  5,
    "張":  9, "翼":  1, "軫":  7,
}
_SHUKU_KANKEI_NAME = {
    0:"命宿", 1:"業宿",26:"業宿", 2:"胎宿",25:"胎宿",
    3:"友宿",24:"友宿", 4:"親宿",23:"親宿", 5:"栄宿",22:"栄宿",
    6:"安宿",21:"安宿", 7:"危宿",20:"危宿", 8:"成宿",19:"成宿",
    9:"収宿",18:"収宿",10:"開宿",17:"開宿",11:"閉宿",16:"閉宿",
    12:"建宿",15:"建宿",13:"除宿",14:"除宿",
}
_SHUKU_KANKEI_SCORE = {
    "命宿":0,"業宿":-8,"胎宿":+3,"友宿":+5,"親宿":+7,"栄宿":+10,
    "安宿":+6,"危宿":-7,"成宿":+6,"収宿":+4,"開宿":+8,"閉宿":-6,
    "建宿":+4,"除宿":-2,"満宿":+7,
}
_SHUKU_KANKEI_DESC = {
    "命宿":"本命と同じ宿。基準となる年。",
    "業宿":"業の宿。過去の因縁が表れやすく、波乱含みの年。",
    "胎宿":"胎の宿。新しいものが生まれる再生の年。",
    "友宿":"友の宿。人との縁が深まり、協力を得やすい年。",
    "親宿":"親の宿。信頼と親密な関係が育まれる大吉の年。",
    "栄宿":"栄の宿。最も恵まれた繁栄の年。大きな好機。",
    "安宿":"安の宿。平和で安定した落ち着きある年。",
    "危宿":"危の宿。危険や障害が生じやすい注意の年。",
    "成宿":"成の宿。努力が実を結び達成感を得られる年。",
    "収宿":"収の宿。収穫・蓄積の年。内側を固める時期。",
    "開宿":"開の宿。新たな扉が開く発展・開運の年。",
    "閉宿":"閉の宿。物事が停滞しやすく、内省の必要な年。",
    "建宿":"建の宿。基盤を築き、将来へ備える建設の年。",
    "除宿":"除の宿。変化と除去。不要なものを手放す年。",
    "満宿":"満の宿。充満・豊かさ。満ちた実感のある年。",
}

_MONTH_BASE_IDX = {
    1:11, 2:13, 3:15, 4:17, 5:19, 6:21,
    7:24, 8:0,  9:2,  10:4, 11:7, 12:9,
}

_GRAPH_COLORS = {
    "九星":"#66ccaa", "四柱":"#e2b96f",
    "数秘":"#a0c8ff", "宿曜":"#ff9eb5", "合成":"#ffffff",
}

# ============================================================
# 補助関数
# ============================================================

def _gokyo(tg):   return tg // 2
def _inyo(tg):    return "陽" if tg % 2 == 0 else "陰"
def _clamp10(v):  return max(-10.0, min(10.0, round(float(v), 2)))

def _reduce_number(n):
    while n > 9 and n not in (11, 22, 33):
        n = sum(int(c) for c in str(n))
    return n

def calc_tsuhen(nichi_tg, other_tg):
    n_go = _gokyo(nichi_tg); o_go = _gokyo(other_tg)
    same = (nichi_tg % 2 == other_tg % 2)
    if n_go == o_go:                       rel = "同"
    elif _GOKEI_NEXT.get(n_go) == o_go:   rel = "我生"
    elif _GOKEI_PREV.get(n_go) == o_go:   rel = "生我"
    elif _GOKOKU_NEXT.get(n_go) == o_go:  rel = "我克"
    elif _GOKOKU_PREV.get(n_go) == o_go:  rel = "克我"
    else:                                   return "不明"
    return _TSUHEN_TABLE[rel][same]

def score_fmt(v):
    if v >= 6:    return f"🟢 {v:+.1f}"
    elif v >= 2:  return f"🔵 {v:+.1f}"
    elif v >= -2: return f"⚪ {v:+.1f}"
    else:         return f"🔴 {v:+.1f}"

# ============================================================
# 占術計算
# ============================================================

def calc_numerology(year, month, day):
    return _reduce_number(sum(int(c) for c in f"{year}{month:02d}{day:02d}"))

def calc_personal_year(birth_month, birth_day, target_year):
    return _reduce_number(
        sum(int(c) for c in f"{birth_month:02d}{birth_day:02d}") +
        sum(int(c) for c in str(target_year))
    )

_ZODIAC_TABLE = [
    ((1,20),"山羊座"),((2,19),"水瓶座"),((3,21),"魚座"),((4,20),"牡羊座"),
    ((5,21),"牡牛座"),((6,21),"双子座"),((7,23),"蟹座"),((8,23),"獅子座"),
    ((9,23),"乙女座"),((10,23),"天秤座"),((11,22),"蠍座"),((12,22),"射手座"),
    ((12,32),"山羊座"),
]

def get_zodiac(month, day):
    for boundary, sign in _ZODIAC_TABLE:
        if (month, day) < boundary: return sign
    return "山羊座"

def _calc_shinoh(raw):
    nichi_go = _gokyo(raw["day_tg"])
    return sum(1 for k in ("year_tg","month_tg","day_tg","hour_tg")
               if _gokyo(raw[k]) == nichi_go) >= 2

def get_shichusuimei(year, month, day, hour):
    d = sxtwl.fromSolar(year, month, day)
    ygz = d.getYearGZ(); mgz = d.getMonthGZ()
    dgz = d.getDayGZ();  hgz = d.getHourGZ(hour)
    gz  = lambda g: TENKAN[g.tg] + CHISHI[g.dz]
    ntg = dgz.tg
    raw = {
        "year_tg":ygz.tg,"year_dz":ygz.dz,"month_tg":mgz.tg,"month_dz":mgz.dz,
        "day_tg":dgz.tg,"day_dz":dgz.dz,"hour_tg":hgz.tg,"hour_dz":hgz.dz,
    }
    return {
        "年柱":gz(ygz),"月柱":gz(mgz),"日柱":gz(dgz),"時柱":gz(hgz),
        "日干":TENKAN[ntg],
        "日干_五行":["木","火","土","金","水"][_gokyo(ntg)],
        "日干_陰陽":_inyo(ntg),
        "通変星":{"年柱":calc_tsuhen(ntg,ygz.tg),"月柱":calc_tsuhen(ntg,mgz.tg),
                  "日柱":"（日主）","時柱":calc_tsuhen(ntg,hgz.tg)},
        "身旺":_calc_shinoh(raw),"_raw":raw,
    }

def calc_juni_un(nichi_tg, other_dz):
    key = (nichi_tg//2) if nichi_tg%2==0 else (5+nichi_tg//2)
    return JUNI_UN_NAMES[_JUNI_TABLE.get(key,_JUNI_TABLE[0])[other_dz%12]]

def calc_kiki_bonus(ts_name, is_shinoh):
    cat = next((c for c,ns in _KIKI_SHINOH.items() if ts_name in ns), None)
    if cat is None: return 0
    return 2 if cat in ({"財","官","食傷"} if is_shinoh else {"比劫","印"}) else -2

def get_year_shichu_detail(nichi_tg, is_shinoh, target_year):
    gz = sxtwl.fromSolar(target_year,2,4).getYearGZ()
    ts = calc_tsuhen(nichi_tg, gz.tg)
    ju = calc_juni_un(nichi_tg, gz.dz)
    kk = calc_kiki_bonus(ts, is_shinoh)
    return {
        "通変星":ts,"十二運":ju,"喜忌補正":kk,
        "スコア":_clamp10(_TSUHEN_SCORE.get(ts,0)*0.6+_JUNI_UN_SCORE.get(ju,0)*0.4+kk),
    }

def get_month_shichu_detail(nichi_tg, is_shinoh, year, month):
    """月別四柱スコア（節入り月基準）"""
    SEKKI_DAYS = {1:6,2:4,3:6,4:5,5:6,6:6,7:7,8:8,9:8,10:8,11:7,12:7}
    day = SEKKI_DAYS.get(month, 6)
    gz  = sxtwl.fromSolar(year, month, day).getMonthGZ()
    ts  = calc_tsuhen(nichi_tg, gz.tg)
    ju  = calc_juni_un(nichi_tg, gz.dz)
    kk  = calc_kiki_bonus(ts, is_shinoh)
    return {
        "通変星":ts,"十二運":ju,"喜忌補正":kk,
        "スコア":_clamp10(_TSUHEN_SCORE.get(ts,0)*0.6+_JUNI_UN_SCORE.get(ju,0)*0.4+kk),
    }

def get_kyusei(year, month, day):
    solar = Solar.fromYmd(year, month, day)
    lunar = solar.getLunar()
    alias = lambda r: KYUSEI_ALIAS.get(r, r)
    ys = alias(lunar.getYearNineStar().toString())
    ms = alias(lunar.getMonthNineStar().toString())
    ds = alias(lunar.getDayNineStar().toString())
    yn = KYUSEI_MAP.get(ys,0); mn = KYUSEI_MAP.get(ms,0)
    kn, kk = _KEISHA_MONTH.get(mn,(f"{ms}傾斜",""))
    return {
        "年命星":ys,"月命星":ms,"日命星":ds,
        "傾斜宮名":kn,"傾斜キーワード":kk,
        "組み合わせ特徴":_KEISHA_COMBO.get((yn,mn),f"{ys}の本質を{ms}が彩る個性"),
        "年命星_数":yn,"月命星_数":mn,
    }

def get_kyusei_position(honmei, target_year):
    center = ((3-(target_year-2024)-1)%9)+1
    return ((honmei+4-center)%9)+1

def calc_kyusei_compat(honmei_num, position):
    hg = _KYUSEI_GOGYO.get(honmei_num,0)
    mg = _KYUSEI_GOGYO.get(position,0)
    if hg==mg:                            return 2
    elif _KQ_GOKEI_NEXT.get(hg)==mg:     return 3
    elif _KQ_GOKEI_NEXT.get(mg)==hg:     return 1
    elif _KQ_GOKOKU_NEXT.get(hg)==mg:    return -1
    elif _KQ_GOKOKU_NEXT.get(mg)==hg:    return -3
    return 0

def calc_keisha_bonus(honmei_pos, keisha_num, target_year):
    kp   = get_kyusei_position(keisha_num, target_year)
    diff = min((honmei_pos-kp)%9,(kp-honmei_pos)%9)
    return 2 if diff==0 else 1 if diff==1 else 0

def calc_kyusei_score(honmei_num, keisha_num, target_year):
    pos = get_kyusei_position(honmei_num, target_year)
    b   = _KYUSEI_BASE_SCORE.get(pos,0)
    c   = calc_kyusei_compat(honmei_num, pos)
    k   = calc_keisha_bonus(pos, keisha_num, target_year)
    return {"宮位":pos,"基本":b,"相性補正":c,"傾斜補正":k,"スコア":_clamp10(b+c+k)}

def get_month_kyusei_score(honmei_num, keisha_num, year, month):
    """月命星の宮位を使った月別九星スコア"""
    solar = Solar.fromYmd(year, month, 1)
    m_raw = solar.getLunar().getMonthNineStar().toString()
    m_star = KYUSEI_ALIAS.get(m_raw, m_raw)
    m_num  = KYUSEI_MAP.get(m_star, 0)
    pos    = get_kyusei_position(m_num, year)
    b      = _KYUSEI_BASE_SCORE.get(pos, 0)
    c      = calc_kyusei_compat(honmei_num, pos)
    k      = calc_keisha_bonus(pos, keisha_num, year)
    return {"宮位":pos,"スコア":_clamp10(b+c+k),"月命星":m_star}

# ---- 宿曜 ----

def _get_lunar_day_jst(target):
    search = _ephem.Date(target - _timedelta(days=35))
    nm = _ephem.next_new_moon(search)
    for _ in range(3):
        nm_jst      = (_ephem.Date(nm).datetime()      + _timedelta(hours=9)).date()
        nm_next     = _ephem.next_new_moon(nm+1)
        nm_next_jst = (_ephem.Date(nm_next).datetime() + _timedelta(hours=9)).date()
        if nm_jst <= target < nm_next_jst:
            return (target - nm_jst).days + 1
        nm = nm_next
    return _LunarDate.fromSolarDate(target.year, target.month, target.day).day

def _calc_shuku_from_date(year, month, day):
    target  = _date(year, month, day)
    lunar   = _LunarDate.fromSolarDate(year, month, day)
    jst_day = _get_lunar_day_jst(target)
    idx     = (_MONTH_BASE_IDX[lunar.month] + jst_day - 1) % 27
    return _SHUKU_27_ORDER[idx], lunar, jst_day

def get_shukuyo(year, month, day, **kw):
    try:
        xiu, lunar, jd = _calc_shuku_from_date(year, month, day)
        return {
            "宿_raw":xiu,"宿_表示":SHUKU_27_YOMI.get(xiu,f"{xiu}宿"),
            "基本スコア":_SHUKU_SCORE.get(xiu,0),
            "旧暦年":lunar.year,"旧暦月":f"{'（閏）'if lunar.isLeapMonth else ''}{lunar.month}月",
            "旧暦日_jst":jd,"C値":None,
        }
    except Exception as e:
        return {"宿_raw":None,"宿_表示":f"エラー:{e}","基本スコア":0,
                "旧暦年":None,"旧暦月":None,"旧暦日_jst":None,"C値":None}

def get_year_shuku(target_year):
    try:
        xiu,*_ = _calc_shuku_from_date(target_year,2,4); return xiu
    except: return None

def get_month_shuku(year, month):
    """月の象徴宿（節入り日基準）"""
    SEKKI_DAYS = {1:6,2:4,3:6,4:5,5:6,6:6,7:7,8:8,9:8,10:8,11:7,12:7}
    try:
        xiu,*_ = _calc_shuku_from_date(year, month, SEKKI_DAYS.get(month,6))
        return xiu
    except: return None

def get_shuku_kankei(honmei_xiu, target_xiu):
    hi = _SHUKU_27_INDEX.get(honmei_xiu,0)
    ti = _SHUKU_27_INDEX.get(target_xiu,0)
    diff = (ti-hi+27)%27
    name = _SHUKU_KANKEI_NAME.get(diff,"命宿")
    return {"関係名":name,"補正":_SHUKU_KANKEI_SCORE.get(name,0),
            "説明":_SHUKU_KANKEI_DESC.get(name,""),"差分":diff}

def calc_shukuyo_score(honmei_xiu, target_year):
    yx = get_year_shuku(target_year)
    if not yx or not honmei_xiu:
        return {"年宿_raw":None,"年宿_表示":"（計算不可）","基本スコア":0,
                "関係名":"不明","関係補正":0,"関係説明":"","スコア":0.0}
    bs = _SHUKU_SCORE.get(yx,0)
    kk = get_shuku_kankei(honmei_xiu, yx)
    return {"年宿_raw":yx,"年宿_表示":SHUKU_27_YOMI.get(yx,yx),
            "基本スコア":bs,"関係名":kk["関係名"],"関係補正":kk["補正"],
            "関係説明":kk["説明"],"スコア":_clamp10(bs*0.6+kk["補正"]*0.4)}

def calc_month_shukuyo_score(honmei_xiu, year, month):
    mx = get_month_shuku(year, month)
    if not mx or not honmei_xiu:
        return {"スコア":0.0,"月宿_表示":"（計算不可）","関係名":"不明"}
    bs = _SHUKU_SCORE.get(mx,0)
    kk = get_shuku_kankei(honmei_xiu, mx)
    return {"スコア":_clamp10(bs*0.6+kk["補正"]*0.4),
            "月宿_表示":SHUKU_27_YOMI.get(mx,mx),"関係名":kk["関係名"]}

# ============================================================
# バイオリズム統合計算（重みを引数で受け取る）
# ============================================================

def calc_biorhythm_scores(
    birth_month, birth_day, nichi_tg, is_shinoh,
    honmei_num, keisha_num, honmei_xiu, year_range,
    weights=None,
):
    if weights is None:
        weights = {"九星":0.35,"四柱":0.35,"宿曜":0.15,"数秘":0.15}
    total_w = sum(weights.values()) or 1.0
    w = {k: v/total_w for k,v in weights.items()}

    years = list(year_range)
    s_ky,s_sc,s_su,s_ss,s_go = [],[],[],[],[]
    details = []

    for y in years:
        kd  = calc_kyusei_score(honmei_num, keisha_num, y)
        ks  = kd["スコア"]
        sd  = get_year_shichu_detail(nichi_tg, is_shinoh, y)
        cs  = sd["スコア"]
        py  = calc_personal_year(birth_month, birth_day, y)
        ns  = float(_PERSONAL_YEAR_SCORE.get(py,0))
        ssd = calc_shukuyo_score(honmei_xiu, y)
        ss  = ssd["スコア"]
        gs  = _clamp10(ks*w["九星"]+cs*w["四柱"]+ns*w["数秘"]+ss*w["宿曜"])

        s_ky.append(ks);s_sc.append(cs);s_su.append(ns);s_ss.append(ss);s_go.append(gs)
        details.append({
            "year":y,"九星宮位":kd["宮位"],"相性補正":kd["相性補正"],
            "傾斜補正":kd["傾斜補正"],"九星スコア":ks,
            "通変星":sd["通変星"],"十二運":sd["十二運"],"喜忌補正":sd["喜忌補正"],
            "四柱スコア":cs,"個人年数":py,"数秘スコア":ns,
            "立春の宿":ssd["年宿_表示"],"宿との関係":ssd["関係名"],
            "関係説明":ssd["関係説明"],"宿曜スコア":ss,"合成スコア":gs,
        })
    return {"years":years,"九星":s_ky,"四柱":s_sc,"数秘":s_su,
            "宿曜":s_ss,"合成":s_go,"details":details}

def calc_monthly_biorhythm(
    birth_month, birth_day, nichi_tg, is_shinoh,
    honmei_num, keisha_num, honmei_xiu, year,
    weights=None,
):
    """今年の月別バイオリズム計算"""
    if weights is None:
        weights = {"九星":0.35,"四柱":0.35,"宿曜":0.15,"数秘":0.15}
    total_w = sum(weights.values()) or 1.0
    w = {k:v/total_w for k,v in weights.items()}

    months = list(range(1,13))
    s_ky,s_sc,s_su,s_ss,s_go = [],[],[],[],[]
    details = []

    for m in months:
        mk  = get_month_kyusei_score(honmei_num, keisha_num, year, m)
        ks  = mk["スコア"]
        ms  = get_month_shichu_detail(nichi_tg, is_shinoh, year, m)
        cs  = ms["スコア"]
        py  = calc_personal_year(birth_month, birth_day, year)
        ns  = float(_PERSONAL_YEAR_SCORE.get(py,0))
        mss = calc_month_shukuyo_score(honmei_xiu, year, m)
        ss  = mss["スコア"]

        gs  = _clamp10(ks*w["九星"]+cs*w["四柱"]+ns*w["数秘"]+ss*w["宿曜"])
        s_ky.append(ks);s_sc.append(cs);s_su.append(ns);s_ss.append(ss);s_go.append(gs)
        details.append({
            "month":m,"九星スコア":ks,"月命星":mk.get("月命星",""),
            "四柱スコア":cs,"通変星":ms["通変星"],"十二運":ms["十二運"],
            "数秘スコア":ns,"宿曜スコア":ss,
            "月宿":mss.get("月宿_表示",""),"宿との関係":mss.get("関係名",""),
            "合成スコア":gs,
        })
    return {"months":months,"九星":s_ky,"四柱":s_sc,
            "数秘":s_su,"宿曜":s_ss,"合成":s_go,"details":details}

# ============================================================
# 計算コア（UIと分離） — 1人分をまとめて計算
# ============================================================

def compute_person(year, month, day, hour, weights, current_year):
    """1人分の全占術＋バイオリズムを計算して結果dictを返す（Streamlit非依存）"""
    numerology = calc_numerology(year, month, day)
    zodiac     = get_zodiac(month, day)
    shichu     = get_shichusuimei(year, month, day, hour)
    kyusei     = get_kyusei(year, month, day)
    shukuyo    = get_shukuyo(year, month, day)

    bio = calc_biorhythm_scores(
        birth_month=month, birth_day=day,
        nichi_tg=shichu["_raw"]["day_tg"], is_shinoh=shichu["身旺"],
        honmei_num=kyusei["年命星_数"], keisha_num=kyusei["月命星_数"],
        honmei_xiu=shukuyo["宿_raw"],
        year_range=range(current_year-5, current_year+11),
        weights=weights,
    )
    bio_monthly = calc_monthly_biorhythm(
        birth_month=month, birth_day=day,
        nichi_tg=shichu["_raw"]["day_tg"], is_shinoh=shichu["身旺"],
        honmei_num=kyusei["年命星_数"], keisha_num=kyusei["月命星_数"],
        honmei_xiu=shukuyo["宿_raw"], year=current_year,
        weights=weights,
    )
    return {
        "year":year, "month":month, "day":day, "hour":hour,
        "numerology":numerology, "zodiac":zodiac,
        "shichu":shichu, "kyusei":kyusei, "shukuyo":shukuyo,
        "bio":bio, "bio_monthly":bio_monthly, "weights":weights,
    }

# ============================================================
# グラフ
# ============================================================

def build_biorhythm_chart(bio, current_year, visibility, opacity):
    years = bio["years"]
    fig   = go.Figure()
    for key in ["九星","四柱","数秘","宿曜"]:
        vis = True if visibility.get(key, True) else "legendonly"
        fig.add_trace(go.Scatter(
            x=years, y=bio[key], name=key,
            mode="lines+markers", visible=vis,
            line=dict(color=_GRAPH_COLORS[key], width=1.8, dash="dot"),
            marker=dict(size=5), opacity=opacity["individual"],
            hovertemplate=f"<b>{key}</b><br>%{{x}}年: %{{y:+.1f}}<extra></extra>",
        ))
    vis_go = True if visibility.get("合成", True) else "legendonly"
    fig.add_trace(go.Scatter(
        x=years, y=bio["合成"], name="★ 総合運",
        mode="lines+markers", visible=vis_go,
        line=dict(color=_GRAPH_COLORS["合成"], width=4),
        marker=dict(size=9), opacity=opacity["composite"],
        hovertemplate="<b>★総合運</b><br>%{x}年: %{y:+.2f}<extra></extra>",
    ))
    fig.add_vline(x=current_year, line_width=1.5, line_dash="dash",
                  line_color="#ff6b6b",
                  annotation_text=f"  {current_year}年（現在）",
                  annotation_font_color="#ff6b6b", annotation_font_size=11)
    fig.add_hline(y=0, line_width=1, line_color="#555", line_dash="dot")
    fig.update_layout(
        title=dict(text="✦ 統合命占バイオリズム ✦",
                   font=dict(size=16,color="#e2b96f"),x=0.5),
        paper_bgcolor="#0d1526", plot_bgcolor="#0d1526",
        font=dict(color="#c8d8ee",family="Noto Serif JP, serif"),
        legend=dict(orientation="h",x=0.5,xanchor="center",y=-0.15,
                    font=dict(size=12),bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(title="年",gridcolor="#1e3050",tickmode="linear",dtick=1),
        yaxis=dict(title="運勢スコア",gridcolor="#1e3050",range=[-11,11]),
        hovermode="x unified", margin=dict(l=50,r=30,t=60,b=80), height=440,
    )
    return fig

def build_monthly_chart(bio_m, current_month, visibility, opacity):
    months    = bio_m["months"]
    month_lbl = [f"{m}月" for m in months]
    fig = go.Figure()
    for key in ["九星","四柱","数秘","宿曜"]:
        vis = True if visibility.get(key, True) else "legendonly"
        fig.add_trace(go.Scatter(
            x=month_lbl, y=bio_m[key], name=key,
            mode="lines+markers", visible=vis,
            line=dict(color=_GRAPH_COLORS[key],width=1.8,dash="dot"),
            marker=dict(size=5), opacity=opacity["individual"],
            hovertemplate=f"<b>{key}</b><br>%{{x}}: %{{y:+.1f}}<extra></extra>",
        ))
    vis_go = True if visibility.get("合成", True) else "legendonly"
    fig.add_trace(go.Scatter(
        x=month_lbl, y=bio_m["合成"], name="★ 総合運",
        mode="lines+markers", visible=vis_go,
        line=dict(color="#ffffff",width=4), marker=dict(size=9),
        opacity=opacity["composite"],
        hovertemplate="<b>★総合運</b><br>%{x}: %{y:+.2f}<extra></extra>",
    ))
    if 1 <= current_month <= 12:
        fig.add_vline(x=current_month - 1, line_width=1.5,
                      line_dash="dash", line_color="#ff6b6b",
                      annotation_text=f"  {current_month}月（今月）",
                      annotation_font_color="#ff6b6b", annotation_font_size=11)
    fig.add_hline(y=0, line_width=1, line_color="#555", line_dash="dot")
    fig.update_layout(
        title=dict(text="✦ 月別バイオリズム（今年） ✦",
                   font=dict(size=15,color="#e2b96f"),x=0.5),
        paper_bgcolor="#0d1526", plot_bgcolor="#0d1526",
        font=dict(color="#c8d8ee",family="Noto Serif JP, serif"),
        legend=dict(orientation="h",x=0.5,xanchor="center",y=-0.15,
                    font=dict(size=12),bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(gridcolor="#1e3050"),
        yaxis=dict(title="運勢スコア",gridcolor="#1e3050",range=[-11,11]),
        hovermode="x unified", margin=dict(l=50,r=30,t=60,b=80), height=400,
    )
    return fig

# ============================================================
# UI ヘルパー
# ============================================================

def pillar_card_html(label, kanshi, tsuhen):
    tc = "#aaddff" if tsuhen == "（日主）" else "#e2b96f"
    return f"""<div style="background:linear-gradient(160deg,#1a1a2e,#0f2040);
        border:1px solid #e2b96f44;border-radius:10px;padding:18px 10px 14px;
        text-align:center;color:#f0e6d3;min-height:120px;display:flex;
        flex-direction:column;justify-content:space-between;">
      <div style="font-size:0.7rem;color:#e2b96f;letter-spacing:3px;">{label}</div>
      <div style="font-size:2.2rem;font-weight:bold;letter-spacing:6px;">{kanshi}</div>
      <div style="font-size:0.78rem;color:{tc};letter-spacing:1px;">{tsuhen}</div>
    </div>"""

def info_card_html(title, body, accent="#e2b96f"):
    return f"""<div style="background:linear-gradient(135deg,#12192a,#1c2840);
        border-left:3px solid {accent};border-radius:6px;
        padding:14px 16px;margin:8px 0;color:#dde8f5;">
      <div style="font-size:0.72rem;color:{accent};letter-spacing:2px;margin-bottom:6px;">{title}</div>
      <div style="font-size:0.92rem;line-height:1.7;">{body}</div>
    </div>"""

def json_card_html(data: dict, fields: list, accent="#a0c8ff"):
    """JSONデータから指定フィールドをカード表示"""
    if not data: return ""
    lines = []
    for f in fields:
        if f in data:
            lines.append(f"<b style='color:{accent};'>{f}：</b>{data[f]}")
    body = "<br>".join(lines)
    return f"""<div style="background:linear-gradient(135deg,#0e1a30,#162238);
        border-left:3px solid {accent};border-radius:6px;
        padding:12px 16px;margin:6px 0;color:#dde8f5;font-size:0.88rem;line-height:1.8;">
        {body}</div>"""

def section_title(icon, text):
    st.markdown(f"<h3 style='letter-spacing:3px;margin-bottom:4px;'>{icon} {text}</h3>",
                unsafe_allow_html=True)

def score_badge(v):
    if v >= 6:   color,lbl = "#66ccaa","大吉"
    elif v >= 2: color,lbl = "#a0c8ff","吉"
    elif v >= -2:color,lbl = "#888888","平"
    else:        color,lbl = "#ff9eb5","注意"
    return f"<span style='color:{color};font-weight:bold;'>{lbl}（{v:+.1f}）</span>"

# ============================================================
# 今日の運気計算
# ============================================================

def calc_today_fortune(birth_date, honmei_num, keisha_num, honmei_xiu,
                       nichi_tg, is_shinoh):
    today = datetime.date.today()
    y,m,d = today.year, today.month, today.day
    solar  = Solar.fromYmd(y, m, d)
    d_raw  = solar.getLunar().getDayNineStar().toString()
    d_star = KYUSEI_ALIAS.get(d_raw, d_raw)
    d_num  = KYUSEI_MAP.get(d_star, 0)
    sy = get_shukuyo(y, m, d)
    mk = get_month_kyusei_score(honmei_num, keisha_num, y, m)
    ms = get_month_shichu_detail(nichi_tg, is_shinoh, y, m)
    return {
        "日付":today.strftime("%Y年%m月%d日"),
        "日命星":d_star,
        "今日の宿":sy["宿_表示"],
        "今月九星スコア":mk["スコア"],
        "今月四柱スコア":ms["スコア"],
        "通変星":ms["通変星"],
        "十二運":ms["十二運"],
    }

# ============================================================
# PDF スタイル定義
# ============================================================

def _ps(name, **kw):
    base = dict(fontName=_F, fontSize=9, textColor=_C_WHITE, leading=16, spaceAfter=0)
    base.update(kw)
    return ParagraphStyle(name, **base)

_PS_TITLE    = _ps("title",   fontName=_FB, fontSize=18, textColor=_C_GOLD,
                   alignment=1, leading=26, spaceAfter=2)
_PS_SUBTITLE = _ps("sub",     fontSize=9,  textColor=_C_SILVER, alignment=1, leading=14)
_PS_DATE     = _ps("date",    fontSize=8,  textColor=_C_SILVER, alignment=1, leading=13)
_PS_SEC      = _ps("sec",     fontName=_FB, fontSize=11, textColor=_C_GOLD,
                   leading=18, spaceAfter=2)
_PS_BODY     = _ps("body",    fontSize=8.5, leading=15, spaceAfter=2)
_PS_SMALL    = _ps("small",   fontSize=7.5, textColor=_C_SILVER, leading=12)
_PS_SCORE    = _ps("score",   fontName=_FB, fontSize=9, textColor=_C_ACCENT, leading=14)

_PS_A5_TITLE  = _ps("a5t", fontName=_FB, fontSize=14, textColor=_C_GOLD,
                    alignment=1, leading=20, spaceAfter=2)
_PS_A5_BODY   = _ps("a5b", fontSize=8, leading=14, spaceAfter=2)
_PS_A5_SMALL  = _ps("a5s", fontSize=7, textColor=_C_SILVER, leading=11)
_PS_A5_SCORE  = _ps("a5sc",fontName=_FB, fontSize=10, textColor=_C_ACCENT,
                    alignment=1, leading=16)


def _sec_header(text):
    return [
        HRFlowable(width="100%", thickness=0.5, color=_C_GOLD, spaceAfter=3),
        Paragraph(f"◆ {text}", _PS_SEC),
        Spacer(1, 2*mm),
    ]


def _make_table(data, col_ratios, page_width, header_row=True):
    """汎用テーブル生成"""
    cw = [page_width * r for r in col_ratios]
    tbl = Table(data, colWidths=cw)
    styles = [
        ("FONTNAME",     (0,0), (-1,-1), _F),
        ("FONTSIZE",     (0,0), (-1,-1), 8),
        ("LEADING",      (0,0), (-1,-1), 13),
        ("ALIGN",        (0,0), (-1,-1), "CENTER"),
        ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
        ("GRID",         (0,0), (-1,-1), 0.3, colors.HexColor("#c9952a44")),
        ("TOPPADDING",   (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
        ("LEFTPADDING",  (0,0), (-1,-1), 4),
        ("TEXTCOLOR",    (0,0), (-1,-1), _C_WHITE),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [_C_ROW1, _C_ROW2]),
    ]
    if header_row:
        styles += [
            ("BACKGROUND", (0,0), (-1,0), _C_SECTION),
            ("TEXTCOLOR",  (0,0), (-1,0), _C_GOLD),
            ("FONTNAME",   (0,0), (-1,0), _FB),
        ]
    tbl.setStyle(TableStyle(styles))
    return tbl


def _on_page(canvas, doc, page_size):
    """全ページ共通の背景・枠線・フッター描画"""
    W, H = page_size
    canvas.saveState()
    canvas.setFillColor(_C_BG)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    canvas.setStrokeColor(_C_GOLD)
    canvas.setLineWidth(1.0)
    canvas.rect(7*mm, 7*mm, W-14*mm, H-14*mm, fill=0, stroke=1)
    canvas.setStrokeColor(colors.HexColor("#c9952a55"))
    canvas.setLineWidth(0.3)
    canvas.rect(9*mm, 9*mm, W-18*mm, H-18*mm, fill=0, stroke=1)
    canvas.setFont(_F, 7)
    canvas.setFillColor(_C_SILVER)
    canvas.drawCentredString(W/2, 10*mm,
        "統合命占マスター版　｜　数秘術・西洋占星術・四柱推命・九星気学・宿曜占星術")
    canvas.drawRightString(W-10*mm, 10*mm, f"p.{doc.page}")
    canvas.restoreState()


def _fig_to_image(fig, width_mm, height_mm):
    """Plotly図をReportLab Imageオブジェクトに変換"""
    try:
        img_bytes = fig.to_image(format="png", width=int(width_mm*4),
                                  height=int(height_mm*4), scale=2)
        buf = io.BytesIO(img_bytes)
        return RLImage(buf, width=width_mm*mm, height=height_mm*mm)
    except Exception:
        return None


def _score_color_pdf(v):
    if v >= 6:    return _C_ACCENT
    elif v >= 2:  return colors.HexColor("#a0c8ff")
    elif v >= -2: return _C_WHITE
    else:         return _C_WARN


def _profile_section_a4(client_meta, page_w):
    """① A4：相談者プロフィール節（項目があれば返す、無ければ空リスト）"""
    rows = _client_profile_rows(client_meta or {})
    if not rows:
        return []
    flow = _sec_header("相談者プロフィール")
    data = [["項目", "内容"]]
    for k, v in rows:
        data.append([k, v])
    tbl = _make_table(data, [0.3, 0.7], page_w)
    # プロフィール見出し色を紫系に
    tbl.setStyle(TableStyle([("TEXTCOLOR", (0,1), (0,-1), _C_PROFILE),
                             ("FONTNAME",  (0,1), (0,-1), _FB)]))
    flow.append(tbl)
    flow.append(Spacer(1, 4*mm))
    return flow


# ============================================================
# A4 PDF 生成
# ============================================================

def generate_pdf_a4(
    year, month, day, hour, name,
    numerology, zodiac, shichu, kyusei, shukuyo,
    bio, bio_monthly,
    fig_year, fig_month,
    current_year,
    souhyou="",
    client_meta=None,
) -> bytes:
    buf = io.BytesIO()
    W, H = A4
    mg   = 16 * mm
    page_w = W - mg * 2

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=mg, rightMargin=mg,
        topMargin=13*mm, bottomMargin=16*mm,
    )

    def on_p(c, d): _on_page(c, d, A4)

    story = []
    today_str = datetime.date.today().strftime("%Y年%m月%d日")
    name_str  = f"　{name} 様" if name else ""

    _C_SUUHI  = colors.HexColor("#4a7cc7")
    _C_SEIZA  = colors.HexColor("#9b59b6")
    _C_SHUKU  = colors.HexColor("#c0392b")
    _C_KYUSEI = colors.HexColor("#27ae60")
    _C_SHICHU = colors.HexColor("#d35400")

    # ===== ヘッダー =====
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph("✦ 統合命占 鑑定書 ✦", _PS_TITLE))
    story.append(Paragraph(
        "数秘術 ｜ 西洋占星術 ｜ 四柱推命 ｜ 九星気学 ｜ 宿曜占星術", _PS_SUBTITLE))
    story.append(Spacer(1, 1*mm))
    story.append(Paragraph(
        f"鑑定日：{today_str}　｜　"
        f"生年月日：{year}年{month}月{day}日　{hour:02d}:00 生まれ{name_str}",
        _PS_DATE,
    ))
    story.append(Spacer(1, 3*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=_C_GOLD))
    story.append(Spacer(1, 3*mm))

    # ===== 基本命盤サマリー =====
    story += _sec_header("基本命盤サマリー")

    detail_map = {d["year"]: d for d in bio["details"]}
    cur = detail_map.get(current_year, bio["details"][0])

    cw = [page_w * r for r in [0.22, 0.28, 0.22, 0.28]]
    summary_rows = [
        ["ライフパスナンバー", str(numerology),
         "太陽星座", zodiac,
         _C_SUUHI, _C_SUUHI, _C_SEIZA, _C_SEIZA],
        ["本命宿", shukuyo["宿_表示"],
         "本命星（九星）", kyusei["年命星"],
         _C_SHUKU, _C_SHUKU, _C_KYUSEI, _C_KYUSEI],
        ["月命星", kyusei["月命星"],
         "傾斜宮", kyusei["傾斜宮名"],
         _C_KYUSEI, _C_KYUSEI, _C_KYUSEI, _C_KYUSEI],
        ["日干", f"{shichu['日干']}（{shichu['日干_陰陽']}・{shichu['日干_五行']}行）",
         "身旺/身弱", "身旺（強）" if shichu["身旺"] else "身弱（弱）",
         _C_SHICHU, _C_SHICHU, _C_SHICHU, _C_SHICHU],
    ]
    hdr_data = [["項目", "結果", "項目", "結果"]]
    body_data = [[r[0], r[1], r[2], r[3]] for r in summary_rows]
    sum_tbl = Table(hdr_data + body_data, colWidths=cw)
    sum_styles = [
        ("FONTNAME",     (0,0), (-1,-1), _F),
        ("FONTSIZE",     (0,0), (-1,-1), 8.5),
        ("LEADING",      (0,0), (-1,-1), 13),
        ("ALIGN",        (0,0), (-1,-1), "CENTER"),
        ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
        ("GRID",         (0,0), (-1,-1), 0.3, colors.HexColor("#c9952a44")),
        ("TOPPADDING",   (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
        ("BACKGROUND",   (0,0), (-1,0), _C_SECTION),
        ("TEXTCOLOR",    (0,0), (-1,0), _C_GOLD),
        ("FONTNAME",     (0,0), (-1,0), _FB),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [_C_ROW1, _C_ROW2]),
    ]
    for i, r in enumerate(summary_rows, start=1):
        lc, lvc, rc, rvc = r[4], r[5], r[6], r[7]
        sum_styles += [
            ("TEXTCOLOR", (0,i), (0,i), lc),
            ("TEXTCOLOR", (1,i), (1,i), lvc),
            ("TEXTCOLOR", (2,i), (2,i), rc),
            ("TEXTCOLOR", (3,i), (3,i), rvc),
            ("FONTNAME",  (0,i), (0,i), _FB),
            ("FONTNAME",  (2,i), (2,i), _FB),
        ]
    sum_tbl.setStyle(TableStyle(sum_styles))
    story.append(sum_tbl)
    story.append(Spacer(1, 4*mm))

    # ===== ① 相談者プロフィール =====
    story += _profile_section_a4(client_meta, page_w)

    # ===== 四柱推命 =====
    story += _sec_header("四柱推命　命式")
    pillar_data = [
        ["年柱", "月柱", "日柱（日主）", "時柱"],
        [shichu["年柱"], shichu["月柱"], shichu["日柱"], shichu["時柱"]],
        [shichu["通変星"]["年柱"], shichu["通変星"]["月柱"],
         "（日主）", shichu["通変星"]["時柱"]],
    ]
    story.append(_make_table(pillar_data, [0.25]*4, page_w))
    story.append(Spacer(1, 2*mm))

    ts_rows = [["柱", "通変星", "特徴・解釈"]]
    lmap = {"年柱":"年柱（先祖・社会）","月柱":"月柱（親・仕事）","時柱":"時柱（子供・晩年）"}
    for key in ["年柱","月柱","時柱"]:
        tname = shichu["通変星"][key]
        td    = get_tsuhen_desc(tname)
        desc  = td.get("解説", TSUHEN_DESC_BASIC.get(tname,"")) if td else TSUHEN_DESC_BASIC.get(tname,"")
        ts_rows.append([lmap[key], tname, desc])
    story.append(_make_table(ts_rows, [0.30, 0.15, 0.55], page_w))
    story.append(Spacer(1, 4*mm))

    # ===== 九星気学 =====
    story += _sec_header("九星気学")
    cy_ky = calc_kyusei_score(kyusei["年命星_数"], kyusei["月命星_数"], current_year)
    ky_rows = [
        ["本命星（年命星）", kyusei["年命星"], "月命星", kyusei["月命星"]],
        ["傾斜宮", kyusei["傾斜宮名"], "今年の入宮", f"{cy_ky['宮位']}宮"],
        ["本命星×傾斜宮の特徴", kyusei["組み合わせ特徴"], "", ""],
    ]
    story.append(_make_table(ky_rows, [0.22, 0.28, 0.22, 0.28], page_w))
    story.append(Spacer(1, 4*mm))

    # ===== 宿曜占星術 =====
    story += _sec_header("宿曜占星術")
    cy_ss = calc_shukuyo_score(shukuyo["宿_raw"], current_year)
    base_sc  = shukuyo["基本スコア"]
    base_lbl = "大吉" if base_sc>=6 else "吉" if base_sc>=2 else "平" if base_sc>=-2 else "注意"
    sy_rows = [
        ["本命宿", shukuyo["宿_表示"], "固有運気", f"{base_lbl}（{base_sc:+d}）"],
        [f"今年の宿", cy_ss["年宿_表示"],
         "本命宿との関係", f"{cy_ss['関係名']}（補正 {cy_ss['関係補正']:+d}）"],
        ["関係の意味", cy_ss["関係説明"], "", ""],
    ]
    story.append(_make_table(sy_rows, [0.22, 0.28, 0.22, 0.28], page_w))
    story.append(Spacer(1, 4*mm))

    # ===== 年次バイオリズムグラフ =====
    story += _sec_header(f"年次バイオリズム（{current_year-3}〜{current_year+7}年）")
    img_year = _fig_to_image(fig_year, page_w/mm, 80)
    if img_year:
        story.append(img_year)
    else:
        story.append(Paragraph("（グラフ生成にはkaleido が必要です）", _PS_SMALL))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        "※ スコア: +6以上=大吉 / +2〜+5=吉 / ±2未満=平 / -3以下=注意", _PS_SMALL))
    story.append(Spacer(1, 3*mm))

    # ===== 月別バイオリズムグラフ =====
    story += _sec_header(f"月別バイオリズム（{current_year}年）")
    img_month = _fig_to_image(fig_month, page_w/mm, 72)
    if img_month:
        story.append(img_month)
    else:
        story.append(Paragraph("（グラフ生成にはkaleido が必要です）", _PS_SMALL))
    story.append(Spacer(1, 4*mm))

    # ===== 総評 =====
    story += _sec_header("総　評")
    if souhyou and souhyou.strip():
        for line in souhyou.split("\n"):
            line = line.strip()
            if line:
                story.append(Paragraph(line, _PS_BODY))
            else:
                story.append(Spacer(1, 3*mm))
    else:
        story.append(Spacer(1, 40*mm))

    story.append(Spacer(1, 3*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_C_GOLD))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        "※ 本鑑定書は占術的観点からの参考情報です。重要な判断は必ずご自身の意思でお決めください。",
        _PS_SMALL,
    ))

    doc.build(story, onFirstPage=on_p, onLaterPages=on_p)
    buf.seek(0)
    return buf.read()


# ============================================================
# A5 PDF 生成（簡易版）
# ============================================================

def generate_pdf_a5(
    year, month, day, name,
    numerology, zodiac, kyusei, shukuyo,
    bio, bio_monthly,
    fig_year, fig_month,
    current_year,
    client_meta=None,
) -> bytes:
    buf = io.BytesIO()
    W, H = A5
    mg   = 10 * mm
    page_w = W - mg * 2

    doc = SimpleDocTemplate(
        buf, pagesize=A5,
        leftMargin=mg, rightMargin=mg,
        topMargin=11*mm, bottomMargin=13*mm,
    )

    def on_p(c, d): _on_page(c, d, A5)

    story = []
    today_str = datetime.date.today().strftime("%Y年%m月%d日")

    _C_SUUHI  = colors.HexColor("#4a7cc7")
    _C_SEIZA  = colors.HexColor("#9b59b6")
    _C_SHUKU  = colors.HexColor("#c0392b")
    _C_KYUSEI = colors.HexColor("#27ae60")

    # ===== ヘッダー =====
    story.append(Spacer(1, 1*mm))
    story.append(Paragraph("✦ 統合命占 簡易鑑定カード ✦", _PS_A5_TITLE))
    story.append(Paragraph(
        f"鑑定日：{today_str}　｜　{year}年{month}月{day}日 生まれ"
        + (f"　{name} 様" if name else ""),
        _PS_DATE,
    ))
    story.append(Spacer(1, 1*mm))
    story.append(HRFlowable(width="100%", thickness=0.8, color=_C_GOLD))
    story.append(Spacer(1, 2*mm))

    # ===== 命盤サマリー =====
    cw = [page_w * r for r in [0.25, 0.25, 0.25, 0.25]]
    sum_rows_data = [
        ["本命星（九星）", kyusei["年命星"],   "月命星",          kyusei["月命星"],
         _C_KYUSEI, _C_KYUSEI, _C_KYUSEI, _C_KYUSEI],
        ["本命宿（宿曜）", shukuyo["宿_表示"], "ライフパスナンバー", str(numerology),
         _C_SHUKU, _C_SHUKU, _C_SUUHI, _C_SUUHI],
        ["太陽星座",      zodiac,             "傾斜宮",           kyusei["傾斜宮名"],
         _C_SEIZA, _C_SEIZA, _C_KYUSEI, _C_KYUSEI],
    ]
    hdr_d  = [["本命星・本命宿・星座・数秘", "", "", ""]]
    body_d = [[r[0], r[1], r[2], r[3]] for r in sum_rows_data]
    sum_tbl = Table(hdr_d + body_d, colWidths=cw)
    sum_styles = [
        ("FONTNAME",      (0,0), (-1,-1), _F),
        ("FONTSIZE",      (0,0), (-1,-1), 7.5),
        ("LEADING",       (0,0), (-1,-1), 12),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#c9952a44")),
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("BACKGROUND",    (0,0), (-1,0), _C_SECTION),
        ("TEXTCOLOR",     (0,0), (-1,0), _C_GOLD),
        ("FONTNAME",      (0,0), (-1,0), _FB),
        ("SPAN",          (0,0), (-1,0)),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [_C_ROW1, _C_ROW2]),
    ]
    for i, r in enumerate(sum_rows_data, start=1):
        lc, lvc, rc, rvc = r[4], r[5], r[6], r[7]
        sum_styles += [
            ("TEXTCOLOR", (0,i), (0,i), lc),
            ("TEXTCOLOR", (1,i), (1,i), lvc),
            ("TEXTCOLOR", (2,i), (2,i), rc),
            ("TEXTCOLOR", (3,i), (3,i), rvc),
            ("FONTNAME",  (0,i), (0,i), _FB),
            ("FONTNAME",  (2,i), (2,i), _FB),
        ]
    sum_tbl.setStyle(TableStyle(sum_styles))
    story.append(sum_tbl)
    story.append(Spacer(1, 2*mm))

    # ===== ① 相談者プロフィール（コンパクト1行） =====
    prof_rows = _client_profile_rows(client_meta or {})
    if prof_rows:
        txt = "　｜　".join(f"{k}：{v}" for k, v in prof_rows)
        story.append(Paragraph(
            f"<font color='#b08cd9'>【相談者】</font> {txt}",
            _PS_A5_SMALL))
        story.append(Spacer(1, 2*mm))

    # ===== 年次バイオリズムグラフ =====
    story.append(HRFlowable(width="100%", thickness=0.4, color=_C_GOLD))
    story.append(Spacer(1, 1*mm))
    story.append(Paragraph(
        f"◆ 年次バイオリズム（{current_year}〜{current_year+4}年）",
        _ps("a5yh", fontName=_FB, fontSize=8, textColor=_C_GOLD, leading=13)))
    story.append(Spacer(1, 1*mm))
    img_year = _fig_to_image(fig_year, page_w/mm, 52)
    if img_year:
        story.append(img_year)
    else:
        story.append(Paragraph("（kaleidoが必要です）", _PS_A5_SMALL))
    story.append(Spacer(1, 2*mm))

    # ===== 月別バイオリズムグラフ =====
    story.append(HRFlowable(width="100%", thickness=0.4, color=_C_GOLD))
    story.append(Spacer(1, 1*mm))
    story.append(Paragraph(
        f"◆ 月別バイオリズム（{current_year}年）",
        _ps("a5mh", fontName=_FB, fontSize=8, textColor=_C_GOLD, leading=13)))
    story.append(Spacer(1, 1*mm))
    img_month = _fig_to_image(fig_month, page_w/mm, 50)
    if img_month:
        story.append(img_month)
    else:
        story.append(Paragraph("（kaleidoが必要です）", _PS_A5_SMALL))
    story.append(Spacer(1, 2*mm))

    # ===== フッター =====
    story.append(HRFlowable(width="100%", thickness=0.5, color=_C_GOLD))
    story.append(Spacer(1, 1*mm))
    story.append(Paragraph(
        "※ 本カードは占術的観点からの参考情報です。　"
        "スコア: +6以上=大吉 / +2〜+5=吉 / ±2未満=平 / -3以下=注意",
        _PS_A5_SMALL,
    ))

    doc.build(story, onFirstPage=on_p, onLaterPages=on_p)
    buf.seek(0)
    return buf.read()


# ============================================================
# 表示（UI） — 1人分を現在のコンテナに描画
# ============================================================

def render_person(result, meta, current_year, current_month,
                  visibility, opacity, key_prefix, narrow=False):
    """
    1人分の鑑定結果を、いま開いているコンテナ（全幅／カラム／expander）に描画する。
    - key_prefix : ウィジェットキーの一意化（複数人表示での競合防止）
    - narrow     : True のとき（二人横並び）カラムを縦積みに組み替えて窮屈さを回避
    ③の「入れ物」を差し替えても、この関数はそのまま使い回せる設計。
    """
    year   = result["year"];   month = result["month"]
    day    = result["day"];    hour  = result["hour"]
    numerology = result["numerology"]; zodiac = result["zodiac"]
    shichu = result["shichu"]; kyusei = result["kyusei"]
    shukuyo = result["shukuyo"]
    bio = result["bio"]; bio_monthly = result["bio_monthly"]
    weights = result["weights"]
    disp_name = meta.get("name", "") if meta else ""

    detail_map = {d["year"]: d for d in bio["details"]}
    cur = detail_map.get(current_year, bio["details"][0])

    # ---- 個人ヘッダー ----
    name_disp = f"　{disp_name} 様" if disp_name else ""
    st.markdown(
        f"<div style='text-align:center;color:#c9952a;font-size:1.02rem;"
        f"letter-spacing:2px;padding:6px 0;'>"
        f"✦ {year}年{month}月{day}日　{hour:02d}:00 生まれ{name_disp} の命盤 ✦<br>"
        f"<span style='font-size:0.9rem;color:#66ccaa;'>"
        f"今年（{current_year}年）の総合運: {score_badge(cur['合成スコア'])}</span></div>",
        unsafe_allow_html=True,
    )

    # ---- ① 相談者プロフィール表示 ----
    prof_rows = _client_profile_rows(meta or {})
    if prof_rows:
        body = "　｜　".join(f"<b>{k}</b>：{v}" for k, v in prof_rows)
        st.markdown(info_card_html("相談者プロフィール", body, accent="#b08cd9"),
                    unsafe_allow_html=True)
    st.divider()

    # =====================================================
    # A. 数秘術 / 西洋占星術 / 宿曜
    # =====================================================
    section_title("🔢", "数秘術 ／ 西洋占星術 ／ 宿曜占星術")
    if narrow:
        st.metric("ライフパスナンバー", numerology)
        st.metric("太陽星座", zodiac)
        st.metric("本命宿", shukuyo["宿_表示"])
    else:
        ca, cb, cc = st.columns(3)
        with ca: st.metric("ライフパスナンバー", numerology)
        with cb: st.metric("太陽星座", zodiac)
        with cc: st.metric("本命宿", shukuyo["宿_表示"])

    num_desc = get_numerology_desc(numerology)
    if num_desc:
        with st.expander(f"📖 数秘 {numerology} の詳細解説"):
            fields = ["キーワード","性格","才能","課題","仕事","恋愛","金運"]
            st.markdown(json_card_html(num_desc, fields, accent="#a0c8ff"),
                        unsafe_allow_html=True)

    zod_desc = get_zodiac_desc(zodiac)
    if zod_desc:
        with st.expander(f"📖 {zodiac} の詳細解説"):
            fields = ["期間","支配星","元素","キーワード","性格","才能","恋愛","仕事","今年のポイント"]
            st.markdown(json_card_html(zod_desc, fields, accent="#e2b96f"),
                        unsafe_allow_html=True)

    honmei_xiu = shukuyo["宿_raw"]
    base_sc    = shukuyo["基本スコア"]
    base_color = "#66ccaa" if base_sc>=2 else "#ff9eb5" if base_sc<-2 else "#aaaaaa"
    base_lbl   = "大吉" if base_sc>=6 else "吉" if base_sc>=2 else "平" if base_sc>=-2 else "注意"
    cy_ss      = calc_shukuyo_score(honmei_xiu, current_year)

    st.markdown(info_card_html(
        f"本命宿の特性　／　今年（{current_year}年）との関係",
        f"本命宿の固有運気：<span style='color:{base_color};font-weight:bold;'>"
        f"{base_lbl}（{base_sc:+d}）</span><br>"
        f"今年の宿：{cy_ss['年宿_表示']}　→　"
        f"<span style='color:#e2b96f;font-weight:bold;'>{cy_ss['関係名']}</span>　"
        f"（補正 {cy_ss['関係補正']:+d}）<br>"
        f"<span style='font-size:0.85rem;color:#aac8e8;'>{cy_ss['関係説明']}</span>",
        accent="#ff9eb5",
    ), unsafe_allow_html=True)

    shu_desc = get_shukuyo_desc(honmei_xiu) if honmei_xiu else {}
    if shu_desc:
        with st.expander(f"📖 {shukuyo['宿_表示']} の詳細解説"):
            fields = ["読み","象徴","性格","恋愛","仕事","金運","今年のテーマ"]
            st.markdown(json_card_html(shu_desc, fields, accent="#ff9eb5"),
                        unsafe_allow_html=True)
    st.divider()

    # =====================================================
    # B. 四柱推命
    # =====================================================
    section_title("🏯", "四柱推命")
    if narrow:
        r1 = st.columns(2)
        with r1[0]:
            st.markdown(pillar_card_html("年柱", shichu["年柱"], shichu["通変星"]["年柱"]),
                        unsafe_allow_html=True)
        with r1[1]:
            st.markdown(pillar_card_html("月柱", shichu["月柱"], shichu["通変星"]["月柱"]),
                        unsafe_allow_html=True)
        r2 = st.columns(2)
        with r2[0]:
            st.markdown(pillar_card_html("日柱", shichu["日柱"], shichu["通変星"]["日柱"]),
                        unsafe_allow_html=True)
        with r2[1]:
            st.markdown(pillar_card_html("時柱", shichu["時柱"], shichu["通変星"]["時柱"]),
                        unsafe_allow_html=True)
    else:
        p1,p2,p3,p4 = st.columns(4)
        for col,key in zip([p1,p2,p3,p4],["年柱","月柱","日柱","時柱"]):
            with col:
                st.markdown(pillar_card_html(key,shichu[key],shichu["通変星"][key]),
                            unsafe_allow_html=True)
    st.caption("※ 月柱は節入り基準のため、誕生日が月初・月末の場合は前後の月柱になることがあります。")
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    def _render_daishu():
        shinoh_c = "#66ccaa" if shichu["身旺"] else "#ff9eb5"
        st.markdown(info_card_html(
            "日干（日主）",
            f"<span style='font-size:1.8rem;font-weight:bold;letter-spacing:4px;'>"
            f"{shichu['日干']}</span><br>"
            f"<span style='font-size:0.82rem;color:#aac8e8;'>"
            f"{shichu['日干_陰陽']}・{shichu['日干_五行']}行</span><br>"
            f"<span style='font-size:0.82rem;color:{shinoh_c};'>"
            f"{'身旺（強）' if shichu['身旺'] else '身弱（弱）'}</span>",
            accent="#aaddff",
        ), unsafe_allow_html=True)

    def _render_tsuhen():
        st.markdown("**🔍 通変星の解釈**　―　日主から見た各柱の意味")
        label_map = {"年柱":"年柱（先祖・社会）","月柱":"月柱（親・仕事）","時柱":"時柱（子供・晩年）"}
        for key in ["年柱","月柱","時柱"]:
            tname    = shichu["通変星"][key]
            ts_d     = get_tsuhen_desc(tname)
            basic    = TSUHEN_DESC_BASIC.get(tname,"")
            body     = ts_d.get("解説", basic) if ts_d else basic
            extra    = ""
            if ts_d:
                extra = f"<br><span style='font-size:0.82rem;color:#aac8e8;'>" \
                        f"強み: {ts_d.get('強み','')}　注意: {ts_d.get('注意点','')}</span>"
            st.markdown(info_card_html(
                f"{label_map[key]}　→　{tname}", body + extra,
            ), unsafe_allow_html=True)

    if narrow:
        _render_daishu()
        _render_tsuhen()
    else:
        dai_col, ts_col = st.columns([1,3])
        with dai_col: _render_daishu()
        with ts_col:  _render_tsuhen()
    st.divider()

    # =====================================================
    # C. 九星気学
    # =====================================================
    section_title("🌀", "九星気学")

    def _render_ky_left():
        st.metric("本命星（年命星）", kyusei["年命星"])
        st.metric("月命星",           kyusei["月命星"])
        st.metric("日命星",           kyusei["日命星"])
        cy_ky = calc_kyusei_score(kyusei["年命星_数"],kyusei["月命星_数"],current_year)
        st.markdown(info_card_html(
            f"今年（{current_year}年）の宮位と補正",
            f"入宮：<b>{cy_ky['宮位']}宮</b>　基本 {cy_ky['基本']:+d}　"
            f"五行相性 {cy_ky['相性補正']:+d}　傾斜 {cy_ky['傾斜補正']:+d}　"
            f"→　<b>{cy_ky['スコア']:+.1f}</b>",
            accent="#66ccaa",
        ), unsafe_allow_html=True)

    def _render_ky_right():
        st.markdown(info_card_html(
            f"傾斜宮：{kyusei['傾斜宮名']}", kyusei["傾斜キーワード"], accent="#66ccaa",
        ), unsafe_allow_html=True)
        st.markdown(info_card_html(
            "本命星 × 傾斜宮 の特徴", kyusei["組み合わせ特徴"], accent="#a0c8ff",
        ), unsafe_allow_html=True)

    if narrow:
        _render_ky_left()
        _render_ky_right()
    else:
        kg1,kg2 = st.columns(2)
        with kg1: _render_ky_left()
        with kg2: _render_ky_right()

    ky_desc = get_kyusei_desc(kyusei["年命星"])
    if ky_desc:
        with st.expander(f"📖 {kyusei['年命星']} の詳細解説"):
            fields = ["象徴","基本性格","才能","対人","仕事運","金運","開運の鍵"]
            st.markdown(json_card_html(ky_desc, fields, accent="#66ccaa"),
                        unsafe_allow_html=True)
    st.divider()

    # =====================================================
    # D. 統合バイオリズム（年次）
    # =====================================================
    section_title("📈", "統合バイオリズム（年次）")
    bio_range = st.slider(
        "表示年の範囲", current_year-5, current_year+10,
        (current_year-3, current_year+7), step=1, key=f"{key_prefix}_range",
    )
    display_bio = calc_biorhythm_scores(
        birth_month=month, birth_day=day,
        nichi_tg=shichu["_raw"]["day_tg"], is_shinoh=shichu["身旺"],
        honmei_num=kyusei["年命星_数"], keisha_num=kyusei["月命星_数"],
        honmei_xiu=shukuyo["宿_raw"],
        year_range=range(bio_range[0], bio_range[1]+1),
        weights=weights,
    )
    fig = build_biorhythm_chart(display_bio, current_year, visibility, opacity)
    st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_fig_year")
    st.markdown("""<div style='font-size:0.8rem;color:#666;line-height:1.8;'>
      🟢 +6以上=大吉 ／ 🔵 +2〜+5=吉 ／ ⚪ ±2未満=平 ／ 🔴 -3以下=注意
    </div>""", unsafe_allow_html=True)

    with st.expander("📋 年別詳細データ"):
        hdr = ("| 年 | 総合 | 九星 | 四柱 | 数秘 | 宿曜 | 通変星 | 十二運 | 宿との関係 |")
        sep = "|---|---|---|---|---|---|---|---|---|"
        rows = [hdr,sep]
        for d in display_bio["details"]:
            y    = d["year"]
            mark = " ◀" if y==current_year else ""
            rows.append(
                f"| **{y}{mark}** | **{score_fmt(d['合成スコア'])}** "
                f"| {score_fmt(d['九星スコア'])} | {score_fmt(d['四柱スコア'])} "
                f"| {score_fmt(d['数秘スコア'])} | {score_fmt(d['宿曜スコア'])} "
                f"| {d['通変星']} | {d['十二運']} | {d['宿との関係']} |"
            )
        st.markdown("\n".join(rows))
    st.divider()

    # =====================================================
    # E. 月別バイオリズム（今年）
    # =====================================================
    section_title("🌙", f"月別バイオリズム（{current_year}年）")
    fig_m = build_monthly_chart(bio_monthly, current_month, visibility, opacity)
    st.plotly_chart(fig_m, use_container_width=True, key=f"{key_prefix}_fig_month")

    with st.expander("📋 月別詳細データ"):
        hdr_m = "| 月 | 総合 | 九星 | 四柱 | 宿曜 | 通変星 | 十二運 | 月宿 | 宿との関係 |"
        sep_m = "|---|---|---|---|---|---|---|---|---|"
        rows_m = [hdr_m, sep_m]
        for d in bio_monthly["details"]:
            mm_ = d["month"]
            mark = " ◀" if mm_==current_month else ""
            rows_m.append(
                f"| **{mm_}月{mark}** | **{score_fmt(d['合成スコア'])}** "
                f"| {score_fmt(d['九星スコア'])} | {score_fmt(d['四柱スコア'])} "
                f"| {score_fmt(d['宿曜スコア'])} | {d['通変星']} | {d['十二運']} "
                f"| {d.get('月宿','―')} | {d.get('宿との関係','―')} |"
            )
        st.markdown("\n".join(rows_m))
    st.caption(
        "※ 月別バイオリズムは節入り・新月基準の近似計算です。"
        "数秘スコアは年単位のため月ごとに同値となります。")
    st.divider()

    # =====================================================
    # F. PDF 出力
    # =====================================================
    section_title("📄", "PDF 鑑定書の出力")
    st.markdown("**📝 総評（A4版に掲載されます）**")
    souhyou = st.text_area(
        "総評を入力してください（空白の場合は記入欄として出力されます）",
        height=120,
        placeholder="例：全体的に安定した運気の持ち主です。今年は九星が8宮に入り大きな変化の年となります。…",
        key=f"{key_prefix}_souhyou",
    )
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    def _render_pdf_a4():
        st.markdown(info_card_html(
            "📋 A4版（詳細鑑定書）",
            "命盤サマリー（色分け）・相談者プロフィール・四柱推命・九星気学・宿曜占星術・"
            "年次グラフ・月別グラフ・総評欄を収録。お客様への提出・保存用に。",
            accent="#66ccaa",
        ), unsafe_allow_html=True)
        if st.button("　📄 A4版PDFを生成　", use_container_width=True, key=f"{key_prefix}_btn_a4"):
            with st.spinner("A4 PDF を生成中…（グラフ画像変換に少し時間がかかります）"):
                try:
                    _fig_y = build_biorhythm_chart(display_bio, current_year, visibility, opacity)
                    _fig_m = build_monthly_chart(bio_monthly, current_month, visibility, opacity)
                    pdf_bytes_a4 = generate_pdf_a4(
                        year=year, month=month, day=day, hour=hour, name=disp_name,
                        numerology=numerology, zodiac=zodiac,
                        shichu=shichu, kyusei=kyusei, shukuyo=shukuyo,
                        bio=bio, bio_monthly=bio_monthly,
                        fig_year=_fig_y, fig_month=_fig_m,
                        current_year=current_year, souhyou=souhyou,
                        client_meta=meta,
                    )
                    st.session_state[f"pdf_a4_{key_prefix}"] = pdf_bytes_a4
                    st.success("✅ A4版PDF 生成完了！")
                except Exception as e:
                    st.error(f"PDF生成エラー: {e}")
        if f"pdf_a4_{key_prefix}" in st.session_state:
            name_part = f"_{disp_name}" if disp_name else ""
            st.download_button(
                label="　⬇️ A4版をダウンロード　",
                data=st.session_state[f"pdf_a4_{key_prefix}"],
                file_name=f"fortune_a4{name_part}_{year}{month:02d}{day:02d}.pdf",
                mime="application/pdf", use_container_width=True,
                key=f"{key_prefix}_dl_a4",
            )

    def _render_pdf_a5():
        st.markdown(info_card_html(
            "🃏 A5版（簡易鑑定カード）",
            "本命星・本命宿・ライフパスナンバー・星座を色分け表示。相談者プロフィール、"
            "年次バイオリズム（今年〜5年）・月別バイオリズム（今年）を収録。手元メモや簡易お渡し用に。",
            accent="#a0c8ff",
        ), unsafe_allow_html=True)
        if st.button("　🃏 A5版PDFを生成　", use_container_width=True, key=f"{key_prefix}_btn_a5"):
            with st.spinner("A5 PDF を生成中…"):
                try:
                    _fig_y5_data = calc_biorhythm_scores(
                        birth_month=month, birth_day=day,
                        nichi_tg=shichu["_raw"]["day_tg"], is_shinoh=shichu["身旺"],
                        honmei_num=kyusei["年命星_数"], keisha_num=kyusei["月命星_数"],
                        honmei_xiu=shukuyo["宿_raw"],
                        year_range=range(current_year, current_year+5),
                        weights=weights,
                    )
                    fig_y5 = build_biorhythm_chart(_fig_y5_data, current_year, visibility, opacity)
                    fig_m5 = build_monthly_chart(bio_monthly, current_month, visibility, opacity)
                    pdf_bytes_a5 = generate_pdf_a5(
                        year=year, month=month, day=day, name=disp_name,
                        numerology=numerology, zodiac=zodiac,
                        kyusei=kyusei, shukuyo=shukuyo,
                        bio=bio, bio_monthly=bio_monthly,
                        fig_year=fig_y5, fig_month=fig_m5,
                        current_year=current_year, client_meta=meta,
                    )
                    st.session_state[f"pdf_a5_{key_prefix}"] = pdf_bytes_a5
                    st.success("✅ A5版PDF 生成完了！")
                except Exception as e:
                    st.error(f"PDF生成エラー: {e}")
        if f"pdf_a5_{key_prefix}" in st.session_state:
            name_part = f"_{disp_name}" if disp_name else ""
            st.download_button(
                label="　⬇️ A5版をダウンロード　",
                data=st.session_state[f"pdf_a5_{key_prefix}"],
                file_name=f"fortune_a5{name_part}_{year}{month:02d}{day:02d}.pdf",
                mime="application/pdf", use_container_width=True,
                key=f"{key_prefix}_dl_a5",
            )

    if narrow:
        _render_pdf_a4()
        _render_pdf_a5()
    else:
        pdf_col1, pdf_col2 = st.columns(2)
        with pdf_col1: _render_pdf_a4()
        with pdf_col2: _render_pdf_a5()

    st.caption("※ グラフ画像の埋め込みには kaleido ライブラリが必要です（pip install kaleido）")


# ============================================================
# 入力ブロック（1人分）
# ============================================================

def person_input_block(i, clients):
    """1人分の入力欄。戻り値: {name, birth, hour, meta}"""
    st.markdown(f"##### 👤 {i+1}人目")
    opts = ["（新規入力）"] + [f"{c['name']}（{c['birth']}）" for c in clients]
    sel = st.selectbox("相談者", opts, key=f"pin_sel_{i}")

    if sel == "（新規入力）":
        name  = st.text_input("お名前（任意）", key=f"pin_name_{i}")
        birth = st.date_input(
            "生年月日", value=datetime.date(1990,1,1),
            min_value=datetime.date(1900,1,1),
            max_value=datetime.date.today(), key=f"pin_birth_{i}")
        meta = {"name":name,"blood_type":"","marital":"",
                "relationship":"","breakup_when":"","breakup_why":""}
    else:
        idx = opts.index(sel) - 1
        c   = clients[idx]
        name  = c["name"]
        birth = datetime.date.fromisoformat(c["birth"])
        st.caption(f"🎂 {c['birth']}")
        pr = _client_profile_rows(c)
        if pr:
            st.caption("　｜　".join(f"{k}:{v}" for k,v in pr))
        if c.get("memo"):
            st.caption(f"📝 {c['memo']}")
        meta = {
            "name":name,
            "blood_type":c.get("blood_type",""),
            "marital":c.get("marital",""),
            "relationship":c.get("relationship",""),
            "breakup_when":c.get("breakup_when",""),
            "breakup_why":c.get("breakup_why",""),
        }

    hour = st.selectbox("出生時", list(range(24)), index=12,
                        format_func=lambda h:f"{h:02d}:00", key=f"pin_hour_{i}")
    return {"name":name, "birth":birth, "hour":hour, "meta":meta}


# ============================================================
# 限定公開用パスワードゲート
# ============================================================

def _check_password():
    """
    限定公開用の簡易認証。
    st.secrets に app_password が設定されていればゲートを表示し、
    無ければ素通り（ローカル開発時はパスワード不要）。
    """
    try:
        expected = st.secrets.get("app_password", None)
    except Exception:
        expected = None
    if not expected:
        return True  # 未設定（ローカル等）はそのまま通す
    if st.session_state.get("_auth_ok"):
        return True

    st.markdown(
        "<div style='text-align:center;padding:40px 0 10px;'>"
        "<h2 style='letter-spacing:4px;'>🔒 統合命占マスター版</h2>"
        "<p style='color:#888;'>このアプリは限定公開です。パスワードを入力してください。</p>"
        "</div>", unsafe_allow_html=True)
    pw = st.text_input("パスワード", type="password", key="_auth_pw",
                       label_visibility="collapsed", placeholder="パスワード")
    if pw:
        if pw == expected:
            st.session_state["_auth_ok"] = True
            st.rerun()
        else:
            st.error("パスワードが違います。")
    st.stop()


# ============================================================
# メインアプリ
# ============================================================

def main():
    st.set_page_config(
        page_title="統合命占マスター", page_icon="🔯",
        layout="wide", initial_sidebar_state="expanded",
    )
    _check_password()   # 限定公開ゲート（secretsにapp_passwordが有る時だけ作動）
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+JP:wght@400;700&display=swap');
    html,body,[class*="css"]{font-family:'Noto Serif JP',serif;}
    .stMetric label{font-size:0.78rem;letter-spacing:1px;}
    div[data-testid="stMetricValue"]{font-size:1.7rem;}
    .stButton>button{
        background:linear-gradient(135deg,#7c5c20,#c9952a);
        color:#fff;border:none;font-family:'Noto Serif JP',serif;
        letter-spacing:4px;font-size:1rem;padding:10px 0;}
    .stButton>button:hover{opacity:0.85;}
    </style>""", unsafe_allow_html=True)

    # =========================================================
    # サイドバー
    # =========================================================
    with st.sidebar:
        st.markdown("## ⚙️ マスター設定")
        st.markdown("---")

        # ---- 相談者 新規登録（① 追加項目つき） ----
        st.markdown("### 👤 相談者 新規登録")
        with st.expander("＋ 新しい相談者を登録", expanded=False):
            c_name  = st.text_input("お名前", placeholder="例：山田 太郎", key="reg_name")
            c_birth = st.date_input(
                "生年月日", value=datetime.date(1990,1,1),
                min_value=datetime.date(1900,1,1),
                max_value=datetime.date.today(), key="reg_birth")
            c_blood = st.selectbox("血液型", BLOOD_OPTIONS, key="reg_blood")
            c_marital  = st.radio("婚姻状況", MARITAL_OPTIONS, horizontal=True, key="reg_marital")
            c_relation = st.radio("交際状況", RELATION_OPTIONS, horizontal=True, key="reg_relation")
            c_bwhen = st.text_input("別れた時期", placeholder="例：2023年春 / 半年前", key="reg_bwhen")
            c_bwhy  = st.text_area("別れた理由", placeholder="例：価値観の違い", height=70, key="reg_bwhy")
            c_memo  = st.text_area("メモ", placeholder="相談内容・特記事項など", height=70, key="reg_memo")
            if st.button("💾 相談者を保存", use_container_width=True, key="reg_save"):
                if c_name:
                    ok = add_client(c_name, c_birth, c_memo,
                                    blood_type=c_blood, marital=c_marital,
                                    relationship=c_relation,
                                    breakup_when=c_bwhen, breakup_why=c_bwhy)
                    if ok:
                        st.success(f"✅ {c_name} さんを登録しました")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.warning("同じ名前・生年月日の方が既に登録されています")
                else:
                    st.warning("お名前を入力してください")

        # ---- 登録済み相談者の管理 ----
        clients = load_clients()
        st.markdown("### 👥 登録済み相談者")
        if clients:
            del_opts = ["（選択）"] + [f"{c['name']}（{c['birth']}）" for c in clients]
            del_sel = st.selectbox("削除する相談者", del_opts, key="del_sel")
            if del_sel != "（選択）":
                didx = del_opts.index(del_sel) - 1
                if st.button("🗑️ この相談者を削除", use_container_width=True, key="del_btn"):
                    clients.pop(didx)
                    save_clients(clients)
                    st.success("削除しました")
                    st.rerun()
        else:
            st.caption("まだ登録がありません。")

        st.markdown("---")

        # ---- バイオリズム比率ツマミ（全員共通） ----
        st.markdown("### ⚖️ バイオリズム比率")
        w_ky = st.slider("九星気学", 0, 100, 35, 5, key="w_ky")
        w_sc = st.slider("四柱推命", 0, 100, 35, 5, key="w_sc")
        w_ss = st.slider("宿曜占星術", 0, 100, 15, 5, key="w_ss")
        w_su = st.slider("数秘術", 0, 100, 15, 5, key="w_su")
        total_w = w_ky + w_sc + w_ss + w_su
        if total_w > 0:
            st.caption(f"合計: {total_w}  →  実効比率: 九星{w_ky/total_w*100:.0f}% / "
                       f"四柱{w_sc/total_w*100:.0f}% / 宿曜{w_ss/total_w*100:.0f}% / "
                       f"数秘{w_su/total_w*100:.0f}%")
        else:
            st.warning("比率の合計が0です。いずれかを上げてください。")

        st.markdown("---")

        # ---- グラフ表示調整（全員共通） ----
        st.markdown("### 🎨 グラフ表示調整")
        st.markdown("**系列のON/OFF**")
        vis_ky = st.checkbox("九星気学", True, key="vis_ky")
        vis_sc = st.checkbox("四柱推命", True, key="vis_sc")
        vis_ss = st.checkbox("宿曜占星術", True, key="vis_ss")
        vis_su = st.checkbox("数秘術", True, key="vis_su")
        vis_go = st.checkbox("★ 総合運", True, key="vis_go")
        st.markdown("**透明度**")
        op_individual = st.slider("個別系列の透明度", 0.1, 1.0, 0.75, 0.05, key="op_ind")
        op_composite  = st.slider("総合運の透明度", 0.1, 1.0, 1.0, 0.05, key="op_comp")

        visibility = {"九星":vis_ky,"四柱":vis_sc,"宿曜":vis_ss,"数秘":vis_su,"合成":vis_go}
        opacity    = {"individual":op_individual,"composite":op_composite}

        st.markdown("---")
        st.markdown("""<div style='font-size:0.75rem;color:#666;line-height:1.8;'>
        【JSONファイル】<br>
        data/shukuyo.json　宿曜27宿<br>
        data/kyusei.json　九星気学<br>
        data/general.json　数秘・星座・通変星
        </div>""", unsafe_allow_html=True)

    # =========================================================
    # ヘッダー + 今日の運気
    # =========================================================
    st.markdown("""<div style='text-align:center;padding:12px 0 4px;'>
      <h1 style='letter-spacing:8px;font-size:2rem;margin:0;'>✦ 統合命占　マスター版 ✦</h1>
      <p style='color:#888;font-size:0.85rem;letter-spacing:3px;margin-top:6px;'>
        数秘術 ｜ 西洋占星術 ｜ 四柱推命 ｜ 九星気学 ｜ 宿曜占星術
      </p></div>""", unsafe_allow_html=True)

    _today = datetime.date.today()
    _td_solar  = Solar.fromYmd(_today.year, _today.month, _today.day)
    _td_d_raw  = _td_solar.getLunar().getDayNineStar().toString()
    _td_d_star = KYUSEI_ALIAS.get(_td_d_raw, _td_d_raw)
    _td_shuku  = get_shukuyo(_today.year, _today.month, _today.day)

    st.markdown(
        f"""<div style='text-align:center;background:linear-gradient(135deg,#0a1525,#12223a);
        border:1px solid #c9952a44;border-radius:8px;padding:10px;margin:8px 0;'>
        <span style='color:#888;font-size:0.8rem;letter-spacing:2px;'>
        📅 今日 {_today.strftime('%Y年%m月%d日')} の天象</span><br>
        <span style='color:#e2b96f;font-size:1.1rem;font-weight:bold;'>
        日命星: {_td_d_star}</span>
        <span style='color:#666;'> ｜ </span>
        <span style='color:#ff9eb5;font-size:1.1rem;font-weight:bold;'>
        今日の宿: {_td_shuku['宿_表示']}</span>
        </div>""",
        unsafe_allow_html=True,
    )
    st.divider()

    # =========================================================
    # ② モード選択
    # =========================================================
    mode = st.radio(
        "👥 表示モード", ["一人用", "二人用", "多人数用（最大5人）"],
        horizontal=True, key="mode_sel",
    )
    if mode == "一人用":
        n_people = 1
    elif mode == "二人用":
        n_people = 2
    else:
        n_people = st.slider("人数", 2, 5, 3, key="multi_n")

    # ---- 人数分の入力欄 ----
    inputs = []
    if n_people <= 2:
        cols = st.columns(n_people)
        for i in range(n_people):
            with cols[i]:
                with st.container(border=True):
                    inputs.append(person_input_block(i, clients))
    else:
        # 多人数：2列グリッドで入力欄を配置
        for row_start in range(0, n_people, 2):
            gcols = st.columns(2)
            for j, gc in enumerate(gcols):
                i = row_start + j
                if i >= n_people:
                    break
                with gc:
                    with st.container(border=True):
                        inputs.append(person_input_block(i, clients))

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    run = st.button("　占　う　", use_container_width=True, type="primary", key="run_all")

    current_year  = datetime.date.today().year
    current_month = datetime.date.today().month

    # ---- 「占う」実行 → 全員分を計算して保存 ----
    if run:
        weights = {"九星":w_ky,"四柱":w_sc,"宿曜":w_ss,"数秘":w_su}
        persons = []
        with st.spinner("天の声を聴いています…"):
            try:
                for inp in inputs:
                    b = inp["birth"]
                    res = compute_person(b.year, b.month, b.day, inp["hour"],
                                         weights, current_year)
                    persons.append({"result": res, "meta": inp["meta"]})
            except Exception as e:
                st.error(f"計算エラー: {e}"); st.stop()
        st.session_state["persons"] = persons
        st.session_state["mode"]    = mode

    # ---- 結果がなければ案内 ----
    if "persons" not in st.session_state:
        st.info("相談者を選ぶ／入力して「占う」ボタンを押してください。")
        return

    persons = st.session_state["persons"]
    np_ = len(persons)
    st.divider()

    # =========================================================
    # ② / ③ 結果表示
    # =========================================================
    if np_ == 1:
        # 一人用：全幅
        render_person(persons[0]["result"], persons[0]["meta"],
                      current_year, current_month, visibility, opacity,
                      key_prefix="p0", narrow=False)

    elif np_ == 2:
        # 二人用：横並び（narrow=Trueで窮屈さ回避）
        c1, c2 = st.columns(2)
        with c1:
            render_person(persons[0]["result"], persons[0]["meta"],
                          current_year, current_month, visibility, opacity,
                          key_prefix="p0", narrow=True)
        with c2:
            render_person(persons[1]["result"], persons[1]["meta"],
                          current_year, current_month, visibility, opacity,
                          key_prefix="p1", narrow=True)

    else:
        # 多人数用（③）：各人を expander で。開閉でその場確認、閉じれば最小化。
        st.markdown(
            "<div style='color:#888;font-size:0.85rem;'>"
            "▼ 各パネルの見出しをクリックで開閉できます（開いた人だけ表示／閉じれば最小化）。</div>",
            unsafe_allow_html=True,
        )
        for i, p in enumerate(persons):
            nm = p["meta"].get("name") or f"{i+1}人目"
            r  = p["result"]
            cur = {d["year"]: d for d in r["bio"]["details"]}.get(
                current_year, r["bio"]["details"][0])
            sc = cur["合成スコア"]
            tag = "大吉" if sc>=6 else "吉" if sc>=2 else "平" if sc>=-2 else "注意"
            title = (f"👤 {i+1}人目：{nm}　"
                     f"（{r['year']}年{r['month']}月{r['day']}日 / 今年の総合運 {tag} {sc:+.1f}）")
            with st.expander(title, expanded=(i == 0)):
                render_person(r, p["meta"], current_year, current_month,
                              visibility, opacity, key_prefix=f"p{i}", narrow=False)


if __name__ == "__main__":
    main()
