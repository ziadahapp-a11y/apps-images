#!/usr/bin/env python3
"""
يبني brand/ibm_plex_arabic_embedded.css بتضمين ملفات woff2 كـ base64.

السبب: الرندر يتم في بيئة بلا وصول إلى fonts.googleapis.com، و@import
يفشل بصمت فيرتد التصميم لخط افتراضي يكسر الهوية. التضمين يجعل الملف
يرندر بنفس الشكل في أي بيئة وبدون إنترنت.

المصدر: حزمة @ibm/plex-sans-arabic من npm، رخصة OFL.
"""
import base64
from pathlib import Path

SRC = Path("/home/claude/fonts/package/fonts/complete/woff2")
OUT = Path(__file__).resolve().parent.parent / "brand" / "ibm_plex_arabic_embedded.css"

# الأوزان المستخدمة فقط. لا يوجد وزن 900 في هذه العائلة، وأثقل وزن متاح هو Bold 700.
WEIGHTS = [
    ("IBMPlexSansArabic-Light.woff2", 300),
    ("IBMPlexSansArabic-Regular.woff2", 400),
    ("IBMPlexSansArabic-Medium.woff2", 500),
    ("IBMPlexSansArabic-SemiBold.woff2", 600),
    ("IBMPlexSansArabic-Bold.woff2", 700),
]

FACE = """@font-face {
  font-family: 'IBM Plex Sans Arabic';
  font-style: normal;
  font-weight: %d;
  font-display: block;
  src: url(data:font/woff2;charset=utf-8;base64,%s) format('woff2');
}
"""


def main():
    parts = [
        "/* IBM Plex Sans Arabic - مضمّن base64 للرندر بلا إنترنت.\n"
        "   المصدر: @ibm/plex-sans-arabic (npm)، رخصة SIL Open Font License 1.1.\n"
        "   أثقل وزن متاح 700. لا يوجد 900 في هذه العائلة. */\n"
    ]

    for filename, weight in WEIGHTS:
        blob = (SRC / filename).read_bytes()
        parts.append(FACE % (weight, base64.b64encode(blob).decode("ascii")))

    OUT.write_text("\n".join(parts), encoding="utf-8")
    print("%s  (%.0f KB, %d أوزان)" % (OUT.name, OUT.stat().st_size / 1024, len(WEIGHTS)))


if __name__ == "__main__":
    main()
