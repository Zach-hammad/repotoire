import { NextResponse } from 'next/server';
import { Redis } from '@upstash/redis';

const redis = Redis.fromEnv();

// GET /api/stats - Public aggregate stats (social proof)
export async function GET() {
  try {
    const [
      analyses,
      files,
      functions,
      findings,
      fixesGenerated,
      fixesApplied,
      waitlistCount,
      topLanguages,
      topDetectors,
      gradeDistribution,
    ] = await Promise.all([
      redis.get('telemetry:analyses'),
      redis.get('telemetry:files'),
      redis.get('telemetry:functions'),
      redis.get('telemetry:findings'),
      redis.get('telemetry:fixes:generated'),
      redis.get('telemetry:fixes:applied'),
      redis.get('waitlist:count'),
      redis.zrange('telemetry:languages', 0, 9, { rev: true, withScores: true }),
      redis.zrange('telemetry:detectors', 0, 9, { rev: true, withScores: true }),
      redis.zrange('telemetry:grades', 0, -1, { withScores: true }),
    ]);

    // Format language stats
    const languages: Record<string, number> = {};
    if (Array.isArray(topLanguages)) {
      for (let i = 0; i < topLanguages.length; i += 2) {
        languages[topLanguages[i] as string] = topLanguages[i + 1] as number;
      }
    }

    // Format detector stats
    const detectors: Record<string, number> = {};
    if (Array.isArray(topDetectors)) {
      for (let i = 0; i < topDetectors.length; i += 2) {
        detectors[topDetectors[i] as string] = topDetectors[i + 1] as number;
      }
    }

    // Format grade distribution
    const grades: Record<string, number> = {};
    if (Array.isArray(gradeDistribution)) {
      for (let i = 0; i < gradeDistribution.length; i += 2) {
        grades[gradeDistribution[i] as string] = gradeDistribution[i + 1] as number;
      }
    }

    return NextResponse.json({
      // Aggregate totals
      totals: {
        analyses: analyses || 0,
        filesAnalyzed: files || 0,
        functionsAnalyzed: functions || 0,
        issuesFound: findings || 0,
        fixesGenerated: fixesGenerated || 0,
        fixesApplied: fixesApplied || 0,
        waitlist: waitlistCount || 0,
      },
      // Breakdowns
      topLanguages: languages,
      topDetectors: detectors,
      gradeDistribution: grades,
      // Computed stats
      computed: {
        avgFindingsPerAnalysis: analyses ? Math.round((findings as number || 0) / (analyses as number)) : 0,
        fixApplyRate: fixesGenerated ? Math.round(((fixesApplied as number || 0) / (fixesGenerated as number)) * 100) : 0,
      },
      // Cache headers
      generatedAt: new Date().toISOString(),
    }, {
      headers: {
        'Cache-Control': 'public, s-maxage=60, stale-while-revalidate=300',
      },
    });
  } catch (error) {
    console.error('[stats] Error:', error);
    // Return zeros if KV not configured
    return NextResponse.json({
      totals: {
        analyses: 0,
        filesAnalyzed: 0,
        functionsAnalyzed: 0,
        issuesFound: 0,
        fixesGenerated: 0,
        fixesApplied: 0,
        waitlist: 0,
      },
      topLanguages: {},
      topDetectors: {},
      gradeDistribution: {},
      computed: { avgFindingsPerAnalysis: 0, fixApplyRate: 0 },
      generatedAt: new Date().toISOString(),
    });
  }
}
