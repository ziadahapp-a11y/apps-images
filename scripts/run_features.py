#!/usr/bin/env python3
"""
مشغّل الخصائص — نظام التصميم الجديد (دليل الأيجنت v1.0).

يقرأ content/features.yml، يرندر بطاقة كل وحدة بمقاس كل منصة، يدفع
الأصول، وينشئ مسودات بفر. الجدول: ٣ بوستات في الأسبوع لكل منصة، على
شبكة الأحد/الثلاثاء/الخميس. كل وحدة تُنشر في يومها على المنصات الثلاث:
    إكس ٩ص · لِنكدإن ١١ص · إنستقرام ٨م (كلها بتوقيت الرياض).

التشغيل الأسبوعي (بلا --date) يعالج كل وحدات أقرب أسبوع سعودي معلّق
(الأحد→السبت) دفعةً واحدة — أي مسودات الأيام الثلاثة معاً.

    python3 scripts/run_features.py                 # أقرب أسبوع معلّق (٣ وحدات)
    python3 scripts/run_features.py --date 2026-09-13   # وحدة واحدة بتاريخها
    python3 scripts/run_features.py --dry-run       # يرندر محلياً بلا دفع/بفر

لا شيء يُنشر: كل منشور saveToDraft. صاحب الحساب يعتمد من بفر.
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from buffer_client import Buffer, BufferError
import os

RAW = "https://raw.githubusercontent.com/%s/%s/%s/%s"
LEDGER = ROOT / "content" / "features_ledger.json"
JPEG_QUALITY = 92
SCALE = 2

# أوقات النشر في يوم الوحدة نفسه لكل المنصات: (خدمة، وقت). لا إزاحة أيام —
# كل بوست يُنشر في يوم وحدته على المنصات الثلاث، بفارق ساعات يوزّع الظهور.
SLOTS = [("twitter", "09:00"), ("linkedin", "11:00"), ("instagram", "20:00")]

# المقاس لكل منصة: قالب و أبعاد
FRAMES = {
    "twitter":   {"aspect": "wide", "w": 1600, "h": 900},
    "linkedin":  {"aspect": "wide", "w": 1600, "h": 900},
    "instagram": {"aspect": "tall", "w": 1080, "h": 1350},
}

ICONS = {
    "return": '<svg viewBox="0 0 24 24" fill="none"><path d="M4 12a8 8 0 1 1 2.3 5.6" stroke="#fff" stroke-width="2" stroke-linecap="round"/><path d="M4 20v-5h5" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "tag": '<svg viewBox="0 0 24 24" fill="none"><path d="M4 12.5V5.5A1.5 1.5 0 0 1 5.5 4h7L20 11.5a1.5 1.5 0 0 1 0 2.1l-6.4 6.4a1.5 1.5 0 0 1-2.1 0L4 12.5z" stroke="#fff" stroke-width="2" stroke-linejoin="round"/><circle cx="8.5" cy="8.5" r="1.4" fill="#fff"/></svg>',
    "box": '<svg viewBox="0 0 24 24" fill="none"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z" stroke="#fff" stroke-width="2" stroke-linejoin="round"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>',
    "gift": '<svg viewBox="0 0 24 24" fill="none"><path d="M4 11h16v9H4z" stroke="#fff" stroke-width="2" stroke-linejoin="round"/><path d="M3 7h18v4H3zM12 7v13" stroke="#fff" stroke-width="2" stroke-linejoin="round"/><path d="M12 7S10.5 4 8.5 4a2 2 0 0 0 0 4M12 7s1.5-3 3.5-3a2 2 0 0 1 0 4" stroke="#fff" stroke-width="2"/></svg>',
    "cart": '<svg viewBox="0 0 24 24" fill="none"><path d="M6 8h12l-1 10.5a2 2 0 0 1-2 1.8H9a2 2 0 0 1-2-1.8L6 8z" stroke="#fff" stroke-width="2" stroke-linejoin="round"/><path d="M9 8V6.2a3 3 0 0 1 6 0V8" stroke="#fff" stroke-width="2"/><path d="M12 11.6v4.8M9.6 14h4.8" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg>',
    "chart": '<svg viewBox="0 0 24 24" fill="none"><rect x="3" y="13" width="4" height="8" rx="1.5" fill="#fff"/><rect x="10" y="8" width="4" height="13" rx="1.5" fill="#fff"/><rect x="17" y="4" width="4" height="17" rx="1.5" fill="#fff"/></svg>',
}


# مشاهد السكرين الحي: لكل خاصية شاشة تعرض تجربة العميل الفعلية، بمنتجات
# CSS شبه-3D حسب مجال المتجر (المثال: عطور). يُحقن في {{SCREEN}}.
SCREENS = {
    "abandon": """
      <div class="sbar"><div class="sbar__t">سلة العميل</div><div class="sbar__i"></div></div>
      <div class="slist">
        <div class="ci"><div class="p3d p3d--perfume"></div><div class="ci__b"><div class="ci__n">عطر فاخر</div><div class="ci__p"><bdi>240</bdi> ريال</div></div><div class="ci__q">×<bdi>1</bdi></div></div>
        <div class="ci"><div class="p3d p3d--gen"></div><div class="ci__b"><div class="ci__n">مبخرة خشب</div><div class="ci__p"><bdi>120</bdi> ريال</div></div><div class="ci__q">×<bdi>1</bdi></div></div>
      </div>
      <div class="scrim"></div>
      <div class="win">
        <div class="win__zaid">زيد يقترح الآن</div>
        <div class="win__h">مهلاً! خصمك ١٥٪ لو أكملت طلبك الحين 🎁</div>
        <div class="win__s">حافظنا لك على سلّتك — أكمل قبل ما ينتهي العرض.</div>
        <div class="timer"><span class="timer__box">09:58</span><span class="timer__t">باقٍ على العرض</span></div>
        <div class="win__cta">أكمل الطلب وخذ الخصم</div>
      </div>""",
    "addtocart": """
      <div class="sbar"><div class="sbar__t">سلة العميل</div><div class="sbar__i"></div></div>
      <div class="slist">
        <div class="ci"><div class="p3d p3d--perfume"></div><div class="ci__b"><div class="ci__n">عطر فاخر</div><div class="ci__p"><bdi>240</bdi> ريال</div></div><div class="ci__q">×<bdi>1</bdi></div></div>
      </div>
      <div class="scrim"></div>
      <div class="sug">
        <div class="sug__h">✦ زيد يقترح: <b>أكمل تشكيلتك</b></div>
        <div class="sug__it"><div class="p3d p3d--gen p3d--sm"></div><div class="sug__nm">مبخرة خشب</div><div class="sug__pr"><bdi>120</bdi> ريال</div><div class="sug__add">+</div></div>
        <div class="sug__it"><div class="p3d p3d--box p3d--sm"></div><div class="sug__nm">فحم طبيعي</div><div class="sug__pr"><bdi>35</bdi> ريال</div><div class="sug__add">+</div></div>
        <div class="sug__cta">أضف الكل للسلة</div>
      </div>""",
    "coupon": """
      <div class="cobar">
        <div class="sbar__t" style="margin-bottom:10px">إتمام الطلب</div>
        <div class="co__row"><span>مجموع الطلب</span><b><bdi>240</bdi> ريال</b></div>
        <div class="co__row co__row--mut"><span>الشحن</span><span>مجاني</span></div>
        <div class="cpnbar"><div class="cpnbar__ic">✓</div><div class="cpnbar__t">✦ زيد فعّل كود <b>ZIADAH</b> — وفّرت <bdi>25</bdi> ريال</div></div>
      </div>
      <div class="co__pay">ادفع الآن</div>""",
    "bundle": """
      <div class="sbar"><div class="sbar__t">صفحة المنتج</div><div class="sbar__i"></div></div>
      <div style="padding:18px 20px 0"><div class="p3d p3d--perfume" style="width:100%; height:158px; border-radius:18px"></div></div>
      <div class="scrim"></div>
      <div class="sug">
        <div class="sug__h">✦ زيد يقترح: <b>طقم كامل</b></div>
        <div class="sug__it"><div class="p3d p3d--box p3d--sm"></div><div class="sug__nm">عطر + كريم + بخور</div><div class="sug__pr"><bdi>320</bdi> ريال</div></div>
        <div class="cpnbar" style="margin-top:8px"><div class="cpnbar__ic">٪</div><div class="cpnbar__t">وفّر <bdi>70</bdi> ريال مع الطقم</div></div>
        <div class="sug__cta">أضف الطقم للسلة</div>
      </div>""",
    "thanks": """
      <div class="thx">
        <div class="thx__c"></div>
        <div class="thx__h">شكراً على طلبك ✓</div>
        <div class="thx__s">وصلنا طلبك وجاري تجهيزه.</div>
      </div>
      <div class="scrim"></div>
      <div class="thx__sug">
        <div class="sug__h">✦ زيد: <b>عملاء اشتروا هذا أخذوا معه</b></div>
        <div class="sug__it"><div class="p3d p3d--gen p3d--sm"></div><div class="sug__nm">مبخرة خشب</div><div class="sug__pr"><bdi>120</bdi> ريال</div><div class="sug__add">+</div></div>
        <div class="sug__cta">أضفه لطلبك</div>
      </div>""",
    "pdp": """
      <div class="sbar"><div class="sbar__t">صفحة المنتج</div><div class="sbar__i"></div></div>
      <div style="padding:16px 20px 0"><div class="p3d p3d--perfume" style="width:100%; height:150px; border-radius:18px"></div></div>
      <div class="scrim"></div>
      <div class="sug">
        <div class="sug__h">✦ زيد يقترح: <b>الترقية الأفضل</b></div>
        <div class="sug__it"><div class="p3d p3d--gen p3d--sm"></div><div class="sug__nm">الحجم الأكبر ١٠٠مل</div><div class="sug__pr"><bdi>+60</bdi> ريال</div><div class="sug__add">↑</div></div>
        <div class="cpnbar" style="margin-top:8px"><div class="cpnbar__ic">✦</div><div class="cpnbar__t">قيمة أعلى بفرق بسيط — أفضل صفقة له</div></div>
        <div class="sug__cta">رقّي الطلب</div>
      </div>""",
    "crosssell": """
      <div class="sbar"><div class="sbar__t">صفحة المنتج</div><div class="sbar__i"></div></div>
      <div style="padding:16px 20px 0"><div class="p3d p3d--perfume" style="width:100%; height:140px; border-radius:18px"></div></div>
      <div class="fbt">
        <div class="fbt__h">✦ زيد: يُشترى معاً كثيراً</div>
        <div class="fbt__row">
          <div class="fbt__p"><div class="p3d p3d--perfume p3d--sm"></div></div>
          <div class="fbt__x">+</div>
          <div class="fbt__p"><div class="p3d p3d--gen p3d--sm"></div></div>
          <div class="fbt__x">+</div>
          <div class="fbt__p"><div class="p3d p3d--box p3d--sm"></div></div>
        </div>
        <div class="fbt__tot"><span>الإجمالي معاً</span><b><bdi>395</bdi> ريال</b></div>
        <div class="sug__cta">أضف الثلاثة للسلة</div>
      </div>""",
    "shipbar": """
      <div class="sbar"><div class="sbar__t">سلة العميل</div><div class="sbar__i"></div></div>
      <div class="slist">
        <div class="ci"><div class="p3d p3d--perfume"></div><div class="ci__b"><div class="ci__n">عطر فاخر</div><div class="ci__p"><bdi>240</bdi> ريال</div></div><div class="ci__q">×<bdi>1</bdi></div></div>
      </div>
      <div class="scrim"></div>
      <div class="ship">
        <div class="ship__z">✦ زيد</div>
        <div class="ship__h">باقٍ <bdi>35</bdi> ريال على الشحن المجاني 🚚</div>
        <div class="ship__bar"><span style="width:82%"></span></div>
        <div class="ship__it"><div class="p3d p3d--gen p3d--sm"></div><div class="sug__nm">فحم طبيعي</div><div class="sug__pr"><bdi>35</bdi> ريال</div><div class="sug__add">+</div></div>
        <div class="sug__cta">أضفه واحصل على شحن مجاني</div>
      </div>""",
    "home": """
      <div class="sbar"><div class="sbar__t">الرئيسية</div><div class="sbar__i"></div></div>
      <div class="home__hero">
        <div class="home__z">✦ مختار لك من زيد</div>
        <div class="home__ht">لأنك تحب العطور الشرقية</div>
      </div>
      <div class="home__grid">
        <div class="htile"><div class="p3d p3d--perfume"></div><div class="htile__n">عطر عود</div><div class="htile__p"><bdi>240</bdi> ريال</div></div>
        <div class="htile"><div class="p3d p3d--gen"></div><div class="htile__n">مبخرة خشب</div><div class="htile__p"><bdi>120</bdi> ريال</div></div>
        <div class="htile"><div class="p3d p3d--box"></div><div class="htile__n">طقم بخور</div><div class="htile__p"><bdi>180</bdi> ريال</div></div>
        <div class="htile"><div class="p3d p3d--perfume"></div><div class="htile__n">دهن عود</div><div class="htile__p"><bdi>320</bdi> ريال</div></div>
      </div>""",
    "stock": """
      <div class="sbar"><div class="sbar__t">صفحة المنتج</div><div class="sbar__i"></div></div>
      <div style="padding:16px 20px 0; position:relative">
        <div class="p3d p3d--perfume" style="width:100%; height:150px; border-radius:18px"></div>
        <div class="stk__badge">🔥 آخر <bdi>3</bdi> قطع</div>
      </div>
      <div class="stk">
        <div class="stk__h">الكمية شبه منتهية</div>
        <div class="stk__bar"><span style="width:12%"></span></div>
        <div class="stk__n">بقي <bdi>3</bdi> من <bdi>25</bdi> — اطلبه قبل ما يخلص</div>
        <div class="sug__cta">أضفه للسلة الآن</div>
      </div>""",
    "dashboard": """
      <div class="dash">
        <div class="dash__t">لوحة زيادة · أثر زيد</div>
        <div class="dash__kpi">
          <div class="kpi"><div class="kpi__n">+136,871</div><div class="kpi__l">ريال إضافي</div></div>
          <div class="kpi"><div class="kpi__n">1,086</div><div class="kpi__l">تحويل من زيد</div></div>
        </div>
        <div class="dash__ch">
          <div class="dbar" style="height:34%"></div>
          <div class="dbar" style="height:48%"></div>
          <div class="dbar" style="height:44%"></div>
          <div class="dbar" style="height:66%"></div>
          <div class="dbar" style="height:82%"></div>
          <div class="dbar dbar--hi" style="height:100%"></div>
        </div>
        <div class="dash__cap">أثر زيد على متوسط قيمة الطلب</div>
      </div>""",
    "season": """
      <div class="sbar"><div class="sbar__t">الرئيسية</div><div class="sbar__i"></div></div>
      <div class="seas">
        <div class="seas__bn">
          <div class="seas__z">✦ زيد جهّز حملتك</div>
          <div class="seas__h">اليوم الوطني 🇸🇦</div>
          <div class="seas__s">عروض وحزم مقترحة جاهزة للتفعيل بضغطة</div>
          <div class="timer"><span class="timer__box">03:12:40</span><span class="timer__t">على انطلاق الحملة</span></div>
        </div>
        <div class="home__grid" style="padding-top:14px">
          <div class="htile"><div class="p3d p3d--box"></div><div class="htile__n">حزمة الوطني</div><div class="htile__p"><bdi>299</bdi> ريال</div></div>
          <div class="htile"><div class="p3d p3d--perfume"></div><div class="htile__n">عطر مميّز</div><div class="htile__p"><bdi>240</bdi> ريال</div></div>
        </div>
      </div>""",
}


def sh(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


def saudi_week_start(d: date) -> date:
    """بداية الأسبوع السعودي (الأحد) للتاريخ المعطى. weekday: إثنين=0..أحد=6."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _bdi_lead(it: str) -> str:
    """لفّ رقم/نسبة في بداية العنصر بـ bdi ليبقى LTR."""
    m = re.match(r"^([+\-]?[\d,٪%]+)\s+(.*)$", it)
    return ('<bdi>%s</bdi> %s' % (m.group(1), m.group(2))) if m else it


def render_checks(items: list) -> str:
    # النص أولاً (يبدأ من حافة اليمين) وعلامة الصح بعده (يساراً).
    return "".join('<div class="chk"><span class="chk__t">%s</span><span class="chk__c"></span></div>' % _bdi_lead(i) for i in items)


def render_probs(items: list) -> str:
    # النص أولاً (يبدأ من حافة اليمين) والعلامة في نهاية السطر (يسار).
    return "".join('<div class="prob"><span class="prob__t">%s</span><span class="mk mk--x"></span></div>' % i for i in items)


def render_rows(items: list, mark: str) -> str:
    # النص أولاً (يبدأ من حافة اليمين) والعلامة في نهاية السطر (يسار).
    return "".join('<div class="row"><span class="row__t">%s</span><span class="mk mk--%s"></span></div>' % (i, mark) for i in items)


def render_stat2(cards: list) -> str:
    out = []
    for c in cards:
        out.append('<div class="stat2__c"><div class="stat2__store">%s</div>'
                   '<div class="stat2__n"><bdi>%s</bdi></div>'
                   '<div class="stat2__l">%s</div></div>'
                   % (c.get("store", ""), c.get("n", ""), c.get("l", "")))
    return "".join(out)


def fill(unit: dict, defaults: dict, aspect: str) -> str:
    tpl = ROOT / "templates" / ("card_%s%s.html" % (unit["card_type"], "_ig" if aspect == "tall" else ""))
    if not tpl.exists():
        raise SystemExit("لا قالب %s للنوع %s. أضفه في templates/." % (tpl.name, unit["card_type"]))
    html = tpl.read_text(encoding="utf-8")
    html = html.replace("<!--MARK-->", (ROOT / "brand" / "logo_mark_white.svg").read_text(encoding="utf-8"))

    # قاموس شامل لكل الأنواع؛ القالب يأخذ ما يحتاجه فقط. الافتراضي "" يضمن
    # عدم بقاء أي علامة غير معبَّأة مهما كان النوع.
    v = {
        "PILL": unit.get("pill", defaults.get("pill", "")),
        "ICON": ICONS.get(unit.get("icon", "chart"), ICONS["chart"]),
        "KICKER": unit.get("kicker", ""),
        "KICKERC": unit.get("kicker", ""),
        "TITLE": unit.get("title", ""),
        "TITLE_IG": unit.get("title_ig", unit.get("title", "")),
        "DESC": unit.get("desc", ""),
        "CHECKS": render_checks(unit.get("checks", [])),
        "PROBS": render_probs(unit.get("probs", [])),
        "STAT2": render_stat2(unit.get("stat2", [])),
        "AFTER_ROWS": render_rows(unit.get("after_rows", []), "c"),
        "NOW_ROWS": render_rows(unit.get("now_rows", []), "x"),
        "DEMO_NUM": unit.get("demo_num", ""),
        "DEMO_WORD": unit.get("demo_word", ""),
        "DEMO_NUM_FS": str(unit.get("demo_num_fs", 118)),
        "DEMO_NUM_FS_IG": str(unit.get("demo_num_fs_ig", unit.get("demo_num_fs", 90))),
        "DEMO_LABEL": unit.get("demo_label", ""),
        "DEMO_LABEL_IG": unit.get("demo_label_ig", unit.get("demo_label", "")),
        "DEMO_EX": unit.get("demo_ex", "مثال"),
        "DEMO_EXTX": unit.get("demo_extx", ""),
        "DEMO_EXTX_IG": unit.get("demo_extx_ig", unit.get("demo_extx", "")),
        "SCREEN": SCREENS.get(unit.get("screen", "addtocart"), SCREENS["addtocart"]),
    }
    for k, val in v.items():
        html = html.replace("{{%s}}" % k, str(val))
    left = [t.split("}}")[0] for t in html.split("{{")[1:]]
    if left:
        raise SystemExit("علامات لم تُعبَّأ في %s: %s" % (tpl.name, ", ".join(left)))
    return html


def render_png(html: str, w: int, h: int, out_png: Path, browser) -> None:
    import tempfile, shutil
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shutil.copytree(ROOT / "brand", tmp / "brand")
        (tmp / "templates").mkdir()
        page_file = tmp / "templates" / "card.html"
        page_file.write_text(html, encoding="utf-8")
        pg = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=SCALE)
        pg.goto(page_file.as_uri())
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(650)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        pg.query_selector(".frame").screenshot(path=str(out_png))
        pg.close()


def repo_info():
    remote = sh("git", "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[:/](?P<o>[^/]+)/(?P<r>[^/.]+)", remote)
    if not m:
        raise SystemExit("الـ remote ليس على GitHub: " + remote)
    return m.group("o"), m.group("r")


def push(paths, label: str) -> str:
    rel = [str(p.relative_to(ROOT)) for p in paths]
    sh("git", "add", *rel)
    if sh("git", "status", "--porcelain", *rel).stdout.strip():
        c = sh("git", "commit", "-m", "assets(features): %s [skip ci]" % label)
        if c.returncode != 0:
            raise SystemExit("فشل الـ commit:\n" + c.stdout + c.stderr)
    p = sh("git", "push")
    if p.returncode != 0:
        raise SystemExit("فشل الـ push:\n" + p.stderr)
    return sh("git", "rev-parse", "HEAD").stdout.strip()


def verify(url: str, attempts: int = 8):
    last = ""
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                if r.headers.get("Content-Type", "").startswith("image/"):
                    return
        except Exception as e:
            last = str(e)
        time.sleep(2 + i)
    raise SystemExit("الرابط لا يخدم صورة:\n  %s\n  %s" % (url, last))


def led_load():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    return {"runs": {}}


def led_save(d):
    LEDGER.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def led_push():
    sh("git", "add", str(LEDGER.relative_to(ROOT)))
    if not sh("git", "status", "--porcelain", str(LEDGER.relative_to(ROOT))).stdout.strip():
        return
    sh("git", "commit", "-m", "features ledger [skip ci]")
    pr = sh("git", "push")
    if pr.returncode != 0:
        raise SystemExit("فشل دفع السجل:\n" + pr.stderr)


def render_unit(unit: dict, defaults: dict, dry_run: bool, browser) -> dict:
    """يرندر مقاسي الوحدة (wide/tall) ويحفظها JPEG. يُعيد {aspect: path}."""
    iso = unit["date"]
    aspects = {FRAMES[s]["aspect"] for s, _ in SLOTS}
    assets = (ROOT / "out" / iso if dry_run else ROOT / "social" / iso)
    jpegs = {}
    for asp in aspects:
        w = 1600 if asp == "wide" else 1080
        h = 900 if asp == "wide" else 1350
        html = fill(unit, defaults, asp)
        png = ROOT / "out" / iso / ("card_%s.png" % asp)
        render_png(html, w, h, png, browser)
        dst = assets / ("%s-%s-%s.jpg" % (iso, unit["slug"], asp))
        dst.parent.mkdir(parents=True, exist_ok=True)
        Image.open(png).convert("RGB").save(dst, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True)
        jpegs[asp] = dst
        print("   رندر %s %dx%d → %s (%d KB)" % (asp, w, h, dst.name, dst.stat().st_size / 1024))
    return jpegs


def draft_unit(unit: dict, jpegs: dict, channels: dict, buf, existing: list) -> list:
    """يدفع أصول الوحدة وينشئ مسودات بفر للمنصات الثلاث في يوم الوحدة."""
    iso = unit["date"]
    target = date.fromisoformat(iso)
    owner, repo = repo_info()
    sha = push(list(jpegs.values()), "%s %s" % (iso, unit["slug"]))
    urls = {asp: RAW % (owner, repo, sha, str(j.relative_to(ROOT))) for asp, j in jpegs.items()}
    for u in urls.values():
        verify(u)

    kicker = unit.get("kicker", "")
    alt = "%s — %s | زيادة" % (kicker.replace("خاصية · ", ""), unit.get("title", ""))
    created = []
    for service, hhmm in SLOTS:
        ch = channels["channels"][service]
        when = datetime.combine(target, datetime.strptime(hhmm, "%H:%M").time())
        due = when.strftime("%Y-%m-%dT%H:%M:00+03:00")
        # منع تكرار: نفس القناة نفس الموعد
        if any(e.get("channelId") == ch["id"] and (e.get("dueAt") or "").startswith(when.strftime("%Y-%m-%dT%H:%M"))
               for e in existing):
            print("   %s: مسودة بنفس الموعد موجودة، تخطي" % service)
            continue
        text = unit["posts"][service].strip()
        if len(text) > ch["charLimit"]:
            raise SystemExit("نص %s طوله %d > حد %d" % (service, len(text), ch["charLimit"]))
        asp = FRAMES[service]["aspect"]
        try:
            post = buf.create_draft(ch["id"], text, due, [urls[asp]], [alt], service)
        except BufferError as e:
            raise SystemExit("فشل مسودة %s:\n%s" % (service, e))
        created.append({"service": service, "id": post["id"], "dueAt": due})
        print("   %-10s %s  %s" % (service, post["id"], due))
    return {"slug": unit["slug"], "commit": sha, "drafts": created}


def select_units(plan: dict, led: dict, args) -> list:
    """يختار الوحدات المطلوب إنشاؤها. --date: وحدة واحدة. وإلا: كل وحدات
    أقرب أسبوع سعودي (أحد→سبت) فيه وحدة معلّقة لم تُنشأ بعد."""
    weeks = plan["weeks"]
    if args.date:
        unit = next((w for w in weeks if w["date"] == args.date), None)
        if not unit:
            print("لا وحدة لتاريخ %s." % args.date)
            return []
        return [unit]

    today_iso = date.today().isoformat()
    pending = sorted((w["date"] for w in weeks
                      if w["date"] >= today_iso and w["date"] not in led["runs"]))
    if not pending:
        dates = sorted(w["date"] for w in weeks)
        print("لا أسبوع معلّق للإنشاء (كله مُنشأ أو المخزون نفد).")
        if dates and dates[-1] < today_iso:
            print("⚠️ نفد مخزون الوحدات (آخرها %s). أضف وحدات في content/features.yml." % dates[-1])
        return []

    wk = saudi_week_start(date.fromisoformat(pending[0]))
    wk_end = wk + timedelta(days=6)
    units = [w for w in weeks
             if wk.isoformat() <= w["date"] <= wk_end.isoformat()
             and w["date"] not in led["runs"]]
    units.sort(key=lambda w: w["date"])
    print("أسبوع %s → %s: %d وحدة معلّقة" % (wk.isoformat(), wk_end.isoformat(), len(units)))
    return units


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="تاريخ وحدة واحدة YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    plan = yaml.safe_load((ROOT / "content" / "features.yml").read_text(encoding="utf-8"))
    channels = yaml.safe_load((ROOT / "content" / "channels.yml").read_text(encoding="utf-8"))
    defaults = plan.get("defaults", {})
    led = led_load()

    units = select_units(plan, led, args)
    if not units:
        return

    exe = os.environ.get("PW_CHROMIUM_EXECUTABLE")
    buf = None
    existing = []
    if not args.dry_run:
        buf = Buffer()
        existing = buf.drafts(channels["organization"]["id"])

    total = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        try:
            for unit in units:
                iso = unit["date"]
                if led["runs"].get(iso) and not args.force and not args.dry_run:
                    print("• %s نُفّذ سابقاً — تخطي (احذف مسوداته واستخدم --force لإعادته)." % iso)
                    continue
                print("=== وحدة %s: %s (%s) ===" % (iso, unit["slug"], unit["card_type"]))
                jpegs = render_unit(unit, defaults, args.dry_run, browser)
                if args.dry_run:
                    continue
                rec = draft_unit(unit, jpegs, channels, buf, existing)
                led["runs"][iso] = rec
                led_save(led)
                total += len(rec["drafts"])
        finally:
            browser.close()

    if args.dry_run:
        print("dry-run: تم الرندر بلا دفع/بفر.")
        return
    led_push()
    print("\nتم: %d مسودة عبر %d وحدة. لا شي منشور. راجعها في بفر." % (total, len(units)))


if __name__ == "__main__":
    main()
