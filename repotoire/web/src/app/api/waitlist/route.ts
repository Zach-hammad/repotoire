import { NextRequest, NextResponse } from 'next/server';
import { Redis } from '@upstash/redis';

// Initialize Redis (uses UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN env vars)
const redis = Redis.fromEnv();

// POST /api/waitlist - Capture leads for Teams tier
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { email, company, size, source, interests } = body;

    if (!email || !email.includes('@')) {
      return NextResponse.json({ error: 'Valid email required' }, { status: 400 });
    }

    const lead = {
      email: email.toLowerCase().trim(),
      company: company || null,
      size: size || null, // team size or repo size
      source: source || 'website', // website, cli, hn, etc
      interests: interests || [], // teams, enterprise, integrations
      createdAt: new Date().toISOString(),
      ip: request.headers.get('x-forwarded-for')?.split(',')[0] || 'unknown',
      userAgent: request.headers.get('user-agent') || 'unknown',
    };

    // Store in Vercel KV (or fallback)
    try {
      // Add to waitlist set
      await redis.sadd('waitlist:emails', email.toLowerCase());
      // Store full lead data
      await redis.hset(`waitlist:lead:${email.toLowerCase()}`, lead);
      // Increment counter
      await redis.incr('waitlist:count');
    } catch (kvError) {
      // KV not configured - log for now
      console.log('[waitlist]', JSON.stringify(lead));
    }

    return NextResponse.json({ success: true, message: 'Added to waitlist' });
  } catch (error) {
    console.error('[waitlist] Error:', error);
    return NextResponse.json({ error: 'Internal error' }, { status: 500 });
  }
}

// GET /api/waitlist - Get waitlist count (public stat)
export async function GET() {
  try {
    const count = await redis.get('waitlist:count') || 0;
    return NextResponse.json({ count });
  } catch {
    return NextResponse.json({ count: 0 });
  }
}
