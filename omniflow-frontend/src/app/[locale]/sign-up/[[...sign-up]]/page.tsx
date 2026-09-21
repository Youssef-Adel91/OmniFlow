import { SignUp } from "@clerk/nextjs";

export default function SignUpPage({
  params: { locale },
}: {
  params: { locale: string };
}) {
  return (
    <div className="flex min-h-screen items-center justify-center py-12">
      <SignUp routing="hash" />
    </div>
  );
}
