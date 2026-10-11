# دليل التشغيل — OmniFlow (للطوارئ)

> كل الأوامر تُنفَّذ على الخادم من مجلد النشر: `cd /opt/omniflow/omniflow-deploy`
> اختصار: `C="docker compose --env-file .env.prod -f docker-compose.prod.yml"`
> **قاعدة ذهبية:** لا تحذف volumes، لا تنفّذ `down -v`، ولا تعدّل `.env.prod` أثناء الطوارئ قبل أخذ نسخة (`./backup.sh`).

## 0) نظرة سريعة (30 ثانية)
```bash
C="docker compose --env-file .env.prod -f docker-compose.prod.yml"
$C ps                                             # أي خدمة Exited/unhealthy؟
curl -fsS https://DOMAIN/health/ready             # قاعدة البيانات + Redis + Kafka
docker exec prod-fastapi python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health/workers').read().decode())"
docker exec prod-fastapi python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health/lag').read().decode())"   # تأخّر Kafka + رسائل DLQ + رسائل صادرة عالقة > 5 دقائق (warn/critical؛ للتنبيه فقط)
./backup-status.sh                                # هل آخر نسخة احتياطية حديثة؟ وهل لها نسخة خارج الخادم؟
```
تنبيهات Prometheus (إن شُغّلت المراقبة): `ssh -L 9091:127.0.0.1:9091 user@server` ثم افتح `localhost:9091/alerts`.

---
## 1) البوت لا يرد على العملاء
الترتيب من الأرجح إلى الأقل:
1. **الاشتراك موقوف؟** البوت يصمت عمدًا للمنشأة الموقوفة/المنتهية تجربتها. تحقق: واجهة الفوترة للمنشأة، أو
   `docker exec prod-postgres psql -U $POSTGRES_USER $POSTGRES_DB -c "select status, current_period_end, grace_ends_at, is_exempt from subscriptions where tenant_id='<TENANT>'"`.
   - لإعادة التفعيل بعد دفع خارج البوابة: `POST /api/v1/billing/admin/tenants/<id>/activate` (انظر §9).
   - **مفتاح الطوارئ** إن كانت البوابة نفسها هي المشكلة: ضع `BILLING_GATE_ENABLED=false` في `.env.prod` ثم `$C up -d fastapi-backend worker-invoker` (يفتح الجميع؛ أعده `true` بعد الحل).
2. **العمال حيّون؟** `/health/workers` (الأمر أعلاه) — أي عامل `missing` فهو متوقف أو معلّق:
   `$C logs --tail=100 worker-invoker` ثم `$C restart worker-invoker` (غيّر الاسم: worker-router / worker-dispatcher / worker-vcard-gatekeeper / worker-vault / worker-broadcast).
3. **Kafka متأخر؟** (§4).
4. **مفتاح LLM منتهٍ/محدود؟** ابحث في سجل `worker-invoker` عن `rate_limit`/`401`/`quota`. الحل: تدوير المفتاح (§11).
5. **قناة واتساب/إنستجرام مفصولة؟** الإعدادات ← القنوات ← «فحص الاتصال». رمز Meta منتهٍ ⇒ إعادة الربط.
6. **خارج ساعات العمل أو تحويل لموظف؟** البوت يردّ برسالة خارج الدوام/التحويل حسب إعدادات المنشأة — ليس عطلاً.

## 2) الموقع/الـ API لا يعمل
```bash
$C ps nginx fastapi-backend nextjs-frontend
$C logs --tail=80 nginx fastapi-backend
curl -fsS http://localhost/nginx-health          # nginx حي؟
```
- `502` من nginx ⇒ الخدمة الخلفية متوقفة: `$C up -d fastapi-backend nextjs-frontend` ثم راقب `$C logs -f fastapi-backend`.
- `/health/ready` يعيد 503 ⇒ اقرأ الجسم: يحدد `database` أو `redis` أو `kafka` — انتقل للقسم المناسب أدناه.
- الشهادة منتهية/غير صالحة ⇒ §8.

## 3) قاعدة البيانات (PostgreSQL) وقعت
```bash
$C ps postgres pgbouncer && $C logs --tail=100 postgres
$C up -d postgres pgbouncer            # restart: always يعيدها غالبًا وحده
```
- **لا تحذف volume `postgres_prod_data` أبدًا.** إن تلف القرص أو فُقدت البيانات: الاسترجاع من نسخة (§12).
- امتلاء الاتصالات: ينبّه Prometheus `PostgresConnectionsHigh`. افحص `select count(*), state from pg_stat_activity group by 2;` وأعد تشغيل الخدمة المتسرّبة.
- بعد الاستعادة شغّل `$C --profile maintenance run --rm --no-deps migrate` للتأكد أن المخطط على آخر نسخة.

## 4) Kafka (Redpanda): تأخّر (lag) أو رسائل في DLQ
```bash
docker exec prod-redpanda rpk cluster health
docker exec prod-redpanda rpk group list
docker exec prod-redpanda rpk group describe omniflow.ai-workers.v1       # عمود LAG: يجب أن يقترب من 0
docker exec prod-redpanda rpk topic list | grep -i dlq
docker exec prod-redpanda rpk topic consume <اسم-topic-dlq> -n 5           # تفحّص سبب الفشل
```
مجموعات المستهلكين: `omniflow.ai-workers.v1` (router+invoker) · `omniflow.notification-svc.v1` (dispatcher) · `omniflow.broadcast-workers.v1` · `omniflow.vcard-gatekeeper.v1` · `omniflow.vault-workers.v1`.
- LAG ينمو ⇒ العامل معلّق: أعد تشغيله (§1.2). الرسائل لا تضيع (تُعاد قراءتها من آخر offset ملتزَم).
- رسائل DLQ ⇒ افتح سجل العامل المقابل لمعرفة الاستثناء؛ **لا تعد تشغيل الرسائل القديمة بشكل أعمى** (قد تُرسل ردودًا قديمة لعملاء).

## 5) Redis
```bash
$C logs --tail=50 redis; docker exec prod-redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning info memory | head
```
فقدان Redis يعني فقدان ذاكرة المحادثة المؤقتة وعدّادات الاستهلاك غير المحفوظة (تُحفظ كل 5 دقائق في قاعدة البيانات) — لا فقدان في البيانات الدائمة. أعد تشغيله ثم `$C restart worker-invoker worker-router`.

## 6) Qdrant (بحث العقارات للبوت)
`$C logs --tail=50 qdrant`. عند فقدان الفهارس: واجهة العقارات ← «إعادة الفهرسة» (تعيد البناء من قاعدة البيانات). الاسترجاع من لقطة: `./restore.sh qdrant <collection> <snapshot-file>`.

## 7) القرص ممتلئ
`df -h /; docker system df`. ابدأ بـ: سجلات Docker القديمة (محدودة 50MB×3 لكل حاوية)، `backups/` (يحتفظ بـ `RETENTION_DAYS`)، ثم `docker image prune` **للصور غير المستخدمة فقط** (لا `-a` ولا volumes). لا تحذف مجلدات `*_prod_data`.

## 8) شهادة TLS
```bash
echo | openssl s_client -connect DOMAIN:443 -servername DOMAIN 2>/dev/null | openssl x509 -noout -dates
systemctl list-timers 'omniflow-certbot*'; journalctl -u omniflow-certbot.service -n 50 --no-pager
```
تجديد يدوي: الأمر في آخر مخرجات `deploy.sh` (certbot ثم `docker exec prod-nginx nginx -s reload`). الفشل المعتاد: DNS لا يشير للخادم أو المنفذ 80 مغلق.

## 9) المدفوعات
- **عميل دفع ولم يُفعَّل:** افتح لوحة Moyasar ← الدفعة ← تأكد أنها `paid`. ثم في واجهة العميل «الفوترة» (صفحة العودة تتحقق من المزوّد تلقائيًا). إن لم يُفعَّل: تحقق من وصول الـ webhook:
  `$C logs fastapi-backend | grep payment_webhook` (رفض ⇒ سر الـ webhook في `.env.prod` لا يطابق لوحة Moyasar).
- **تفعيل يدوي (تحويل بنكي):**
  ```bash
  curl -sS -X POST https://DOMAIN/api/v1/billing/admin/tenants/<TENANT_ID>/activate \
    -H "X-Billing-Key: $BILLING_ADMIN_API_KEY" -H 'Content-Type: application/json' \
    -d '{"plan_code":"professional","interval":"monthly","periods":1,"reference":"TRF-1001","note":"تحويل بنكي"}'
  ```
  يُنشئ معاملة وفاتورة ويُسجَّل في `audit_logs`.
- **تجديدات متعثرة:** `select status, retry_count, last_payment_error from subscriptions where status in ('active','past_due');` ومهمة `omniflow.billing_sweep` تعمل كل 15 دقيقة في `celery-beat`/`celery-worker`.
- **الاستهلاك لا يتحدّث:** مهمة `omniflow.billing_usage_flush` كل 5 دقائق — تحقق من سجل `celery-worker`.

## 10) طابور الدعم والإشعارات
لوحة الموظفين: `/ar/support/staff` (بريدك في `SUPPORT_STAFF_EMAILS`). الإشعارات بالبريد لا تصل ⇒ `EMAIL_PROVIDER`/المفاتيح فارغة أو خاطئة: ابحث `email_send_failed` في السجل. الإشعارات داخل التطبيق لا تعتمد على البريد.

## 11) تدوير مفتاح مسرّب أو منتهٍ
1. أنشئ مفتاحًا جديدًا في لوحة المزوّد (Google/Meta/Moyasar/Clerk…) **قبل** إلغاء القديم.
2. حدّث `.env.prod` (لا تطبعه ولا تلصقه في دردشة/تذكرة)، ثم `$C up -d` للخدمات المتأثرة.
3. ألغِ المفتاح القديم في لوحة المزوّد. 4. تحقق من الوظيفة (رسالة اختبار/دفعة sandbox).
5. إن كان المفتاح قد وصل إلى Git: التدوير وحده كافٍ؛ لا تعدّل التاريخ دون قرار صريح من المالك.

## 12) الاسترجاع من نسخة احتياطية
**PostgreSQL** — الأمر الجاهز: `./restore.sh postgres backups/postgres/omniflow_<ts>.dump omniflow_restore` (ينشئ قاعدة *جديدة* ويرفض الكتابة فوق قاعدة موجودة أو الحية، ثم يطبع فحصًا سريعًا). وللتحقق دون Docker: `python -m scripts.pg_restore_drill --source postgresql://…` من مجلد الـbackend. الخطوات اليدوية المكافئة (الإجراء الآمن: استرجع إلى قاعدة *جديدة* وافحصها قبل أي استبدال):
```bash
set -a; . ./.env.prod; set +a
ls backups/postgres | tail                                              # اختر ملف .dump
docker cp backups/postgres/omniflow_<ts>.dump prod-postgres:/tmp/restore.dump
docker exec prod-postgres createdb -U "$POSTGRES_USER" omniflow_restore
docker exec prod-postgres pg_restore -U "$POSTGRES_USER" -d omniflow_restore --no-owner /tmp/restore.dump
docker exec prod-postgres psql -U "$POSTGRES_USER" omniflow_restore -c "select count(*) from tenants; select max(created_at) from messages;"
```
إن كانت البيانات سليمة ويلزم الاستبدال: أوقف التطبيق (`$C stop fastapi-backend worker-router worker-invoker worker-dispatcher worker-vcard-gatekeeper worker-vault worker-broadcast celery-worker celery-beat`)، أعد تسمية القاعدتين (`ALTER DATABASE ... RENAME TO ...`) في نافذة صيانة، ثم `./deploy.sh` (يعيد منح صلاحيات دور التطبيق عبر `provision-app-role.sql`).
**Qdrant / MinIO:** `./restore.sh qdrant <collection> <snapshot-file>` و`./restore.sh minio <backup-bucket>/<timestamp>/<source-bucket> <destination-bucket>`.
**تدريب:** `tests/restore_drill.sh` يختبر استرجاع Qdrant وMinIO فقط (على مكدّس تطوير فيه الحاويات، مع متغيرات الأسماء في رأس الملف) — **لم يُشغَّل في البيئة التي كُتب فيها هذا الدليل**، وكذلك لم يُجرَّب استرجاع PostgreSQL على الخادم الإنتاجي. افعل ذلك كل ربع سنة.

## 13) الرجوع إلى إصدار سابق
```bash
./rollback.sh <git-tag-أو-commit>              # تشغيل تجريبي: يطبع الخطوات فقط
./rollback.sh <git-tag-أو-commit> --yes        # تنفيذ (يأخذ نسخة أولًا، ولا يمسّ البيانات)
```
المخطط (migrations) لا يُنزَّل افتراضيًا لأن الإصدارات القديمة تعمل عادةً على مخطط أحدث. لا تستخدم `--db-revision` إلا بعد قراءة `downgrade()` لكل مايجريشن بين الإصدارين (ينزّل الجداول ويحذف بياناتها).

## 14) من يُتصل به
[يُستكمل: اسم مسؤول المناوبة، رقمه، ومزوّد الاستضافة/الدعم الفني.]
