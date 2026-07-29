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
from buffer_client import Buffer, BufferError

RAW = "https://raw.githubusercontent.com/%s/%s/%s/%s"
ASSET_DIR = "social"
JPEG_QUALITY = 92

# مواعيد النشر بتوقيت الرياض. الأحد يوم 0 في هذا الجدول.
SCHEDULE = [
    ("linkedin",  0, "10:00"),
    ("instagram", 0, "21:00"),
    ("twitter",   0, "21:30"),
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


def fill_template(unit: dict, defaults: dict) -> Path:
    """يعبّئ علامات القالب من الوحدة، ويكتب قالباً مؤقتاً للرندر."""
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

    html = (ROOT / defaults["template"]).read_text(encoding="utf-8")
    for k, v in values.items():
        html = html.replace("{{%s}}" % k, str(v))

    left = [t for t in html.split("{{")[1:]]
    if left:
        raise SystemExit("علامات لم تُعبَّأ: " + ", ".join(t.split("}}")[0] for t in left))

    out = ROOT / "out" / "_filled.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
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

    print("3) التصدير")
    assets = ROOT / ASSET_DIR / target.isoformat()
    jpegs = export_jpegs(pngs, assets, unit["slug"], target)
    for j in jpegs:
        print("   %s  %d KB" % (j.name, j.stat().st_size / 1024))

    print("4) الدفع")
    owner, repo = repo_info()
    sha = push(jpegs, "%s %s" % (target.isoformat(), unit["slug"]))
    print("   %s/%s @ %s" % (owner, repo, sha[:10]))

    print("5) التحقق أن الروابط تخدم صوراً")
    urls = [RAW % (owner, repo, sha, str(j.relative_to(ROOT))) for j in jpegs]
    for u in urls:
        verify(u)

    alts = [unit.get("alt", {}).get("01", unit["slides"]["badge"] + " | زيادة"),
            unit.get("alt", {}).get("02", unit["slides"]["stat_label"].replace("<br>", " "))]

    print("6) المسودات في بفر")
    if args.dry_run:
        print("   dry-run: تم تخطي الكتابة. الروابط جاهزة:")
        for u in urls:
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

        # انستقرام ياخذ الشريحتين ككاروسيل، ولنكدن كذلك. X الأولى فقط.
        imgs = urls if service in ("instagram", "linkedin") else urls[:1]

        try:
            post = buf.create_draft(ch["id"], body, due, imgs, alts, service)
        except BufferError as e:
            raise SystemExit("فشل إنشاء مسودة %s:\n%s" % (service, e))

        created.append((service, post["id"], due))
        print("   %-10s %s  %s" % (service, post["id"], due))

    print("\nتم: %d مسودة. لا شي منشور." % len(created))
    print("راجعها في بفر واعتمد ما يعجبك.")

    summary = ROOT / "out" / target.isoformat() / "run.json"
    summary.write_text(json.dumps({
        "date": target.isoformat(),
        "slug": unit["slug"],
        "commit": sha,
        "images": urls,
        "drafts": [{"service": s, "id": i, "dueAt": d} for s, i, d in created],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
