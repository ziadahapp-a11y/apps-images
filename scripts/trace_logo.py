#!/usr/bin/env python3
"""
يحوّل رمز زيادة من AVIF نقطي إلى SVG متجهي.

المشكلة: الملف الأصلي 212x212 نقطي، والرمز أبيض مطبوع على مربع داكن.
وضعه كما هو فوق خلفية بنفسجية يظهر مربعاً أسود، وتكبيره إلى عرض
شريحة 1080 يجعله ضبابياً.

الحل: عتبة لونية تفصل الرمز الأبيض عن الخلفية، ثم تتبّع المسار إلى
SVG. النتيجة حادة عند أي مقاس وقابلة لإعادة التلوين بمتغير CSS.

المخرجات في brand/:
  logo_mark.svg        الرمز، currentColor فيرث لون الأب
  logo_mark_white.svg  نسخة بيضاء جاهزة للخلفيات الداكنة
"""
import sys
from pathlib import Path

import numpy as np
import potrace
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else None

# عتبة الفصل. الهيستوغرام ثنائي القمة: 65% من البكسلات تحت 10 (الخلفية)
# و11% فوق 250 (الرمز). العتبة عند 216 تفصلهما وتستبعد تدرج الخلفية.
THRESHOLD = 216


def trace(src: Path):
    img = Image.open(src).convert("RGBA")

    # تسطيح على أسود: أي شفافية تصبح خلفية، والرمز وحده يبقى فاتحاً
    flat = Image.alpha_composite(Image.new("RGBA", img.size, (0, 0, 0, 255)), img)
    gray = np.array(flat.convert("L"))

    mask = gray > THRESHOLD
    if not mask.any():
        raise SystemExit("لم يُعثر على شكل فاتح. راجع العتبة.")

    # قص الهامش الفارغ حتى يبدأ الـ viewBox من حدود الرمز تماماً
    rows, cols = np.where(mask)
    y0, y1 = rows.min(), rows.max() + 1
    x0, x1 = cols.min(), cols.max() + 1
    mask = mask[y0:y1, x0:x1]

    h, w = mask.shape
    # المكتبة تعتبر الصفر مقدمةً لا خلفية، فبدون العكس نحصل على
    # النقيض تماماً. مقيس: IoU مع القناع 0.2%، ومع معكوسه 99.3%.
    path = potrace.Bitmap(~mask).trace(turdsize=12, alphamax=1.0)

    parts = []
    for curve in path:
        sp = curve.start_point
        d = ["M%.2f %.2f" % (sp.x, sp.y)]
        for seg in curve:
            ep = seg.end_point
            if seg.is_corner:
                c = seg.c
                d.append("L%.2f %.2f L%.2f %.2f" % (c.x, c.y, ep.x, ep.y))
            else:
                a, b = seg.c1, seg.c2
                d.append("C%.2f %.2f %.2f %.2f %.2f %.2f" % (a.x, a.y, b.x, b.y, ep.x, ep.y))
        d.append("Z")
        parts.append(" ".join(d))

    return w, h, parts


TPL = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
    'fill="%s" fill-rule="evenodd" aria-label="رمز زيادة">\n'
    '  <path d="%s"/>\n'
    "</svg>\n"
)


def main():
    if SRC is None or not SRC.exists():
        raise SystemExit("مرر مسار ملف الرمز الأصلي.")

    w, h, parts = trace(SRC)
    d = " ".join(parts)

    out = ROOT / "brand"
    (out / "logo_mark.svg").write_text(TPL % (w, h, "currentColor", d), encoding="utf-8")
    (out / "logo_mark_white.svg").write_text(TPL % (w, h, "#FFFFFF", d), encoding="utf-8")

    print("viewBox %dx%d | %d مسار | %d حرف" % (w, h, len(parts), len(d)))


if __name__ == "__main__":
    main()
