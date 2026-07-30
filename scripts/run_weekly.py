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
import re
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
import ledger
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

# فعل تفعيل في آخر النص. القاعدة: لا بوست ينتهي بمعلومة، لأن المعلومة
# بلا خطوة تبني وعياً لا يتحول. يُفحَص آخر 180 حرفاً فقط: دعوة في وسط
# النص لا تُحسب، فالقارئ يقرر عند النهاية.
ACTION_RE = re.compile(
    r"(فعّل|فعّلها|فعّله|جهّز|ابنِ|ابدأ|افتح|اختر|شغّل|"
    r"الرابط في البايو|من لوحة متجرك)"
)
ACTION_TAIL = 180


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


PROOF_KINDS = ("stat", "capabilities")


def strip_unused_proof(html: str, keep: str) -> str:
    """
    يحذف بلوكات الإثبات غير المستخدمة.

    القالب يحمل كل الأنواع، والتعبئة تُبقي واحداً. البديل كان قالباً
    لكل نوع، وهو يضاعف مواضع تعديل الهوية ويجعلها تتفرّق.
    """
    if keep not in PROOF_KINDS:
        raise SystemExit(
            "proof=%s غير معروف. المسموح: %s" % (keep, " أو ".join(PROOF_KINDS))
        )
    for kind in PROOF_KINDS:
        if kind == keep:
            html = html.replace("<!--PROOF:%s-->" % kind, "") \
                       .replace("<!--/PROOF:%s-->" % kind, "")
        else:
            html = re.sub(r"<!--PROOF:%s-->.*?<!--/PROOF:%s-->" % (kind, kind),
                          "", html, flags=re.S)
    return html


def render_caps(items: list) -> str:
    return "".join(
        '<div class="cap"><div class="cap__tick"></div>'
        '<div class="cap__text">%s</div></div>' % i for i in items
    )


def fill_template(unit: dict, defaults: dict, template: Path = None) -> Path:
    """يعبّئ علامات القالب من الوحدة، ويكتب قالباً مؤقتاً للرندر."""
    s = {**defaults, **unit.get("slides", {})}

    lit = int(s.get("lit", defaults.get("lit", 5)))
    if lit not in LIT_X:
        raise SystemExit(
            "lit=%d غير مسموح. المسموح 4 أو 5 فقط، فهما العقدتان الواقعتان "
            "تحت الجوال ويصح وصل الخط الصاعد إليهما." % lit
        )

    proof = s.get("proof", "stat")
    caps = s.get("caps", []) or []
    if proof == "capabilities" and not (2 <= len(caps) <= 4):
        raise SystemExit(
            "proof=capabilities يحتاج بين قدرتين وأربع في حقل caps، "
            "ووجدت %d. أكثر من أربع يزدحم في المقاس." % len(caps)
        )

    values = {
        "BADGE": s["badge"],
        "TITLE": s["title"],
        "LEAD": s["lead"],
        "SHEET_TITLE": s["sheet_title"],
        "SHEET_SUB": s["sheet_sub"],
        "SHEET_CTA": s["sheet_cta"],
        "CONNECTOR_X": str(LIT_X[lit]),
        "CAPS_LEAD": s.get("caps_lead", ""),
        "CAPS_LIST": render_caps(caps),
        "CAPS_NOTE": s.get("caps_note", ""),
        # القالب يحمل حقول الرقم دائماً، فنعطيها قيماً فارغة عند
        # عدم استخدامها بدل أن يفشل فحص العلامات غير المعبَّأة
        "STAT_NUM": s.get("stat_num", ""),
        "STAT_LABEL": s.get("stat_label", ""),
        "STAT2_NUM": s.get("stat2_num", ""),
        "STAT2_LABEL": s.get("stat2_label", ""),
    }
    for i in range(8):
        values["ON_%d" % i] = "rail__node--on" if i == lit else ""

    tpl = template or (ROOT / defaults["templates"]["ig"])
    html = tpl.read_text(encoding="utf-8")
    html = strip_unused_proof(html, proof)
    for k, v in values.items():
        html = html.replace("{{%s}}" % k, str(v))

    left = [t.split("}}")[0] for t in html.split("{{")[1:]]
    if left:
        raise SystemExit("علامات لم تُعبَّأ في %s: %s" % (tpl.name, ", ".join(left)))

    out = ROOT / "out" / ("_filled_%s.html" % tpl.stem)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def export_jpegs(pngs, outdir: Path, slug: str, target: date, frame: str) -> list:
    outdir.mkdir(parents=True, exist_ok=True)
    out = []
    for png in pngs:
        if png.stem.endswith("_full"):
            continue   # مرجع بصري داخلي للاتصال، لا يُنشر
        n = png.stem.split("_")[-1]
        dst = outdir / ("%s-%s-%s-%s.jpg" % (target.isoformat(), slug, frame, n))
        Image.open(png).convert("RGB").save(
            dst, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True
        )
        out.append(dst)
    return out


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
        # skip ci يمنع أي دورة تشغيل متسلسلة لو أُضيف مشغّل push لاحقاً
        c = sh("git", "commit", "-m", "assets: %s [skip ci]" % label)
        if c.returncode != 0:
            raise SystemExit("فشل الـ commit:\n" + c.stdout + c.stderr)
    p = sh("git", "push")
    if p.returncode != 0:
        raise SystemExit("فشل الـ push:\n" + p.stderr)
    return sh("git", "rev-parse", "HEAD").stdout.strip()


def ledger_push() -> None:
    """يلتزم سجل التكرار ويدفعه. بدونه لا ذاكرة بين التشغيلات."""
    f = "content/ledger.json"
    sh("git", "add", f)
    if not sh("git", "status", "--porcelain", f).stdout.strip():
        return
    c = sh("git", "commit", "-m", "ledger: تسجيل ما أُنشئ [skip ci]")
    if c.returncode != 0:
        raise SystemExit(
            "فشل التزام السجل:\n%s%s\n"
            "لا تتجاهل هذا: سجل غير ملتزم يعني أن التشغيل القادم "
            "سيعيد نفس المحتوى." % (c.stdout, c.stderr)
        )
    pr = sh("git", "push")
    if pr.returncode != 0:
        raise SystemExit(
            "فشل دفع السجل:\n%s\n"
            "المسودات أُنشئت لكن السجل لم يُدفع. أضفه يدوياً قبل أي "
            "تشغيل آخر، وإلا تكرر المحتوى." % pr.stderr
        )


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


def plan_month(plan: dict, ch: dict, month: str) -> None:
    """
    يعرض خطة الشهر ويتحقق من تمايز وحداته، بلا أي رندر أو إنشاء.

    الخطة شهرية لكن إنشاء المسودات يبقى أسبوعياً: أربع وحدات دفعةً
    تعني 12 منشوراً، وسقف الخطة المجانية 10. فالتخطيط شهري والتنفيذ
    أسبوعي، ولا نلمس السقف.
    """
    y, m = (int(x) for x in month.split("-"))
    weeks = [w for w in plan["weeks"]
             if date.fromisoformat(w["date"]).year == y
             and date.fromisoformat(w["date"]).month == m]

    print("خطة %s: %d وحدة" % (month, len(weeks)))
    if not weeks:
        print("لا وحدات لهذا الشهر. أضفها في content/quarter.yml.")
        return

    led = ledger.load()
    seen_slides, seen_text, problems = {}, {}, []

    for w in weeks:
        occ = "  [%s]" % w["occasion"] if w.get("occasion") else ""
        print("\n  %s  %s%s" % (w["date"], w["slug"], occ))
        print("     الهوك: %s" % w["slides"]["title"].replace("<br>", " ")
              .replace('<span class="u-accent">', "").replace("</span>", ""))

        sfp = ledger.slide_fingerprint({**plan["defaults"], **w["slides"]})
        if sfp in seen_slides:
            problems.append("تصميم %s مطابق لتصميم %s" % (w["slug"], seen_slides[sfp]))
        seen_slides[sfp] = w["slug"]
        if sfp in led["fingerprints"]:
            problems.append("تصميم %s نُشر سابقاً في %s"
                            % (w["slug"], led["fingerprints"][sfp]["date"]))

        for svc, text in w["posts"].items():
            n, lim = len(text.strip()), ch["channels"][svc]["charLimit"]
            tags = text.count("#")
            lo, hi = ch["channels"][svc]["hashtags"]
            flag = ""
            if n > lim:
                flag += " ✗طويل"
            if not (lo <= tags <= hi):
                flag += " ✗هاشتاق"
            if not ACTION_RE.search(text.strip()[-ACTION_TAIL:]):
                flag += " ✗بلا تفعيل"

            print("     %-10s %4d/%d حرف  %d#%s" % (svc, n, lim, tags, flag))
            if flag:
                problems.append("%s / %s:%s" % (w["slug"], svc, flag))

            fp = ledger.fingerprint(svc, text)
            if fp in seen_text:
                problems.append("نص %s/%s مطابق لـ %s" % (w["slug"], svc, seen_text[fp]))
            seen_text[fp] = "%s/%s" % (w["slug"], svc)
            if fp in led["fingerprints"]:
                problems.append("نص %s/%s نُشر سابقاً في %s"
                                % (w["slug"], svc, led["fingerprints"][fp]["date"]))

    # ---- توازن الخطة ----
    # هذا الفحص موجود لأن الإصدار السابق أنتج ثلاثة عشر بوستاً كلها
    # عن أرقام. السبب كان حقل الرقم الإلزامي في القالب، والنتيجة خطة
    # تتحدث عن حجم منجزنا لا عن حال التاجر. الفحص يمنع الانزلاق ثانيةً.
    from collections import Counter
    proofs = Counter(w["slides"].get("proof", "stat") for w in weeks)
    acts = Counter(w.get("act", "غير محدد") for w in weeks)

    print("   الإثبات: " + " · ".join("%s %d" % kv for kv in proofs.items()))
    print("   الفصول: " + " · ".join("%s %d" % kv for kv in acts.items()))

    if proofs.get("stat", 0) > len(weeks) / 2:
        problems.append(
            "%d من %d وحدة تقودها أرقام. الحد نصف الشهر. "
            "حوّل بعضها إلى proof: capabilities: الرسالة عن ما يصير "
            "ممكناً للتاجر لا عن حجم ما أنجزناه."
            % (proofs["stat"], len(weeks))
        )

    if "غير محدد" in acts:
        problems.append("وحدات بلا حقل act. كل وحدة تنتمي لفصل في رحلة التاجر.")

    if not (acts.get("activation", 0) or acts.get("occasion", 0)):
        problems.append(
            "لا وحدة activation ولا occasion في الشهر. خطة بلا دعوة "
            "تفعيل واضحة تبني وعياً ولا تحوّله."
        )

    print()
    if problems:
        print("مشاكل لازم تُحل قبل التشغيل:")
        for x in problems:
            print("   - " + x)
        raise SystemExit(1)
    print("الخطة سليمة: كل وحدة متمايزة ولا شي منها نُشر سابقاً.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="تاريخ أحد بصيغة YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="يرندر ويتحقق بلا كتابة في بفر")
    ap.add_argument("--force", action="store_true",
                    help="يتجاوز فحص التكرار. احذف المسودات القديمة من بفر أولاً")
    ap.add_argument("--plan-month", metavar="YYYY-MM",
                    help="يعرض خطة الشهر ويتحقق من تمايز وحداته بلا أي إنشاء")
    args = ap.parse_args()

    plan = yaml.safe_load((ROOT / "content" / "quarter.yml").read_text(encoding="utf-8"))
    channels = yaml.safe_load((ROOT / "content" / "channels.yml").read_text(encoding="utf-8"))

    if args.plan_month:
        plan_month(plan, channels, args.plan_month)
        return

    target = date.fromisoformat(args.date) if args.date else next_sunday(date.today())
    print("1) الهدف: أحد %s" % target.isoformat())

    # ---- الطبقة الأولى: هل نُفّذ هذا التاريخ سابقاً؟ ----
    led = ledger.load()
    ledger.check_run(led, target.isoformat(), force=args.force)

    check_occasions(plan)
    unit = pick_unit(plan, target)
    print("   الوحدة: %s" % unit["slug"])

    # ---- الطبقة الأولى، تكملة: بصمة التصميم وبصمة كل نص ----
    slides_all = {**plan["defaults"], **unit.get("slides", {})}
    fps = ledger.check_content(led, target.isoformat(), unit["slug"],
                               slides_all, unit["posts"], force=args.force)
    print("   فحص التكرار: %d بصمة جديدة، لا تطابق" % len(fps))

    # المقاسات المطلوبة: ig يخدم انستقرام ولنكدن، و x يخدم X وحده
    frames = sorted({channels["channels"][s]["frame"] for s in unit["posts"]})

    print("2) الرندر والفحص بمقاس كل منصة")
    outbase = ROOT / "out" / target.isoformat()
    assets = ROOT / ASSET_DIR / target.isoformat()
    by_frame = {}
    for fr in frames:
        tpl = ROOT / plan["defaults"]["templates"][fr]
        filled = fill_template(unit, plan["defaults"], tpl)
        pngs = render_slides.render(filled, outbase / fr, strict=True, frame=fr, prefix=fr)
        by_frame[fr] = export_jpegs(pngs, assets, unit["slug"], target, fr)
        dims = render_slides.FRAMES[fr]
        print("   %-3s %dx%d ×2  |  %d صورة" % (fr, dims["w"], dims["h"], len(by_frame[fr])))
        for j in by_frame[fr]:
            print("      %s  %d KB" % (j.name, j.stat().st_size / 1024))

    jpegs = [j for fr in frames for j in by_frame[fr]]

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

    # ---- الطبقة الثانية: نسأل بفر نفسه، لا ملفاً محلياً ----
    existing = buf.drafts(channels["organization"]["id"])
    print("   مسودات قائمة في بفر: %d" % len(existing))

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

        # كل منصة تاخذ صور مقاسها هي، لا صور منصة أخرى
        fr = ch["frame"]
        pool = [u for u in urls if ("-%s-" % fr) in u]
        want = ch["images"]
        imgs = pool if want == "all" else pool[:int(want)]
        if not imgs:
            raise SystemExit("لا صور بمقاس %s لقناة %s" % (fr, service))

        if not ACTION_RE.search(body[-ACTION_TAIL:]):
            raise SystemExit(
                "نص %s ينتهي بمعلومة لا بخطوة. المعلومة بلا دعوة تفعيل "
                "تبني وعياً لا يتحول. أضف خطوة محددة في آخره." % service
            )

        lo, hi = ch["hashtags"]
        n_tags = body.count("#")
        if not (lo <= n_tags <= hi):
            raise SystemExit(
                "نص %s فيه %d هاشتاق، والمسموح بين %d و%d. "
                "الحشو يُخفَّض ترتيبه على هذي المنصة."
                % (service, n_tags, lo, hi)
            )

        ledger.check_buffer(existing, ch["id"], due)

        try:
            post = buf.create_draft(ch["id"], body, due, imgs, alts, service)
        except BufferError as e:
            raise SystemExit("فشل إنشاء مسودة %s:\n%s" % (service, e))

        created.append((service, post["id"], due))
        print("   %-10s %s  %s" % (service, post["id"], due))

    ledger.record(led, target.isoformat(), unit["slug"], sha, fps,
                  [{"service": s_, "id": i_, "dueAt": d_} for s_, i_, d_ in created])

    # حرج: وظيفة GitHub تستنسخ الريبو من جديد في كل تشغيل، فسجل غير
    # ملتزم يبدأ فارغاً كل مرة ويعود التكرار. الالتزام هنا هو ما يجعل
    # منع التكرار يعمل بين التشغيلات لا داخل التشغيل الواحد.
    ledger_push()
    print("   السجل حُدّث والتُزم: content/ledger.json")

    print("\nتم: %d مسودة. لا شي منشور." % len(created))
    print("راجعها في بفر واعتمد ما يعجبك.")
    print("إعادة تشغيل نفس التاريخ سترفض تلقائياً.")

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
