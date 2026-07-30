#!/usr/bin/env python3
"""
عميل بفر لإنشاء المسودات.

نقطة النهاية: https://api.buffer.com  (GraphQL)
المصادقة: Authorization: Bearer <المفتاح>  من متغير البيئة BUFFER_API_KEY

الشكل مأخوذ من التوثيق الرسمي لـ createPost، ومتحقَّق منه فعلياً:
مسودات زيادة الثلاث الأولى أُنشئت بنفس البنية ونجحت.

ملاحظة على نوع المدخل: اسم نوع الـ input في GraphQL غير موثق صراحة،
فبدل تخمينه نستخرجه من الـ schema عبر introspection في أول نداء.
هذا يجعل العميل يصحح نفسه لو أعاد بفر تسمية النوع، بدل أن يفشل بخطأ
غامض. بفر غيّر صيغة الوسائط فعلاً في 25 مايو 2026، فالاحتراس مبرَّر.
"""
import json
import os
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.buffer.com"
TIMEOUT = 45
RETRIES = 3


class BufferError(RuntimeError):
    pass


class Buffer:
    def __init__(self, token: str = None):
        self.token = token or os.environ.get("BUFFER_API_KEY", "").strip()
        if not self.token:
            raise BufferError(
                "BUFFER_API_KEY غير موجود. أضفه كسر في الريبو: "
                "Settings > Secrets and variables > Actions"
            )
        self._input_type = None

    # ---------------------------------------------------------------- النقل

    def _call(self, query: str, variables: dict = None) -> dict:
        body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        req = urllib.request.Request(
            ENDPOINT,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.token,
            },
        )

        last = None
        for attempt in range(RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    payload = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:400]
                # 401 و 403 لا تُعاد المحاولة فيهما، المفتاح خطأ ولن يتغير
                if e.code in (401, 403):
                    raise BufferError("المصادقة فشلت (%d). راجع المفتاح.\n%s" % (e.code, detail))
                last = "HTTP %d: %s" % (e.code, detail)
            except Exception as e:
                last = str(e)
            time.sleep(2 + attempt * 3)
        else:
            raise BufferError("تعذّر الوصول إلى بفر بعد %d محاولات:\n%s" % (RETRIES, last))

        if payload.get("errors"):
            raise BufferError("خطأ GraphQL:\n" + json.dumps(payload["errors"], ensure_ascii=False, indent=2))
        return payload.get("data") or {}

    # ------------------------------------------------------- اكتشاف النوع

    def _discover_input_type(self) -> str:
        """يستخرج اسم نوع مدخل createPost من الـ schema بدل تخمينه."""
        if self._input_type:
            return self._input_type

        q = """
        query {
          __schema {
            mutationType {
              fields { name args { name type { name kind ofType { name kind } } } }
            }
          }
        }
        """
        fields = self._call(q)["__schema"]["mutationType"]["fields"]
        field = next((f for f in fields if f["name"] == "createPost"), None)
        if not field:
            raise BufferError("لا يوجد createPost في الـ schema. راجع صلاحيات المفتاح.")

        arg = next((a for a in field["args"] if a["name"] == "input"), None)
        if not arg:
            raise BufferError("createPost بلا وسيط input. تغيّرت الواجهة.")

        t = arg["type"]
        name = t.get("name") or (t.get("ofType") or {}).get("name")
        if not name:
            raise BufferError("تعذّر تحديد نوع المدخل من الـ schema.")

        self._input_type = name
        return name

    # ------------------------------------------------------------- قراءة

    def channels(self, org_id: str) -> list:
        q = """
        query($id: String!) {
          organization(id: $id) { channels { id service name } }
        }
        """
        return self._call(q, {"id": org_id})["organization"]["channels"]

    def drafts(self, org_id: str) -> list:
        """
        المسودات القائمة. تُقرأ قبل كل إنشاء لمنع التكرار حتى لو ضاع
        السجل المحلي أو شُغّل النظام من جهاز آخر.
        """
        q = """
        query($id: String!) {
          posts(organizationId: $id, status: [draft], first: 50) {
            edges { node { id channelId dueAt text } }
          }
        }
        """
        try:
            data = self._call(q, {"id": org_id})
        except BufferError as e:
            # لا نكمل على العمياء: عدم القدرة على القراءة يعني عدم
            # القدرة على منع التكرار، وهذا سبب كافٍ للتوقف.
            raise BufferError(
                "تعذّر قراءة المسودات القائمة، فلا أستطيع ضمان عدم التكرار:\n%s" % e
            )
        edges = (data.get("posts") or {}).get("edges") or []
        return [e["node"] for e in edges if e.get("node")]

    # ------------------------------------------------------------- كتابة

    def create_draft(self, channel_id: str, text: str, due_at: str,
                     images: list, alt_texts: list, service: str) -> dict:
        """
        ينشئ مسودة واحدة. لا ينشر أبداً: saveToDraft ثابت على true.

        images    قائمة روابط عامة مباشرة
        alt_texts نص بديل لكل صورة، بنفس الترتيب
        service   instagram | twitter | linkedin
        """
        assets = []
        for i, url in enumerate(images):
            alt = alt_texts[i] if i < len(alt_texts) else (alt_texts[-1] if alt_texts else "")
            assets.append({"image": {"url": url, "metadata": {"altText": alt}}})

        payload = {
            "channelId": channel_id,
            "text": text,
            "schedulingType": "automatic",
            "mode": "customScheduled",
            "dueAt": due_at,
            "saveToDraft": True,     # لا يُغيَّر. المراجعة البشرية شرط.
            "assets": assets,
        }

        if service == "instagram":
            # انستقرام يرفض المنشور بلا هذين الحقلين
            payload["metadata"] = {"instagram": {"type": "post", "shouldShareToFeed": True}}

        input_type = self._discover_input_type()
        q = """
        mutation CreateDraft($input: %s!) {
          createPost(input: $input) {
            ... on PostActionSuccess {
              post { id status dueAt channelId assets { mimeType } }
            }
            ... on MutationError { message }
          }
        }
        """ % input_type

        res = self._call(q, {"input": payload})["createPost"]

        if "message" in res and res.get("message"):
            raise BufferError("بفر رفض المنشور: " + res["message"])

        post = res.get("post")
        if not post:
            raise BufferError("رد غير متوقع من بفر:\n" + json.dumps(res, ensure_ascii=False))
        if post.get("status") != "draft":
            raise BufferError(
                "الحالة %s وليست draft. أوقف التشغيل وافحص قبل أي شي."
                % post.get("status")
            )
        return post
