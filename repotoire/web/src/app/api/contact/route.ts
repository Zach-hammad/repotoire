import { NextResponse } from 'next/server';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { name, email, company, message } = body;

    // Validate required fields
    if (!name || !email || !message) {
      return NextResponse.json(
        { error: 'Missing required fields' },
        { status: 400 }
      );
    }

    // Log the contact form submission
    // In production, send to email service, CRM, or Slack
    console.info('[Contact Form]', {
      name,
      email,
      company: company || 'N/A',
      message,
      timestamp: new Date().toISOString(),
    });

    // TODO: Integrate with email service (SendGrid, Resend, etc.)
    // await sendEmail({
    //   to: 'hello@repotoire.com',
    //   subject: `Contact form: ${name}`,
    //   body: `From: ${name} <${email}>\nCompany: ${company}\n\n${message}`,
    // });

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('[Contact Form Error]', error);
    return NextResponse.json(
      { error: 'Failed to process request' },
      { status: 500 }
    );
  }
}
