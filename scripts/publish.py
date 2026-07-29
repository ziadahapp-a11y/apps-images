#!/usr/bin/env python3
"""
خط النشر الأسبوعي عبر GitHub.

    python3 scripts/publish.py content/posts/2026-08-02.yml

الترتيب:
  1. رندر الشرائح مع فاحص التخطيط، ويتوقف كلياً عند أي خطأ
  2. تصدير JPEG q92 بترميز 4:4:4
  3. commit ودفع الأصول إلى الريبو
  4. بناء روابط raw والتحقق أن كلاً منها يرجّع image/* فعلاً
  5. كتابة out/<date>/manifest.json

لماذا GitHub بدل مستضيف آخر:
  raw.githubusercontent.com يرجّع content-type صحيح حسب الامتداد
  (مقيس: image/png و image/jpeg) مع access-control-allow-origin: *،
  وهذا كل ما يحتاجه بفر لجلب الصورة. وبما أن النظام نفسه في ريبو،
  كل أصل منشور يصير له تاريخ إصدار بلا خدمة إضافية.

شرط واحد: الريبو لازم يكون public. raw لا يخدم الريبو الخاص بلا توكن،
وبفر لن يستطيع الجلب. أصول التسويق تُنشر علناً أصلاً، لكن لا تضع في
هذا الريبو أي شيء غير مخصص للنشر.

إنشاء المسودات في بفر يتولاه كلود كود قراءةً من المانيفست عبر موصّل
Buffer، فلا حاجة لتوكن بفر في أي ملف.
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_slides

JPEG_QUALITY = 92
# الوسيط الثالث هو الـ commit SHA لا اسم الفرع، لضمان ثبات المحتوى.
RAW = "https://raw.githubusercontent.com/%s/%s/%s/%s"


def sh(*args, **kw):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, **kw)


def repo_info():
    """يستخرج المالك والريبو والفرع من git نفسه، فلا يوجد إعداد يدوي يتعفّن."""
    remote = sh("git", "remote", "get-url", "origin")
    if remote.returncode != 0:
        raise SystemExit("لا يوجد remote اسمه origin. أنشئ الريبو واربطه أولاً.")

    url = remote.stdout.strip()
    m = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", url)
    if not m:
        raise SystemExit("الـ remote ليس على GitHub: " + url)

    branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    return m.group("owner"), m.group("repo"), branch


def export_jpegs(pngs, outdir: Path, slug: str):
    """يحوّل الشرائح إلى JPEG، ويتخطى صورة الكانفس الكامل فهي مرجع داخلي."""
    outdir.mkdir(parents=True, exist_ok=True)
    out = []
    for png in pngs:
        if png.name == "canvas_full.png":
            continue
        n = png.stem.split("_")[-1]
        dst = outdir / ("%s-%s.jpg" % (slug, n))
        Image.open(png).convert("RGB").save(
            dst, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True
        )
        out.append(dst)
    return out


def push(paths, date: str) -> str:
    """يدفع الأصول ويُرجع الـ SHA الذي استقرت عنده، لتثبيت الروابط عليه."""
    rel = [str(p.relative_to(ROOT)) for p in paths]
    sh("git", "add", *rel)

    if not sh("git", "status", "--porcelain", *rel).stdout.strip():
        print("   لا تغييرات، الأصول مدفوعة مسبقاً")
    else:
        c = sh("git", "commit", "-m", "assets: %s" % date)
        if c.returncode != 0:
            raise SystemExit("فشل الـ commit:\n" + c.stdout + c.stderr)

    p = sh("git", "push")
    if p.returncode != 0:
        raise SystemExit("فشل الـ push:\n" + p.stderr)

    sha = sh("git", "rev-parse", "HEAD").stdout.strip()
    print("   مدفوع عند %s" % sha[:10])
    return sha


def verify(url: str, attempts: int = 8) -> None:
    """
    يتأكد أن الرابط يرجّع صورة فعلاً.

    الإعادة ضرورية: raw.githubusercontent وراء كاش، وبعد الدفع مباشرة
    قد يرجّع 404 لبضع ثوانٍ. بدون هذا الفحص تظهر المسودة في بفر بصورة
    مكسورة ولا يُكتشف الخطأ إلا بعد النشر.
    """
    last = ""
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                ctype = r.headers.get("Content-Type", "")
                size = int(r.headers.get("Content-Length") or 0)
            if ctype.startswith("image/"):
                print("   %s  %s  %d KB" % (url.rsplit("/", 1)[-1], ctype, size / 1024))
                return
            last = "نوع المحتوى %s وليس صورة" % ctype
        except Exception as e:
            last = str(e)
        time.sleep(2 + i)

    raise SystemExit(
        "الرابط لا يرجّع صورة بعد %d محاولات:\n  %s\n  %s\n"
        "تحقق أن الريبو public وأن الدفع تم على الفرع الصحيح."
        % (attempts, url, last)
    )


def main():
    if len(sys.argv) < 2:
        raise SystemExit("الاستخدام: python3 scripts/publish.py content/posts/<date>.yml")

    spec = yaml.safe_load((ROOT / sys.argv[1]).read_text(encoding="utf-8"))
    date, slug = spec["date"], spec["slug"]

    outdir = ROOT / "out" / date
    assets = ROOT / "assets" / date

    print("1) الرندر والفحص")
    pngs = render_slides.render(ROOT / spec["template"], outdir, strict=True)

    print("2) التصدير JPEG")
    jpegs = export_jpegs(pngs, assets, slug)
    for j in jpegs:
        print("   %s  %d KB" % (j.name, j.stat().st_size / 1024))

    print("3) الدفع إلى GitHub")
    owner, repo, branch = repo_info()
    print("   %s/%s @ %s" % (owner, repo, branch))
    sha = push(jpegs, date)

    print("4) التحقق أن الروابط تخدم صوراً")
    # الرابط مثبّت على SHA لا على اسم الفرع. الفرق ليس نظرياً:
    # رابط الفرع يشير إلى ما هو موجود الآن في HEAD، فلو أعدت الرندر
    # وكتبت فوق نفس اسم الملف تغيّر محتوى الرابط تحت مسودة قائمة في
    # بفر بلا أي إشعار. وكاش raw خمس دقائق يزيد الطين بلة: بعد دفع
    # تصحيح مباشرة قد يجلب بفر النسخة القديمة. الـ SHA يلغي الاحتمالين.
    urls = [RAW % (owner, repo, sha, str(j.relative_to(ROOT))) for j in jpegs]
    for u in urls:
        verify(u)

    print("5) المانيفست")
    manifest = {
        "date": date,
        "slug": slug,
        "repo": "%s/%s" % (owner, repo),
        "branch": branch,
        "commit": sha,
        "images": urls,
        "posts": [],
    }
    for e in spec["posts"]:
        manifest["posts"].append({
            "channel": e["channel"],
            "channelId": e["channelId"],
            "dueAt": e["dueAt"],
            "text": e["text"].strip(),
            "altText": e.get("altText", spec.get("altText", "")).strip(),
            # انستقرام ياخذ الشرائح كلها ككاروسيل، وX ولنكدن الأولى فقط
            "images": urls if e["channel"] == "instagram" else urls[:1],
            "saveToDraft": True,
        })

    path = outdir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("   " + str(path))

    print("\nتم. الخطوة التالية في كلود كود:")
    print('  "اقرأ %s وأنشئ المسودات في بفر"' % path.relative_to(ROOT))
    print("كلها saveToDraft، فلا ينشر شيء قبل مراجعتك في بفر.")


if __name__ == "__main__":
    main()
