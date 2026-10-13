import { AppProviders } from "@/components/auth/AppProviders";

export default function Layout({ children }: { children: React.ReactNode }) {
  return <AppProviders>{children}</AppProviders>;
}
