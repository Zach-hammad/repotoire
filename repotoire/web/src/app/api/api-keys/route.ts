import { auth, clerkClient } from '@clerk/nextjs/server';
import { NextRequest, NextResponse } from 'next/server';

/**
 * GET /api/api-keys
 * List all API keys for the current organization using Clerk's API.
 */
export async function GET() {
  try {
    const { userId, orgId } = await auth();

    if (!userId) {
      return NextResponse.json(
        { detail: 'Unauthorized' },
        { status: 401 }
      );
    }

    if (!orgId) {
      return NextResponse.json(
        { detail: 'Organization required' },
        { status: 403 }
      );
    }

    const client = await clerkClient();

    // List API keys for this organization
    const apiKeysResponse = await client.apiKeys.list({
      subject: orgId,
    });

    // Transform to our frontend format
    // Note: createdAt/lastUsedAt are Unix timestamps in milliseconds
    const apiKeys = apiKeysResponse.data.map((key) => ({
      id: key.id,
      name: key.name || 'Unnamed Key',
      key_prefix: key.id.slice(0, 8), // Use ID prefix as identifier
      key_suffix: '••••', // Clerk doesn't expose the key after creation
      scopes: key.scopes || [],
      created_at: new Date(key.createdAt).toISOString(),
      last_used_at: key.lastUsedAt ? new Date(key.lastUsedAt).toISOString() : null,
      expires_at: key.expiration ? new Date(key.expiration).toISOString() : null,
      created_by: key.createdBy || userId,
    }));

    return NextResponse.json(apiKeys);
  } catch (error) {
    console.error('Error fetching API keys:', error);
    return NextResponse.json(
      { detail: 'Failed to fetch API keys' },
      { status: 500 }
    );
  }
}

/**
 * POST /api/api-keys
 * Create a new API key for the current organization using Clerk's API.
 * Returns the full key only once - it cannot be retrieved again.
 */
export async function POST(request: NextRequest) {
  try {
    const { userId, orgId } = await auth();

    if (!userId) {
      return NextResponse.json(
        { detail: 'Unauthorized' },
        { status: 401 }
      );
    }

    if (!orgId) {
      return NextResponse.json(
        { detail: 'Organization required' },
        { status: 403 }
      );
    }

    const body = await request.json();

    // Validate required fields
    if (!body.name || typeof body.name !== 'string') {
      return NextResponse.json(
        { detail: 'Name is required' },
        { status: 400 }
      );
    }

    if (!body.scopes || !Array.isArray(body.scopes) || body.scopes.length === 0) {
      return NextResponse.json(
        { detail: 'At least one scope is required' },
        { status: 400 }
      );
    }

    const client = await clerkClient();

    // Create an org-scoped API key
    const apiKey = await client.apiKeys.create({
      name: body.name,
      subject: orgId, // Org-scoped key
      scopes: body.scopes,
      createdBy: userId,
      // Optional expiration in seconds
      ...(body.expires_in_days && {
        secondsUntilExpiration: body.expires_in_days * 24 * 60 * 60,
      }),
    });

    // Return the full key - this is the only time it's available
    return NextResponse.json({
      id: apiKey.id,
      name: apiKey.name,
      key: apiKey.secret, // The actual secret key
      scopes: apiKey.scopes || body.scopes,
      created_at: new Date(apiKey.createdAt).toISOString(),
      expires_at: apiKey.expiration ? new Date(apiKey.expiration).toISOString() : null,
    });
  } catch (error) {
    console.error('Error creating API key:', error);
    return NextResponse.json(
      { detail: 'Failed to create API key' },
      { status: 500 }
    );
  }
}
