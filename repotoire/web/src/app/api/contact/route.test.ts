import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { POST } from './route';
import { resetRateLimitStoreForTests } from '@/lib/rate-limit';

function makeRequest(body: unknown, ip = '198.51.100.10'): Request {
  return new Request('http://localhost/api/contact', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-forwarded-for': ip,
    },
    body: JSON.stringify(body),
  });
}

const VALID_PAYLOAD = {
  name: 'Alex',
  email: 'alex@example.com',
  company: 'Repotoire',
  message: 'I would like to learn more about enterprise pricing.',
};

describe('POST /api/contact', () => {
  beforeEach(() => {
    resetRateLimitStoreForTests();
    vi.spyOn(console, 'info').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns 400 for invalid payload', async () => {
    const response = await POST(makeRequest({ email: 'bad@example.com' }));
    const data = await response.json();

    expect(response.status).toBe(400);
    expect(data.error).toBe('Invalid request payload');
  });

  it('returns 200 for valid payload', async () => {
    const response = await POST(makeRequest(VALID_PAYLOAD));
    const data = await response.json();

    expect(response.status).toBe(200);
    expect(data.success).toBe(true);
  });

  it('rate limits repeated requests', async () => {
    for (let i = 0; i < 5; i += 1) {
      const response = await POST(makeRequest(VALID_PAYLOAD, '203.0.113.99'));
      expect(response.status).toBe(200);
    }

    const limited = await POST(makeRequest(VALID_PAYLOAD, '203.0.113.99'));
    expect(limited.status).toBe(429);
    expect(limited.headers.get('Retry-After')).toBeTruthy();
  });
});
