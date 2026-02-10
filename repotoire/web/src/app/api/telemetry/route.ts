import { NextRequest, NextResponse } from 'next/server';
import { Redis } from '@upstash/redis';

const redis = Redis.fromEnv();

// Anonymous telemetry - no PII, opt-in only
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const {
      // Session info (anonymous)
      sessionId,      // Random UUID per analysis, not persistent
      cliVersion,
      pythonVersion,
      os,
      
      // Repo stats (anonymous)
      languages,      // ['python', 'typescript']
      fileCount,
      functionCount,
      locTotal,
      
      // Analysis results
      grade,          // A, B, C, D, F
      score,          // 0-100
      findingsCount,
      findingsBySeverity, // {critical: 0, high: 2, medium: 5, low: 10}
      findingsByDetector, // {god-class: 1, long-method: 3}
      
      // Feature usage
      features,       // ['analyze', 'ask', 'fix', 'auto-fix']
      embeddingBackend, // 'local', 'openai', etc
      
      // Timing
      analysisTimeMs,
      
      // Fix stats (if applicable)
      fixesGenerated,
      fixesApproved,
      fixesApplied,
    } = body;

    const event = {
      sessionId: sessionId || 'unknown',
      cliVersion: cliVersion || 'unknown',
      pythonVersion,
      os,
      languages: languages || [],
      fileCount: fileCount || 0,
      functionCount: functionCount || 0,
      locTotal: locTotal || 0,
      grade,
      score,
      findingsCount: findingsCount || 0,
      findingsBySeverity: findingsBySeverity || {},
      findingsByDetector: findingsByDetector || {},
      features: features || [],
      embeddingBackend,
      analysisTimeMs,
      fixesGenerated: fixesGenerated || 0,
      fixesApproved: fixesApproved || 0,
      fixesApplied: fixesApplied || 0,
      timestamp: new Date().toISOString(),
      country: request.headers.get('x-vercel-ip-country') || 'unknown',
    };

    try {
      // Increment global counters
      await redis.incr('telemetry:analyses');
      await redis.incrby('telemetry:files', fileCount || 0);
      await redis.incrby('telemetry:functions', functionCount || 0);
      await redis.incrby('telemetry:findings', findingsCount || 0);
      await redis.incrby('telemetry:fixes:generated', fixesGenerated || 0);
      await redis.incrby('telemetry:fixes:applied', fixesApplied || 0);
      
      // Track languages
      for (const lang of (languages || [])) {
        await redis.zincrby('telemetry:languages', 1, lang);
      }
      
      // Track grades distribution
      if (grade) {
        await redis.zincrby('telemetry:grades', 1, grade);
      }
      
      // Track detector hits
      for (const [detector, count] of Object.entries(findingsByDetector || {})) {
        await redis.zincrby('telemetry:detectors', count as number, detector);
      }
      
      // Track CLI versions
      if (cliVersion) {
        await redis.zincrby('telemetry:versions', 1, cliVersion);
      }
      
      // Track feature usage
      for (const feature of (features || [])) {
        await redis.zincrby('telemetry:features', 1, feature);
      }
      
      // Track embedding backends
      if (embeddingBackend) {
        await redis.zincrby('telemetry:embeddings', 1, embeddingBackend);
      }
      
      // Store recent events (keep last 1000)
      await redis.lpush('telemetry:events', JSON.stringify(event));
      await redis.ltrim('telemetry:events', 0, 999);
      
    } catch (kvError) {
      // KV not configured - log for now
      console.log('[telemetry]', JSON.stringify(event));
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('[telemetry] Error:', error);
    return NextResponse.json({ error: 'Internal error' }, { status: 500 });
  }
}
