import { NextResponse } from "next/server";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function GET() {
  try {
    // Proxy to the backend RSS endpoint
    const response = await fetch(`${API_BASE_URL}/api/v1/changelog/rss`, {
      next: { revalidate: 300 }, // Cache for 5 minutes
    });

    if (!response.ok) {
      throw new Error(`Failed to fetch RSS feed: ${response.status}`);
    }

    const xml = await response.text();

    return new NextResponse(xml, {
      headers: {
        "Content-Type": "application/rss+xml; charset=utf-8",
        "Cache-Control": "public, max-age=300, s-maxage=300",
      },
    });
  } catch (error) {
    console.error("RSS feed error:", error);

    // Return a minimal valid RSS feed on error
    const errorXml = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Repotoire Changelog</title>
    <link>https://repotoire.io/changelog</link>
    <description>Unable to load changelog feed at this time.</description>
  </channel>
</rss>`;

    return new NextResponse(errorXml, {
      status: 503,
      headers: {
        "Content-Type": "application/rss+xml; charset=utf-8",
        "Cache-Control": "no-cache",
      },
    });
  }
}
