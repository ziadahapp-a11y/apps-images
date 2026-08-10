#!/usr/bin/env python3
"""
ورقة الخطة: تولّد صفحة HTML واحدة تعرض جدول المحتوى القادم كاملاً،
فيراجعها صاحب المتجر ويتأكد أن كل شي مجهّز قبل أن تشتغل الأتمتة.

    python3 scripts/plan_sheet.py            # كل ما هو قادم من اليوم
    python3 scripts/plan_sheet.py 2026-09    # شهر محدد

تقرأ نفس مصادر التشغيل (quarter.yml, channels.yml, schedule.yml, FRAMES)
فما تعرضه هو بالضبط ما سيُجدول، لا وصف منفصل يتعفّن.
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_weekly import slots_for, next_sunday      # noqa: E402
import render_slides                              # noqa: E402

AR_DAY = {6: "الأحد", 0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء",
          3: "الخميس", 4: "الجمعة", 5: "السبت"}
AR_MONTH = {1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل", 5: "مايو",
            6: "يونيو", 7: "يوليو", 8: "أغسطس", 9: "سبتمبر", 10: "أكتوبر",
            11: "نوفمبر", 12: "ديسمبر"}
SERVICE_AR = {"linkedin": "لِنكدإن", "instagram": "انستقرام", "twitter": "إكس"}
FRAME_AR = {"ig": "4:5 كاروسيل", "li": "1.91:1 أفقي", "x": "16:9 بطاقة"}


def hhmm12(hhmm: str) -> str:
    t = datetime.strptime(hhmm, "%H:%M")
    ap = "ص" if t.hour < 12 else "م"
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, ap)


def cta_of(text: str) -> str:
    lines = [l.strip() for l in text.strip().split("\n")
             if l.strip() and not l.strip().startswith("#")]
    return lines[-1] if lines else ""


def clean(s: str) -> str:
    for a, b in (("<br>", " "), ('<span class="u-accent">', ""), ("</span>", "")):
        s = s.replace(a, b)
    return s


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build(month: str = None) -> str:
    plan = yaml.safe_load((ROOT / "content" / "quarter.yml").read_text(encoding="utf-8"))
    channels = yaml.safe_load((ROOT / "content" / "channels.yml").read_text(encoding="utf-8"))
    ch = channels["channels"]

    today = date.today().isoformat()
    weeks = sorted(plan["weeks"], key=lambda w: w["date"])
    if month:
        weeks = [w for w in weeks if w["date"].startswith(month)]
        scope = "شهر %s %s" % (AR_MONTH[int(month.split("-")[1])], month.split("-")[0])
    else:
        weeks = [w for w in weeks if w["date"] >= today]
        scope = "من اليوم فصاعداً"

    # تجميع بالأسبوع (أحد الأسبوع)
    groups = {}
    for w in weeks:
        d = date.fromisoformat(w["date"])
        sunday = d - timedelta(days=(d.weekday() - 6) % 7)
        groups.setdefault(sunday.isoformat(), []).append(w)

    cards = []
    for sunday in sorted(groups):
        units = sorted(groups[sunday], key=lambda w: w["date"])
        rows = []
        for u in units:
            d = date.fromisoformat(u["date"])
            title = clean(u["slides"]["title"])
            proof = u["slides"].get("proof", "stat")
            proof_ar = "قدرات" if proof == "capabilities" else "رقم"
            posts = []
            for service, hhmm in slots_for(d):
                txt = u["posts"].get(service)
                if not txt:
                    continue
                fr = ch[service]["frame"]
                posts.append(
                    '<div class="post">'
                    '<div class="post__head"><span class="chan">%s</span>'
                    '<span class="meta">%s · %s</span></div>'
                    '<div class="cta">%s</div></div>'
                    % (esc(SERVICE_AR[service]), hhmm12(hhmm),
                       esc(FRAME_AR[fr]), esc(cta_of(txt)))
                )
            rows.append(
                '<div class="unit">'
                '<div class="unit__day"><b>%s</b><span>%d %s</span></div>'
                '<div class="unit__body">'
                '<div class="unit__title">%s</div>'
                '<div class="unit__tags"><span class="tag tag--%s">%s</span>'
                '<span class="tag">%s</span></div>'
                '<div class="posts">%s</div>'
                '</div></div>'
                % (AR_DAY[d.weekday()], d.day, AR_MONTH[d.month],
                   esc(title), proof, proof_ar, esc(u.get("act", "—")),
                   "".join(posts))
            )
        s = date.fromisoformat(sunday)
        cards.append(
            '<section class="week"><h2>أسبوع %d %s '
            '<span class="week__count">%d وحدات · %d منشور</span></h2>%s</section>'
            % (s.day, AR_MONTH[s.month], len(units), len(units) * 3, "".join(rows))
        )

    total_units = sum(len(g) for g in groups.values())
    fr_ig = render_slides.FRAMES["ig"]
    fr_li = render_slides.FRAMES["li"]
    fr_x = render_slides.FRAMES["x"]
    sizes_line = ("انستقرام %d×%d (4:5) · لِنكدإن %d×%d (1.91:1) · إكس %d×%d (16:9)"
                  % (fr_ig["w"] * 2, fr_ig["h"] * 2, fr_li["w"] * 2, fr_li["h"] * 2,
                     fr_x["w"] * 2, fr_x["h"] * 2))

    subs = {
        "@@SCOPE@@": esc(scope),
        "@@UNITS@@": str(total_units),
        "@@POSTS@@": str(total_units * 3),
        "@@WEEKS@@": str(len(groups)),
        "@@SIZES@@": esc(sizes_line),
        "@@CARDS@@": "".join(cards),
        "@@GENERATED@@": date.today().isoformat(),
    }
    html = TEMPLATE
    for k, v in subs.items():
        html = html.replace(k, v)
    return html


TEMPLATE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>خطة محتوى زيادة — القادم</title>
<style>
  :root{--ink:#1c0434;--ink2:#2a0763;--violet:#550bf5;--glow:#9358e8;
    --green:#5fc26a;--pane:#ffffff;--line:#ece6fb;--muted:#6b5b8a;--soft:#f7f4fe;}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:"Segoe UI",system-ui,-apple-system,"Helvetica Neue",Arial,sans-serif;
    background:var(--soft);color:var(--ink);line-height:1.5;padding:28px 16px 64px}
  .wrap{max-width:940px;margin:0 auto}
  .hero{background:linear-gradient(118deg,var(--ink) 0%,var(--ink2) 62%,#3a0a86 100%);
    color:#fff;border-radius:22px;padding:34px 30px;overflow:hidden;position:relative}
  .hero::after{content:"";position:absolute;top:-40%;left:40%;width:520px;height:520px;
    background:radial-gradient(circle,rgba(147,88,232,.45),transparent 68%)}
  .hero h1{font-size:30px;font-weight:800;letter-spacing:-.02em;position:relative}
  .hero p{margin-top:8px;color:#d7ccf5;font-size:15px;position:relative}
  .stats{display:flex;flex-wrap:wrap;gap:12px;margin-top:22px;position:relative}
  .stat{background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.16);
    border-radius:14px;padding:12px 18px}
  .stat b{display:block;font-size:24px;font-weight:800}
  .stat span{font-size:12px;color:#cdbff0}
  .note{background:#fff;border:1px solid var(--line);border-radius:16px;
    padding:16px 20px;margin-top:16px;font-size:14px;color:#3a2a5c}
  .note b{color:var(--violet)}
  .sizes{margin-top:8px;font-size:12.5px;color:var(--muted)}
  .week{margin-top:26px}
  .week h2{font-size:18px;font-weight:800;color:var(--ink);padding:0 4px 10px;
    display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .week__count{font-size:12px;font-weight:600;color:var(--violet);
    background:#efe9fd;border-radius:20px;padding:3px 12px}
  .unit{display:flex;gap:14px;background:#fff;border:1px solid var(--line);
    border-radius:16px;padding:16px;margin-bottom:12px}
  .unit__day{flex:0 0 82px;text-align:center;border-left:1px solid var(--line);padding-left:12px}
  .unit__day b{display:block;font-size:15px;color:var(--violet)}
  .unit__day span{font-size:12px;color:var(--muted)}
  .unit__body{flex:1 1 auto;min-width:0}
  .unit__title{font-size:16px;font-weight:700;letter-spacing:-.01em}
  .unit__tags{display:flex;gap:6px;margin-top:7px}
  .tag{font-size:11px;font-weight:600;color:var(--muted);background:var(--soft);
    border:1px solid var(--line);border-radius:8px;padding:2px 9px}
  .tag--capabilities{color:#2f7d3a;background:rgba(95,194,106,.13);border-color:rgba(95,194,106,.3)}
  .posts{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
    gap:9px;margin-top:12px}
  .post{background:var(--soft);border:1px solid var(--line);border-radius:11px;padding:10px 12px}
  .post__head{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
  .chan{font-size:13px;font-weight:700;color:var(--ink)}
  .meta{font-size:10.5px;color:var(--muted);white-space:nowrap}
  .cta{margin-top:6px;font-size:12.5px;color:#43356a;
    border-right:3px solid var(--glow);padding-right:9px}
  footer{margin-top:34px;text-align:center;font-size:12px;color:var(--muted)}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>خطة محتوى زيادة — @@SCOPE@@</h1>
    <p>كل ما ستجهّزه الأتمتة كمسودات في بفر. دورك الوحيد: مراجعتها ونشرها.</p>
    <div class="stats">
      <div class="stat"><b>@@WEEKS@@</b><span>أسابيع</span></div>
      <div class="stat"><b>@@UNITS@@</b><span>وحدة محتوى</span></div>
      <div class="stat"><b>@@POSTS@@</b><span>منشور</span></div>
      <div class="stat"><b>3</b><span>منشورات/منصة/أسبوع</span></div>
    </div>
  </div>

  <div class="note">
    <b>كيف تشتغل بلا تدخل:</b> كل خميس 03:00 صباحاً يجهّز النظام وحدات
    الأسبوع القادم الثلاث (أحد/ثلاثاء/خميس) كمسودات في بفر — بالتصميم
    والنص والمقاس الصحيح لكل منصة. لا شيء يُنشر تلقائياً. تفتح بفر مرة
    في الأسبوع، تراجع، وتنشر ما يعجبك.
    <div class="sizes">المقاسات: @@SIZES@@</div>
  </div>

  @@CARDS@@

  <footer>وُلّدت هذه الورقة في @@GENERATED@@ من content/quarter.yml — ما تراه هو ما سيُجدول.</footer>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    out = ROOT / "out" / ("plan-%s.html" % (arg or "upcoming"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(arg), encoding="utf-8")
    print(out)
