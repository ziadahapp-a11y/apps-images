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

# المقاسات المعتمدة لكل منصة، متحقَّق منها من مراجع 2026:
#   انستقرام: 4:5 (1080x1350) هو ما توصي به ميتا رسمياً وتفضّله على 1:1
#   لنكدن   : 1:1 (1080x1080) مقاس مفرد معتمد، يظهر كاملاً على المكتب
#             والجوال، منشور مفرد لا كاروسيل
#   X       : 16:9 هو الوحيد الذي يظهر كاملاً بلا قص في المعاينة
FRAMES = {
    "ig": {"w": 1080, "h": 1350, "container": "canvas"},
    "li": {"w": 1080, "h": 1080, "container": "card"},
    "x":  {"w": 1600, "h": 900,  "container": "card"},
}

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


def render(template: Path, outdir: Path, strict: bool = True,
           frame: str = "ig", prefix: str = "slide") -> list:
    outdir.mkdir(parents=True, exist_ok=True)
    written = []

    with tempfile.TemporaryDirectory() as tmp:
        page_path = build_page(template, Path(tmp))

        with sync_playwright() as p:
            browser = p.chromium.launch()
            f = FRAMES[frame]
            fw, fh, container = f["w"], f["h"], f["container"]
            # النافذة بعرض كافٍ لأي عدد شرائح، والقص يُحسب من موضع
            # العنصر الفعلي لا من صفر. السبب: الصفحة RTL، فعنصر أضيق
            # من النافذة يلتصق يميناً. القص من صفر كان يصوّر الفراغ.
            page = browser.new_page(
                viewport={"width": fw * 6, "height": fh},
                device_scale_factor=SCALE,
            )
            page.goto(page_path.as_uri())
            page.wait_for_load_state("networkidle")
            # مهلة تشكيل الخط العربي قبل التصوير
            page.wait_for_timeout(700)

            el = page.query_selector("#" + container)
            if el is None:
                raise SystemExit("لا يوجد عنصر #%s في القالب." % container)

            box = el.evaluate(
                "el => { const r = el.getBoundingClientRect();"
                " return {x: r.x, y: r.y, w: r.width, h: r.height}; }"
            )
            origin_x, origin_y = box["x"], box["y"]
            width = box["w"]
            count = max(1, round(width / fw))

            if abs(box["h"] - fh) > 1:
                raise SystemExit(
                    "ارتفاع #%s هو %.0f والمتوقع %d. راجع القالب."
                    % (container, box["h"], fh)
                )

            # الكانفس كاملاً، للمراجعة البصرية للاتصال بين الشرائح
            # فحص التخطيط قبل التصدير. الأخطاء هنا لا تُرى بالعين
            # في مراجعة سريعة لكنها تظهر بعد النشر.
            problems = check_layout.check(page, frame=frame, container=container,
                                          slide_w=fw, origin_x=origin_x, origin_y=origin_y)
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

            if count > 1:
                full = outdir / ("%s_full.png" % prefix)
                page.screenshot(path=str(full), clip={
                    "x": origin_x, "y": origin_y, "width": width, "height": fh})
                written.append(full)

            for i in range(count):
                target = outdir / ("%s_%02d.png" % (prefix, i + 1))
                # الـ clip بإحداثيات CSS لا بالبكسل الفعلي، فيبقى 1080
                # بينما الملف الناتج 2160 بفضل device_scale_factor
                page.screenshot(
                    path=str(target),
                    clip={"x": origin_x + i * fw, "y": origin_y,
                          "width": fw, "height": fh},
                )
                written.append(target)

            browser.close()

    return written


if __name__ == "__main__":
    tpl = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "templates/carousel_connected.html")
    out = ROOT / (sys.argv[2] if len(sys.argv) > 2 else "out/test")
    fr  = sys.argv[3] if len(sys.argv) > 3 else "ig"
    for f in render(tpl, out, frame=fr):
        print(f)
