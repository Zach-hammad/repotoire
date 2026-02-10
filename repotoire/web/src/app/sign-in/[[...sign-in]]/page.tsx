import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="mx-auto w-full max-w-md space-y-6 p-6">
        <div className="text-center space-y-2">
          <h1 className="text-2xl font-bold tracking-tight">
            Welcome back
          </h1>
          <p className="text-muted-foreground">
            Sign in to your Repotoire account
          </p>
        </div>
        <SignIn
          appearance={{
            elements: {
              rootBox: "mx-auto w-full",
              card: "shadow-none p-0 bg-transparent",
            },
          }}
          routing="path"
          path="/sign-in"
          signUpUrl="/sign-up"
        />
      </div>
    </div>
  );
}
