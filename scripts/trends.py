#!/usr/bin/env python3
"""
مقترِح زوايا ترند أسبوعية لزيادة — استشاري فقط.

لا يكتب محتوى ولا رقماً ولا يلمس بفر. يجمع عناوين رائجة في مجال التجارة
الإلكترونية والتطبيقات في السعودية من مصادر RSS مجانية، يرتّبها بحسب
صلتها بمجال زيادة، ويقترح زاوية لكل عنوان. صاحب الحساب (أو وكيل خطة
السوشال ميديا) يحوّل الزاوية المناسبة إلى وحدة في content/quarter.yml
ملتزماً بقواعد الأرقام والهوية.

لماذا استشاري لا تلقائي: الأرقام المعتمدة محدودة، والادعاءات ممنوعة إلا
موثّقة. حقن عنوان ترند مباشرة في منشور يخاطر بكسر هاتين القاعدتين، فيبقى
القرار بشرياً.

المصادر (RSS مجانية، لا مفاتيح):
  - Google News عربي: بحث بمصطلحات المجال، gl=SA
  - Google Trends اليومي: geo=SA
  - مدونتا زد وسلة (إن توفّر RSS)

    python3 scripts/trends.py                 # يطبع أفضل الزوايا
    python3 scripts/trends.py --json out/trends.json
    python3 scripts/trends.py --top 15
"""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 20
UA = "Mozilla/5.0 (ziadah-trends/1.0)"

# مصطلحات المجال: عنوان يحوي أكثر منها = أوثق صلة بزيادة
KEYWORDS = [
    "تجارة إلكترونية", "التجارة الإلكترونية", "متجر إلكتروني", "متجر الكتروني",
    "متاجر", "تسوق", "التسوق", "أونلاين", "اونلاين", "سلة", "زد", "زيد",
    "عروض", "خصم", "خصومات", "تخفيضات", "الجمعة البيضاء", "كوبون", "شحن",
    "طلب", "الطلب", "سلة التسوق", "تجار", "التاجر", "التجّار", "مبيعات",
    "بيع", "منتجات", "منتج", "دفع", "المدفوعات", "ولاء", "العملاء", "عميل",
    "موسم", "المواسم", "اليوم الوطني", "يوم التأسيس", "رمضان", "العيد",
    "العودة للمدارس", "ذكاء اصطناعي", "تطبيق", "تطبيقات", "ريادة الأعمال",
    "متجرك", "منصة", "منصات", "زد سلة", "SME", "المتاجر",
]

# مواسم يرصدها المقترح خصيصاً لأنها الأعلى قيمة لمتجر
SEASON_HINTS = {
    "الجمعة البيضاء": "موسم ذروة: زاوية رفع قيمة الطلب أثناء الزحمة.",
    "اليوم الوطني": "موسم سعودي: حملة موسمية مستقلة تشغّلها وتوقفها.",
    "يوم التأسيس": "موسم سعودي: الاستعداد المبكر يكسب الموسم.",
    "رمضان": "سلوك شراء مختلف: السلة أكبر والاقتراح المكمّل خدمة.",
    "العيد": "شراء للآخرين: المكمّل هو ما يكمّل الهدية.",
    "العودة للمدارس": "العميل يشتري قائمة لا قطعة: اقتراح ما يكمّلها.",
}

SOURCES = [
    ("Google News · تجارة إلكترونية",
     "https://news.google.com/rss/search?q=%D8%AA%D8%AC%D8%A7%D8%B1%D8%A9%20%D8%A5%D9%84%D9%83%D8%AA%D8%B1%D9%88%D9%86%D9%8A%D8%A9%20%D8%A7%D9%84%D8%B3%D8%B9%D9%88%D8%AF%D9%8A%D8%A9&hl=ar&gl=SA&ceid=SA:ar"),
    ("Google News · متاجر وتسوق",
     "https://news.google.com/rss/search?q=%D9%85%D8%AA%D8%AC%D8%B1%20%D8%A5%D9%84%D9%83%D8%AA%D8%B1%D9%88%D9%86%D9%8A%20OR%20%D8%AA%D8%B3%D9%88%D9%82%20%D8%A3%D9%88%D9%86%D9%84%D8%A7%D9%8A%D9%86&hl=ar&gl=SA&ceid=SA:ar"),
    ("Google Trends · SA اليومي",
     "https://trends.google.com/trending/rss?geo=SA"),
    ("مدونة سلة", "https://salla.sa/blog/feed/"),
    ("مدونة زد", "https://zid.sa/blog/feed/"),
]


def fetch(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except Exception as e:
        print("   [تعذّر] %s — %s" % (url.split("//")[-1][:40], e), file=sys.stderr)
        return None


def _text(el):
    return (el.text or "").strip() if el is not None else ""


def parse_items(xml_bytes: bytes) -> list:
    """يستخرج (title, link) من RSS. يدعم Google Trends عبر ht:news_item."""
    out = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        if not title:
            continue
        # Google Trends: العنوان كلمة رائجة، والتفاصيل في ht:news_item_title
        ns = "{https://trends.google.com/trending/rss}"
        traffic = item.find(ns + "approx_traffic")
        extra = ""
        if traffic is not None:
            extra = " (~%s بحث)" % _text(traffic)
            ni = item.find(ns + "news_item")
            if ni is not None:
                nl = ni.find(ns + "news_item_url")
                if nl is not None and _text(nl):
                    link = _text(nl)
        out.append((title + extra, link))
    return out


def score(title: str) -> int:
    t = title
    s = sum(1 for k in KEYWORDS if k in t)
    return s


def angle_for(title: str) -> str:
    for season, hint in SEASON_HINTS.items():
        if season in title:
            return hint
    return "اربطها بلحظة تشغيل مناسبة في المتجر، ورفع قيمة الطلب باقتراح المكمّل."


def collect(top: int) -> list:
    seen, rows = set(), []
    for name, url in SOURCES:
        print("• %s" % name, file=sys.stderr)
        data = fetch(url)
        if not data:
            continue
        for title, link in parse_items(data):
            key = re.sub(r"\s+", " ", title).strip().lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            sc = score(title)
            if sc <= 0:
                continue
            rows.append({"title": title, "link": link, "source": name,
                         "score": sc, "angle": angle_for(title)})
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:top]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", help="مسار كتابة النتائج JSON")
    args = ap.parse_args()

    print("جمع الترندات من %d مصدر...\n" % len(SOURCES), file=sys.stderr)
    rows = collect(args.top)

    if not rows:
        print("لا نتائج ذات صلة الآن (قد تكون المصادر محجوبة في هذه البيئة).")
        print("الوظيفة على GitHub Actions تصل للمصادر بلا بروكسي.")
    else:
        print("أفضل %d زاوية ترند (استشاري — حوّلها لوحدة يدوياً):\n" % len(rows))
        for i, r in enumerate(rows, 1):
            print("%2d. [صلة %d] %s" % (i, r["score"], r["title"]))
            print("    الزاوية: %s" % r["angle"])
            if r["link"]:
                print("    المصدر: %s — %s" % (r["source"], r["link"]))
            print("")

    if args.json:
        p = ROOT / args.json if not Path(args.json).is_absolute() else Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print("كُتب: %s" % p, file=sys.stderr)


if __name__ == "__main__":
    main()
