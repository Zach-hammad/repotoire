import { NextResponse } from "next/server";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "https://api.repotoire.io/api/v1";

export async function GET() {
  return NextResponse.redirect(`${API_BASE_URL}/status/rss`);
}
