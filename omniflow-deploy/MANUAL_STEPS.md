# ما يجب أن تفعله أنت يدويًا قبل الإطلاق (لا يمكن أتمتته من الكود)

لم يُنفَّذ أي نشر ولم يُلمس أي خادم. هذه القائمة بالترتيب:

## أ) أشياء تشتريها/تنشئها
- [ ] **دومين** + سجلات DNS (A/AAAA) تشير إلى الخادم؛ ودومين فرعي `clerk.<domain>` إذا طلبه Clerk للإنتاج (CNAME).
- [ ] **خادم (VPS)** Linux بـ Docker + Compose v2. تقدير أوّلي: 4 vCPU / 8–16 GB RAM / 100+ GB SSD (صورة الباك إند كبيرة؛ **لم تُجرَ اختبارات حمل**).
- [ ] **حساب Clerk للإنتاج** (Production instance) + webhook على `https://<domain>/api/v1/webhooks/clerk` + السر.
- [ ] **حساب Moyasar** (مفاتيح sandbox أولًا ثم live) + webhook على `https://<domain>/api/v1/webhooks/payments` بنفس قيمة `MOYASAR_WEBHOOK_SECRET`.
- [ ] **Sentry** (DSN)، **مزوّد بريد** (Resend أو SMTP) + دومين المرسل (SPF/DKIM).
- [ ] **وجهة النسخ الاحتياطي خارج الخادم** (bucket S3-متوافق أو خادم آخر) وأداة النقل (`rclone`/`aws`/`rsync`) مثبّتة على الخادم.
- [ ] مفاتيح Meta (واتساب/إنستجرام) وGemini/OpenAI — **جديدة**، وتدوير المسرّبة سابقًا (Gemini وInstagram) في لوحتَي Google وMeta.

## ب) إعداد الخادم
1. `git clone` ثم `cd omniflow-deploy && cp .env.prod.example .env.prod && chmod 600 .env.prod`.
2. املأ كل قيمة `<REQUIRED…>`. كلمات مرور قوية ومختلفة؛ `APP_POSTGRES_USER` مختلف عن مالك المخطط.
3. `sed -i 's/SERVER_DOMAIN/دومينك/g' nginx/prod.conf nginx/snippets/security-headers.conf` (الملفان معًا).
4. `./deploy.sh` (يعمل بشهادة مؤقتة ذاتية التوقيع أولًا) ثم أصدر شهادة Let's Encrypt بالأمر الذي يطبعه، وأعد تحميل nginx.
5. `cp systemd/omniflow-*.{service,timer} /etc/systemd/system/` بعد تعديل المسارات والمستخدم، ثم `systemctl enable --now omniflow-backup.timer omniflow-certbot.timer`.
6. اضبط `OFFHOST_COMMAND` ثم `./backup.sh` يدويًا مرة؛ تأكد أن `./backup-status.sh` يعطي `OK`.
7. أضف مستخدم `/docs`: الأمر في مخرجات `deploy.sh`.
8. المراقبة: `docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.monitoring.yml up -d` ثم عرّف **مستلم تنبيهات حقيقي** في `monitoring/alertmanager.yml` (افتراضيًا لا يرسل شيئًا) وفعّل مهمة `blackbox-tls` في `prometheus.yml`.

## ج) قرارات تجارية/قانونية مطلوبة منك
- [ ] الأسعار والحدود النهائية للباقات ثم `UPDATE plans SET ..., is_placeholder=false` (الإنتاج يرفض الدفع على الخطط التجريبية).
- [ ] مدة التجربة والسماح، وهل يُحجب البوت عند 100% من الرسائل (`BILLING_ENFORCE_MESSAGE_LIMIT`).
- [ ] الرقم الضريبي وبيانات البائع في `.env.prod`؛ سياسة الاسترداد.
- [ ] النصوص القانونية (سياسة الخصوصية والشروط) — الصفحتان الحاليتان **نص مؤقت**.
- [ ] مدد الاحتفاظ بالبيانات؛ قائمة موظفي الدعم (`SUPPORT_STAFF_EMAILS`).
- [ ] متى تُلغى إعفاءات المنشآت القائمة (`subscriptions.is_exempt`) لتبدأ فوترتها.

## د) اختبارات قبول على الخادم الفعلي (لم يجرِها أحد بعد)
- [ ] دخول Clerk وإنشاء منشأة وإنهاء الـ onboarding؛ دعوة موظف وقبولها.
- [ ] دفعة sandbox كاملة → webhook → تفعيل → فاتورة PDF؛ ثم فشل دفع → past_due → إيقاف → إعادة تفعيل.
- [ ] رسالة واتساب حقيقية لرقم تجريبي → ردّ البوت؛ إيقاف اشتراك → صمت البوت.
- [ ] استرجاع PostgreSQL حقيقي من نسخة إلى قاعدة جديدة (RUNBOOK §12)، و`bash tests/restore_drill.sh` لـ Qdrant/MinIO على مكدّس تجريبي.
- [ ] اختبار حمل تمثيلي وضبط حدود الموارد (لا توجد حدود CPU/ذاكرة في compose حاليًا عمدًا).
- [ ] فحص رؤوس الأمان: `curl -sI https://<domain> | grep -i -E 'content-security|x-frame|strict-transport'` وفتح الموقع من المتصفح بلا أخطاء CSP في الـ console (خصوصًا Clerk).
