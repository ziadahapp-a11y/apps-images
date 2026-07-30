#!/usr/bin/env python3
"""
يدمج خطة الإكسل مع الوحدات المصمّمة في مصدر واحد.

    python3 scripts/merge_plans.py

المشكلة التي يحلّها:
  ملف الإكسل ينشر ثلاث أفكار في الأحد الواحد. النظام كان يقبل وحدة
  واحدة لكل تاريخ ويرفض ما عداها كتكرار. النتيجة أن استيراد الملف
  كما هو يسقط ثلثي محتواه.

القرار: توزيع الأفكار الثلاث على أحد وثلاثاء وخميس.
  ثلاث أفكار × ثلاث منصات = تسعة منشورات، وسقف خطة بفر عشرة.
  فالإيقاع الأسبوعي في الملف يبقى كما هو، والتعارض يختفي من أصله
  لأن كل فكرة تحصل على تاريخ نشر خاص بها.
  والأيام نفسها هي أيام الذروة التي بُني عليها جدول النشر.

الأولوية عند التزاحم:
  المناسبة تأخذ الأحد دائماً، فهو أقوى يوم في الأسبوع.
  وحين يكون في الأسبوع مناسبة، يُخفَّض الدائم إلى فكرة واحدة، وهذي
  قاعدة من دليل استخدام الملف لا اجتهاد مني.
"""
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# أيام النشر داخل الأسبوع، إزاحةً من الأحد
SLOTS = [("sun", 0), ("tue", 2), ("thu", 4)]

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2}


def load(name: str) -> dict:
    p = ROOT / "content" / name
    if not p.exists():
        raise SystemExit("%s غير موجود. شغّل import_plan.py أولاً." % name)
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def main():
    imported = load("imported.yml")
    designed = load("quarter.yml")

    # الوحدات المصمّمة مفهرسةً بالتاريخ: هي التي لها شرائح ونصوص جاهزة،
    # فتُقدَّم على فكرة الإكسل المجرّدة في نفس الأسبوع.
    by_date = {w["date"]: w for w in designed["weeks"]}

    weeks = defaultdict(list)
    for u in imported["units"]:
        weeks[u["date"]].append(u)

    merged, backlog, notes, stats = [], [], [], defaultdict(int)

    for week_start in sorted(weeks):
        items = weeks[week_start]

        # المناسبة أولاً، ثم الأولوية، ثم ترتيب الملف الأصلي
        items.sort(key=lambda u: (
            0 if u["act"] == "occasion" else 1,
            PRIORITY_ORDER.get(u["priority"], 3),
            u.get("source_row") or 0,
        ))

        has_occasion = any(u["act"] == "occasion" for u in items)
        if has_occasion and len(items) > 2:
            # قاعدة الملف: أسبوع فيه مناسبة يُخفَّض دائمه إلى فكرة واحدة
            kept, dropped = items[:2], items[2:]
            notes.append(
                "أسبوع %s فيه مناسبة: أُبقيت المناسبة وفكرة واحدة، "
                "وأُجّلت %d فكرة" % (week_start, len(dropped))
            )
            # المؤجّلة تدخل قائمة انتظار لا تُحذف. حذفها يفقد ثلث
            # محتوى الملف صامتاً، وهذا أسوأ من تأجيل معلن.
            for d in dropped:
                d["deferred_from"] = week_start
                d.pop("date", None)
                backlog.append(d)
                stats["backlog"] += 1
            items = kept

        base = date.fromisoformat(week_start)
        for (slot_name, offset), unit in zip(SLOTS, items):
            pub = base + timedelta(days=offset)
            unit["date"] = pub.isoformat()
            unit["slot"] = slot_name

            # وحدة مصمّمة بنفس تاريخ الأسبوع؟ نرقّي فكرة الإكسل بشرائحها
            ready = by_date.get(week_start)
            if ready and slot_name == "sun" and unit["act"] != "occasion":
                unit["slides"] = ready["slides"]
                unit["posts"] = ready["posts"]
                unit["design_source"] = ready["slug"]
                stats["designed"] += 1
            elif ready and slot_name == "sun" and unit["act"] == "occasion" \
                    and ready.get("occasion"):
                unit["slides"] = ready["slides"]
                unit["posts"] = ready["posts"]
                unit["design_source"] = ready["slug"]
                stats["designed"] += 1
            else:
                stats["needs_design"] += 1

            merged.append(unit)
            stats[slot_name] += 1

    # ملء الفراغات: أسابيع فيها أقل من ثلاث أفكار تستقبل من قائمة
    # الانتظار، فلا تبقى فكرة معلّقة وفي الجدول خانة فارغة.
    filled = 0
    used = {u["date"] for u in merged}
    for week_start in sorted(weeks):
        base = date.fromisoformat(week_start)
        for slot_name, offset in SLOTS:
            if not backlog:
                break
            pub = (base + timedelta(days=offset)).isoformat()
            if pub in used:
                continue
            u = backlog.pop(0)
            u["date"] = pub
            u["slot"] = slot_name
            u["filled_gap"] = True
            merged.append(u)
            used.add(pub)
            stats[slot_name] += 1
            stats["needs_design"] += 1
            filled += 1
    stats["backlog"] = len(backlog)
    if filled:
        notes.append("%d فكرة مؤجّلة وُضعت في خانات فارغة لاحقة" % filled)

    merged.sort(key=lambda u: u["date"])

    out = {
        "generated_from": [imported["source"], "quarter.yml"],
        "slots": dict(SLOTS),
        "totals": dict(stats),
        "units": merged,
        "backlog": backlog,
    }
    path = ROOT / "content" / "merged.yml"
    path.write_text(yaml.dump(out, allow_unicode=True, sort_keys=False, width=100),
                    encoding="utf-8")

    dates = [u["date"] for u in merged]
    print("دُمجت %d وحدة إلى %s" % (len(merged), path.relative_to(ROOT)))
    print("المدى: %s إلى %s" % (min(dates), max(dates)))
    print()
    print("التوزيع على الأيام: أحد %d · ثلاثاء %d · خميس %d"
          % (stats["sun"], stats["tue"], stats["thu"]))
    print("جاهزة بالتصميم: %d | تحتاج تصميماً: %d | في قائمة الانتظار: %d"
          % (stats["designed"], stats["needs_design"], stats["backlog"]))
    print()

    dupes = len(dates) - len(set(dates))
    print("تواريخ مكررة بعد التوزيع: %d %s"
          % (dupes, "(التعارض انتهى)" if dupes == 0 else "← لا يزال هناك تعارض"))

    posts_per_week = defaultdict(int)
    for u in merged:
        d = date.fromisoformat(u["date"])
        posts_per_week[(d - timedelta(days=(d.weekday() - 6) % 7)).isoformat()] += 3
    worst = max(posts_per_week.values()) if posts_per_week else 0
    print("أقصى منشورات في أسبوع: %d (سقف بفر 10) %s"
          % (worst, "سليم" if worst <= 10 else "← يتجاوز السقف"))

    if notes:
        print()
        print("قرارات اتُّخذت (%d):" % len(notes))
        for n in notes[:8]:
            print("   - " + n)
        if len(notes) > 8:
            print("   ... و%d أخرى" % (len(notes) - 8))


if __name__ == "__main__":
    main()
