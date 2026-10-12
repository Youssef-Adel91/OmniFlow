# الفاتورة الإلكترونية (ZATCA المرحلة الثانية) — وحدة `omniflow-backend/src/einvoice/`

> **الحالة: مبنية ومُختبرة محليًا، غير مُتحقَّق منها مقابل ZATCA.** لا SDK رسمي ولا ملفات أمثلة رسمية ولا بيانات اعتماد Sandbox كانت متاحة. كل ما يلي مبني على المواصفة المنشورة،
> ومُختبَر داخليًا (البنية، سلسلة الـ hash، توقيع/تحقق، كشف التلاعب، ICV الذري، منطق إعادة المحاولة، RLS). **لا تفعّلها في الإنتاج قبل المراجعة بالأداة الرسمية** (انظر `docs/morning-verification.md`).

## المفاتيح الأمنية (مفتاحان مستقلان)
| متغير | الافتراضي | المعنى |
|---|---|---|
| `EINVOICE_ENABLED` | `false` | مفتاح الميزة كلها. مطفأ = لا إصدار ولا إرسال ولا endpoints (404). |
| `EINVOICE_ENV` | `sandbox` | `sandbox` \| `simulation` \| `production`. |
| `EINVOICE_ALLOW_PRODUCTION_SUBMIT` | `false` | لا يُرسَل لإنتاج ZATCA إلا إذا `EINVOICE_ENV=production` **و** هذا `true` معًا. |

## الشرط القانوني قبل التفعيل
الكيان المُصدِر (الشركة التي ستظهر كبائع) يجب أن يكون **مسجّلًا في ضريبة القيمة المضافة في السعودية** ولديه رقم ضريبي من 15 رقمًا، وأن يكون مكتملًا تسجيله في منصة فاتورة (Fatoora) ويملك OTP لإصدار الـ CSID. بدون ذلك يبقى المفتاح `EINVOICE_ENABLED=false`. هذا قرار عمل لا يستطيع الكود التحقق منه.

متغيرات البائع المطلوبة: `BILLING_SELLER_NAME`, `BILLING_SELLER_TAX_NUMBER`, `BILLING_SELLER_CR_NUMBER`, `BILLING_SELLER_STREET`, `_BUILDING`, `_DISTRICT`, `_CITY`, `_POSTAL_ZONE`. بدون الرقم الضريبي ترفض الوحدة إنشاء المستند (`InvalidInvoice`) **قبل** أن تتحرك السلسلة، ولا تُعطّل الدفع.

## ماذا تفعل الوحدة
1. **UBL 2.1** (`ubl.py`): فاتورة ضريبية 388 (B2B، `0100000`) ومبسّطة (B2C، `0200000`)، إشعار دائن 381 (للاسترداد)، إشعار مدين 383. حقول ZATCA: ProfileID `reporting:1.0`، UUID، ICV، PIH، QR، `cac:Signature`، TaxTotal مزدوج، BillingReference + InstructionNote للإشعارات.
   تُحدَّد النوعية تلقائيًا: إن كان لدى المشتري (المنشأة) رقم ضريبي → B2B (Clearance)، وإلا → مبسّطة (Reporting).
2. **ICV وسلسلة PIH ذرّية** (`service.py`): صف `einvoice_units` يُقفل `FOR UPDATE`، يُحسب ICV = العداد+1 وPIH = آخر hash، يُبنى المستند ويُوقَّع، ثم يُحدَّث العداد وآخر hash في **نفس المعاملة**. اختبار 12 إصدارًا متزامنًا يثبت: بلا فجوات ولا تكرار وكل مستند يشير لسابقه. أول PIH = base64(hex(sha256("0"))).
3. **التوقيع** (`signing.py`): XAdES-style enveloped، ECDSA secp256k1 + SHA-256، مراجع الفاتورة (مع استبعاد UBLExtensions وcac:Signature وQR) وSignedProperties، وفق ما أعرفه من عينات ZATCA (digest الفاتورة base64 للـ bytes؛ digest الـ SignedProperties والشهادة base64 للـ hex).
4. **QR TLV** (`tlv.py`): الوسوم 1–9 (البائع، الرقم الضريبي، الوقت، الإجمالي، الضريبة، hash، التوقيع، المفتاح العام، توقيع الشهادة للمبسّطة).
5. **التهيئة** (`certs.py` + endpoints الموظفين): توليد مفتاح (يُخزَّن مشفّرًا بـ Fernet، لا يُطبع ولا يدخل Git) وCSR بصيغة ZATCA (قالب الشهادة حسب البيئة، SAN بـ SN/UID/title/registeredAddress/businessCategory) ← Compliance CSID بـ OTP ← Production CSID.
6. **الإرسال** (`client.py` + `submit_due`): Clearance للـ B2B، Reporting للمبسّطة، backoff أُسّي حتى 6 ساعات، `rejected` نهائي عند 400 (يحتاج تصحيح بيانات)، `dead` بعد `EINVOICE_MAX_ATTEMPTS`، 409 يُعامَل كنجاح، مهمة Celery كل 5 دقائق.
7. **PDF/A-3** (`pdf.py`): الفاتورة بالعربية مع QR والـ XML الموقّع مضمّنًا كمرفق (WeasyPrint `pdf/a-3b`). فُحص بصريًا (انظر التقرير) وبنيويًا (المرفق موجود وموقّعه صالح، وXMP يعلن PDF/A-3).
8. **الربط**: `issue_invoice` (دفع/تفعيل يدوي) و`refunds.issue` (إشعار دائن) يستدعيان `issue_safely` — خطأ في الفاتورة الإلكترونية لا يتراجع عنه الدفع أبدًا.

## ما لم يُجرَّب فعليًا
- أي اتصال بخوادم ZATCA (Sandbox/Simulation/Production). صيغة المسارات والرؤوس من التوثيق العام.
- فحص XML/التوقيع بأداة ZATCA الرسمية (SDK Validator) ومقارنة مع عيّنات ZATCA الرسمية.
- قبول `X509IssuerName`/ترتيب العناصر/الـ whitespace كما تتوقعه ZATCA بالضبط (نقاط معروفة بالحساسية).
- مسح الـ QR بتطبيق ZATCA. الشهادة المستخدمة في الاختبارات ذاتية التوقيع وللتطوير فقط (`dev_self_signed_cert`).

## تشغيل
```
# تهيئة (من لوحة الموظف أو curl بحساب موظف)
POST /api/v1/einvoice/onboarding/csr           {org_unit, address, business_category, unit_serial}
POST /api/v1/einvoice/onboarding/compliance-csid {otp}
POST /api/v1/einvoice/onboarding/production-csid
GET  /api/v1/einvoice/status        GET /api/v1/einvoice/documents?status=dead
POST /api/v1/einvoice/documents/{id}/retry
```
