import { NextResponse } from "next/server";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "https://repotoire-api.fly.dev/api/v1";

export async function GET() {
  return NextResponse.redirect(`${API_BASE_URL}/status/rss`);
}
