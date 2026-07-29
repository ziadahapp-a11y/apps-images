#!/usr/bin/env python3
"""
المشغّل الأسبوعي. يُنادى من وظيفة GitHub Actions.

    python3 scripts/run_weekly.py                 # يختار الأحد القادم تلقائياً
    python3 scripts/run_weekly.py --date 2026-08-02
    python3 scripts/run_weekly.py --dry-run       # يرندر ويتحقق بلا كتابة في بفر

الترتيب:
  1. يحدد أحد الأسبوع القادم، ويفحص إن كانت مناسبة تقع في نافذته
  2. يجيب وحدة المحتوى من content/quarter.yml
  3. يعبّئ القالب ويرندر مع فاحص التخطيط
  4. يصدّر JPEG ويدفع الأصول إلى الريبو
  5. يبني روابط raw مثبّتة على الـ commit SHA ويتحقق أنها تخدم صوراً
  6. ينشئ ثلاث مسودات في بفر

لا شيء ينشر. كل منشور saveToDraft.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_slides
import render_card
from buffer_client import Buffer, BufferError

RAW = "https://raw.githubusercontent.com/%s/%s/%s/%s"
ASSET_DIR = "social"
JPEG_QUALITY = 92

# مواعيد النشر بتوقيت الرياض. الأحد هو يوم الإرساء (0)، والخميس قبله (-3)
# هو اليوم الذي يعمل فيه الكرون، فتبدأ الدورة منه. الوحدة الواحدة تُعاد
# عبر الأسبوع لرفع الوصول: سبعة منشورات من أصل عشرة تسمح بها الخطة.
#   الخميس (-3): انستقرام + X   |   الأحد (0): لنكدن + انستقرام + X
#   الثلاثاء (+2): لنكدن + X
SCHEDULE = [
    ("instagram", -3, "20:30"),   # الخميس 8:30 م
    ("twitter",   -3, "21:30"),   # الخميس 9:30 م
    ("linkedin",   0, "10:00"),   # الأحد 10:00 ص
    ("instagram",  0, "21:00"),   # الأحد 9:00 م
    ("twitter",    0, "21:30"),   # الأحد 9:30 م
    ("linkedin",   2, "10:00"),   # الثلاثاء 10:00 ص
    ("twitter",    2, "21:30"),   # الثلاثاء 9:30 م
]

# العقد المسموح إضاءتها: الوحيدتان الواقعتان تحت الجوال
LIT_X = {4: 1214, 5: 1483}


def sh(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


def next_sunday(today: date) -> date:
    """الأحد القادم. لو اليوم أحد، يرجّع الأحد الذي بعده."""
    ahead = (6 - today.weekday()) % 7   # الاثنين=0 ... الأحد=6
    return today + timedelta(days=ahead or 7)


def check_occasions(plan: dict) -> None:
    """
    تحقق تخطيطي لا تشغيلي: يبلّغ عن مناسبة لا يغطيها أي أسبوع.
    لا يغيّر الاختيار، فقط ينبّه أن الخطة فيها ثغرة.
    """
    weeks = [date.fromisoformat(w["date"]) for w in plan["weeks"]]
    for occ in plan.get("occasions", []):
        d = date.fromisoformat(occ["date"])
        lead = occ.get("lead_days", 7)
        window = [w for w in weeks if d - timedelta(days=lead) <= w <= d]
        if not window:
            print("   تنبيه تخطيطي: %s (%s) لا يغطيها أي أسبوع في المخزون"
                  % (occ["name"], occ["date"]))


def pick_unit(plan: dict, target: date) -> dict:
    """
    قائمة weeks هي المصدر الوحيد للحقيقة: تاريخ واحد يقابل وحدة واحدة.

    كان المنطق سابقاً يسمح لنافذة مناسبة بتجاوز الجدول، لكن نافذة
    مدتها عشرة أيام تغطي أحدين، فكانت نفس وحدة المناسبة تُنشر أسبوعين
    متتاليين. الآن المناسبة تُجدول كوحدة عادية بتاريخها، والغموض ينتهي.
    """
    iso = target.isoformat()
    unit = next((w for w in plan["weeks"] if w["date"] == iso), None)
    if unit:
        return unit

    dates = sorted(w["date"] for w in plan["weeks"])
    raise SystemExit(
        "لا توجد وحدة لتاريخ %s.\n"
        "المخزون يغطي من %s إلى %s.\n"
        "أضف وحدات جديدة في content/quarter.yml قبل انتهائه."
        % (iso, dates[0], dates[-1])
    )


# قوالب البطاقات المفردة بمقاس كل منصة. X أفقي 16:9، ولنكدن 1.91:1.
# انستقرام يبقى الكاروسيل المتصل 4:5 في defaults["template"].
CARD_SPECS = {
    "x":  ("templates/x_card.html", 1600, 900),
    "li": ("templates/linkedin_card.html", 1200, 627),
}


def build_values(unit: dict, defaults: dict) -> dict:
    """القيم المشتركة بين الكاروسيل والبطاقات من مصدر واحد، فلا تختلف."""
    s = {**defaults, **unit.get("slides", {})}

    lit = int(s.get("lit", defaults.get("lit", 5)))
    if lit not in LIT_X:
        raise SystemExit(
            "lit=%d غير مسموح. المسموح 4 أو 5 فقط، فهما العقدتان الواقعتان "
            "تحت الجوال ويصح وصل الخط الصاعد إليهما." % lit
        )

    values = {
        "BADGE": s["badge"],
        "TITLE": s["title"],
        "LEAD": s["lead"],
        "STAT_NUM": s["stat_num"],
        "STAT_LABEL": s["stat_label"],
        "STAT2_NUM": s["stat2_num"],
        "STAT2_LABEL": s["stat2_label"],
        "SHEET_TITLE": s["sheet_title"],
        "SHEET_SUB": s["sheet_sub"],
        "SHEET_CTA": s["sheet_cta"],
        "CONNECTOR_X": str(LIT_X[lit]),
    }
    for i in range(8):
        values["ON_%d" % i] = "rail__node--on" if i == lit else ""
    return values


def _fill(html: str, values: dict, where: str) -> str:
    for k, v in values.items():
        html = html.replace("{{%s}}" % k, str(v))
    left = [t.split("}}")[0] for t in html.split("{{")[1:]]
    if left:
        raise SystemExit("علامات لم تُعبَّأ في %s: %s" % (where, ", ".join(left)))
    return html


def fill_template(unit: dict, defaults: dict) -> Path:
    """يعبّئ قالب الكاروسيل المتصل (انستقرام) ويكتب قالباً مؤقتاً للرندر."""
    html = _fill((ROOT / defaults["template"]).read_text(encoding="utf-8"),
                 build_values(unit, defaults), defaults["template"])
    out = ROOT / "out" / "_filled.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def render_cards(unit: dict, defaults: dict, target: date) -> dict:
    """يرندر بطاقتي X ولنكدن بمقاس كل منصة، ويرجّع {key: png_path}."""
    values = build_values(unit, defaults)
    root = ROOT / "out" / target.isoformat() / "cards"
    out = {}
    for key, (tpl, w, h) in CARD_SPECS.items():
        html = _fill((ROOT / tpl).read_text(encoding="utf-8"), values, tpl)
        d = root / key
        d.mkdir(parents=True, exist_ok=True)
        filled = d / Path(tpl).name
        filled.write_text(html, encoding="utf-8")
        out[key] = render_card.render(filled, d, w, h, strict=True)
    return out


def export_jpegs(pngs, outdir: Path, slug: str, target: date) -> list:
    outdir.mkdir(parents=True, exist_ok=True)
    out = []
    for png in pngs:
        if png.name == "canvas_full.png":
            continue
        n = png.stem.split("_")[-1]
        dst = outdir / ("%s-%s-%s.jpg" % (target.isoformat(), slug, n))
        Image.open(png).convert("RGB").save(
            dst, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True
        )
        out.append(dst)
    return out


def to_jpeg(png: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.open(png).convert("RGB").save(
        dst, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True
    )
    return dst


def repo_info():
    remote = sh("git", "remote", "get-url", "origin").stdout.strip()
    import re
    m = re.search(r"github\.com[:/](?P<o>[^/]+)/(?P<r>[^/.]+)", remote)
    if not m:
        raise SystemExit("الـ remote ليس على GitHub: " + remote)
    return m.group("o"), m.group("r")


def push(paths, label: str) -> str:
    rel = [str(p.relative_to(ROOT)) for p in paths]
    sh("git", "add", *rel)
    if sh("git", "status", "--porcelain", *rel).stdout.strip():
        # skip ci يمنع أي دورة تشغيل متسلسلة لو أُضيف مشغّل push لاحقاً
        c = sh("git", "commit", "-m", "assets: %s [skip ci]" % label)
        if c.returncode != 0:
            raise SystemExit("فشل الـ commit:\n" + c.stdout + c.stderr)
    p = sh("git", "push")
    if p.returncode != 0:
        raise SystemExit("فشل الـ push:\n" + p.stderr)
    return sh("git", "rev-parse", "HEAD").stdout.strip()


def verify(url: str, attempts: int = 8):
    """
    raw وراء كاش، وبعد الدفع مباشرة قد يرجّع 404 لثوانٍ. الإعادة ضرورية.
    بدون هذا الفحص تظهر المسودة بصورة مكسورة ولا يُكتشف إلا بعد النشر.
    """
    last = ""
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                ct = r.headers.get("Content-Type", "")
                sz = int(r.headers.get("Content-Length") or 0)
            if ct.startswith("image/"):
                print("      %s  %s  %d KB" % (url.rsplit("/", 1)[-1], ct, sz / 1024))
                return
            last = "النوع %s وليس صورة" % ct
        except Exception as e:
            last = str(e)
        time.sleep(2 + i)
    raise SystemExit(
        "الرابط لا يخدم صورة بعد %d محاولات:\n  %s\n  %s\n"
        "تأكد أن الريبو public." % (attempts, url, last)
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="تاريخ أحد بصيغة YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="يرندر ويتحقق بلا كتابة في بفر")
    args = ap.parse_args()

    plan = yaml.safe_load((ROOT / "content" / "quarter.yml").read_text(encoding="utf-8"))
    channels = yaml.safe_load((ROOT / "content" / "channels.yml").read_text(encoding="utf-8"))

    target = date.fromisoformat(args.date) if args.date else next_sunday(date.today())
    print("1) الهدف: أحد %s" % target.isoformat())

    check_occasions(plan)
    unit = pick_unit(plan, target)
    print("   الوحدة: %s" % unit["slug"])

    print("2) الرندر والفحص")
    filled = fill_template(unit, plan["defaults"])
    pngs = render_slides.render(filled, ROOT / "out" / target.isoformat(), strict=True)
    cards = render_cards(unit, plan["defaults"], target)

    print("3) التصدير")
    assets = ROOT / ASSET_DIR / target.isoformat()
    slug, iso = unit["slug"], target.isoformat()
    ig = export_jpegs(pngs, assets, slug, target)                        # كاروسيل انستقرام 4:5
    xj = to_jpeg(cards["x"], assets / ("%s-%s-x.jpg" % (iso, slug)))     # بطاقة X 16:9
    lij = to_jpeg(cards["li"], assets / ("%s-%s-li.jpg" % (iso, slug)))  # بطاقة لنكدن 1.91:1
    files = ig + [xj, lij]
    for j in files:
        print("   %s  %d KB" % (j.name, j.stat().st_size / 1024))

    print("4) الدفع")
    owner, repo = repo_info()
    sha = push(files, "%s %s" % (iso, slug))
    print("   %s/%s @ %s" % (owner, repo, sha[:10]))

    print("5) التحقق أن الروابط تخدم صوراً")
    def raw(j):
        return RAW % (owner, repo, sha, str(j.relative_to(ROOT)))
    ig_urls = [raw(j) for j in ig]
    x_url, li_url = raw(xj), raw(lij)
    for u in ig_urls + [x_url, li_url]:
        verify(u)

    # صورة كل قناة بمقاسها: انستقرام كاروسيل 4:5، X بطاقته 16:9، لنكدن بطاقته
    chan_images = {"instagram": ig_urls, "twitter": [x_url], "linkedin": [li_url]}

    alt01 = unit.get("alt", {}).get("01", unit["slides"]["badge"] + " | زيادة")
    alt02 = unit.get("alt", {}).get("02", unit["slides"]["stat_label"].replace("<br>", " "))
    chan_alts = {"instagram": [alt01, alt02], "twitter": [alt01], "linkedin": [alt01]}

    print("6) المسودات في بفر")
    if args.dry_run:
        print("   dry-run: تم تخطي الكتابة. الروابط جاهزة:")
        for u in ig_urls + [x_url, li_url]:
            print("      " + u)
        return

    buf = Buffer()
    created = []
    for service, day_offset, hhmm in SCHEDULE:
        text = unit["posts"].get(service)
        if not text:
            print("   لا نص لـ %s، تخطي" % service)
            continue

        ch = channels["channels"][service]
        limit = ch["charLimit"]
        body = text.strip()
        if len(body) > limit:
            raise SystemExit(
                "نص %s طوله %d ويتجاوز حد %d. اقصره في quarter.yml."
                % (service, len(body), limit)
            )

        when = datetime.combine(target + timedelta(days=day_offset),
                                datetime.strptime(hhmm, "%H:%M").time())
        due = when.strftime("%Y-%m-%dT%H:%M:00+03:00")

        try:
            post = buf.create_draft(ch["id"], body, due,
                                    chan_images[service], chan_alts[service], service)
        except BufferError as e:
            raise SystemExit("فشل إنشاء مسودة %s:\n%s" % (service, e))

        created.append((service, post["id"], due))
        print("   %-10s %s  %s" % (service, post["id"], due))

    print("\nتم: %d مسودة. لا شي منشور." % len(created))
    print("راجعها في بفر واعتمد ما يعجبك.")

    summary = ROOT / "out" / target.isoformat() / "run.json"
    summary.write_text(json.dumps({
        "date": iso,
        "slug": slug,
        "commit": sha,
        "images": {"instagram": ig_urls, "twitter": [x_url], "linkedin": [li_url]},
        "drafts": [{"service": s, "id": i, "dueAt": d} for s, i, d in created],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
