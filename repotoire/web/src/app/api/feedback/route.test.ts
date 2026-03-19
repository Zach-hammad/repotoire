import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { POST } from './route';
import { resetRateLimitStoreForTests } from '@/lib/rate-limit';

function makeRequest(body: unknown, ip = '198.51.100.20'): Request {
  return new Request('http://localhost/api/feedback', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-forwarded-for': ip,
    },
    body: JSON.stringify(body),
  });
}

const VALID_PAYLOAD = {
  type: 'feature',
  message: 'Please add a project-level suppression workflow for noisy detectors.',
  email: 'dev@example.com',
  cliVersion: '0.3.113',
  context: 'CI integration',
};

describe('POST /api/feedback', () => {
  beforeEach(() => {
    resetRateLimitStoreForTests();
    delete process.env.UPSTASH_REDIS_REST_URL;
    delete process.env.UPSTASH_REDIS_REST_TOKEN;
    vi.spyOn(console, 'info').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns 400 when message is too short', async () => {
    const response = await POST(makeRequest({ message: 'short' }));
    const data = await response.json();

    expect(response.status).toBe(400);
    expect(data.error).toBe('Invalid request payload');
  });

  it('accepts valid payload even when storage env is missing', async () => {
    const response = await POST(makeRequest(VALID_PAYLOAD));
    const data = await response.json();

    expect(response.status).toBe(200);
    expect(data.success).toBe(true);
  });

  it('rate limits repeated requests', async () => {
    for (let i = 0; i < 15; i += 1) {
      const response = await POST(makeRequest(VALID_PAYLOAD, '203.0.113.77'));
      expect(response.status).toBe(200);
    }

    const limited = await POST(makeRequest(VALID_PAYLOAD, '203.0.113.77'));
    expect(limited.status).toBe(429);
    expect(limited.headers.get('Retry-After')).toBeTruthy();
  });
});
