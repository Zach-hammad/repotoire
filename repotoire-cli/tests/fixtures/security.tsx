/**
 * TSX fixture with intentional React and security issues.
 * Used by integration tests to verify React-specific detectors fire.
 */

import React, { useState, useEffect } from "react";

// ============================================================================
// 1. React hook inside a conditional
// ============================================================================

interface UserProfileProps {
    userId: string;
    isLoggedIn: boolean;
}

function UserProfile({ userId, isLoggedIn }: UserProfileProps) {
    // Hook called conditionally - violates Rules of Hooks
    if (isLoggedIn) {
        const [user, setUser] = useState(null);
        useEffect(() => {
            fetch(`/api/users/${userId}`).then(r => r.json()).then(setUser);
        }, [userId]);
    }

    const [theme, setTheme] = useState("light");

    return <div className={theme}>Profile for {userId}</div>;
}

// ============================================================================
// 2. dangerouslySetInnerHTML with user input
// ============================================================================

interface CommentProps {
    content: string;
    authorHtml: string;
}

function UnsafeComment({ content, authorHtml }: CommentProps) {
    return (
        <div>
            <div dangerouslySetInnerHTML={{ __html: content }} />
            <span dangerouslySetInnerHTML={{ __html: authorHtml }} />
        </div>
    );
}

// ============================================================================
// 3. More React anti-patterns
// ============================================================================

function ConditionalHooks({ show }: { show: boolean }) {
    if (show) {
        const [count, setCount] = useState(0);
        return <button onClick={() => setCount(count + 1)}>{count}</button>;
    }
    return <div>Hidden</div>;
}

function EarlyReturnHook({ data }: { data: any }) {
    if (!data) {
        return <div>Loading...</div>;
    }
    // Hook after early return - violates Rules of Hooks
    const [processed, setProcessed] = useState(false);
    useEffect(() => {
        setProcessed(true);
    }, [data]);

    return <div>{processed ? "Done" : "Processing"}</div>;
}

// ============================================================================
// 4. XSS via innerHTML in React component
// ============================================================================

function RawHtmlRenderer({ html }: { html: string }) {
    const ref = React.useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (ref.current) {
            ref.current.innerHTML = html;
        }
    }, [html]);

    return <div ref={ref} />;
}

// ============================================================================
// 5. SQL in a React component (server-side rendering context)
// ============================================================================

async function UserPage({ params }: { params: { id: string } }) {
    const db: any = {};
    const query = "SELECT * FROM users WHERE id = '" + params.id + "'";
    const user = await db.query(query);
    return <div>{user.name}</div>;
}

// ============================================================================
// 6. Magic numbers in JSX
// ============================================================================

function Dashboard() {
    return (
        <div style={{ maxWidth: 1200, padding: 16 }}>
            <div style={{ height: 64 }}>Header</div>
            <div style={{ gridTemplateColumns: "repeat(3, 1fr)", gap: 24 }}>
                Content
            </div>
        </div>
    );
}

// ============================================================================
// 7. console.log in component
// ============================================================================

function DebugComponent({ data }: { data: any }) {
    console.log("DebugComponent render:", data);
    console.log("props:", JSON.stringify(data));

    return <pre>{JSON.stringify(data, null, 2)}</pre>;
}

export { UserProfile, UnsafeComment, ConditionalHooks, EarlyReturnHook,
         RawHtmlRenderer, UserPage, Dashboard, DebugComponent };
