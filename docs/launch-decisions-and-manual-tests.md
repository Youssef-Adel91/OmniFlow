# قرارات الإطلاق المفتوحة وأوامر الاختبار اليدوي

> المحتوى الدائم المنقول من `NIGHT_REPORT.md` (تقرير ليلة 2026-10-10، البرومبتات 3–6) بعد دمج كل الـ PRs في `main`.

## قرارات تحتاج قرارًا من صاحب المنتج
1. **قيم الدعم الحقيقية:** `SUPPORT_WHATSAPP_NUMBER`, `SUPPORT_ZOOM_URL`, `SUPPORT_DOCS_URL`, `SUPPORT_ADMIN_API_KEY`, `S3_SUPPORT_BUCKET` — بلا قيم تختفي بطاقات التواصل ويُعطَّل رد الموظفين (لم تُوضع قيم ولم يُلمس `.env`).
2. **handoff الافتراضي:** `auto_escalate` مفعّل افتراضيًا ⇒ مستأجر لم يفتح الإعدادات يحصل على رسالة تحويل (كان بلا رد). يمكن جعله OFF.
3. المستأجرون يحتاجون `whatsapp_waba_id` ليظهر قوالب الحملات.
4. **الأدوار:** المدقّق (auditor) يفتح ويقرأ تذاكر مستأجره — قرار تحفّظي قابل للتغيير.
5. أسعار الخطط placeholders (`plans.is_placeholder`)؛ الدفع الإنتاجي يرفضها حتى تُحدَّد الأسعار الحقيقية.

## ما لم يُنفَّذ (من تقرير الليلة، لم يتغير بعد الدمج)
- تأجيل تحميل الرسوم البيانية (lazy-load)؛ `KpiCard` يستورد recharts أيضًا.
- مراجعة التباين = استبدال آلي لأصناف `text-gray-500/600` بـ `400`، وليست قياسًا لكل الشاشات.

## ما لم يُختبر فعليًا (ملخص — التفصيل في `IMPLEMENTATION_STATUS.md`)
- شاشات جديدة في متصفح حقيقي مع Clerk؛ إرسال واتساب حقيقي؛ قوالب Meta الحية؛ شكل webhook الفعلي لحالات التسليم وردود الأزرار.
- Kafka حقيقي لسلسلة router→invoker→handoff؛ أثر النبرة/اللهجة على نموذج حقيقي.
- S3/MinIO حقيقي للمرفقات (الاختبارات تخزّن في الذاكرة).

## أوامر الاختبار
```
cd omniflow-backend
alembic upgrade head            # يطبّق حتى 0027
alembic downgrade -1 && alembic upgrade head   # تحقق آخر مايجريشن (down ثم up)

# اختبارات backend (تحتاج Postgres غير superuser للـ RLS)
export OMNIFLOW_TEST_DATABASE_URL=postgresql+asyncpg://<owner>@<host>/<db>
export OMNIFLOW_TEST_RLS_URL=postgresql+asyncpg://<app_user>@<host>/<db>
python -m unittest discover -s tests -t .

# frontend
cd omniflow-frontend && npx tsc --noEmit && npx eslint src && node --test tests/*.test.cjs && npx next build
```
بعد إنشاء جداول جديدة على قاعدة اختبار محلية: `GRANT ALL ON ALL TABLES IN SCHEMA public TO app_user;` (وللـ sequences).

## اختبار يدوي لكل ميزة
- **العملاء:** /customers → إضافة عميل بموافقة، استيراد CSV بعمود موافقة، تصدير CSV، فتح الدرج.
- **الحملات:** /broadcasts → شريط الجاهزية، قالب معتمد، معاينة الجمهور (يستبعد غير الموافقين)، جدولة مع تأكيد العدد.
- **الإعدادات:** /settings → ساعات عمل ثم رسالة «اكلم موظف» خارج الدوام، «اختبار البوت».
- **الدعم:** /support → بحث، تذكرة بمرفق، رد، إغلاق وإعادة فتح.
