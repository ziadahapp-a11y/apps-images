#!/usr/bin/env python3
"""
يرندر قالب الكاروسيل المتصل ويقصّه إلى شرائح 1080x1350.

الكانفس يُرسم كقطعة واحدة عرضها (عدد الشرائح × 1080)، ثم يُقص بـ clip
بدل تصوير كل شريحة على حدة، فتبقى العناصر العابرة لخط اللحام متطابقة
تماماً بين الشريحتين بلا فرق بكسل واحد.

الاستخدام:
    python3 scripts/render_slides.py templates/carousel_connected.html out/test
"""
import sys
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_layout

SLIDE_W = 1080
SLIDE_H = 1350

# التصدير بضعف الكثافة: 2160x2700 للشريحة الواحدة.
# هذا هو المقاس المستخدم فعلياً في منشورات زيادة السابقة، وانستقرام
# يقبل حتى 1440 عرضاً ثم يعيد الضغط، فالتصدير بضعف الكثافة يعطي النص
# العربي حواف أنظف بعد ضغط المنصة.
SCALE = 2
ROOT = Path(__file__).resolve().parent.parent


def build_page(template: Path, workdir: Path) -> Path:
    """
    ينسخ القالب وأصول الهوية إلى مجلد مؤقت.

    عند وصول ملف الشعار الحقيقي: ضعه في brand/logo_white.svg وسيُحقن
    تلقائياً مكان <!--LOGO--> بدل البديل النصي.
    """
    html = template.read_text(encoding="utf-8")

    mark = ROOT / "brand" / "logo_mark_white.svg"
    if not mark.exists():
        raise SystemExit("brand/logo_mark_white.svg مفقود. شغّل scripts/trace_logo.py أولاً.")
    html = html.replace("<!--MARK-->", mark.read_text(encoding="utf-8"))

    templates_dir = workdir / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "brand", workdir / "brand")

    page = templates_dir / template.name
    page.write_text(html, encoding="utf-8")
    return page


def render(template: Path, outdir: Path, strict: bool = True) -> list:
    outdir.mkdir(parents=True, exist_ok=True)
    written = []

    with tempfile.TemporaryDirectory() as tmp:
        page_path = build_page(template, Path(tmp))

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": SLIDE_W * 2, "height": SLIDE_H},
                device_scale_factor=SCALE,
            )
            page.goto(page_path.as_uri())
            page.wait_for_load_state("networkidle")
            # مهلة تشكيل الخط العربي قبل التصوير
            page.wait_for_timeout(700)

            # فحص التخطيط قبل التصدير. الأخطاء هنا لا تُرى بالعين
            # في مراجعة سريعة لكنها تظهر بعد النشر.
            problems = check_layout.check(page)
            if problems:
                print("\n!! فشل فحص التخطيط:\n")
                for p in problems:
                    print("   - " + p)
                print("")
                if strict:
                    browser.close()
                    raise SystemExit(1)
            else:
                print("فحص التخطيط: سليم")

            canvas = page.query_selector("#canvas")
            width = canvas.evaluate("el => el.offsetWidth")
            count = width // SLIDE_W

            # الكانفس كاملاً، للمراجعة البصرية للاتصال بين الشرائح
            full = outdir / "canvas_full.png"
            page.screenshot(path=str(full), clip={"x": 0, "y": 0, "width": width, "height": SLIDE_H})
            written.append(full)

            for i in range(count):
                target = outdir / ("slide_%02d.png" % (i + 1))
                # الـ clip بإحداثيات CSS لا بالبكسل الفعلي، فيبقى 1080
                # بينما الملف الناتج 2160 بفضل device_scale_factor
                page.screenshot(
                    path=str(target),
                    clip={"x": i * SLIDE_W, "y": 0, "width": SLIDE_W, "height": SLIDE_H},
                )
                written.append(target)

            browser.close()

    return written


if __name__ == "__main__":
    tpl = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "templates/carousel_connected.html")
    out = ROOT / (sys.argv[2] if len(sys.argv) > 2 else "out/test")
    for f in render(tpl, out):
        print(f)
