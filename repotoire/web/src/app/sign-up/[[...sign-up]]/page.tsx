import { SignUp } from "@clerk/nextjs";

export default function SignUpPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="mx-auto w-full max-w-md space-y-6 p-6">
        <div className="text-center space-y-2">
          <h1 className="text-2xl font-bold tracking-tight">
            Create an account
          </h1>
          <p className="text-muted-foreground">
            Get started with Repotoire
          </p>
        </div>
        <SignUp
          appearance={{
            elements: {
              rootBox: "mx-auto w-full",
              card: "shadow-none p-0 bg-transparent",
            },
          }}
          routing="path"
          path="/sign-up"
          signInUrl="/sign-in"
        />
      </div>
    </div>
  );
}
