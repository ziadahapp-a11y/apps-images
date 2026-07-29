#!/usr/bin/env python3
"""
يرندر بطاقة مفردة (إطار واحد) بمقاس منصة محدد ويصدّرها PNG + JPEG.

بخلاف الكاروسيل المتصل (شريحتان 1080×1350)، هذه بطاقة واحدة بمقاس
المنصة: X أفقي 16:9، ولنكدن 1200×627. تستخدم نفس توكنز الهوية ونفس
الشعار المتجهي، فلا تنكسر الهوية.

    python3 scripts/render_card.py templates/x_card.html out/test/x 1600 900

فاحص البطاقة يشتغل قبل التصدير — لا تعطّله:
  1. سقوط الخط    : ارتداد لخط النظام يكسر الهوية
  2. الهوامش      : عنصر يخرج عن الهامش الآمن فيُقص على المنصة
  3. التراكب      : كتلتان نصيتان فوق بعض

العناصر التي يجب أن تبقى داخل الإطار وألا تتراكب تُعلَّم بالكلاس `.chk`.
"""
import sys
import shutil
import tempfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SCALE = 2
JPEG_QUALITY = 92
SAFE_PAD = 48   # أدنى هامش مقبول من حافة البطاقة (بإحداثيات CSS)

PROBE = r"""
() => {
  const out = { fonts: {}, boxes: [] };
  out.fonts.bold    = document.fonts.check('700 96px "IBM Plex Sans Arabic"');
  out.fonts.regular = document.fonts.check('400 30px "IBM Plex Sans Arabic"');
  out.fonts.light   = document.fonts.check('300 34px "IBM Plex Sans Arabic"');

  document.querySelectorAll('.chk').forEach((el, i) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    out.boxes.push({
      sel: (el.getAttribute('data-name') || el.className) + '[' + i + ']',
      left: r.left, right: r.right, top: r.top, bottom: r.bottom,
      contains: [...el.querySelectorAll('.chk')].length,
    });
  });
  out.w = document.querySelector('#card').offsetWidth;
  out.h = document.querySelector('#card').offsetHeight;
  return out;
}
"""


def _overlap(a, b):
    w = min(a["right"], b["right"]) - max(a["left"], b["left"])
    h = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
    return w * h if (w > 0 and h > 0) else 0


def _check(page, w, h) -> list:
    data = page.evaluate(PROBE)
    errors = []

    for name, ok in data["fonts"].items():
        if not ok:
            errors.append("الخط: وزن %s لم يُحمّل، التصميم مرتد لخط النظام" % name)

    boxes = data["boxes"]

    # الهوامش الآمنة
    for b in boxes:
        if b["left"] < SAFE_PAD:
            errors.append("هامش: %s يبعد %.0f فقط عن الحافة اليسرى" % (b["sel"], b["left"]))
        if b["right"] > w - SAFE_PAD:
            errors.append("هامش: %s يبعد %.0f فقط عن الحافة اليمنى" % (b["sel"], w - b["right"]))
        if b["top"] < SAFE_PAD:
            errors.append("هامش: %s يبعد %.0f فقط عن الحافة العليا" % (b["sel"], b["top"]))
        if b["bottom"] > h - SAFE_PAD:
            errors.append("هامش: %s يبعد %.0f فقط عن الحافة السفلى" % (b["sel"], h - b["bottom"]))

    # التراكب: نتجاهل زوج الأب مع ابنه (الحاويات فيها chk أبناء)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a["contains"] > 0 or b["contains"] > 0:
                continue
            if _overlap(a, b) > 400:
                errors.append("تراكب: %s فوق %s" % (a["sel"], b["sel"]))

    return errors


def build_page(template: Path, workdir: Path) -> Path:
    html = template.read_text(encoding="utf-8")
    mark = ROOT / "brand" / "logo_mark_white.svg"
    if not mark.exists():
        raise SystemExit("brand/logo_mark_white.svg مفقود. شغّل scripts/trace_logo.py أولاً.")
    html = html.replace("<!--MARK-->", mark.read_text(encoding="utf-8"))
    tdir = workdir / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "brand", workdir / "brand")
    page = tdir / template.name
    page.write_text(html, encoding="utf-8")
    return page


def render(template: Path, outdir: Path, w: int, h: int, strict: bool = True) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        page_path = build_page(template, Path(tmp))
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=SCALE)
            page.goto(page_path.as_uri())
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(700)

            problems = _check(page, w, h)
            if problems:
                print("\n!! فشل فحص البطاقة:\n")
                for pr in problems:
                    print("   - " + pr)
                print("")
                if strict:
                    browser.close()
                    raise SystemExit(1)
            else:
                print("فحص البطاقة: سليم")

            png = outdir / (template.stem + ".png")
            page.screenshot(path=str(png), clip={"x": 0, "y": 0, "width": w, "height": h})
            browser.close()

    jpg = outdir / (template.stem + ".jpg")
    Image.open(png).convert("RGB").save(jpg, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True)
    print("صُدِّر: %s  (%dx%d @%dx)" % (jpg.name, w, h, SCALE))
    return png


if __name__ == "__main__":
    tpl = ROOT / sys.argv[1]
    out = ROOT / sys.argv[2]
    W = int(sys.argv[3]); H = int(sys.argv[4])
    render(tpl, out, W, H)
