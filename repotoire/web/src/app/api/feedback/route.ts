import { NextRequest, NextResponse } from 'next/server';
import { Redis } from '@upstash/redis';

const redis = Redis.fromEnv();

// POST /api/feedback - Collect user feedback
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const {
      type,        // 'bug', 'feature', 'praise', 'other'
      message,
      email,       // optional
      cliVersion,
      context,     // optional - what they were doing
    } = body;

    if (!message || message.length < 10) {
      return NextResponse.json({ error: 'Message too short' }, { status: 400 });
    }

    const feedback = {
      type: type || 'other',
      message: message.slice(0, 2000), // limit length
      email: email || null,
      cliVersion: cliVersion || 'unknown',
      context: context || null,
      createdAt: new Date().toISOString(),
      country: request.headers.get('x-vercel-ip-country') || 'unknown',
    };

    try {
      // Store feedback
      const id = `feedback:${Date.now()}:${Math.random().toString(36).slice(2)}`;
      await redis.hset(id, feedback);
      
      // Add to list for easy retrieval
      await redis.lpush('feedback:list', id);
      await redis.ltrim('feedback:list', 0, 999); // Keep last 1000
      
      // Increment counters
      await redis.incr('feedback:count');
      await redis.incr(`feedback:count:${type || 'other'}`);
    } catch (kvError) {
      console.log('[feedback]', JSON.stringify(feedback));
    }

    return NextResponse.json({ success: true, message: 'Thank you for your feedback!' });
  } catch (error) {
    console.error('[feedback] Error:', error);
    return NextResponse.json({ error: 'Internal error' }, { status: 500 });
  }
}
