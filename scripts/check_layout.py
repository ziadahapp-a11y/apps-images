#!/usr/bin/env python3
"""
فاحص التخطيط.

يُشغَّل قبل كل تصدير. الأخطاء التي يمسكها هي بالضبط ما لا تُرى بالعين
عند المراجعة السريعة، وتظهر بعد النشر:

  1. سقوط الخط    : ارتداد صامت إلى خط النظام يكسر الهوية بالكامل
  2. التراكب      : كتلتان نصيتان فوق بعض داخل نفس الشريحة
  3. عبور اللحام  : نص أو جوال ينقسم بين شريحتين فيصير نصفه مقصوصاً
  4. الهوامش      : محتوى يخرج عن الهامش الآمن فيقصّه العرض على الجوال

الطبقات الرابطة (الخلفية، الريل، الخط السفلي) مستثناة من فحص العبور
لأن عبورها هو الغرض منها.
"""

SAFE_PAD = 60          # أدنى هامش مقبول من حافة الشريحة
SLIDE_W = 1080

# منطقة شبكة انستقرام: الشبكة تعرض الشريحة الأولى مقصوصة لا كاملة،
# فأي عنوان يخرج عن المربع المركزي يظهر مقطوعاً في صفحة الحساب حتى
# لو كان سليماً في الخط الزمني. المصدر: مراجع مقاسات 2026.
GRID_SAFE_H = 1080     # ارتفاع المربع المركزي داخل شريحة 1350

# العناصر التي يجب أن تبقى كاملة داخل شريحة واحدة، لكل قالب محدداته.
# مرور الفحص فراغاً أسوأ من غيابه، لأنه يعطي ثقة كاذبة: لهذا يفشل
# الفاحص صراحةً إن لم يجد أي عنصر يعرفه.
CONTAINED_BY_FRAME = {
    "ig": [".slide__logo", ".hook", ".hook__title", ".hook__lead", ".badge",
           ".tagline", ".slide__foot", ".stat", ".phone"],
    "li": [".slide__logo", ".hook", ".hook__title", ".hook__lead", ".badge",
           ".tagline", ".slide__foot", ".stat", ".phone"],
    "x":  [".logo-slot", ".copy", ".title", ".lead", ".badge",
           ".stat", ".phone", ".foot--r", ".foot--l"],
}

# العناصر التي يُتوقع منها عبور اللحام
CROSSING_OK = [".bg", ".rail", ".footline"]

PROBE = r"""
() => {
  const out = { fonts: {}, boxes: [], crossing: [] };

  out.fonts.bold    = document.fonts.check('700 124px "IBM Plex Sans Arabic"');
  out.fonts.regular = document.fonts.check('400 33px "IBM Plex Sans Arabic"');
  out.fonts.light   = document.fonts.check('300 42px "IBM Plex Sans Arabic"');

  const contained = %s;
  const crossing  = %s;

  for (const sel of contained) {
    document.querySelectorAll(sel).forEach((el, i) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return;
      const slide = el.closest('.slide');
      out.boxes.push({
        sel: sel + '[' + i + ']',
        slide: slide ? slide.id : null,
        left: r.left, right: r.right, top: r.top, bottom: r.bottom
      });
    });
  }

  for (const sel of crossing) {
    const el = document.querySelector(sel);
    if (el) {
      const r = el.getBoundingClientRect();
      out.crossing.push({ sel, width: r.width });
    }
  }

  const root = document.querySelector('#%s');
  out.canvasWidth  = root.offsetWidth;
  out.canvasHeight = root.offsetHeight;
  return out;
}
"""


def _overlap(a, b):
    """مساحة التقاطع بين مستطيلين."""
    w = min(a["right"], b["right"]) - max(a["left"], b["left"])
    h = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
    return w * h if (w > 0 and h > 0) else 0


def check(page, frame: str = "ig", container: str = "canvas", slide_w: int = SLIDE_W,
          origin_x: float = 0.0, origin_y: float = 0.0) -> list:
    """يُرجع قائمة أخطاء. قائمة فارغة تعني أن التخطيط سليم."""
    import json

    contained = CONTAINED_BY_FRAME.get(frame)
    if contained is None:
        raise SystemExit("لا محددات فحص لمقاس %s. أضفها في check_layout." % frame)

    data = page.evaluate(PROBE % (json.dumps(contained), json.dumps(CROSSING_OK), container))
    errors = []

    # تطبيع الإحداثيات لتصير نسبةً إلى الكانفس لا إلى النافذة.
    # ضروري لأن النافذة أعرض من الكانفس والصفحة RTL، فالكانفس يبدأ
    # من إحداثي كبير لا من صفر.
    for b in data["boxes"]:
        b["left"] -= origin_x
        b["right"] -= origin_x
        b["top"] -= origin_y
        b["bottom"] -= origin_y

    if not data["boxes"]:
        return ["الفاحص لم يجد أي عنصر من محددات مقاس %s. "
                "إما القالب غيّر أسماء الأصناف أو المحددات خطأ. "
                "لا تعتبر هذا نجاحاً." % frame]

    # --- 1. الخط ---
    for name, ok in data["fonts"].items():
        if not ok:
            errors.append("الخط: وزن %s لم يُحمّل، التصميم مرتد لخط النظام" % name)

    canvas_w = data["canvasWidth"]
    seams = [slide_w * i for i in range(1, canvas_w // slide_w)]
    boxes = data["boxes"]

    # --- 2. عبور اللحام ---
    for box in boxes:
        for seam in seams:
            if box["left"] < seam < box["right"]:
                errors.append(
                    "عبور لحام: %s يمتد من %.0f إلى %.0f ويقطعه اللحام عند %d"
                    % (box["sel"], box["left"], box["right"], seam)
                )

    # --- 3. التراكب داخل نفس الشريحة ---
    # نتجاهل زوج الأب مع ابنه، فتداخلهما طبيعي
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a["slide"] != b["slide"] or a["slide"] is None:
                continue
            base_a = a["sel"].split("[")[0]
            base_b = b["sel"].split("[")[0]
            if base_a in (".hook",) or base_b in (".hook",):
                continue  # .hook حاوية لعناصرها
            area = _overlap(a, b)
            if area > 400:
                errors.append(
                    "تراكب: %s فوق %s بمساحة %.0f بكسل مربع" % (a["sel"], b["sel"], area)
                )

    # --- 4. الهوامش الآمنة ---
    for box in boxes:
        if box["slide"] is None:
            continue
        slide_left = 0 if box["slide"] == "slide-1" else slide_w * int(box["slide"].split("-")[1]) - slide_w
        local_left = box["left"] - slide_left
        local_right = box["right"] - slide_left
        if local_left < SAFE_PAD:
            errors.append("هامش: %s يبعد %.0f فقط عن الحافة اليسرى" % (box["sel"], local_left))
        if local_right > slide_w - SAFE_PAD:
            errors.append("هامش: %s يبعد %.0f فقط عن الحافة اليمنى" % (box["sel"], slide_w - local_right))

    # --- 5. منطقة شبكة انستقرام ---
    # الشبكة تقصّ الشريحة الأولى، فالعنوان لازم ينجو من القص.
    if frame == "ig":
        top = (data.get("canvasHeight", 0) - GRID_SAFE_H) / 2
        bottom = top + GRID_SAFE_H
        for box in boxes:
            if box["slide"] != "slide-1":
                continue
            if box["sel"].split("[")[0] not in (".hook__title", ".badge"):
                continue
            if box["top"] < top or box["bottom"] > bottom:
                errors.append(
                    "شبكة انستقرام: %s خارج المربع المركزي (%.0f إلى %.0f) "
                    "فيظهر مقطوعاً في صفحة الحساب"
                    % (box["sel"], top, bottom)
                )

    # --- 6. الطبقات الرابطة يجب أن تعبر فعلاً ---
    for layer in data["crossing"] if frame != "x" else []:
        if layer["width"] < canvas_w - 2:
            errors.append(
                "طبقة رابطة: %s عرضها %.0f وليس %d، فلن تعبر اللحام"
                % (layer["sel"], layer["width"], canvas_w)
            )

    return errors
