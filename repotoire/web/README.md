# Repotoire Web Dashboard

A modern web dashboard for managing AI-generated code fixes in the Repotoire code health platform.

## Features

- **Dashboard Overview**: Real-time analytics with charts showing fix trends, confidence distribution, and file hotspots
- **Fix Management**: Browse, filter, and manage AI-generated code fixes
- **Fix Review**: Side-by-side diff viewer with syntax highlighting, evidence panel, and approval workflow
- **Dark Mode**: Full dark mode support with system theme detection
- **Responsive Design**: Mobile-friendly interface

## Tech Stack

- **Next.js 16** - React framework with App Router
- **TypeScript** - Type-safe development
- **Tailwind CSS 4** - Utility-first styling
- **shadcn/ui** - High-quality UI components
- **Recharts** - Data visualization
- **SWR** - Data fetching and caching

## Quick Start

### Prerequisites

- Node.js 18+
- npm or yarn

### Development

```bash
# Navigate to web directory
cd repotoire/web

# Install dependencies
npm install

# Start development server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) to view the landing page.
Navigate to [http://localhost:3000/dashboard](http://localhost:3000/dashboard) for the fix management dashboard.

### Configuration

Create a `.env.local` file (or copy from `.env.local.example`):

```bash
# API Configuration
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1

# Enable mock data for development
NEXT_PUBLIC_USE_MOCK=false
```

When `NEXT_PUBLIC_API_URL` is empty or `NEXT_PUBLIC_USE_MOCK=true`, the dashboard uses mock data.

### Build for Production

```bash
npm run build
npm start
```

## Project Structure

```
repotoire/web/
├── src/
│   ├── app/                    # Next.js App Router pages
│   │   ├── dashboard/          # Dashboard pages
│   │   │   ├── page.tsx        # Overview/analytics
│   │   │   ├── fixes/          # Fix list and review
│   │   │   ├── files/          # File hotspots
│   │   │   └── settings/       # User settings
│   │   ├── layout.tsx          # Root layout with theme
│   │   └── page.tsx            # Landing page
│   ├── components/
│   │   ├── dashboard/          # Dashboard-specific components
│   │   │   ├── diff-viewer.tsx # Code diff visualization
│   │   │   └── theme-toggle.tsx
│   │   ├── sections/           # Landing page sections (v0)
│   │   └── ui/                 # shadcn/ui components
│   ├── lib/
│   │   ├── api.ts              # API client
│   │   ├── hooks.ts            # SWR data hooks
│   │   ├── mock-data.ts        # Development mock data
│   │   └── utils.ts            # Utility functions
│   └── types/
│       └── index.ts            # TypeScript type definitions
├── package.json
├── tsconfig.json
└── README.md
```

## API Endpoints

The dashboard expects these backend endpoints:

### Fixes

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/fixes` | List fixes with filters |
| GET | `/fixes/{id}` | Get fix details |
| POST | `/fixes/{id}/approve` | Approve a fix |
| POST | `/fixes/{id}/reject` | Reject a fix |
| POST | `/fixes/{id}/apply` | Apply an approved fix |
| POST | `/fixes/{id}/comment` | Add comment |
| GET | `/fixes/{id}/comments` | Get comments |
| POST | `/fixes/batch/approve` | Batch approve |
| POST | `/fixes/batch/reject` | Batch reject |

### Analytics

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/analytics/summary` | Dashboard summary |
| GET | `/analytics/trends` | Time-series data |
| GET | `/analytics/by-type` | Fix type breakdown |
| GET | `/analytics/by-file` | File hotspots |

## Development Notes

### Adding Components

Use shadcn CLI to add new components:

```bash
npx shadcn@latest add <component-name>
```

### Mock Data

The mock data system (`src/lib/mock-data.ts`) generates realistic fix proposals for development. To customize:

1. Edit `generateMockFixes()` to change the data
2. Use `NEXT_PUBLIC_USE_MOCK=true` to enable

### API Integration

The API client (`src/lib/api.ts`) automatically:
- Uses mock data when API URL is not configured
- Handles errors with proper error types
- Supports filtering, pagination, and sorting

### Theme Customization

Theme variables are defined in `src/app/globals.css`. The dashboard uses the dark theme by default.

## Contributing

See the main [CLAUDE.md](../../CLAUDE.md) for contribution guidelines.
