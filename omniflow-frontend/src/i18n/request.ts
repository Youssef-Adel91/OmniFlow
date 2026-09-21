import { getRequestConfig } from "next-intl/server";
import { routing } from "./routing";

export default getRequestConfig(async ({ requestLocale }) => {
  // next-intl v3.22+ passes requestLocale as a Promise — await it
  let locale = await requestLocale;

  // Fall back to the default locale if undefined or unsupported
  if (!locale || !(routing.locales as readonly string[]).includes(locale)) {
    locale = routing.defaultLocale;
  }

  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
    timeZone: "Asia/Riyadh",
    formats: {
      number: {
        currency: {
          style: "currency",
          currency: "SAR",
          minimumFractionDigits: 0,
          maximumFractionDigits: 0,
        },
      },
    },
  };
});
