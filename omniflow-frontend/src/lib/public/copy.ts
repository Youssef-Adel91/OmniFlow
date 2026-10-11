/** Bilingual (ar / en) copy for the public pages. Only statements that are true of the product today; no invented statistics, customers or awards. */
import type { PublicLocale } from "@/lib/public/site";

export interface Copy {
  brand: string;
  nav: { features: string; how: string; pricing: string; faq: string; signIn: string; start: string; lang: string; menuLabel: string };
  hero: { badge: string; title: string; subtitle: string; ctaPrimary: string; ctaSecondary: string; note: string };
  features: { title: string; intro: string; items: { title: string; desc: string }[] };
  how: { title: string; steps: { title: string; desc: string }[] };
  faq: { title: string; items: { q: string; a: string }[] };
  cta: { title: string; desc: string; button: string };
  footer: { product: string; legal: string; privacy: string; terms: string; refund: string; deletion: string; pricing: string; rights: string; draftNote: string };
  pricing: {
    metaTitle: string; metaDescription: string; title: string; subtitle: string; monthly: string; yearly: string; perMonth: string; perYear: string; plusVat: string; totalWithVat: string;
    save: string; contact: string; contactCta: string; startTrial: string; trialNote: string; limitsTitle: string; topupsTitle: string; topupsDesc: string; replies: string; validity: string;
    addonsTitle: string; addonsDesc: string; seatAddon: string; numberAddon: string; unavailable: string; notesTitle: string; notes: string[]; refundLink: string;
    features: { replies: (n: string) => string; seats: (n: number) => string; numbers: (n: number) => string; properties: (n: string) => string; contacts: (n: string) => string; campaigns: string; noCampaigns: string; custom: string[] };
    names: Record<string, string>; blurbs: Record<string, string>;
  };
  refund: { metaTitle: string; title: string; draft: string; sections: { h: string; p: string[] }[] };
  deletion: { metaTitle: string; title: string; draft: string; sections: { h: string; p: string[] }[]; statusTitle: string; statusLabel: string; statusButton: string; backToInstructions: string };
  status: { title: string; codeLabel: string; check: string; checking: string; invalid: string; notFound: string; error: string; requestedAt: string; completedAt: string; states: Record<string, string> };
  privacy: { metaTitle: string; title: string; sections: { h: string; p: string[] }[] };
  terms: { metaTitle: string; title: string; sections: { h: string; p: string[] }[] };
  legalDraft: string;
}

const ar: Copy = {
  brand: "OmniFlow",
  nav: { features: "المزايا", how: "كيف يعمل", pricing: "الأسعار", faq: "أسئلة شائعة", signIn: "تسجيل الدخول", start: "ابدأ تجربة مجانية", lang: "English", menuLabel: "القائمة الرئيسية" },
  hero: {
    badge: "لمكاتب العقارات في المملكة",
    title: "ردّ فوري على عملائك على واتساب وإنستجرام، وصندوق واحد لكل المحادثات",
    subtitle: "مساعد ذكي يجيب عملاءك اعتمادًا على عقاراتك ومعلومات شركتك، ويحوّل المحادثة إلى موظف عند الحاجة، مع إدارة العملاء والحملات والتقارير في مكان واحد.",
    ctaPrimary: "ابدأ تجربة مجانية 14 يومًا",
    ctaSecondary: "شاهد الأسعار",
    note: "بدون بطاقة بنكية للتجربة. تُدفع رسوم رسائل واتساب لشركة Meta مباشرة.",
  },
  features: {
    title: "ما الذي تحصل عليه",
    intro: "كل ما يلزم لإدارة المحادثات العقارية اليومية، مبنيّ على الأنظمة المعمول بها في المملكة.",
    items: [
      { title: "صندوق وارد موحّد", desc: "محادثات واتساب وإنستجرام وماسنجر في شاشة واحدة، مع إسناد المحادثات للموظفين وملاحظات داخلية وحالات واضحة." },
      { title: "مساعد يعرف عقاراتك", desc: "يجيب من قائمة عقاراتك ومعلومات شركتك التي ترفعها أنت، ويمكنك ضبط النبرة واللهجة وساعات العمل وقواعد التحويل لموظف." },
      { title: "استيراد العقارات بسهولة", desc: "ارفع ملف Excel أو CSV، راجع الأعمدة والأخطاء قبل الاستيراد، وتتم فهرسة العقارات تلقائيًا ليستفيد منها المساعد." },
      { title: "إدارة العملاء والموافقات", desc: "ملف لكل عميل مع سجل المحادثات والمواعيد، وتسجيل موافقة التواصل التسويقي وفق نظام حماية البيانات الشخصية (PDPL)." },
      { title: "حملات واتساب بقوالب معتمدة", desc: "حملات تستهدف العملاء الموافقين فقط، باستخدام قوالب Meta المعتمدة، مع متابعة التسليم." },
      { title: "تحليلات وتقارير", desc: "لوحة مؤشرات لحجم المحادثات وسرعة الاستجابة والعملاء المهتمين، وتقارير قابلة للتصدير." },
      { title: "فريق وصلاحيات", desc: "أدوار للمدير والموظف والمدقّق، ودعوة الزملاء بالبريد، وسجل تدقيق للإجراءات الحساسة." },
      { title: "فوترة واضحة بالريال", desc: "أسعار شهرية وسنوية قبل الضريبة مع إظهار ضريبة القيمة المضافة، وفواتير PDF قابلة للتنزيل." },
    ],
  },
  how: {
    title: "من التسجيل إلى أول رد",
    steps: [
      { title: "سجّل وابدأ التجربة", desc: "أنشئ حسابك وتبدأ تجربة مجانية لمدة 14 يومًا دون بطاقة." },
      { title: "أضف معلومات شركتك وعقاراتك", desc: "اكتب نبذة عن شركتك وسياساتها، وارفع عقاراتك من ملف أو أضفها يدويًا." },
      { title: "اربط قناتك", desc: "اربط واتساب للأعمال أو صفحة فيسبوك/إنستجرام من الإعدادات واتبع الخطوات الإرشادية." },
      { title: "جرّب المساعد ثم شغّله", desc: "اختبر ردود المساعد من شاشة «اختبار البوت»، ثم اترك له الرد وتدخّل أنت متى شئت." },
    ],
  },
  faq: {
    title: "أسئلة شائعة",
    items: [
      { q: "هل أحتاج بطاقة بنكية للتجربة المجانية؟", a: "لا. مدة التجربة 14 يومًا بحدود محدودة (300 رد ذكاء اصطناعي، قناة واحدة، مقعدان، 50 عقارًا، بدون حملات). بعد انتهائها يتحول الحساب للقراءة فقط وتبقى بياناتك محفوظة 30 يومًا." },
      { q: "من يدفع رسوم رسائل واتساب؟", a: "رسوم المحادثات والقوالب يدفعها العميل لشركة Meta مباشرة حسب تسعيرها، ولا تدخل في سعر الباقة." },
      { q: "هل يمكن للمساعد أن يخطئ؟", a: "نعم، ردود الذكاء الاصطناعي تُولَّد آليًا. يمكنك ضبط القواعد، وتحويل المحادثات الحساسة لموظف، ومراجعة كل المحادثات من الصندوق." },
      { q: "كيف تُحمى بيانات عملائي؟", a: "تُعزل بيانات كل منشأة عن غيرها على مستوى قاعدة البيانات، وتُسجَّل الإجراءات الحساسة في سجل تدقيق، ولديك أدوات تصدير ومحو لبيانات العملاء. راجع سياسة الخصوصية." },
      { q: "كيف أحذف بياناتي؟", a: "من الإعدادات ← الخصوصية يمكن تصدير بيانات عميل أو محو هويته، ويمكن طلب حذف بيانات الربط مع Meta من خلال صفحة تعليمات حذف البيانات." },
      { q: "هل أستطيع استرداد المبلغ؟", a: "نعم خلال 14 يومًا من أول دفعة وفق سياسة الاسترداد (مسودة بانتظار المراجعة القانونية)." },
    ],
  },
  cta: { title: "جرّب OmniFlow على محادثاتك الحقيقية", desc: "14 يومًا مجانًا بدون بطاقة. ألغِ في أي وقت.", button: "ابدأ الآن" },
  footer: { product: "المنتج", legal: "قانوني", privacy: "سياسة الخصوصية", terms: "الشروط والأحكام", refund: "سياسة الاسترداد", deletion: "تعليمات حذف البيانات", pricing: "الأسعار", rights: "جميع الحقوق محفوظة.", draftNote: "الصفحات القانونية مسودات بانتظار مراجعة محامٍ." },
  pricing: {
    metaTitle: "الأسعار", metaDescription: "باقات OmniFlow الشهرية والسنوية بالريال السعودي، قبل ضريبة القيمة المضافة 15%.",
    title: "أسعار بسيطة وواضحة", subtitle: "كل الأسعار بالريال السعودي قبل ضريبة القيمة المضافة (15%) التي تُضاف عند الدفع. الاشتراك السنوي يعادل 10 أشهر.",
    monthly: "شهري", yearly: "سنوي", perMonth: "/ شهر", perYear: "/ سنة", plusVat: "+ ضريبة", totalWithVat: "الإجمالي شاملًا الضريبة", save: "وفّر",
    contact: "تواصل معنا", contactCta: "تواصل معنا", startTrial: "ابدأ التجربة المجانية", trialNote: "14 يومًا مجانًا بدون بطاقة",
    limitsTitle: "ما تشمله كل باقة", topupsTitle: "باقات ردود إضافية", topupsDesc: "تُستهلك بعد حصة باقتك الشهرية، وصلاحيتها 12 شهرًا. حصة الباقة الشهرية لا تُرحَّل للشهر التالي.", replies: "رد", validity: "صلاحية 12 شهرًا",
    addonsTitle: "إضافات", addonsDesc: "تُضاف للباقة وتُحتسب شهريًا.", seatAddon: "مقعد إضافي", numberAddon: "رقم واتساب إضافي", unavailable: "قريبًا",
    notesTitle: "ملاحظات مهمة", notes: [
      "جميع الأسعار قبل ضريبة القيمة المضافة (15%)؛ تُضاف الضريبة وتظهر في الفاتورة.",
      "رسوم محادثات واتساب يدفعها العميل لشركة Meta مباشرة وليست جزءًا من سعر الباقة.",
      "الذكاء الاصطناعي: عند استهلاك حصة الردود الشهرية يتحول المساعد افتراضيًا إلى موظف، ويمكنك شراء باقة ردود إضافية أو الترقية.",
    ],
    refundLink: "سياسة الاسترداد",
    features: {
      replies: (n) => `${n} رد ذكاء اصطناعي شهريًا`, seats: (n) => `${n} ${n === 1 ? "مقعد" : "مقاعد"}`, numbers: (n) => `${n} ${n === 1 ? "رقم واتساب" : "أرقام واتساب"}`,
      properties: (n) => `${n} عقار`, contacts: (n) => `${n} جهة اتصال`, campaigns: "حملات تسويقية (بموافقة العملاء)", noCampaigns: "بدون الحملات التسويقية",
      custom: ["حدود مخصّصة حسب احتياجك", "عرض سعر خاص", "تواصل معنا لمناقشة احتياجك"],
    },
    names: { starter: "الأساسية", pro: "الاحترافية", business: "الأعمال", enterprise: "المؤسسات" },
    blurbs: { starter: "للمكاتب الصغيرة", pro: "للمكاتب النشطة", business: "للشركات المتوسطة", enterprise: "للمؤسسات الكبيرة" },
  },
  refund: {
    metaTitle: "سياسة الاسترداد", title: "سياسة الاسترداد", draft: "مسودة بانتظار مراجعة محامٍ — لا تُعدّ نصًا قانونيًا نهائيًا.",
    sections: [
      { h: "ضمان استرداد لمدة 14 يومًا", p: ["يحق لك طلب استرداد أول دفعة اشتراك خلال 14 يومًا من تاريخ الدفع."] },
      { h: "ما الذي يُسترد", p: ["يُسترد المبلغ المدفوع بعد خصم قيمة الاستهلاك الفعلي للخدمة خلال الفترة (تكلفة الردود الآلية المنفّذة) ورسوم بوابة الدفع. تُحسب ضريبة القيمة المضافة على المبلغ المسترد."] },
      { h: "ما لا يشمله الضمان", p: ["التجديدات والترقيات والإضافات وباقات الردود الإضافية، وكل دفعة بعد الأولى. [تُراجع الجهة القانونية الاستثناءات.]"] },
      { h: "كيف تطلب الاسترداد", p: ["افتح تذكرة من «الدعم» داخل المنصة خلال المهلة. تُراجع فرقنا الطلب وتنفّذ الاسترداد عبر بوابة الدفع نفسها، وتصدر لك إشعارًا دائنًا بالمبلغ. [مدة وصول المبلغ تُحدَّد بعد مراجعة بوابة الدفع.]"] },
      { h: "بعد الاسترداد الكامل", p: ["ينتهي اشتراكك فورًا وتتحول الخدمة إلى وضع القراءة ثم تُطبّق مدد الاحتفاظ بالبيانات."] },
    ],
  },
  deletion: {
    metaTitle: "تعليمات حذف بيانات المستخدم", title: "تعليمات حذف بيانات المستخدم", draft: "مسودة بانتظار مراجعة محامٍ.",
    sections: [
      { h: "إذا ربطت حسابك في فيسبوك أو إنستجرام بـ OmniFlow", p: [
        "1) افتح إعدادات حسابك في فيسبوك ← «التطبيقات والمواقع».",
        "2) اختر OmniFlow ثم «إزالة» وفعّل خيار حذف بياناتك إن ظهر.",
        "3) سترسل Meta لنا طلب حذف تلقائيًا، فنحذف رموز الربط ومعرّفات الصفحة والحساب المرتبطة بك وتُسجَّل العملية.",
        "4) تحصل على رمز تأكيد ورابط لمتابعة الحالة (أو أدخل الرمز في الأسفل).",
      ] },
      { h: "بيانات عملاء منشأتك", p: ["مدير الحساب يستطيع من «الإعدادات ← الخصوصية والامتثال» تصدير بيانات أي عميل أو محو هويته. تُسجَّل هذه الإجراءات في سجل التدقيق."] },
      { h: "طلب مباشر", p: ["افتح تذكرة من «الدعم» داخل المنصة، أو [بيانات التواصل مع مسؤول حماية البيانات — تُستكمل قبل الإطلاق]."] },
      { h: "ما الذي نحذفه وما نحتفظ به", p: ["نحذف ما يرتبط بالمستخدم من رموز وصول ومعرّفات ربط. قد نحتفظ بسجلات مالية وضريبية يوجب النظام حفظها. [المدد التفصيلية تُحدَّد من الجهة القانونية.]"] },
    ],
    statusTitle: "متابعة حالة طلب", statusLabel: "رمز التأكيد", statusButton: "تحقق من الحالة", backToInstructions: "عودة للتعليمات",
  },
  status: {
    title: "حالة طلب حذف البيانات", codeLabel: "رمز التأكيد", check: "تحقق", checking: "جارٍ التحقق…", invalid: "أدخل رمز التأكيد الذي استلمته.", notFound: "لا يوجد طلب بهذا الرمز.", error: "تعذّر التحقق الآن، حاول لاحقًا.",
    requestedAt: "تاريخ الطلب", completedAt: "تاريخ الاكتمال",
    states: { received: "تم استلام الطلب وجارٍ التنفيذ.", completed: "اكتمل الحذف: أُزيلت بيانات الربط المرتبطة بحسابك.", no_data: "اكتمل الطلب: لا توجد لدينا بيانات مرتبطة بهذا الحساب." },
  },
  privacy: {
    metaTitle: "سياسة الخصوصية", title: "سياسة الخصوصية",
    sections: [
      { h: "مقدمة", p: ["توضح هذه السياسة كيف تتعامل منصة OmniFlow مع البيانات الشخصية وفق نظام حماية البيانات الشخصية في المملكة العربية السعودية (PDPL)."] },
      { h: "البيانات التي نعالجها", p: ["بيانات حساب المنشأة وموظفيها، وبيانات عملاء المنشأة التي ترد عبر قنوات التواصل (الاسم، رقم الجوال، محتوى المحادثات)، وبيانات الفوترة. لا نحفظ بيانات البطاقات المصرفية؛ تُعالَج لدى مزوّد الدفع."] },
      { h: "الأدوار", p: ["المنشأة العميلة هي «المتحكم» في بيانات عملائها، وOmniFlow «معالِج» يعمل بتعليماتها."] },
      { h: "حقوق أصحاب البيانات", p: ["يستطيع مدير الحساب من الإعدادات ← الخصوصية والامتثال الاطلاع على موافقات العميل وتصدير بياناته ومحو هويته. تُسجَّل هذه الإجراءات في سجل التدقيق. لحذف بيانات الربط مع Meta راجع صفحة تعليمات حذف البيانات."] },
      { h: "الاحتفاظ بالبيانات", p: ["[يُحدَّد من الجهة القانونية: مدد الاحتفاظ لكل فئة من البيانات.]"] },
      { h: "التواصل", p: ["[بيانات التواصل مع مسؤول حماية البيانات — تُستكمل قبل الإطلاق.]"] },
    ],
  },
  terms: {
    metaTitle: "الشروط والأحكام", title: "الشروط والأحكام",
    sections: [
      { h: "مقدمة", p: ["تحكم هذه الشروط استخدام منصة OmniFlow من قبل المنشآت العقارية."] },
      { h: "الاشتراك والدفع", p: ["تُحتسب الاشتراكات شهريًا أو سنويًا بالريال السعودي مضافًا إليها ضريبة القيمة المضافة. [تفاصيل التجديد والإلغاء تُستكمل من الجهة المختصة.] راجع أيضًا سياسة الاسترداد."] },
      { h: "الاستخدام المقبول", p: ["يلتزم العميل بالحصول على موافقة عملائه قبل الرسائل التسويقية، وبعدم استخدام المنصة في أي نشاط مخالف للأنظمة."] },
      { h: "الذكاء الاصطناعي", p: ["تُولَّد ردود المساعد الذكي آليًا وقد تحتوي أخطاء؛ المنشأة مسؤولة عن مراجعة المعلومات الجوهرية مع عملائها."] },
      { h: "إيقاف الخدمة", p: ["قد تُوقف الخدمة عند عدم سداد الاشتراك بعد فترة سماح، وتبقى بيانات المنشأة متاحة للاطلاع والتصدير."] },
      { h: "المسؤولية والقانون الواجب التطبيق", p: ["[تُستكمل من الجهة القانونية.]"] },
    ],
  },
  legalDraft: "نص مؤقت: هذه الصفحة مسودة بانتظار مراجعة محامٍ، ويجب استبدالها بالنص المعتمد قبل الإطلاق التجاري.",
};

const en: Copy = {
  brand: "OmniFlow",
  nav: { features: "Features", how: "How it works", pricing: "Pricing", faq: "FAQ", signIn: "Sign in", start: "Start free trial", lang: "العربية", menuLabel: "Main navigation" },
  hero: {
    badge: "For real-estate offices in Saudi Arabia",
    title: "Instant replies to your customers on WhatsApp and Instagram — one inbox for every conversation",
    subtitle: "An AI assistant that answers from your own listings and company information, hands the chat to a person when needed, and keeps customers, campaigns and reports in one place.",
    ctaPrimary: "Start a 14-day free trial", ctaSecondary: "See pricing",
    note: "No card needed for the trial. WhatsApp conversation fees are paid to Meta directly.",
  },
  features: {
    title: "What you get",
    intro: "Everything needed to run day-to-day real-estate conversations, built around Saudi regulations.",
    items: [
      { title: "One shared inbox", desc: "WhatsApp, Instagram and Messenger chats on one screen, with assignment to team members, internal notes and clear statuses." },
      { title: "An assistant that knows your listings", desc: "It answers from the listings and company information you upload; you control tone, dialect, working hours and the rules for handing over to a person." },
      { title: "Easy property import", desc: "Upload an Excel or CSV file, review the column mapping and errors before importing; listings are indexed automatically for the assistant." },
      { title: "Customers and consent", desc: "A profile per customer with conversations and appointments, and marketing-consent records aligned with the Personal Data Protection Law (PDPL)." },
      { title: "WhatsApp campaigns with approved templates", desc: "Campaigns reach opted-in customers only, using Meta-approved templates, with delivery tracking." },
      { title: "Analytics and reports", desc: "A dashboard for conversation volume, response speed and interested customers, plus exportable reports." },
      { title: "Team and permissions", desc: "Admin, agent and auditor roles, e-mail invitations, and an audit log for sensitive actions." },
      { title: "Clear billing in riyals", desc: "Monthly and yearly prices before VAT with VAT shown separately, and downloadable PDF invoices." },
    ],
  },
  how: {
    title: "From sign-up to the first reply",
    steps: [
      { title: "Sign up and start the trial", desc: "Create your account and start a 14-day free trial, no card." },
      { title: "Add your company and listings", desc: "Describe your company and its policies, and upload listings from a file or add them by hand." },
      { title: "Connect your channel", desc: "Connect WhatsApp Business or your Facebook / Instagram page from Settings and follow the guided steps." },
      { title: "Test, then switch it on", desc: "Try the assistant in the \"Test the bot\" screen, then let it answer and step in whenever you like." },
    ],
  },
  faq: {
    title: "Frequently asked questions",
    items: [
      { q: "Do I need a card for the free trial?", a: "No. The trial lasts 14 days with limited caps (300 AI replies, one channel, two seats, 50 listings, no campaigns). When it ends the account becomes read-only and your data is kept for 30 days." },
      { q: "Who pays WhatsApp message fees?", a: "Conversation and template fees are paid by you to Meta directly at Meta's pricing; they are not part of the plan price." },
      { q: "Can the assistant be wrong?", a: "Yes, AI replies are generated automatically. You can set rules, hand sensitive chats to a person, and review every conversation in the inbox." },
      { q: "How is my customers' data protected?", a: "Each company's data is isolated from the others at database level, sensitive actions are recorded in an audit log, and you have export and erasure tools for customer data. See the privacy policy." },
      { q: "How do I delete my data?", a: "In Settings → Privacy you can export a customer's data or erase their identity, and you can request deletion of your Meta connection data from the data-deletion instructions page." },
      { q: "Can I get a refund?", a: "Yes, within 14 days of your first payment under the refund policy (a draft awaiting legal review)." },
    ],
  },
  cta: { title: "Try OmniFlow on your real conversations", desc: "14 days free, no card. Cancel any time.", button: "Get started" },
  footer: { product: "Product", legal: "Legal", privacy: "Privacy policy", terms: "Terms & conditions", refund: "Refund policy", deletion: "Data deletion instructions", pricing: "Pricing", rights: "All rights reserved.", draftNote: "Legal pages are drafts awaiting lawyer review." },
  pricing: {
    metaTitle: "Pricing", metaDescription: "OmniFlow monthly and yearly plans in Saudi riyals, before 15% VAT.",
    title: "Simple, transparent pricing", subtitle: "All prices are in Saudi riyals before 15% VAT, which is added at checkout. Yearly billing equals 10 months.",
    monthly: "Monthly", yearly: "Yearly", perMonth: "/ month", perYear: "/ year", plusVat: "+ VAT", totalWithVat: "Total including VAT", save: "Save",
    contact: "Contact us", contactCta: "Contact us", startTrial: "Start free trial", trialNote: "14 days free, no card",
    limitsTitle: "What each plan includes", topupsTitle: "Extra reply packs", topupsDesc: "Used after your monthly plan quota; valid for 12 months. The monthly plan quota does not roll over.", replies: "replies", validity: "valid 12 months",
    addonsTitle: "Add-ons", addonsDesc: "Added to your plan and billed monthly.", seatAddon: "Extra seat", numberAddon: "Extra WhatsApp number", unavailable: "Coming soon",
    notesTitle: "Important notes", notes: [
      "All prices are before 15% VAT; VAT is added and shown on the invoice.",
      "WhatsApp conversation fees are paid by you to Meta directly and are not part of the plan price.",
      "AI: when the monthly reply quota is used up the assistant hands over to a person by default; you can buy an extra reply pack or upgrade.",
    ],
    refundLink: "Refund policy",
    features: {
      replies: (n) => `${n} AI replies per month`, seats: (n) => `${n} ${n === 1 ? "seat" : "seats"}`, numbers: (n) => `${n} WhatsApp ${n === 1 ? "number" : "numbers"}`,
      properties: (n) => `${n} listings`, contacts: (n) => `${n} contacts`, campaigns: "Marketing campaigns (opted-in customers)", noCampaigns: "No marketing campaigns",
      custom: ["Limits tailored to you", "Custom quote", "Contact us to discuss your needs"],
    },
    names: { starter: "Starter", pro: "Pro", business: "Business", enterprise: "Enterprise" },
    blurbs: { starter: "For small offices", pro: "For active offices", business: "For mid-sized companies", enterprise: "For large organisations" },
  },
  refund: {
    metaTitle: "Refund policy", title: "Refund policy", draft: "Draft awaiting lawyer review — not a final legal text.",
    sections: [
      { h: "14-day money-back guarantee", p: ["You may request a refund of your first subscription payment within 14 days of the payment date."] },
      { h: "What is refunded", p: ["The amount paid minus the actual consumption of the service during the period (the cost of automated replies already delivered) and the payment-gateway fees. VAT is recalculated on the refunded amount."] },
      { h: "What is not covered", p: ["Renewals, upgrades, add-ons, extra reply packs and every payment after the first. [Exceptions to be reviewed by the legal team.]"] },
      { h: "How to request a refund", p: ["Open a ticket from \"Support\" inside the platform within the window. Our team reviews it and refunds through the same payment gateway, and you receive a credit note for the amount. [Time for the money to arrive to be set after checking the gateway.]"] },
      { h: "After a full refund", p: ["Your subscription ends immediately, the service becomes read-only and the data-retention periods then apply."] },
    ],
  },
  deletion: {
    metaTitle: "User data deletion instructions", title: "User data deletion instructions", draft: "Draft awaiting lawyer review.",
    sections: [
      { h: "If you connected your Facebook or Instagram account to OmniFlow", p: [
        "1) Open your Facebook account settings → \"Apps and Websites\".",
        "2) Select OmniFlow, choose \"Remove\" and tick the option to delete your data if shown.",
        "3) Meta automatically sends us a deletion request; we delete the access tokens and Page / account identifiers tied to you and record the action.",
        "4) You receive a confirmation code and a link to follow the status (or enter the code below).",
      ] },
      { h: "Your company's customer data", p: ["A workspace admin can, under Settings → Privacy & compliance, export any customer's data or erase their identity. These actions are recorded in the audit log."] },
      { h: "Direct request", p: ["Open a ticket from \"Support\" inside the platform, or [contact details of the data-protection officer — to be completed before launch]."] },
      { h: "What we delete and what we keep", p: ["We delete access tokens and connection identifiers tied to the user. We may keep financial and tax records that the law requires. [Detailed retention periods to be set by the legal team.]"] },
    ],
    statusTitle: "Check the status of a request", statusLabel: "Confirmation code", statusButton: "Check status", backToInstructions: "Back to instructions",
  },
  status: {
    title: "Data deletion request status", codeLabel: "Confirmation code", check: "Check", checking: "Checking…", invalid: "Enter the confirmation code you received.", notFound: "No request found for this code.", error: "Could not check right now, please try again later.",
    requestedAt: "Requested on", completedAt: "Completed on",
    states: { received: "Request received and being processed.", completed: "Deletion completed: the connection data tied to your account was removed.", no_data: "Request completed: we hold no data tied to this account." },
  },
  privacy: {
    metaTitle: "Privacy policy", title: "Privacy policy",
    sections: [
      { h: "Introduction", p: ["This policy explains how the OmniFlow platform handles personal data under the Saudi Personal Data Protection Law (PDPL)."] },
      { h: "Data we process", p: ["Company account and employee data, the company's customer data received through messaging channels (name, phone number, conversation content), and billing data. We do not store bank-card data; it is handled by the payment provider."] },
      { h: "Roles", p: ["The customer company is the \"controller\" of its customers' data and OmniFlow is a \"processor\" acting on its instructions."] },
      { h: "Data-subject rights", p: ["An account admin can, under Settings → Privacy & compliance, view a customer's consents, export their data and erase their identity. These actions are recorded in the audit log. To delete Meta connection data see the data-deletion instructions page."] },
      { h: "Retention", p: ["[To be set by the legal team: retention periods per data category.]"] },
      { h: "Contact", p: ["[Contact details of the data-protection officer — to be completed before launch.]"] },
    ],
  },
  terms: {
    metaTitle: "Terms & conditions", title: "Terms & conditions",
    sections: [
      { h: "Introduction", p: ["These terms govern the use of the OmniFlow platform by real-estate companies."] },
      { h: "Subscription and payment", p: ["Subscriptions are billed monthly or yearly in Saudi riyals plus VAT. [Renewal and cancellation details to be completed by the competent team.] See also the refund policy."] },
      { h: "Acceptable use", p: ["The customer must obtain its customers' consent before marketing messages and must not use the platform for any activity that breaches the law."] },
      { h: "Artificial intelligence", p: ["The AI assistant's replies are generated automatically and may contain errors; the company is responsible for reviewing material information with its customers."] },
      { h: "Suspension", p: ["The service may be suspended if the subscription is unpaid after a grace period; the company's data stays available to view and export."] },
      { h: "Liability and governing law", p: ["[To be completed by the legal team.]"] },
    ],
  },
  legalDraft: "Placeholder text: this page is a draft awaiting lawyer review and must be replaced with the approved text before commercial launch.",
};

export const COPY: Record<PublicLocale, Copy> = { ar, en };
export function copyFor(locale: PublicLocale): Copy { return COPY[locale]; }
