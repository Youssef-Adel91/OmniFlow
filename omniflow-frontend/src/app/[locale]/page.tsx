/**
 * app/[locale]/page.tsx — OmniFlow AI Landing Page
 *
 * Sections:
 *  1. Hero — High-impact Arabic headline + dual CTAs
 *  2. How It Works — 4-step pipeline (WhatsApp → Vector DB → VCard → Broadcast)
 *  3. Pricing — 3 tiers from SRS §1.2.2 (Economic / Professional / Enterprise)
 *  4. Prerequisites Checklist — Trust-building onboarding requirements
 *  5. Footer
 *
 * Design: Luxury Dark Navy #0a1128 × Royal Gold #C9A84C — RTL Arabic-first
 */

import type { Metadata } from "next";
import Link from "next/link";
import {
  MessageSquare,
  Mic,
  Building2,
  ShieldCheck,
  Megaphone,
  CheckCircle2,
  ArrowLeft,
  Zap,
  Star,
  Globe,
  Layers,
  Phone,
  FileText,
  List,
  ChevronLeft,
  Sparkles,
  Lock,
  Cpu,
  Users,
} from "lucide-react";

// ── Metadata ──────────────────────────────────────────────────────────────────

export const metadata: Metadata = {
  title: "OmniFlow AI — مساعد المبيعات العقاري الذكي",
  description:
    "أغلق صفقاتك العقارية 24/7 مع أول مساعد مبيعات ذكي يرد بالصوت والنص عبر واتساب. الخطة المناسبة لكل حجم وكالة.",
  openGraph: {
    title: "OmniFlow AI — مساعد المبيعات العقاري الذكي",
    description: "ربط واتساب، فلترة العملاء، وإعادة الاستهداف — كل ذلك بالذكاء الاصطناعي.",
    locale: "ar_SA",
    type: "website",
  },
};

// ── Constants ─────────────────────────────────────────────────────────────────

const STEPS = [
  {
    step: "01",
    icon: MessageSquare,
    title: "الربط الشامل (Omnichannel)",
    desc: "اربط حسابات Meta Business (واتساب، إنستقرام)، وتيك توك، وإكس بنقرة واحدة. نجمع كل عملائك في مسار بيعي واحد مدعوم بالذكاء الاصطناعي.",
    badge: "Omnichannel Sync",
    badgeColor: "text-blue-400 bg-blue-400/10 border-blue-400/30",
    glow: "group-hover:shadow-blue-500/20",
  },
  {
    step: "02",
    icon: Building2,
    title: "رفع العقارات",
    desc: "ارفع قائمة عقاراتك لتُفهرَس تلقائياً في قاعدة Vector DB — يجد المساعد أقرب عقار لأي طلب عميل في ثوانٍ.",
    badge: "Vector DB Sync",
    badgeColor: "text-[#C9A84C] bg-[#C9A84C]/10 border-[#C9A84C]/30",
    glow: "group-hover:shadow-gold-500/20",
  },
  {
    step: "03",
    icon: ShieldCheck,
    title: "فلترة العملاء ذكياً",
    desc: "حارس VCard يتحقق من هوية كل عميل ويجمع بياناته تلقائياً قبل أن يصلك — لا تضيع وقتك في عملاء غير جادين.",
    badge: "VCard Gatekeeper",
    badgeColor: "text-emerald-400 bg-emerald-400/10 border-emerald-400/30",
    glow: "group-hover:shadow-emerald-500/20",
  },
  {
    step: "04",
    icon: Megaphone,
    title: "إعادة الاستهداف",
    desc: "محرك VIP Broadcast يُرسل عروض مخصصة للعملاء المهتمين — مثالي للعقارات خارج السوق والإيجارات اليومية.",
    badge: "VIP Broadcast",
    badgeColor: "text-purple-400 bg-purple-400/10 border-purple-400/30",
    glow: "group-hover:shadow-purple-500/20",
  },
];

const PRICING = [
  {
    id: "economic",
    tier: "الاقتصادية",
    tierEn: "Economic",
    persona: "المستشار أحمد الصائغ الرقمي",
    tagline: "مساعد جاهز — شخصية ثابتة، انطلق فوراً",
    price: "١٩٩",
    currency: "ر.س",
    period: "/ شهرياً",
    highlight: false,
    badge: null,
    features: [
      "شخصية مساعد ثابتة (أحمد الصائغ)",
      "ردود نصية وصوتية عبر واتساب",
      "فهرسة حتى ٥٠٠ عقار",
      "VCard Gatekeeper — جمع البيانات",
      "لوحة تحكم أساسية",
      "دعم فني خلال ٢٤ ساعة",
    ],
    cta: "ابدأ مجاناً ١٤ يوم",
    ctaStyle:
      "border border-gold-500/50 text-gold-400 hover:bg-gold-500/10 hover:border-gold-400",
    icon: Zap,
    ideal: "للوسطاء والأفراد",
    cardBg: "bg-navy-900/60",
    borderStyle: "border border-white/5 hover:border-gold-500/30",
  },
  {
    id: "professional",
    tier: "الاحترافية",
    tierEn: "Professional",
    persona: "هوية علامتك التجارية الخاصة",
    tagline: "هوية مخصصة ومحرك استدلال متقدم",
    price: "٧٩٩",
    currency: "ر.س",
    period: "/ شهرياً",
    highlight: true,
    badge: "الأكثر طلباً",
    features: [
      "هوية علامة تجارية مخصصة بالكامل",
      "محرك الاستدلال المتقدم (Reasoning Engine)",
      "فهرسة حتى ٥٠٠٠ عقار",
      "VCard Gatekeeper + تسجيل العملاء",
      "VIP Broadcast — حتى ١٠٠٠ رسالة/يوم",
      "تقارير وتحليلات متقدمة",
      "دعم فني ذو أولوية ٨ ساعات",
    ],
    cta: "ابدأ مجاناً ١٤ يوم",
    ctaStyle: "bg-gradient-to-l from-gold-600 to-gold-400 text-navy-900 font-bold hover:brightness-110",
    icon: Star,
    ideal: "للوكالات متوسطة الحجم",
    cardBg: "bg-gradient-to-b from-[#1a0e00]/40 to-navy-900/80",
    borderStyle: "border-2 border-gold-500/60 hover:border-gold-400",
  },
  {
    id: "enterprise",
    tier: "المؤسسية",
    tierEn: "Enterprise",
    persona: "وايت ليبل ١٠٠٪ — ملكيتك الكاملة",
    tagline: "بنية تحتية مخصصة وSLA مضمون",
    price: "تسعير مخصص",
    currency: "",
    period: "",
    highlight: false,
    badge: "White Label",
    features: [
      "وايت ليبل ١٠٠٪ (اسمك، دومينك، سيرفرك)",
      "RAG Pipelines مخصصة لمخزونك",
      "تكامل مع أنظمة CRM الخارجية",
      "VIP Broadcast غير محدود",
      "SLA ٩٩.٩٩٪ مع Failover تلقائي",
      "مدير حساب مخصص",
      "دعم فني ٢٤/٧ عبر واتساب المباشر",
    ],
    cta: "تحدث مع فريق المبيعات",
    ctaStyle:
      "border border-white/20 text-white hover:bg-white/5 hover:border-white/40",
    icon: Globe,
    ideal: "للشركات الكبرى والمطورين",
    cardBg: "bg-navy-900/40",
    borderStyle: "border border-white/10 hover:border-white/25",
  },
];

const PREREQUISITES = [
  {
    icon: Phone,
    title: "رقم واتساب أعمال غير مربوط",
    desc: "رقم هاتف جديد أو مخصص للأعمال — لم يُستخدم مسبقاً مع WhatsApp Business API.",
  },
  {
    icon: Users,
    title: "حسابات تواصل اجتماعي موثقة",
    desc: "حسابات تواصل اجتماعي موثقة (Instagram, TikTok, X) لربطها بالمنظومة.",
  },
  {
    icon: FileText,
    title: "سجل تجاري ساري",
    desc: "لازم لإتمام توثيق Meta Business Verification وضمان استمرارية الاتصال.",
  },
  {
    icon: List,
    title: "قائمة عقاراتك الحالية",
    desc: "ملف Excel أو CSV أو رابط نظامك العقاري — نفهرسها في Vector DB في أقل من ساعة.",
  },
];

const STATS = [
  { value: "٩٨٪", label: "معدل الرد الفوري" },
  { value: "+٤٠٪", label: "زيادة في التحويلات" },
  { value: "٢٤/٧", label: "متاح دائماً" },
  { value: "<٣ث", label: "متوسط وقت الرد" },
];

// ── Page Component ─────────────────────────────────────────────────────────────

export default function LandingPage() {
  return (
    <div
      dir="rtl"
      className="min-h-screen bg-[#0a1128] text-white font-arabic antialiased overflow-x-hidden"
      style={{ fontFamily: "'Noto Kufi Arabic', 'Cairo', system-ui, sans-serif" }}
    >
      {/* ────────────────────────── GLOBAL STYLES ──────────────────────────── */}
      <style dangerouslySetInnerHTML={{ __html: `
        @import url('https://fonts.googleapis.com/css2?family=Noto+Kufi+Arabic:wght@300;400;500;600;700;800;900&display=swap');
        
        .gold-text {
          background: linear-gradient(135deg, #C9A84C 0%, #f0d070 40%, #C9A84C 80%, #a07830 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          background-clip: text;
        }
        .gold-shimmer {
          background: linear-gradient(90deg, #C9A84C 0%, #f0d070 50%, #C9A84C 100%);
          background-size: 200% 100%;
          animation: shimmer 3s ease-in-out infinite;
        }
        @keyframes shimmer {
          0%   { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
        @keyframes float {
          0%, 100% { transform: translateY(0px); }
          50%       { transform: translateY(-10px); }
        }
        @keyframes pulse-ring {
          0% { transform: scale(0.8); opacity: 1; }
          100% { transform: scale(2); opacity: 0; }
        }
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(24px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .float { animation: float 4s ease-in-out infinite; }
        .fade-up { animation: fadeUp 0.7s ease both; }
        .fade-up-delay-1 { animation: fadeUp 0.7s ease 0.15s both; }
        .fade-up-delay-2 { animation: fadeUp 0.7s ease 0.30s both; }
        .fade-up-delay-3 { animation: fadeUp 0.7s ease 0.45s both; }
        
        .hero-grid {
          background-image:
            linear-gradient(rgba(201,168,76,0.04) 1px, transparent 1px),
            linear-gradient(90deg, rgba(201,168,76,0.04) 1px, transparent 1px);
          background-size: 60px 60px;
        }
        .card-glow {
          transition: box-shadow 0.3s ease, transform 0.3s ease;
        }
        .card-glow:hover {
          transform: translateY(-4px);
        }
        .step-connector::after {
          content: '';
          position: absolute;
          top: 50%;
          left: -2rem;
          width: 2rem;
          height: 2px;
          background: linear-gradient(90deg, transparent, rgba(201,168,76,0.4));
        }
        .pricing-popular-glow {
          box-shadow: 0 0 60px rgba(201,168,76,0.15), 0 0 120px rgba(201,168,76,0.08);
        }
        .whatsapp-bubble {
          animation: float 3s ease-in-out infinite;
        }
        .nav-blur {
          backdrop-filter: blur(20px);
          -webkit-backdrop-filter: blur(20px);
          background: rgba(10, 17, 40, 0.85);
          border-bottom: 1px solid rgba(201,168,76,0.1);
        }
      `}} />

      {/* ──────────────────────────── NAVBAR ───────────────────────────────── */}
      <header className="nav-blur fixed top-0 inset-x-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          {/* Logo */}
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-bl from-[#C9A84C] to-[#8a6520] flex items-center justify-center">
              <Cpu className="w-4 h-4 text-[#0a1128]" />
            </div>
            <span className="text-lg font-bold tracking-tight">
              <span className="gold-text">Omni</span>
              <span className="text-white">Flow</span>
              <span className="text-[#C9A84C] text-sm mr-1">AI</span>
            </span>
          </div>

          {/* Nav Links */}
          <nav className="hidden md:flex items-center gap-6 text-sm text-white/60">
            <a href="#how-it-works" className="hover:text-white transition-colors">كيف يعمل؟</a>
            <a href="#pricing" className="hover:text-white transition-colors">الأسعار</a>
            <a href="#prerequisites" className="hover:text-white transition-colors">متطلبات البدء</a>
          </nav>

          {/* CTA */}
          <div className="flex items-center gap-3">
            <Link
              href="/ar/sign-in"
              className="hidden sm:inline-flex text-sm text-white/70 hover:text-white transition-colors px-4 py-2"
            >
              تسجيل الدخول
            </Link>
            <Link
              href="/ar/sign-up"
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold text-[#0a1128] bg-gradient-to-l from-[#C9A84C] to-[#e0c060] hover:brightness-110 transition-all active:scale-95"
            >
              ابدأ الآن
              <ChevronLeft className="w-3.5 h-3.5" />
            </Link>
          </div>
        </div>
      </header>

      {/* ────────────────────────── HERO SECTION ───────────────────────────── */}
      <section className="relative min-h-screen flex items-center pt-16 hero-grid overflow-hidden">
        {/* Background radial glows */}
        <div
          className="absolute inset-0 pointer-events-none"
          aria-hidden="true"
        >
          <div className="absolute top-1/4 right-1/4 w-96 h-96 bg-[#C9A84C]/8 rounded-full blur-[100px]" />
          <div className="absolute bottom-1/3 left-1/4 w-80 h-80 bg-blue-600/6 rounded-full blur-[120px]" />
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-[#C9A84C]/3 rounded-full blur-[160px]" />
        </div>

        <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20 lg:py-32">
          <div className="grid lg:grid-cols-2 gap-16 items-center">
            {/* Left — Text */}
            <div className="order-2 lg:order-1">
              {/* Badge */}
              <div className="fade-up inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-[#C9A84C]/30 bg-[#C9A84C]/8 text-[#C9A84C] text-xs font-semibold mb-6">
                <Sparkles className="w-3.5 h-3.5" />
                أول مساعد مبيعات عقاري ذكي في السعودية
              </div>

              {/* Headline */}
              <h1 className="fade-up-delay-1 text-3xl sm:text-4xl lg:text-5xl xl:text-6xl font-black leading-[1.2] mb-6 tracking-tight">
                <span className="text-white">أغلق صفقاتك العقارية </span>
                <span className="gold-text">٢٤/٧</span>
                <br />
                <span className="text-white">مع أول مساعد مبيعات ذكي</span>
                <br />
                <span className="text-white/80 text-2xl sm:text-3xl lg:text-4xl font-bold">
                  يرد بالصوت والنص.
                </span>
              </h1>

              {/* Sub-headline */}
              <p className="fade-up-delay-2 text-white/60 text-base sm:text-lg leading-relaxed mb-10 max-w-xl">
                أتمتة كاملة عبر{" "}
                <span className="text-white font-semibold">جميع المنصات (واتساب، إنستقرام، تيك توك، إكس)</span> — مساعدك الذكي يوحد كل محادثاتك في صندوق وارد ذكي واحد ويرد فورياً بـ
                <span className="text-[#C9A84C] font-semibold"> رسائل صوتية ونصية</span>،
                يفلتر الجادين، ويرسل العروض المخصصة.{" "}
                <span className="text-white font-semibold">صفر عملاء ضائعين.</span>
              </p>

              {/* Feature Pills */}
              <div className="fade-up-delay-2 flex flex-wrap gap-2.5 mb-10">
                {[
                  { icon: MessageSquare, text: "واتساب API" },
                  { icon: Mic, text: "ردود صوتية" },
                  { icon: ShieldCheck, text: "فلترة العملاء" },
                  { icon: Megaphone, text: "VIP Broadcast" },
                ].map(({ icon: Icon, text }) => (
                  <span
                    key={text}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-white/75 text-sm"
                  >
                    <Icon className="w-3.5 h-3.5 text-[#C9A84C]" />
                    {text}
                  </span>
                ))}
              </div>

              {/* CTAs */}
              <div className="fade-up-delay-3 flex flex-col sm:flex-row gap-4">
                <Link
                  href="/ar/sign-up"
                  id="hero-cta-primary"
                  className="group inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl text-base font-bold text-[#0a1128] bg-gradient-to-l from-[#C9A84C] to-[#e8cc6a] hover:brightness-110 transition-all active:scale-95 shadow-lg shadow-[#C9A84C]/20"
                  style={{ animation: "pulse-gold 2s ease-in-out infinite" }}
                >
                  ابدأ الآن — مجانياً ١٤ يوم
                  <ChevronLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" />
                </Link>
                <a
                  href="#pricing"
                  id="hero-cta-secondary"
                  className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl text-base font-semibold text-white border border-white/20 hover:bg-white/5 hover:border-white/40 transition-all active:scale-95"
                >
                  تواصل مع المبيعات
                  <Phone className="w-4 h-4" />
                </a>
              </div>

              {/* Trust Signals */}
              <div className="fade-up-delay-3 flex items-center gap-5 mt-8 text-white/40 text-xs">
                <span className="flex items-center gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  بدون بطاقة ائتمان
                </span>
                <span className="flex items-center gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  إعداد في أقل من يوم
                </span>
                <span className="flex items-center gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  ألغِ في أي وقت
                </span>
              </div>
            </div>

            {/* Right — Visual Mockup */}
            <div className="order-1 lg:order-2 flex items-center justify-center relative">
              {/* Glowing background circle */}
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <div className="w-72 h-72 rounded-full bg-[#C9A84C]/10 blur-[60px]" />
              </div>

              {/* Phone Frame */}
              <div className="relative z-10 w-64 float">
                <div className="bg-[#111827] rounded-[2.5rem] p-3 border border-white/10 shadow-2xl shadow-black/50">
                  {/* Phone screen */}
                  <div className="bg-[#0d1117] rounded-[2rem] overflow-hidden">
                    {/* Status bar */}
                    <div className="bg-[#1a2540] px-4 py-2 flex items-center gap-2.5">
                      <div className="w-8 h-8 rounded-full bg-gradient-to-bl from-[#C9A84C] to-[#8a6520] flex items-center justify-center flex-shrink-0">
                        <Cpu className="w-4 h-4 text-[#0a1128]" />
                      </div>
                      <div>
                        <p className="text-white text-xs font-semibold leading-tight">OmniFlow AI</p>
                        <p className="text-emerald-400 text-[10px] flex items-center gap-1">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" />
                          متصل الآن
                        </p>
                      </div>
                    </div>

                    {/* Chat messages */}
                    <div className="p-3 space-y-2.5 min-h-[360px]" dir="rtl">
                      {/* Customer message */}
                      <div className="flex justify-end">
                        <div className="bg-[#1a5c30] text-white text-xs rounded-xl rounded-tl-sm px-3 py-2 max-w-[80%] leading-relaxed shadow">
                          السلام عليكم، أبحث عن شقة في حي النرجس
                        </div>
                      </div>

                      {/* AI reply */}
                      <div className="flex justify-start">
                        <div className="bg-[#1e2a3a] text-white/90 text-xs rounded-xl rounded-tr-sm px-3 py-2 max-w-[85%] leading-relaxed shadow border border-[#C9A84C]/10">
                          أهلاً بك! 👋 عندي ثلاث شقق رائعة في النرجس تناسبك. قبل ما أرسل التفاصيل، ممكن تشارك اسمك ورقمك؟
                        </div>
                      </div>

                      {/* VCard collect */}
                      <div className="flex justify-end">
                        <div className="bg-[#1a5c30] text-white text-xs rounded-xl rounded-tl-sm px-3 py-2 max-w-[80%] leading-relaxed shadow">
                          تفضل بطاقتي 📇
                          <div className="mt-1.5 bg-[#15472a] rounded-lg px-2 py-1.5 flex items-center gap-1.5">
                            <div className="w-4 h-4 rounded-full bg-[#C9A84C]/80" />
                            <span className="text-[#C9A84C] font-medium">محمد السالم</span>
                          </div>
                        </div>
                      </div>

                      {/* AI voice note */}
                      <div className="flex justify-start">
                        <div className="bg-[#1e2a3a] text-white/90 text-xs rounded-xl rounded-tr-sm px-3 py-2 max-w-[85%] shadow border border-[#C9A84C]/10">
                          <div className="flex items-center gap-2 text-[#C9A84C]">
                            <Mic className="w-3.5 h-3.5 flex-shrink-0" />
                            <div className="flex-1 h-1 bg-white/10 rounded-full overflow-hidden">
                              <div className="h-full w-3/5 gold-shimmer rounded-full" />
                            </div>
                            <span className="text-white/50 text-[10px]">0:12</span>
                          </div>
                          <p className="text-white/50 text-[10px] mt-1">رسالة صوتية — شرح الشقق المتاحة</p>
                        </div>
                      </div>

                      {/* Qdrant match */}
                      <div className="flex justify-start">
                        <div className="bg-[#C9A84C]/10 border border-[#C9A84C]/25 text-[#C9A84C] text-xs rounded-xl rounded-tr-sm px-3 py-2 max-w-[85%] shadow">
                          <p className="font-semibold mb-1">أقرب ٣ عقارات موجودة:</p>
                          <p>🏠 شقة ٣ غرف — ٨٥٠٬٠٠٠ ر.س</p>
                          <p>🏠 شقة ٤ غرف — ١.١م ر.س</p>
                          <p>🏢 بنتهاوس — ١.٨م ر.س</p>
                        </div>
                      </div>
                    </div>

                    {/* Input bar */}
                    <div className="bg-[#1a2540] px-3 py-2.5 flex items-center gap-2">
                      <div className="flex-1 bg-[#243058] rounded-full px-3 py-1.5 text-white/30 text-[11px]">
                        اكتب رسالة...
                      </div>
                      <div className="w-7 h-7 rounded-full bg-[#C9A84C] flex items-center justify-center flex-shrink-0">
                        <Mic className="w-3.5 h-3.5 text-[#0a1128]" />
                      </div>
                    </div>
                  </div>
                </div>

                {/* Floating badges */}
                <div className="absolute -top-3 -right-4 px-3 py-1.5 rounded-full bg-emerald-500/90 text-white text-[11px] font-bold shadow-lg shadow-emerald-500/30 whatsapp-bubble">
                  ✓ عميل محفوظ
                </div>
                <div
                  className="absolute -bottom-3 -left-4 px-3 py-1.5 rounded-full bg-[#C9A84C] text-[#0a1128] text-[11px] font-bold shadow-lg shadow-[#C9A84C]/30"
                  style={{ animation: "float 3.5s ease-in-out 1s infinite" }}
                >
                  ⚡ رد خلال ٢ث
                </div>
              </div>
            </div>
          </div>

          {/* Stats bar */}
          <div className="mt-20 grid grid-cols-2 md:grid-cols-4 gap-px bg-white/5 rounded-2xl overflow-hidden border border-white/5">
            {STATS.map(({ value, label }) => (
              <div
                key={label}
                className="bg-[#0e1830] px-6 py-5 text-center hover:bg-[#111f3a] transition-colors"
              >
                <p className="text-2xl sm:text-3xl font-black gold-text">{value}</p>
                <p className="text-white/50 text-xs sm:text-sm mt-1">{label}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ─────────────────────── HOW IT WORKS ──────────────────────────────── */}
      <section id="how-it-works" className="relative py-24 lg:py-32">
        {/* Subtle top border glow */}
        <div className="absolute top-0 inset-x-0 h-px bg-gradient-to-r from-transparent via-[#C9A84C]/30 to-transparent" />

        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          {/* Section header */}
          <div className="text-center mb-16">
            <span className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-[#C9A84C]/25 bg-[#C9A84C]/6 text-[#C9A84C] text-xs font-semibold mb-4">
              <Layers className="w-3.5 h-3.5" />
              كيف يعمل النظام؟
            </span>
            <h2 className="text-3xl sm:text-4xl lg:text-5xl font-black text-white mt-3 mb-4 leading-tight">
              من صفر إلى مساعد ذكي
              <br />
              <span className="gold-text">في أربع خطوات</span>
            </h2>
            <p className="text-white/50 text-base sm:text-lg max-w-2xl mx-auto">
              بنية تحتية جاهزة تعمل خلف الكواليس — أنت تغلق الصفقات، ونحن نتكفل بالباقي.
            </p>
          </div>

          {/* Steps Grid */}
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {STEPS.map(({ step, icon: Icon, title, desc, badge, badgeColor, glow }, idx) => (
              <div
                key={step}
                className={`group relative bg-[#0e1830] rounded-2xl p-6 border border-white/6 hover:border-[#C9A84C]/25 card-glow ${glow} cursor-default`}
                style={{ animationDelay: `${idx * 100}ms` }}
              >
                {/* Step number */}
                <div className="absolute top-4 left-4 text-white/10 text-4xl font-black tabular-nums select-none">
                  {step}
                </div>

                {/* Icon */}
                <div className="relative z-10 w-12 h-12 rounded-xl bg-[#162040] border border-white/8 flex items-center justify-center mb-4 group-hover:border-[#C9A84C]/30 transition-colors">
                  <Icon className="w-5.5 h-5.5 text-[#C9A84C]" style={{ width: "22px", height: "22px" }} />
                </div>

                {/* Title */}
                <h3 className="text-white font-bold text-lg mb-2 leading-tight">{title}</h3>

                {/* Description */}
                <p className="text-white/50 text-sm leading-relaxed mb-4">{desc}</p>

                {/* Badge */}
                <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-semibold ${badgeColor}`}>
                  <span className="w-1.5 h-1.5 rounded-full bg-current opacity-80" />
                  {badge}
                </span>

                {/* Connector arrow (hidden on last item) */}
                {idx < STEPS.length - 1 && (
                  <div className="hidden lg:block absolute -left-3 top-1/2 -translate-y-1/2 z-20">
                    <ArrowLeft className="w-5 h-5 text-[#C9A84C]/30" />
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ──────────────────────────── PRICING ──────────────────────────────── */}
      <section id="pricing" className="relative py-24 lg:py-32 bg-[#080e1f]">
        <div className="absolute top-0 inset-x-0 h-px bg-gradient-to-r from-transparent via-[#C9A84C]/20 to-transparent" />
        <div className="absolute bottom-0 inset-x-0 h-px bg-gradient-to-r from-transparent via-white/5 to-transparent" />

        {/* Background glow */}
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none" aria-hidden>
          <div className="w-[800px] h-[400px] bg-[#C9A84C]/4 rounded-full blur-[120px]" />
        </div>

        <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          {/* Section header */}
          <div className="text-center mb-16">
            <span className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-[#C9A84C]/25 bg-[#C9A84C]/6 text-[#C9A84C] text-xs font-semibold mb-4">
              <Star className="w-3.5 h-3.5" />
              خطط الأسعار
            </span>
            <h2 className="text-3xl sm:text-4xl lg:text-5xl font-black text-white mt-3 mb-4 leading-tight">
              الخطة المناسبة
              <br />
              <span className="gold-text">لكل حجم وكالة</span>
            </h2>
            <p className="text-white/50 text-base sm:text-lg max-w-2xl mx-auto">
              ابدأ بالخطة الاقتصادية وانتقل بسلاسة مع نمو أعمالك — بدون قيود تقنية.
            </p>
          </div>

          {/* Pricing cards */}
          <div className="grid md:grid-cols-3 gap-6 lg:gap-8 items-start">
            {PRICING.map(
              ({
                id,
                tier,
                tierEn,
                persona,
                tagline,
                price,
                currency,
                period,
                highlight,
                badge,
                features,
                cta,
                ctaStyle,
                icon: TierIcon,
                ideal,
                cardBg,
                borderStyle,
              }) => (
                <div
                  key={id}
                  id={`pricing-${id}`}
                  className={`relative rounded-2xl p-7 flex flex-col gap-6 ${cardBg} ${borderStyle} card-glow ${highlight ? "pricing-popular-glow md:-mt-4" : ""} transition-all duration-300`}
                >
                  {/* Popular badge */}
                  {badge && (
                    <div className={`absolute -top-3.5 inset-x-0 flex justify-center`}>
                      <span
                        className={`px-4 py-1 rounded-full text-xs font-bold ${highlight ? "bg-gradient-to-l from-[#C9A84C] to-[#e8cc6a] text-[#0a1128]" : "bg-white/10 text-white/70 border border-white/15"}`}
                      >
                        {badge}
                      </span>
                    </div>
                  )}

                  {/* Header */}
                  <div>
                    <div className="flex items-start justify-between mb-3">
                      <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${highlight ? "bg-[#C9A84C]/15 border border-[#C9A84C]/30" : "bg-white/5 border border-white/8"}`}>
                        <TierIcon className={`w-5 h-5 ${highlight ? "text-[#C9A84C]" : "text-white/60"}`} />
                      </div>
                      <span className="text-xs text-white/30 font-mono">{tierEn}</span>
                    </div>

                    <h3 className={`text-xl font-black ${highlight ? "text-[#C9A84C]" : "text-white"}`}>
                      {tier}
                    </h3>
                    <p className="text-sm text-white/50 mt-1 leading-snug">{persona}</p>
                    <p className="text-xs text-white/35 mt-1">{tagline}</p>

                    <span className="inline-block mt-3 px-2.5 py-0.5 rounded-full bg-white/5 text-white/40 text-[11px] border border-white/8">
                      {ideal}
                    </span>
                  </div>

                  {/* Price */}
                  <div className="border-t border-white/6 pt-5">
                    {price === "تسعير مخصص" ? (
                      <div>
                        <p className="text-2xl font-black text-white">تسعير مخصص</p>
                        <p className="text-white/40 text-sm mt-1">يبدأ من ٢٬٩٩٩ ر.س/شهرياً</p>
                      </div>
                    ) : (
                      <div className="flex items-end gap-1">
                        <span className={`text-4xl font-black ${highlight ? "text-[#C9A84C]" : "text-white"}`}>
                          {price}
                        </span>
                        <span className="text-white/60 text-sm mb-1">{currency}</span>
                        <span className="text-white/40 text-sm mb-1">{period}</span>
                      </div>
                    )}
                  </div>

                  {/* Features */}
                  <ul className="flex flex-col gap-3">
                    {features.map((feat) => (
                      <li key={feat} className="flex items-start gap-2.5 text-sm text-white/70">
                        <CheckCircle2
                          className={`w-4 h-4 flex-shrink-0 mt-0.5 ${highlight ? "text-[#C9A84C]" : "text-emerald-400/70"}`}
                        />
                        {feat}
                      </li>
                    ))}
                  </ul>

                  {/* CTA */}
                  <a
                    href="/ar/sign-up"
                    id={`pricing-cta-${id}`}
                    className={`mt-auto w-full inline-flex items-center justify-center gap-2 px-5 py-3.5 rounded-xl text-sm font-bold transition-all duration-200 active:scale-95 ${ctaStyle}`}
                  >
                    {cta}
                    <ChevronLeft className="w-4 h-4" />
                  </a>
                </div>
              )
            )}
          </div>

          {/* Bottom note */}
          <p className="text-center text-white/30 text-sm mt-10">
            جميع الخطط تشمل فترة تجربة مجانية ١٤ يوم · لا بطاقة ائتمان · يمكنك الإلغاء في أي وقت
          </p>
        </div>
      </section>

      {/* ─────────────────────── PREREQUISITES ─────────────────────────────── */}
      <section id="prerequisites" className="relative py-24 lg:py-32">
        <div className="absolute top-0 inset-x-0 h-px bg-gradient-to-r from-transparent via-white/8 to-transparent" />

        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
          {/* Section header */}
          <div className="text-center mb-14">
            <span className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full border border-emerald-500/25 bg-emerald-500/6 text-emerald-400 text-xs font-semibold mb-4">
              <CheckCircle2 className="w-3.5 h-3.5" />
              قائمة التحقق
            </span>
            <h2 className="text-3xl sm:text-4xl lg:text-5xl font-black text-white mt-3 mb-4 leading-tight">
              ماذا تحتاج
              <span className="gold-text"> للبدء؟</span>
            </h2>
            <p className="text-white/50 text-base sm:text-lg">
              ثلاثة متطلبات بسيطة — ونحن نتكفل بالإعداد التقني الكامل
            </p>
          </div>

          {/* Prerequisites cards */}
          <div className="grid sm:grid-cols-3 gap-6 mb-12">
            {PREREQUISITES.map(({ icon: Icon, title, desc }, idx) => (
              <div
                key={title}
                className="relative bg-[#0e1830] rounded-2xl p-6 border border-white/6 hover:border-emerald-500/25 card-glow group transition-all"
                style={{ animationDelay: `${idx * 120}ms` }}
              >
                {/* Number */}
                <div className="absolute top-4 left-4 text-white/8 text-5xl font-black tabular-nums select-none">
                  {idx + 1}
                </div>

                {/* Icon */}
                <div className="relative z-10 w-12 h-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mb-4 group-hover:border-emerald-500/40 transition-colors">
                  <Icon className="w-5.5 h-5.5 text-emerald-400" style={{ width: "22px", height: "22px" }} />
                </div>

                <h3 className="text-white font-bold text-base mb-2 leading-snug">{title}</h3>
                <p className="text-white/45 text-sm leading-relaxed">{desc}</p>
              </div>
            ))}
          </div>

          {/* Final CTA block */}
          <div className="relative rounded-2xl overflow-hidden border border-[#C9A84C]/20">
            {/* Background */}
            <div className="absolute inset-0 bg-gradient-to-br from-[#0e1830] via-[#101d3a] to-[#0a1128]" />
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none" aria-hidden>
              <div className="w-[500px] h-[200px] bg-[#C9A84C]/6 rounded-full blur-[80px]" />
            </div>

            <div className="relative p-8 sm:p-12 text-center">
              {/* Icons */}
              <div className="flex items-center justify-center gap-3 mb-5">
                {[Lock, Cpu, Zap, Users].map((Icon) => (
                  <div key={Icon.displayName} className="w-9 h-9 rounded-lg bg-[#C9A84C]/10 border border-[#C9A84C]/20 flex items-center justify-center">
                    <Icon className="w-4 h-4 text-[#C9A84C]" />
                  </div>
                ))}
              </div>

              <h3 className="text-2xl sm:text-3xl font-black text-white mb-3">
                جاهز تبدأ؟
              </h3>
              <p className="text-white/55 text-sm sm:text-base mb-8 max-w-md mx-auto leading-relaxed">
                فريقنا التقني يتكفل بربط واتساب، فهرسة العقارات، وإعداد المساعد — خلال أقل من ٢٤ ساعة.
              </p>

              <div className="flex flex-col sm:flex-row gap-4 items-center justify-center">
                <Link
                  href="/ar/sign-in"
                  id="footer-cta-primary"
                  className="inline-flex items-center gap-2 px-8 py-4 rounded-xl text-base font-bold text-[#0a1128] bg-gradient-to-l from-[#C9A84C] to-[#e8cc6a] hover:brightness-110 transition-all active:scale-95 shadow-lg shadow-[#C9A84C]/20"
                >
                  ابدأ مجاناً الآن
                  <ChevronLeft className="w-4 h-4" />
                </Link>
                <a
                  href="https://wa.me/966500000000"
                  id="footer-cta-whatsapp"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 px-8 py-4 rounded-xl text-base font-semibold text-white border border-white/15 hover:bg-white/5 hover:border-white/30 transition-all active:scale-95"
                >
                  <MessageSquare className="w-4 h-4 text-emerald-400" />
                  تواصل عبر واتساب
                </a>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ──────────────────────────── FOOTER ───────────────────────────────── */}
      <footer className="border-t border-white/6 py-10 bg-[#07101f]">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col md:flex-row items-center justify-between gap-6">
            {/* Logo */}
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-lg bg-gradient-to-bl from-[#C9A84C] to-[#8a6520] flex items-center justify-center">
                <Cpu className="w-3.5 h-3.5 text-[#0a1128]" />
              </div>
              <span className="text-base font-bold">
                <span className="gold-text">Omni</span>
                <span className="text-white">Flow</span>
                <span className="text-[#C9A84C] text-sm mr-0.5">AI</span>
              </span>
            </div>

            {/* Links */}
            <nav className="flex flex-wrap items-center justify-center gap-5 text-sm text-white/40">
              <a href="#how-it-works" className="hover:text-white/70 transition-colors">كيف يعمل</a>
              <a href="#pricing" className="hover:text-white/70 transition-colors">الأسعار</a>
              <a href="#prerequisites" className="hover:text-white/70 transition-colors">متطلبات البدء</a>
              <Link href="/ar/sign-in" className="hover:text-white/70 transition-colors">تسجيل الدخول</Link>
            </nav>

            {/* Copyright */}
            <p className="text-white/25 text-xs text-center">
              © {new Date().getFullYear()} OmniFlow AI · جميع الحقوق محفوظة
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
