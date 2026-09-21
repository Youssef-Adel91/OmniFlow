import { SignIn } from "@clerk/nextjs";

export default function SignInPage({
  params: { locale },
}: {
  params: { locale: string };
}) {
  return (
    <div className="flex min-h-screen items-center justify-center py-12">
      <SignIn routing="hash" />
    </div>
  );
}
