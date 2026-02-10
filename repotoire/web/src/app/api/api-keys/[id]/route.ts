import { auth, clerkClient } from '@clerk/nextjs/server';
import { NextRequest, NextResponse } from 'next/server';

/**
 * DELETE /api/api-keys/[id]
 * Revoke (delete) an API key using Clerk's API.
 */
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
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

    const { id } = await params;

    if (!id) {
      return NextResponse.json(
        { detail: 'API key ID is required' },
        { status: 400 }
      );
    }

    const client = await clerkClient();

    // Security: Fetch key first to verify ownership before deletion
    const key = await client.apiKeys.get(id);

    // Return 404 if key doesn't exist (prevents enumeration attacks)
    if (!key) {
      return NextResponse.json(
        { detail: 'API key not found' },
        { status: 404 }
      );
    }

    // Security: Verify the key belongs to the authenticated organization
    if (key.subject !== orgId) {
      // Log authorization bypass attempt for security monitoring
      console.warn(
        `Unauthorized API key deletion attempt: user=${userId} org=${orgId} targetKey=${id}`
      );
      return NextResponse.json(
        { detail: 'Forbidden' },
        { status: 403 }
      );
    }

    // Ownership verified - safe to delete
    await client.apiKeys.delete(id);

    return new NextResponse(null, { status: 204 });
  } catch (error) {
    // Handle case where Clerk SDK throws for invalid/non-existent key IDs
    if (error instanceof Error && error.message.includes('not found')) {
      return NextResponse.json(
        { detail: 'API key not found' },
        { status: 404 }
      );
    }
    console.error('Error revoking API key:', error);
    return NextResponse.json(
      { detail: 'Failed to revoke API key' },
      { status: 500 }
    );
  }
}
