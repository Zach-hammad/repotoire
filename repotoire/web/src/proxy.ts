import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Proxy (middleware) — auth removed (CLI-only product).
 * All routes are public.
 */
export default function proxy(_request: NextRequest) {
  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
  ],
};
