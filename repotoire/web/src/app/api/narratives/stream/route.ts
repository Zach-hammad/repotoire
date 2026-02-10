import { auth } from '@clerk/nextjs/server';
import { NextRequest } from 'next/server';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

export async function GET(request: NextRequest) {
  const { getToken } = await auth();
  const token = await getToken();

  if (!token) {
    return new Response('Unauthorized', { status: 401 });
  }

  const repositoryId = request.nextUrl.searchParams.get('repository_id');
  if (!repositoryId) {
    return new Response('Missing repository_id', { status: 400 });
  }

  const url = `${API_BASE_URL}/narratives/summary/stream?repository_id=${repositoryId}`;

  const response = await fetch(url, {
    headers: {
      'Authorization': `Bearer ${token}`,
      'Accept': 'text/event-stream',
    },
  });

  if (!response.ok) {
    return new Response(response.statusText, { status: response.status });
  }

  // Forward the SSE stream
  return new Response(response.body, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    },
  });
}
