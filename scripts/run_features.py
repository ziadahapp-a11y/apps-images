#!/usr/bin/env python3
"""
مشغّل الخصائص — نظام التصميم الجديد (دليل الأيجنت v1.0).

يقرأ content/features.yml، يرندر بطاقة كل خاصية بمقاس كل منصة، يدفع
الأصول، وينشئ مسودات بفر بأوقات الدليل:
    إكس الثلاثاء ٩ص · لِنكدإن الثلاثاء ١٠ص · إنستقرام الأربعاء ٧م

    python3 scripts/run_features.py                 # الأسبوع القادم (ثلاثاء)
    python3 scripts/run_features.py --date 2026-09-08
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

# منافذ الأسبوع: (خدمة، إزاحة أيام عن الثلاثاء، وقت). إنستقرام أربعاء.
SLOTS = [("twitter", 0, "09:00"), ("linkedin", 0, "10:00"), ("instagram", 1, "19:00")]

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


def sh(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


def next_tuesday(today: date) -> date:
    ahead = (1 - today.weekday()) % 7      # الثلاثاء = 1
    return today + timedelta(days=ahead or 7)


def render_checks(items: list) -> str:
    out = []
    for it in items:
        # لفّ رقم/نسبة في بداية العنصر بـ bdi ليبقى LTR
        m = re.match(r"^([+\-]?[\d,٪%]+)\s+(.*)$", it)
        if m:
            it = '<bdi>%s</bdi> %s' % (m.group(1), m.group(2))
        out.append('<div class="chk"><span class="chk__c"></span>%s</div>' % it)
    return "".join(out)


def fill(unit: dict, defaults: dict, aspect: str) -> str:
    tpl = ROOT / "templates" / ("card_%s%s.html" % (unit["card_type"], "_ig" if aspect == "tall" else ""))
    html = tpl.read_text(encoding="utf-8")
    html = html.replace("<!--MARK-->", (ROOT / "brand" / "logo_mark_white.svg").read_text(encoding="utf-8"))
    v = {
        "PILL": unit.get("pill", defaults.get("pill", "")),
        "ICON": ICONS.get(unit.get("icon", "chart"), ICONS["chart"]),
        "KICKER": unit["kicker"],
        "TITLE": unit["title"],
        "TITLE_IG": unit.get("title_ig", unit["title"]),
        "DESC": unit["desc"],
        "CHECKS": render_checks(unit.get("checks", [])),
        "DEMO_NUM": unit.get("demo_num", ""),
        "DEMO_NUM_FS": str(unit.get("demo_num_fs", 118)),
        "DEMO_NUM_FS_IG": str(unit.get("demo_num_fs_ig", unit.get("demo_num_fs", 90))),
        "DEMO_LABEL": unit.get("demo_label", ""),
        "DEMO_LABEL_IG": unit.get("demo_label_ig", unit.get("demo_label", "")),
        "DEMO_EX": unit.get("demo_ex", "مثال"),
        "DEMO_EXTX": unit.get("demo_extx", ""),
        "DEMO_EXTX_IG": unit.get("demo_extx_ig", unit.get("demo_extx", "")),
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="ثلاثاء الأسبوع YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    plan = yaml.safe_load((ROOT / "content" / "features.yml").read_text(encoding="utf-8"))
    channels = yaml.safe_load((ROOT / "content" / "channels.yml").read_text(encoding="utf-8"))
    defaults = plan.get("defaults", {})

    target = date.fromisoformat(args.date) if args.date else next_tuesday(date.today())
    iso = target.isoformat()
    unit = next((w for w in plan["weeks"] if w["date"] == iso), None)
    if not unit:
        dates = sorted(w["date"] for w in plan["weeks"])
        upcoming = [d for d in dates if d >= iso]
        # خروج نظيف لا فشل: الكرون يعمل كل خميس، وأسبوع بلا خاصية ليس خطأ.
        print("لا خاصية لتاريخ %s — تخطي هذا الأسبوع." % iso)
        if not upcoming:
            print("⚠️ نفد مخزون الخصائص (آخرها %s). أضف وحدات في content/features.yml."
                  % (dates[-1] if dates else "—"))
        return

    led = led_load()
    if led["runs"].get(iso) and not args.force and not args.dry_run:
        raise SystemExit("أسبوع %s نُفّذ سابقاً. استخدم --force بعد حذف مسوداته من بفر." % iso)

    print("=== خاصية %s: %s (%s) ===" % (iso, unit["slug"], unit["card_type"]))
    exe = os.environ.get("PW_CHROMIUM_EXECUTABLE")

    # رندر لكل مقاس مطلوب. في dry-run نكتب الصور في out/ (المتجاهَل) لا في
    # social/ حتى لا نلوّث شجرة العمل بأصول مؤقتة.
    aspects = {FRAMES[s]["aspect"] for s, _, _ in SLOTS}
    assets = (ROOT / "out" / iso if args.dry_run else ROOT / "social" / iso)
    jpegs = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
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
            print("   رندر %s %dx%d → %s (%d KB)" % (asp, w, h, dst.name, dst.stat().st_size/1024))
        browser.close()

    if args.dry_run:
        print("dry-run: تم الرندر بلا دفع/بفر.")
        return

    owner, repo = repo_info()
    sha = push(list(jpegs.values()), "%s %s" % (iso, unit["slug"]))
    urls = {asp: RAW % (owner, repo, sha, str(j.relative_to(ROOT))) for asp, j in jpegs.items()}
    for u in urls.values():
        verify(u)

    buf = Buffer()
    existing = buf.drafts(channels["organization"]["id"])
    alt = "%s — %s | زيادة" % (unit["kicker"].replace("خاصية · ", ""), unit["title"])

    created = []
    for service, day_off, hhmm in SLOTS:
        ch = channels["channels"][service]
        when = datetime.combine(target + timedelta(days=day_off),
                                datetime.strptime(hhmm, "%H:%M").time())
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

    led["runs"][iso] = {"slug": unit["slug"], "commit": sha, "drafts": created}
    led_save(led)
    led_push()
    print("\nتم: %d مسودة. لا شي منشور. راجعها في بفر." % len(created))


if __name__ == "__main__":
    main()
