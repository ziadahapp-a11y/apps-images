#!/usr/bin/env python3
"""
ورقة مراجعة الشهر.

    python3 scripts/proof_sheet.py 2026-09

يبني ملف HTML واحداً مستقلاً فيه كل وحدات الشهر: التصاميم بكل مقاس،
والنصوص الثلاثة، وأطوالها وهاشتاقاتها، وحالة فحص التكرار.

لماذا ملف واحد مضمّن الصور بـ base64:
  المراجعة تحتاج أن تُفتح بضغطة وأن تُرسَل كمرفق واحد. مجلد صور
  ومعه ملف نصي يتفرّق ويضيع الربط بين التصميم ونصه. الملف المستقل
  يُفتح بلا سيرفر ويُشارك كما هو.

الصور تُصغَّر في الورقة عن قصد: الغرض مراجعة التكوين والنص لا فحص
البكسل. الملفات الأصلية بكامل دقتها في out/.
"""
import base64
import io
import sys
import yaml
from datetime import date
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger
import render_slides
import run_weekly as rw

# عرض المعاينة داخل الورقة. كافٍ لمراجعة التكوين والنص.
PREVIEW_W = {"ig": 320, "x": 620}


def thumb(png: Path, frame: str) -> str:
    im = Image.open(png).convert("RGB")
    w = PREVIEW_W[frame]
    im = im.resize((w, round(w * im.size[1] / im.size[0])), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def clean_title(t: str) -> str:
    return (t.replace("<br>", " ")
             .replace('<span class="u-accent">', "")
             .replace("</span>", ""))


CSS = """
:root{--ink:#1c0434;--violet:#550bf5;--soft:#b794f6;--green:#5fc26a;
--red:#dc2626;--paper:#faf9fe;--line:rgba(28,4,52,.10);--muted:rgba(28,4,52,.58)}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'IBM Plex Sans Arabic',system-ui,sans-serif;background:var(--paper);
color:var(--ink);padding:40px 32px 80px;direction:rtl}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:30px;font-weight:700;letter-spacing:-.02em}
.sub{margin-top:8px;font-size:16px;color:var(--muted);line-height:1.6}
.summary{margin-top:24px;padding:18px 22px;border-radius:14px;background:#fff;
border:1px solid var(--line);font-size:15px;line-height:1.9}
.summary b{font-weight:600}
.ok{color:#2f7d3a;font-weight:600}
.bad{color:var(--red);font-weight:600}
.unit{margin-top:34px;background:#fff;border:1px solid var(--line);
border-radius:16px;overflow:hidden}
.unit__head{padding:18px 24px;border-bottom:1px solid var(--line);
display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.unit__date{font-size:15px;font-weight:600;color:var(--violet);
font-variant-numeric:tabular-nums}
.unit__slug{font-size:13px;color:var(--muted);font-family:ui-monospace,monospace}
.unit__occ{font-size:12px;font-weight:600;color:#fff;background:var(--violet);
border-radius:999px;padding:4px 12px}
.unit__title{width:100%;margin-top:6px;font-size:22px;font-weight:700;
letter-spacing:-.02em;line-height:1.4}
.shots{padding:22px 24px;display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start;
background:#fcfbff;border-bottom:1px solid var(--line)}
.shot__label{font-size:12px;font-weight:600;color:var(--muted);margin-bottom:8px}
.shot img{display:block;border-radius:10px;border:1px solid var(--line)}
.row{display:flex;gap:10px}
.posts{padding:8px 24px 24px}
.post{padding:18px 0;border-bottom:1px solid var(--line)}
.post:last-child{border-bottom:0}
.post__bar{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.post__svc{font-size:14px;font-weight:700}
.chip{font-size:12px;font-weight:500;border-radius:6px;padding:3px 9px;
background:rgba(28,4,52,.06);color:var(--muted);font-variant-numeric:tabular-nums}
.chip--bad{background:rgba(220,38,38,.12);color:var(--red);font-weight:600}
.post__text{font-size:15px;line-height:1.85;white-space:pre-wrap;
background:#fcfbff;border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.foot{margin-top:44px;font-size:13px;color:var(--muted);line-height:1.9;
border-top:1px solid var(--line);padding-top:20px}
"""


def build(month: str) -> Path:
    plan = yaml.safe_load((ROOT / "content" / "quarter.yml").read_text(encoding="utf-8"))
    chans = yaml.safe_load((ROOT / "content" / "channels.yml").read_text(encoding="utf-8"))
    led = ledger.load()

    y, m = (int(x) for x in month.split("-"))
    weeks = [w for w in plan["weeks"]
             if date.fromisoformat(w["date"]).year == y
             and date.fromisoformat(w["date"]).month == m]
    if not weeks:
        raise SystemExit("لا وحدات لشهر %s في content/quarter.yml." % month)

    problems, blocks = [], []
    seen_slides, seen_text = {}, {}

    for w in weeks:
        slides_all = {**plan["defaults"], **w["slides"]}

        # ---- التصاميم ----
        shots = []
        for fr in ("ig", "x"):
            tpl = ROOT / plan["defaults"]["templates"][fr]
            filled = rw.fill_template(w, plan["defaults"], tpl)
            pngs = render_slides.render(filled, ROOT / "out" / w["date"] / fr,
                                        strict=True, frame=fr, prefix=fr)
            imgs = [p for p in pngs if not p.stem.endswith("_full")]
            label = ("انستقرام ولنكدن · 4:5 · %d شريحة" % len(imgs)) if fr == "ig" \
                    else "X · 16:9"
            tags = "".join('<img src="%s">' % thumb(p, fr) for p in imgs)
            shots.append('<div class="shot"><div class="shot__label">%s</div>'
                         '<div class="row">%s</div></div>' % (label, tags))

        # ---- فحص التكرار ----
        sfp = ledger.slide_fingerprint(slides_all)
        if sfp in seen_slides:
            problems.append("تصميم %s مطابق لتصميم %s داخل نفس الشهر"
                            % (w["slug"], seen_slides[sfp]))
        seen_slides[sfp] = w["slug"]
        if sfp in led["fingerprints"]:
            problems.append("تصميم %s نُشر سابقاً في %s"
                            % (w["slug"], led["fingerprints"][sfp]["date"]))

        # ---- النصوص ----
        posts = []
        for svc in ("linkedin", "instagram", "twitter"):
            text = w["posts"][svc].strip()
            c = chans["channels"][svc]
            n, lim = len(text), c["charLimit"]
            tg = text.count("#")
            lo, hi = c["hashtags"]

            len_bad = n > lim
            tag_bad = not (lo <= tg <= hi)
            if len_bad:
                problems.append("%s / %s: النص %d ويتجاوز %d" % (w["slug"], svc, n, lim))
            if tag_bad:
                problems.append("%s / %s: %d هاشتاق والمسموح %d-%d"
                                % (w["slug"], svc, tg, lo, hi))

            fp = ledger.fingerprint(svc, text)
            if fp in seen_text:
                problems.append("نص %s/%s مطابق لـ %s" % (w["slug"], svc, seen_text[fp]))
            seen_text[fp] = "%s/%s" % (w["slug"], svc)
            if fp in led["fingerprints"]:
                problems.append("نص %s/%s نُشر سابقاً في %s"
                                % (w["slug"], svc, led["fingerprints"][fp]["date"]))

            names = {"linkedin": "لِنكدن", "instagram": "انستقرام", "twitter": "X"}
            posts.append(
                '<div class="post"><div class="post__bar">'
                '<span class="post__svc">%s</span>'
                '<span class="chip%s">%d / %d حرف</span>'
                '<span class="chip%s">%d هاشتاق (%d-%d)</span>'
                '</div><div class="post__text">%s</div></div>'
                % (names[svc], " chip--bad" if len_bad else "", n, lim,
                   " chip--bad" if tag_bad else "", tg, lo, hi, text)
            )

        occ = ('<span class="unit__occ">%s</span>' % w["occasion"]) if w.get("occasion") else ""
        blocks.append(
            '<section class="unit"><div class="unit__head">'
            '<span class="unit__date">أحد %s</span>'
            '<span class="unit__slug">%s</span>%s'
            '<div class="unit__title">%s</div></div>'
            '<div class="shots">%s</div><div class="posts">%s</div></section>'
            % (w["date"], w["slug"], occ, clean_title(w["slides"]["title"]),
               "".join(shots), "".join(posts))
        )

    verdict = ('<span class="bad">%d مشكلة لازم تُحل قبل الجدولة</span>' % len(problems)) \
        if problems else '<span class="ok">سليمة. لا تكرار ولا تجاوز حدود.</span>'
    plist = ("<br>".join("&bull; " + p for p in problems)) if problems else ""

    font = (ROOT / "brand" / "ibm_plex_arabic_embedded.css").read_text(encoding="utf-8")
    html = (
        '<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
        '<title>مراجعة خطة %s</title><style>%s</style><style>%s</style></head><body>'
        '<div class="wrap"><h1>مراجعة خطة شهر %s</h1>'
        '<p class="sub">راجع التصاميم والنصوص قبل الجدولة. '
        'المعاينات مصغّرة للمراجعة، والملفات بكامل دقتها في مجلد out.</p>'
        '<div class="summary"><b>الوحدات:</b> %d &nbsp;&middot;&nbsp; '
        '<b>المنشورات:</b> %d &nbsp;&middot;&nbsp; <b>الحالة:</b> %s%s</div>'
        '%s<div class="foot">وُلِّدت من content/quarter.yml. '
        'التعديل يكون في ذلك الملف لا في هذي الورقة، ثم أعد التوليد.<br>'
        'الجدولة لا تحدث من هنا: شغّل run_weekly.py أسبوعياً، وكل منشور '
        'يُنشأ مسودة تعتمدها أنت في بفر.</div></div></body></html>'
        % (month, font, CSS, month, len(weeks), len(weeks) * 3, verdict,
           ('<div style="margin-top:12px;line-height:2">%s</div>' % plist) if plist else "",
           "".join(blocks))
    )

    out = ROOT / "out" / ("proof-%s.html" % month)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    print("الوحدات: %d | المنشورات: %d" % (len(weeks), len(weeks) * 3))
    if problems:
        print("مشاكل: %d" % len(problems))
        for p in problems:
            print("   - " + p)
    else:
        print("لا تكرار ولا تجاوز حدود.")
    print(out)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("الاستخدام: python3 scripts/proof_sheet.py YYYY-MM")
    build(sys.argv[1])
