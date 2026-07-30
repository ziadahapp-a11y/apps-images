#!/usr/bin/env python3
"""
سجل ما أُنشئ. هذي الطبقة التي تمنع التكرار.

لماذا هذا الملف موجود:
  الإصدار السابق كان يختار "الأحد القادم" بدالة تعطي نفس التاريخ كل
  يوم من الاثنين إلى السبت. فأي تشغيل متكرر في نفس الأسبوع يعيد نفس
  الوحدة ونفس التصميم وينشئ نفس المسودات. النظام لم يكن يعرف ما أُنشئ
  أمس، فما كان عنده ما يرفض به.

  الحل ليس تعليمات أقوى للوكيل. التعليمات تُنسى وتُقتطع من السياق.
  الحل سجل خارج النموذج يقارن بصمة المحتوى ويرفض التطابق.

البصمة تُحسب على المحتوى نفسه لا على اسم الوحدة، فتغيير الاسم أو
التاريخ لا يفلت من الفحص. النص يُطبَّع قبل الحساب (مسافات وأسطر)،
فإعادة تنسيق نفس الكلام تُكتشف أيضاً.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent / "content" / "ledger.json"


def _empty() -> dict:
    return {"version": 1, "runs": {}, "fingerprints": {}}


def load() -> dict:
    if not LEDGER.exists():
        return _empty()
    try:
        d = json.loads(LEDGER.read_text(encoding="utf-8"))
    except Exception as e:
        # سجل تالف أخطر من سجل غائب: التالف يسمح بالتكرار صامتاً
        raise SystemExit(
            "سجل التكرار تالف: %s\n%s\n"
            "لا تحذفه. أصلحه أو استعده من git history، فحذفه يفتح الباب "
            "لإعادة نشر كل ما نُشر." % (LEDGER, e)
        )
    for k in ("runs", "fingerprints"):
        d.setdefault(k, {})
    return d


def save(d: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize(text: str) -> str:
    """
    يوحّد النص قبل البصمة: يجمع المسافات، يحذف أطراف الأسطر، ويسقط
    المحارف غير الدالة. الهدف أن تُكتشف إعادة صياغة شكلية لنفس الكلام.
    """
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[\u064b-\u0652\u0640]", "", t)   # تشكيل وتطويل
    return t


def fingerprint(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(normalize(str(p)).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def slide_fingerprint(slides: dict) -> str:
    """بصمة التصميم: العنوان والوصف والأرقام. الشكل نفسه لا الملف."""
    keys = ("badge", "title", "lead", "stat_num", "stat_label",
            "stat2_num", "stat2_label", "sheet_title")
    return fingerprint(*[slides.get(k, "") for k in keys])


# ==========================================================================
# الفحوصات
# ==========================================================================

def check_run(led: dict, target_iso: str, force: bool = False) -> None:
    """يرفض إعادة تشغيل تاريخ نُفّذ سابقاً."""
    prev = led["runs"].get(target_iso)
    if prev and not force:
        raise SystemExit(
            "تاريخ %s نُفّذ سابقاً في %s للوحدة '%s'، وأُنشئت %d مسودة.\n"
            "لن أنشئ نفس المحتوى مرة أخرى.\n"
            "إن كنت تريد استبداله فعلاً: احذف المسودات القديمة من بفر أولاً، "
            "ثم شغّل بـ --force."
            % (target_iso, prev.get("createdAt", "?"), prev.get("slug", "?"),
               len(prev.get("drafts", [])))
        )


def check_content(led: dict, target_iso: str, slug: str,
                  slides: dict, posts: dict, force: bool = False) -> list:
    """
    يفحص بصمة التصميم وبصمة كل نص. يُرجع قائمة البصمات لتسجيلها
    بعد النجاح. يرفع خطأً عند أي تطابق مع محتوى نُشر سابقاً.
    """
    out = []

    sfp = slide_fingerprint(slides)
    hit = led["fingerprints"].get(sfp)
    if hit and hit.get("date") != target_iso and not force:
        raise SystemExit(
            "تصميم هذه الوحدة مطابق لتصميم نُشر في %s للوحدة '%s'.\n"
            "العنوان والوصف والأرقام كلها نفسها. غيّر المحتوى فعلاً، "
            "لا اسم الوحدة." % (hit["date"], hit["slug"])
        )
    out.append((sfp, {"date": target_iso, "slug": slug, "kind": "slides"}))

    for service, text in posts.items():
        fp = fingerprint(service, text)
        hit = led["fingerprints"].get(fp)
        if hit and hit.get("date") != target_iso and not force:
            raise SystemExit(
                "نص %s مطابق لنص نُشر في %s للوحدة '%s'.\n"
                "أعد كتابته، ولا تكتفِ بتغيير الهاشتاقات أو الترتيب."
                % (service, hit["date"], hit["slug"])
            )
        out.append((fp, {"date": target_iso, "slug": slug, "kind": service}))

    return out


def check_buffer(existing_drafts: list, channel_id: str, due_at: str) -> None:
    """
    يرفض إن كانت مسودة موجودة أصلاً على نفس القناة ونفس الموعد.

    هذي الطبقة الثانية: تحمي حتى لو ضاع ملف السجل أو شُغّل النظام من
    جهاز آخر، لأنها تسأل بفر نفسه لا ملفاً محلياً.
    """
    target = (due_at or "")[:16]     # حتى الدقيقة
    for d in existing_drafts or []:
        if d.get("channelId") != channel_id:
            continue
        if (d.get("dueAt") or "")[:16] == target:
            raise SystemExit(
                "بفر فيه مسودة أصلاً على هذي القناة بموعد %s (المعرّف %s).\n"
                "لن أنشئ ثانية. احذف القديمة إن كنت تريد استبدالها."
                % (due_at, d.get("id"))
            )


def record(led: dict, target_iso: str, slug: str, commit: str,
           fps: list, drafts: list) -> None:
    led["runs"][target_iso] = {
        "slug": slug,
        "commit": commit,
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "drafts": drafts,
    }
    for fp, meta in fps:
        led["fingerprints"][fp] = meta
    save(led)
