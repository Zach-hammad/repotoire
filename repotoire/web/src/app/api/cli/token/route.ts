import { auth, clerkClient, currentUser } from '@clerk/nextjs/server';
import { NextRequest, NextResponse } from 'next/server';

/**
 * POST /api/cli/token
 *
 * Create or retrieve an API key for CLI authentication.
 * This endpoint creates a dedicated CLI key with full scopes for the authenticated user's organization.
 *
 * The key is created with the name "CLI - {email}" to distinguish it from manually created keys.
 */
export async function POST(request: NextRequest) {
  try {
    const { userId, orgId } = await auth();

    if (!userId) {
      return NextResponse.json(
        { error: 'Unauthorized', detail: 'You must be signed in to connect the CLI.' },
        { status: 401 }
      );
    }

    if (!orgId) {
      return NextResponse.json(
        {
          error: 'NoOrganization',
          detail: 'You must be part of an organization to use the CLI. Please create or join an organization first.'
        },
        { status: 403 }
      );
    }

    // Get user details for the key name and response
    const user = await currentUser();
    const email = user?.emailAddresses?.[0]?.emailAddress || 'Unknown';
    const keyName = `CLI - ${email}`;

    const client = await clerkClient();

    // Check if a CLI key already exists for this user
    const existingKeys = await client.apiKeys.list({
      subject: orgId,
    });

    const existingCliKey = existingKeys.data.find(
      (key) => key.name === keyName && key.createdBy === userId
    );

    if (existingCliKey) {
      // Key exists but we can't retrieve the secret
      // We need to create a new one (Clerk doesn't allow secret retrieval after creation)
      // Delete the old one first to avoid accumulating CLI keys
      await client.apiKeys.delete(existingCliKey.id);
    }

    // Create a new CLI API key with full scopes
    const allScopes = [
      'read:analysis',
      'write:analysis',
      'read:findings',
      'write:findings',
      'read:fixes',
      'write:fixes',
      'read:repositories',
      'write:repositories',
    ];

    const apiKey = await client.apiKeys.create({
      name: keyName,
      subject: orgId,
      scopes: allScopes,
      createdBy: userId,
      // CLI keys don't expire by default (user can revoke from dashboard)
    });

    // Get organization name for display
    const org = await client.organizations.getOrganization({ organizationId: orgId });

    // Log the CLI authentication event
    console.info(`[CLI Auth] User ${email} (${userId}) authenticated CLI for org ${org.name} (${orgId})`);

    return NextResponse.json({
      success: true,
      key: apiKey.secret,
      key_id: apiKey.id,
      user: {
        email,
        name: user?.firstName ? `${user.firstName} ${user.lastName || ''}`.trim() : email,
      },
      organization: {
        id: orgId,
        name: org.name,
      },
      scopes: allScopes,
      created_at: new Date(apiKey.createdAt).toISOString(),
    });
  } catch (error) {
    console.error('[CLI Auth] Error creating CLI token:', error);

    // Handle specific Clerk errors
    if (error instanceof Error) {
      if (error.message.includes('not found')) {
        return NextResponse.json(
          { error: 'NotFound', detail: 'Organization not found.' },
          { status: 404 }
        );
      }
    }

    return NextResponse.json(
      { error: 'ServerError', detail: 'Failed to create CLI authentication token.' },
      { status: 500 }
    );
  }
}
